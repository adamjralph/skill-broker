#!/usr/bin/env python3
"""Replay Corpus extraction and Reviewed Cases (ADR-0020, ticket #54).

Drives the corpus harness at its own seam: extraction from a Hermes-shaped ``state.db``, the
machine-local Case store, the committed reviewed-label store, and SHA-bound Recordings. The
assertions are the ticket's acceptance criteria — per-source opt-in, redaction, deletion,
provenance and the extraction-time Authorised Closure, hand-corrected ground truth, hash-bound
replay, a thin-profile report that never borrows another profile's cases, and a disjoint
threshold/held-out split.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import Broker, RecordingMismatch, TurnOutcome  # noqa: E402
import corpus as corpus_cli  # noqa: E402  (scripts/corpus.py)
from broker.corpus import (  # noqa: E402
    DEFAULT_CONSENTED_SOURCES,
    DEFAULT_STAGE5_MINIMUM,
    LABEL_VERSION,
    NO_SKILL,
    ConsentError,
    CorpusError,
    CorpusStore,
    LabelError,
    LabelStore,
    RecordingStore,
    claim_from_decision,
    decision_for_case,
    digest,
    extract_cases,
    load_reviewed_cases,
    profile_status,
    read_turns,
    redact,
    retention_report,
    sessions_db,
    split_for,
)
from broker.judgment import CHOICE_KEYS  # noqa: E402
from broker.types import Judgment, RouteDecision  # noqa: E402
from broker_fixture import RecordingEvidenceLog, judgment_claim  # noqa: E402
from corpus_fixture import add_session, add_turn, make_corpus_fixture  # noqa: E402
from store_fixture import ALIASED, DISTINCT, write_policy  # noqa: E402


class RedactionTest(unittest.TestCase):
    """Secrets and identifiers are removed from request text at extraction."""

    def assertRedacted(self, text: str, *secrets: str) -> str:
        redacted = redact(text)
        for secret in secrets:
            self.assertNotIn(secret, redacted, (secret, redacted))
        return redacted

    def test_bearer_tokens_and_jwts_are_removed(self) -> None:
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
        redacted = self.assertRedacted(f"Authorization: Bearer {token}", token, "eyJ")
        self.assertIn("Bearer <redacted>", redacted)

    def test_provider_api_keys_are_removed(self) -> None:
        self.assertRedacted("use sk-abcdefghijklmnopqrstuvwx now",
                            "sk-abcdefghijklmnopqrstuvwx")
        self.assertRedacted("token ghp_abcdefghijklmnopqrstuvwx", "ghp_abcdefghijklmnopqrstuvwx")
        self.assertRedacted("key AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE")
        self.assertRedacted("bot xoxb-1234567890-abcdef", "xoxb-1234567890-abcdef")

    def test_key_value_secrets_are_removed(self) -> None:
        redacted = self.assertRedacted("api_key=supersecretvalue", "supersecretvalue")
        self.assertIn("<redacted>", redacted)
        self.assertRedacted("password: hunter2hunter2", "hunter2hunter2")

    def test_private_keys_are_removed(self) -> None:
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertRedacted(f"here {block} done", "MIIEow")

    def test_emails_phones_and_long_ids_are_removed(self) -> None:
        redacted = self.assertRedacted(
            "mail adam@example.com or call +61 412 345 678 id 8528778401",
            "adam@example.com", "+61 412 345 678", "8528778401")
        self.assertIn("<email>", redacted)
        self.assertIn("<phone>", redacted)
        self.assertIn("<id>", redacted)

    def test_ordinary_text_and_dates_are_untouched(self) -> None:
        text = "Write a draft about the 2026-09-15 launch and stillroom-signal-workflow"
        self.assertEqual(redact(text), text)


class SplitTest(unittest.TestCase):
    """The split is deterministic, disjoint and configurable by ratio."""

    def test_split_is_deterministic(self) -> None:
        digest = "a" * 64
        self.assertEqual(split_for(digest, held_out_ratio=0.25),
                         split_for(digest, held_out_ratio=0.25))

    def test_split_buckets_by_ratio(self) -> None:
        held = sum(1 for index in range(400)
                   if split_for(digest(f"case-{index}"), held_out_ratio=0.25) == "held_out")
        self.assertGreater(held, 70)
        self.assertLess(held, 130)

    def test_extremes_assign_every_case(self) -> None:
        self.assertEqual(split_for("b" * 64, held_out_ratio=0.0), "threshold")
        self.assertEqual(split_for("b" * 64, held_out_ratio=1.0), "held_out")


class ExtractionTest(unittest.TestCase):
    """Per-source extraction from a Hermes state DB into the machine-local corpus."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)

    def stage(self) -> None:
        db = self.fixture.db
        add_session(db, "cli-1", "cli", profile_name=self.store.profile, started_at=10.0,
                    title="Draft a post", cwd="/work")
        add_turn(db, "cli-1", "Please write a post using local-writing", timestamp=11.0)
        add_turn(db, "cli-1", "Now shorten it", timestamp=12.0)
        add_session(db, "tg-1", "telegram", profile_name=self.store.profile, started_at=20.0)
        add_turn(db, "tg-1", "a third party said hello", timestamp=21.0)
        add_session(db, "kb-1", "kanban", profile_name=self.store.profile, started_at=30.0)
        add_turn(db, "kb-1", "work kanban task t_1", timestamp=31.0)

    def extract(self, **overrides):
        return extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                             corpus=self.corpus, now="2026-09-21T00:00:00+00:00", **overrides)

    def test_default_extraction_takes_only_agent_authored_sources(self) -> None:
        self.stage()
        report = self.extract()
        self.assertEqual(report.extracted, 2)
        self.assertEqual(report.refused, {"kanban": 1, "telegram": 1})
        sources = {case.source for case in self.corpus.cases(self.store.profile)}
        self.assertEqual(sources, {"cli"})

    def test_an_explicitly_consented_source_is_extracted(self) -> None:
        self.stage()
        report = self.extract(consent=DEFAULT_CONSENTED_SOURCES | {"telegram"})
        self.assertEqual(report.refused, {"kanban": 1})
        self.assertIn("telegram", {case.source for case in self.corpus.cases(self.store.profile)})

    def test_an_unconsented_source_cannot_be_added_to_the_store(self) -> None:
        self.stage()
        self.extract()
        case = self.corpus.cases(self.store.profile)[0]
        self.corpus.record_consent(DEFAULT_CONSENTED_SOURCES, created_at="2026-09-21T00:00:00+00:00")
        with self.assertRaises(ConsentError):
            self.corpus.add(replace(case, source="telegram"))

    def test_the_index_records_the_consented_sources_and_date(self) -> None:
        self.stage()
        self.extract(consent=DEFAULT_CONSENTED_SOURCES | {"telegram"})
        index = self.corpus.index()
        self.assertEqual(index["consented_sources"], sorted(DEFAULT_CONSENTED_SOURCES | {"telegram"}))
        self.assertEqual(index["consented_at"], "2026-09-21T00:00:00+00:00")

    def test_request_text_is_redacted_before_it_is_stored(self) -> None:
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(self.fixture.db, "cli-1", "email adam@example.com for the token ghp_abcdefghijklmnopqrstuvwx")
        self.extract()
        case = self.corpus.cases(self.store.profile)[0]
        self.assertNotIn("adam@example.com", case.request)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwx", case.request)

    def test_non_request_and_other_profile_turns_are_skipped(self) -> None:
        db = self.fixture.db
        add_session(db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(db, "cli-1", "real request", timestamp=1.0)
        add_turn(db, "cli-1", "internal notice", display_kind="internal_notification", timestamp=2.0)
        add_turn(db, "cli-1", "compacted", compressed=1, timestamp=3.0)
        add_turn(db, "cli-1", "inactive", active=0, timestamp=4.0)
        add_session(db, "other-1", "cli", profile_name="some-other-profile")
        add_turn(db, "other-1", "someone else's request", timestamp=5.0)
        report = self.extract()
        self.assertEqual(report.extracted, 1)
        self.assertEqual(report.skipped_other_profile, 1)

    def test_identical_requests_are_recorded_once(self) -> None:
        db = self.fixture.db
        add_session(db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(db, "cli-1", "Repeat me", timestamp=1.0)
        add_turn(db, "cli-1", "Repeat me", timestamp=2.0)
        report = self.extract()
        self.assertEqual(report.extracted, 1)
        self.assertEqual(report.duplicates, 1)

    def test_a_case_records_provenance_and_the_authorised_closure(self) -> None:
        self.stage()
        self.extract()
        case = self.corpus.cases(self.store.profile)[0]
        row = case.to_record()
        self.assertEqual(row["source"], "cli")
        self.assertEqual(row["session_id"], "cli-1")
        self.assertEqual(row["title"], "Draft a post")
        self.assertEqual(row["cwd"], "/work")
        self.assertEqual(row["extracted_at"], "2026-09-21T00:00:00+00:00")
        closure = {entry["id"] for entry in row["authorised_closure"]}
        self.assertEqual(closure, {DISTINCT.id, ALIASED.id})
        self.assertTrue(case.verify())

    def test_a_profile_without_a_policy_fails_closed(self) -> None:
        self.stage()
        self.store.policy_path.unlink()
        with self.assertRaises(CorpusError):
            self.extract()
        self.assertEqual(self.corpus.cases(self.store.profile), [])

    def test_a_failed_extraction_does_not_widen_consent(self) -> None:
        self.stage()
        self.store.policy_path.unlink()
        with self.assertRaises(CorpusError):
            self.extract()
        self.assertEqual(self.corpus.index()["consented_sources"], [])

    def test_an_untagged_turn_belongs_only_to_the_default_profile(self) -> None:
        add_session(self.fixture.db, "cli-1", "cli", profile_name=None)
        add_turn(self.fixture.db, "cli-1", "orphan request", timestamp=1.0)
        report = self.extract()
        self.assertEqual(report.extracted, 0)
        self.assertEqual(report.skipped_other_profile, 1)

    def test_read_turns_exposes_the_turn_stream(self) -> None:
        self.stage()
        turns = read_turns(self.fixture.db)
        self.assertEqual([(turn.source, turn.content) for turn in turns],
                         [("cli", "Please write a post using local-writing"),
                          ("cli", "Now shorten it"),
                          ("telegram", "a third party said hello"),
                          ("kanban", "work kanban task t_1")])


class RetentionTest(unittest.TestCase):
    """The Stage 5/6 retention review can see exactly what raw text is held, per source."""

    def test_retention_reports_counts_sources_and_the_oldest_case(self) -> None:
        fixture = make_corpus_fixture()
        self.addCleanup(fixture.close)
        store = fixture.store
        corpus = CorpusStore(fixture.corpus)
        add_session(fixture.db, "cli-1", "cli", profile_name=store.profile)
        add_turn(fixture.db, "cli-1", "first", timestamp=1.0)
        add_session(fixture.db, "tool-1", "tool", profile_name=store.profile)
        add_turn(fixture.db, "tool-1", "second", timestamp=2.0)
        extract_cases(store.profile, store=store.root, db=fixture.db, corpus=corpus,
                      now="2026-09-21T00:00:00+00:00")
        report = retention_report(corpus, now="2026-12-21T00:00:00+00:00").to_record()
        self.assertEqual(report["now"], "2026-12-21T00:00:00+00:00")
        entry = report["profiles"][0]
        self.assertEqual(entry["cases"], 2)
        self.assertEqual(entry["sources"], {"cli": 1, "tool": 1})
        self.assertEqual(entry["oldest_extracted_at"], "2026-09-21T00:00:00+00:00")


class DeletionTest(unittest.TestCase):
    """Raw request text can be deleted on request, and revocation removes a source's cases."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        db = self.fixture.db
        add_session(db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(db, "cli-1", "first secret request", timestamp=1.0)
        add_turn(db, "cli-1", "second request", timestamp=2.0)
        add_session(db, "tg-1", "telegram", profile_name=self.store.profile)
        add_turn(db, "tg-1", "third party", timestamp=3.0)
        extract_cases(self.store.profile, store=self.store.root, db=db, corpus=self.corpus,
                      consent=DEFAULT_CONSENTED_SOURCES | {"telegram"},
                      now="2026-09-21T00:00:00+00:00")

    def test_purge_case_removes_only_that_case(self) -> None:
        case = next(case for case in self.corpus.cases(self.store.profile)
                    if case.request == "first secret request")
        self.assertTrue(self.corpus.purge_case(self.store.profile, case.case_sha256))
        remaining = [c.request for c in self.corpus.cases(self.store.profile)]
        self.assertNotIn("first secret request", remaining)
        self.assertIn("second request", remaining)

    def test_purge_source_removes_its_cases(self) -> None:
        removed = self.corpus.purge_source("telegram")
        self.assertEqual(removed, 1)
        self.assertNotIn("telegram", {c.source for c in self.corpus.cases(self.store.profile)})

    def test_revoke_source_also_removes_consent(self) -> None:
        self.corpus.revoke_source("telegram")
        self.assertNotIn("telegram", self.corpus.index()["consented_sources"])
        self.assertNotIn("telegram", {c.source for c in self.corpus.cases(self.store.profile)})

    def test_purge_profile_removes_every_case_of_one_profile(self) -> None:
        self.assertEqual(self.corpus.purge_profile(self.store.profile), 3)
        self.assertEqual(list(self.corpus.cases(self.store.profile)), [])


class LabelTest(unittest.TestCase):
    """Only a hand-corrected Reviewed Case carries ground truth; labels hold no request text."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        self.labels = LabelStore(self.fixture.labels)
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(self.fixture.db, "cli-1", "please write something private",
                 timestamp=1.0)
        add_turn(self.fixture.db, "cli-1", "second turn", timestamp=2.0)
        extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                      corpus=self.corpus, now="2026-09-21T00:00:00+00:00")
        self.cases = {case.request: case for case in self.corpus.cases(self.store.profile)}

    def test_review_records_a_primary_skill_outcome(self) -> None:
        case = self.cases["please write something private"]
        review = self.labels.review(case, ALIASED.id, reviewer="adam",
                                    reviewed_at="2026-09-21T01:00:00+00:00")
        self.assertEqual(review.outcome, ALIASED.id)
        self.assertEqual(self.labels.reviews(self.store.profile)[case.case_sha256].outcome, ALIASED.id)

    def test_review_accepts_an_explicit_no_skill_outcome(self) -> None:
        case = self.cases["second turn"]
        self.labels.review(case, NO_SKILL)
        self.assertEqual(self.labels.reviews(self.store.profile)[case.case_sha256].outcome, NO_SKILL)

    def test_a_primary_skill_outcome_must_be_authorised(self) -> None:
        case = self.cases["second turn"]
        with self.assertRaises(LabelError):
            self.labels.review(case, "someone.not-in-the-closure")

    def test_only_reviewed_cases_count(self) -> None:
        reviewed = load_reviewed_cases(self.corpus, self.labels, self.store.profile)
        self.assertEqual(reviewed, [])
        case = self.cases["second turn"]
        self.labels.review(case, NO_SKILL)
        reviewed = load_reviewed_cases(self.corpus, self.labels, self.store.profile)
        self.assertEqual([entry.case.case_sha256 for entry in reviewed], [case.case_sha256])

    def test_the_label_file_never_carries_request_text(self) -> None:
        case = self.cases["please write something private"]
        self.labels.review(case, ALIASED.id)
        path = self.labels.path(self.store.profile)
        text = path.read_text()
        self.assertNotIn("please write something private", text)
        record = json.loads(text)
        self.assertEqual(record["label_version"], LABEL_VERSION)
        self.assertNotIn("request", record)


class RecordingTest(unittest.TestCase):
    """A Recording is bound to its Case by content hash and replays deterministically offline."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        self.recordings = RecordingStore(self.fixture.recordings)
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(self.fixture.db, "cli-1", "Draft it with local-writing", timestamp=1.0)
        extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                      corpus=self.corpus, now="2026-09-21T00:00:00+00:00")
        self.case = self.corpus.cases(self.store.profile)[0]

    def claim(self, primary: str) -> dict:
        return judgment_claim([DISTINCT.id, ALIASED.id], primary=primary)

    def test_write_binds_the_recording_to_the_request_hash(self) -> None:
        recording = self.recordings.write(self.case, self.claim(ALIASED.id))
        self.assertEqual(recording.case_sha256, self.case.case_sha256)
        self.assertTrue(recording.matches(self.case.request))
        self.assertTrue(self.recordings.path(self.store.profile,
                                             self.case.case_sha256).exists())

    def test_bind_replays_the_same_route_decision(self) -> None:
        self.recordings.write(self.case, self.claim(ALIASED.id))
        records = []
        for _ in range(2):
            evidence = RecordingEvidenceLog()
            broker = Broker(store=self.store.root, evidence_log=evidence,
                            judgment_source=self.recordings.bind(self.case))
            result = broker.prepare_turn(self.case.request, self.store.profile,
                                         {"session_id": "s"})
            self.assertEqual(result.outcome, TurnOutcome.GRANTED)
            self.assertEqual([grant.id for grant in result.grants], [ALIASED.id])
            record = dict(evidence.records[0])
            record.pop("judgment_latency_ms")
            records.append(record)
        self.assertEqual(records[0], records[1])

    def test_a_missing_recording_is_refused(self) -> None:
        with self.assertRaises(RecordingMismatch):
            self.recordings.bind(self.case)

    def test_a_recording_refuses_a_drifted_case(self) -> None:
        self.recordings.write(self.case, self.claim(ALIASED.id))
        drifted = replace(self.case, request=self.case.request + " and more")
        with self.assertRaises(RecordingMismatch):
            self.recordings.bind(drifted)


class ClaimFromEvidenceTest(unittest.TestCase):
    """Freeze a Recording from the live broker's recorded Judgment (ticket #60)."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(self.fixture.db, "cli-1", "Draft it with local-writing", timestamp=1.0)
        extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                      corpus=self.corpus, now="2026-09-21T00:00:00+00:00")
        self.case = self.corpus.cases(self.store.profile)[0]

    def claim(self) -> dict:
        return judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)

    def decision(self, *, source: str = "jev", judgment: bool = True,
                 session: str | None = None, request_sha256: str | None = None) -> RouteDecision:
        return RouteDecision(
            profile=self.store.profile,
            session_id=self.case.session_id if session is None else session,
            turn_id="live:ephemeral:turn",
            outcome=TurnOutcome.GRANTED, reasons=(),
            request_sha256=(self.case.request_sha256 if request_sha256 is None
                            else request_sha256),
            request_chars=len(self.case.request),
            judgment=Judgment.from_record(self.claim()) if judgment else None,
            judgment_source=source)

    def test_the_decision_joins_by_request_hash_not_the_live_turn_id(self) -> None:
        decision = self.decision()
        self.assertIs(decision_for_case(self.case, [decision]), decision)

    def test_a_decision_from_another_session_is_not_a_match(self) -> None:
        with self.assertRaises(CorpusError):
            decision_for_case(self.case, [self.decision(session="some-other-session")])

    def test_a_case_without_a_request_hash_is_refused(self) -> None:
        legacy = replace(self.case, request_sha256="")
        with self.assertRaises(CorpusError):
            decision_for_case(legacy, [self.decision()])

    def test_a_decision_without_a_judgment_is_refused(self) -> None:
        with self.assertRaises(CorpusError):
            claim_from_decision(self.decision(judgment=False))

    def test_the_claim_is_exactly_the_choice_the_validator_accepts(self) -> None:
        claim = claim_from_decision(self.decision())
        self.assertEqual(set(claim), CHOICE_KEYS)
        self.assertEqual(claim, self.claim())


class ThinProfileTest(unittest.TestCase):
    """A profile below the Stage 5 minimum is reported, never padded from another profile."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        self.labels = LabelStore(self.fixture.labels)

    def stage(self, profile: str, turns: int) -> None:
        add_session(self.fixture.db, f"{profile}-{turns}", "cli", profile_name=profile)
        for index in range(turns):
            add_turn(self.fixture.db, f"{profile}-{turns}", f"{profile} request {index}",
                     timestamp=float(index))
        extract_cases(profile, store=self.store.root, db=self.fixture.db, corpus=self.corpus,
                      now="2026-09-21T00:00:00+00:00")
        for case in self.corpus.cases(profile):
            self.labels.review(case, NO_SKILL)

    def test_a_thin_profile_stays_in_shadow(self) -> None:
        self.stage(self.store.profile, 3)
        status = profile_status(self.corpus, self.labels, self.store.profile, minimum=20)
        self.assertTrue(status.below_minimum)
        self.assertEqual(status.stage, "shadow")
        self.assertIn("minimum", status.message)

    def test_another_profiles_cases_do_not_pad_a_thin_profile(self) -> None:
        write_policy(self.store.root, "a-fat-profile", foundation=[DISTINCT.id], brokered=[])
        self.stage(self.store.profile, 3)
        self.stage("a-fat-profile", 30)
        status = profile_status(self.corpus, self.labels, self.store.profile, minimum=20)
        self.assertTrue(status.below_minimum)
        self.assertEqual(status.reviewed, 3)

    def test_enough_reviewed_cases_reach_the_gate(self) -> None:
        self.stage(self.store.profile, 22)
        status = profile_status(self.corpus, self.labels, self.store.profile,
                                minimum=DEFAULT_STAGE5_MINIMUM)
        self.assertFalse(status.below_minimum)
        self.assertEqual(status.stage, "gateable")


class ReviewedSplitTest(unittest.TestCase):
    """Reviewed Cases split into a disjoint threshold-setting set and held-out set."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        self.labels = LabelStore(self.fixture.labels)
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        for index in range(40):
            add_turn(self.fixture.db, "cli-1", f"request number {index}",
                     timestamp=float(index))
        extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                      corpus=self.corpus, held_out_ratio=0.25,
                      now="2026-09-21T00:00:00+00:00")
        for case in self.corpus.cases(self.store.profile):
            self.labels.review(case, NO_SKILL)

    def test_every_reviewed_case_lands_in_exactly_one_split(self) -> None:
        reviewed = load_reviewed_cases(self.corpus, self.labels, self.store.profile)
        threshold = [c for c in reviewed if c.split == "threshold"]
        held_out = [c for c in reviewed if c.split == "held_out"]
        self.assertEqual(len(threshold) + len(held_out), len(reviewed))
        self.assertTrue(held_out)
        self.assertTrue(threshold)
        self.assertFalse({c.case.case_sha256 for c in threshold}
                         & {c.case.case_sha256 for c in held_out})

    def test_a_profile_can_load_only_one_split(self) -> None:
        threshold = load_reviewed_cases(self.corpus, self.labels, self.store.profile,
                                        split="threshold")
        held_out = load_reviewed_cases(self.corpus, self.labels, self.store.profile,
                                       split="held_out")
        self.assertTrue(all(case.split == "threshold" for case in threshold))
        self.assertTrue(all(case.split == "held_out" for case in held_out))


class CorpusCliTest(unittest.TestCase):
    """The CLI drives the same harness without importing any repo internals by hand."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        add_session(self.fixture.db, "cli-1", "cli", profile_name=self.store.profile)
        add_turn(self.fixture.db, "cli-1", "a private first request", timestamp=1.0)
        add_turn(self.fixture.db, "cli-1", "a second request", timestamp=2.0)

    def run_cli(self, *argv: str) -> dict:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = corpus_cli.main(list(argv))
        self.assertEqual(code, 0, buffer.getvalue())
        return json.loads(buffer.getvalue())

    def extract(self) -> dict:
        return self.run_cli(
            "extract", "--profile", self.store.profile, "--store", str(self.store.root),
            "--sessions-db", str(self.fixture.db), "--corpus", str(self.fixture.corpus))

    def test_extract_review_status_and_purge_round_trip(self) -> None:
        report = self.extract()
        self.assertEqual(report["reports"][0]["extracted"], 2)
        corpus = CorpusStore(self.fixture.corpus)
        case = next(c for c in corpus.cases(self.store.profile)
                    if c.request == "a second request")
        reviewed = self.run_cli(
            "review", "--profile", self.store.profile, "--case", case.case_sha256,
            "--outcome", NO_SKILL, "--corpus", str(self.fixture.corpus),
            "--labels", str(self.fixture.labels))
        self.assertEqual(reviewed["review"]["outcome"], NO_SKILL)
        status = self.run_cli("status", "--corpus", str(self.fixture.corpus),
                              "--labels", str(self.fixture.labels), "--minimum", "20")
        self.assertEqual(status["profiles"][0]["reviewed"], 1)
        self.assertTrue(status["profiles"][0]["below_minimum"])
        purged = self.run_cli("purge", "--profile", self.store.profile, "--corpus",
                              str(self.fixture.corpus), "--case", case.case_sha256)
        self.assertTrue(purged["purged"]["removed"])
        self.assertIsNone(corpus.get(self.store.profile, case.case_sha256))

    def test_queue_writes_a_machine_local_review_queue_with_prelabels(self) -> None:
        self.extract()

        result = self.run_cli(
            "queue", "--profile", self.store.profile, "--corpus", str(self.fixture.corpus),
            "--store", str(self.store.root), "--judgment", "first_candidate",
            "--write-prelabels")

        self.assertEqual(result["cases"], 2)
        self.assertTrue(result["prelabels_written"])
        queue = Path(result["queue"])
        self.assertTrue(str(queue).startswith(str(self.fixture.corpus)))
        text = queue.read_text(encoding="utf-8")
        corpus = CorpusStore(self.fixture.corpus)
        case = next(c for c in corpus.cases(self.store.profile)
                    if c.request == "a second request")
        self.assertIn(case.case_sha256, text)
        self.assertIn("a second request", text)
        self.assertTrue(corpus.get(self.store.profile, case.case_sha256).prelabel)

    def test_an_unconsented_source_is_refused_not_written(self) -> None:
        add_session(self.fixture.db, "tg-1", "telegram", profile_name=self.store.profile)
        add_turn(self.fixture.db, "tg-1", "third party", timestamp=3.0)
        report = self.extract()
        self.assertEqual(report["reports"][0]["refused"], {"telegram": 1})
        self.assertEqual(len(list(CorpusStore(self.fixture.corpus).cases(self.store.profile))), 2)

    def _second_case(self):
        self.extract()
        corpus = CorpusStore(self.fixture.corpus)
        return next(c for c in corpus.cases(self.store.profile)
                    if c.request == "a second request")

    def _evidence(self, case, *, source: str) -> Path:
        claim = judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)
        decision = RouteDecision(
            profile=self.store.profile, session_id=case.session_id, turn_id="live:ephemeral",
            outcome=TurnOutcome.GRANTED, reasons=(),
            request_sha256=case.request_sha256, request_chars=len(case.request),
            judgment=Judgment.from_record(claim), judgment_source=source)
        path = Path(self.fixture.corpus).parent / "route_decisions.jsonl"
        path.write_text(json.dumps(decision.to_record()) + "\n", encoding="utf-8")
        return path

    def test_record_from_evidence_freezes_the_recorded_judgment(self) -> None:
        case = self._second_case()
        evidence = self._evidence(case, source="jev")
        recorded = self.run_cli(
            "record", "--profile", self.store.profile, "--case", case.case_sha256,
            "--from-evidence", str(evidence), "--corpus", str(self.fixture.corpus),
            "--recordings", str(self.fixture.recordings))
        self.assertEqual(recorded["judgment_source"], "jev")
        self.assertEqual(recorded["recording"]["case_sha256"], case.case_sha256)
        self.assertTrue(RecordingStore(self.fixture.recordings)
                        .path(self.store.profile, case.case_sha256).exists())

    def test_record_from_evidence_refuses_a_non_jev_source(self) -> None:
        case = self._second_case()
        evidence = self._evidence(case, source="first_candidate")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = corpus_cli.main([
                "record", "--profile", self.store.profile, "--case", case.case_sha256,
                "--from-evidence", str(evidence), "--corpus", str(self.fixture.corpus),
                "--recordings", str(self.fixture.recordings)])
        self.assertEqual(code, 1)
        self.assertIn("not live Jev", buffer.getvalue())
        self.assertFalse(RecordingStore(self.fixture.recordings)
                         .path(self.store.profile, case.case_sha256).exists())


class CorpusRootBoundaryTest(unittest.TestCase):
    """Raw request text never lives inside the repository (ADR-0020)."""

    def test_the_corpus_root_may_not_live_in_the_repository(self) -> None:
        repo_corpus = Path(__file__).resolve().parents[1] / "corpus"
        with self.assertRaises(CorpusError):
            CorpusStore(repo_corpus)

    def test_the_default_root_is_machine_local(self) -> None:
        root = CorpusStore().root
        self.assertTrue(str(root).startswith(str(Path.home())))
        self.assertFalse(str(root).startswith(str(Path(__file__).resolve().parents[1])))


class SessionsDbTest(unittest.TestCase):
    """The profile's sessions live in its own Hermes home tree."""

    def test_a_named_profile_uses_its_profile_state_db(self) -> None:
        home = Path("/tmp/hermes-home")
        self.assertEqual(sessions_db("pilot", home), home / "profiles" / "pilot" / "state.db")

    def test_the_default_profile_uses_the_root_state_db(self) -> None:
        home = Path("/tmp/hermes-home")
        self.assertEqual(sessions_db("default", home), home / "state.db")


if __name__ == "__main__":
    unittest.main()
