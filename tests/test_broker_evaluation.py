#!/usr/bin/env python3
"""Offline routing evaluation and pre-registered Soft Thresholds (ticket #55).

Drives the evaluation over Reviewed Cases and SHA-bound Recordings — no network, no model — and
asserts the ticket's acceptance criteria: the per-profile metrics, the held-out split reported
separately and never used for tuning, the pre-registration bound to its corpus and baseline, the
retrieval/Judgment/Grant attribution, and the thin-profile Shadow Mode report.

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

import store_manifest as sm  # noqa: E402
from broker.corpus import (  # noqa: E402
    CorpusStore,
    LabelStore,
    NO_SKILL,
    RecordingStore,
    extract_cases,
)
from broker.evaluation import (  # noqa: E402
    CORRECT,
    CORRECT_NO_SKILL,
    FALSE_INTERVENTION,
    FAILURE,
    GRANT_MISS,
    JUDGMENT_MISS,
    RETRIEVAL_MISS,
    EvaluationError,
    ThresholdRegistry,
    attribute,
    derive_thresholds,
    evaluate_profile,
)
from broker_fixture import judgment_claim  # noqa: E402
from corpus_fixture import add_session, add_turn, make_corpus_fixture  # noqa: E402
from store_fixture import ALIASED, DISTINCT  # noqa: E402

NOW = "2026-09-21T00:00:00+00:00"


class AttributionTest(unittest.TestCase):
    """A wrong outcome is attributed to the stage that decided it (AC5)."""

    def test_a_correct_primary_is_correct(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=ALIASED.id, selected=ALIASED.id,
                      candidates=[ALIASED.id, DISTINCT.id], judgment_primary=ALIASED.id),
            CORRECT)

    def test_a_ground_truth_never_retrieved_is_a_retrieval_miss(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=ALIASED.id, selected=None,
                      candidates=[DISTINCT.id], judgment_primary=None),
            RETRIEVAL_MISS)

    def test_a_candidate_the_judgment_passed_over_is_a_judgment_miss(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=ALIASED.id, selected=DISTINCT.id,
                      candidates=[ALIASED.id, DISTINCT.id], judgment_primary=DISTINCT.id),
            JUDGMENT_MISS)

    def test_a_judgment_choice_the_grant_did_not_supply_is_a_grant_miss(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=ALIASED.id, selected=None,
                      candidates=[ALIASED.id], judgment_primary=ALIASED.id),
            GRANT_MISS)

    def test_a_correct_no_skill_outcome_is_correct(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=None, selected=None,
                      candidates=[ALIASED.id], judgment_primary=None),
            CORRECT_NO_SKILL)

    def test_an_intervention_when_none_was_warranted_is_a_false_intervention(self) -> None:
        self.assertEqual(
            attribute(failed=False, ground_truth=None, selected=ALIASED.id,
                      candidates=[ALIASED.id], judgment_primary=ALIASED.id),
            FALSE_INTERVENTION)

    def test_a_failed_turn_is_a_failure(self) -> None:
        self.assertEqual(
            attribute(failed=True, ground_truth=ALIASED.id, selected=None, candidates=[],
                      judgment_primary=None),
            FAILURE)


class EvaluationFixtureTestCase(unittest.TestCase):
    """A temporary store, corpus, labels and recordings, plus staged Reviewed Cases."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture()
        self.addCleanup(self.fixture.close)
        self.store = self.fixture.store
        self.corpus = CorpusStore(self.fixture.corpus)
        self.labels = LabelStore(self.fixture.labels)
        self.recordings = RecordingStore(self.fixture.recordings)

    def stage(self, entries, *, session_id: str = "cli-1", held_out_ratio: float = 0.0,
              record: bool = True) -> None:
        """Extract this session's turns, hand-review them, and freeze a Recording for each.

        Each entry is ``{"content", "outcome", "recording"?, "reviewed"?, "session"?}``:
        ``outcome`` is the hand-corrected ground truth, ``recording`` is what the frozen Jev
        claim chose (default: the outcome), ``reviewed=False`` leaves the Case unreviewed, and
        ``session`` puts the turn in its own session (default: ``session_id``).
        """
        sessions: dict[str, list[tuple[int, dict]]] = {}
        for index, entry in enumerate(entries):
            sessions.setdefault(entry.get("session", session_id), []).append((index, entry))
        for name, group in sessions.items():
            add_session(self.fixture.db, name, "cli", profile_name=self.store.profile)
            for index, entry in group:
                add_turn(self.fixture.db, name, entry["content"], timestamp=float(index))
        extract_cases(self.store.profile, store=self.store.root, db=self.fixture.db,
                      corpus=self.corpus, held_out_ratio=held_out_ratio, now=NOW)
        cases = {case.request: case for case in self.corpus.cases(self.store.profile)}
        for entry in entries:
            case = cases[entry["content"]]
            if entry.get("reviewed", True):
                self.labels.review(case, entry["outcome"])
            if not record:
                continue
            chosen = entry.get("recording", None if entry["outcome"] == NO_SKILL
                               else entry["outcome"])
            ids = [value.id for value in case.authorised_closure]
            claim = (judgment_claim(ids, primary=None, no_skill=1.0) if chosen is None
                     else judgment_claim(ids, primary=chosen))
            self.recordings.write(case, claim)

    def evaluate(self, *, minimum: int = 1):
        return evaluate_profile(corpus=self.corpus, labels=self.labels,
                                recordings=self.recordings, store=self.store.root,
                                profile=self.store.profile, minimum=minimum)


class MetricsTest(EvaluationFixtureTestCase):
    """The per-profile metrics over Reviewed Cases (AC2)."""

    def test_a_perfect_run_measures_precision_and_recall_of_one(self) -> None:
        self.stage([{"content": "draft prose one", "outcome": ALIASED.id, "session": "cli-1"},
                    {"content": "edit prose two", "outcome": ALIASED.id, "session": "cli-2"}])
        report = self.evaluate()
        metrics = report.threshold
        self.assertEqual(metrics.cases, 2)
        self.assertEqual(metrics.true_positive, 2)
        self.assertEqual(metrics.precision, 1.0)
        self.assertEqual(metrics.recall, 1.0)
        self.assertEqual(metrics.granted, 2)
        self.assertEqual(metrics.unauthorised_grants, 0)
        self.assertEqual(metrics.closure_failures, 0)
        self.assertEqual(metrics.hash_disagreements, 0)
        self.assertEqual(metrics.average_candidates, 2.0)
        self.assertEqual(metrics.pack_deliveries, 2)

    def test_a_wrong_primary_is_a_false_positive_and_a_false_negative(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id,
                     "recording": DISTINCT.id}])
        metrics = self.evaluate().threshold
        self.assertEqual((metrics.true_positive, metrics.false_positive, metrics.false_negative),
                         (0, 1, 1))
        self.assertEqual(metrics.precision, 0.0)
        self.assertEqual(metrics.recall, 0.0)
        self.assertEqual(self.evaluate().attribution[JUDGMENT_MISS], 1)

    def test_the_correct_no_skill_rate_measures_the_silence(self) -> None:
        self.stage([{"content": "just chatting", "outcome": NO_SKILL},
                    {"content": "another hello", "outcome": NO_SKILL,
                     "recording": ALIASED.id}])
        metrics = self.evaluate().threshold
        self.assertEqual(metrics.true_negative, 1)
        self.assertEqual(metrics.false_positive, 1)
        self.assertEqual(metrics.correct_no_skill_rate, 0.5)
        self.assertEqual(self.evaluate().attribution[FALSE_INTERVENTION], 1)

    def test_an_unauthorised_grant_cannot_happen_by_construction(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}])
        metrics = self.evaluate().threshold
        self.assertEqual(metrics.unauthorised_grants, 0)
        self.assertEqual(metrics.unauthorised_grant_rate, 0.0)
        self.assertEqual(metrics.hash_agreement_rate, 1.0)

    def test_duplicate_injection_is_measured_per_session(self) -> None:
        self.stage([{"content": "draft prose one", "outcome": ALIASED.id},
                    {"content": "edit prose two", "outcome": ALIASED.id}])
        metrics = self.evaluate().threshold
        self.assertEqual(metrics.duplicate_suppressions, 1)
        self.assertEqual(metrics.duplicate_injection_rate, 0.5)
        self.assertEqual(metrics.pack_deliveries, 1)  # the second Pack was suppressed

    def test_a_missing_recording_is_a_problem_not_a_live_call(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}], record=False)
        report = self.evaluate()
        self.assertEqual(report.threshold.failures, 1)
        self.assertEqual(report.attribution[FAILURE], 1)
        self.assertTrue(any("recording_missing" in problem for problem in report.problems))

    def test_a_missing_dependency_is_a_closure_failure(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}])
        meta_path = self.store.root / sm.META_NAME
        meta = json.loads(meta_path.read_text())
        meta[ALIASED.path]["dependencies"] = ["missing.skill"]
        meta_path.write_text(json.dumps(meta))
        sm.generate(self.store.root)
        metrics = self.evaluate().threshold
        self.assertEqual(metrics.dependency_failures, 1)
        self.assertEqual(metrics.cycle_failures, 0)
        self.assertEqual(metrics.closure_failures, 1)
        self.assertEqual(metrics.failures, 1)
        self.assertEqual(self.evaluate().attribution_by_stage["failure"], 1)

    def test_a_dependency_cycle_is_a_cycle_failure(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}])
        meta_path = self.store.root / sm.META_NAME
        meta = json.loads(meta_path.read_text())
        meta[ALIASED.path]["dependencies"] = [DISTINCT.id]
        meta[DISTINCT.path]["dependencies"] = [ALIASED.id]
        meta_path.write_text(json.dumps(meta))
        sm.generate(self.store.root)
        metrics = self.evaluate().threshold
        self.assertEqual(metrics.cycle_failures, 1)
        self.assertEqual(metrics.dependency_failures, 0)
        self.assertEqual(metrics.closure_failures, 1)

    def test_a_thin_tuning_set_stays_in_shadow_even_with_enough_cases(self) -> None:
        entries = [{"content": f"request number {index}", "outcome": ALIASED.id}
                   for index in range(20)]
        self.stage(entries, held_out_ratio=0.95)
        report = self.evaluate(minimum=20)
        self.assertGreaterEqual(report.reviewed_cases, 20)
        self.assertLess(report.threshold.cases, 20)
        self.assertTrue(report.below_minimum)
        self.assertEqual(report.stage, "shadow")

    def test_stages_roll_attribution_up_to_retrieval_judgment_and_grant(self) -> None:
        self.stage([{"content": "draft prose one", "outcome": ALIASED.id},
                    {"content": "edit prose two", "outcome": ALIASED.id,
                     "recording": DISTINCT.id},
                    {"content": "just chatting", "outcome": NO_SKILL}])
        stages = self.evaluate().attribution_by_stage
        self.assertEqual(stages["judgment"], 1)
        self.assertEqual(stages["correct"], 2)
        self.assertEqual(stages["retrieval"], 0)
        self.assertEqual(stages["grant"], 0)


class HeldOutTest(EvaluationFixtureTestCase):
    """The held-out split is reported separately and never used for tuning (AC3)."""

    def test_the_splits_are_disjoint_and_cover_every_reviewed_case(self) -> None:
        entries = [{"content": f"request number {index}", "outcome": ALIASED.id}
                   for index in range(12)]
        self.stage(entries, held_out_ratio=1.0)
        report = self.evaluate()
        self.assertEqual(report.threshold.cases, 0)
        self.assertEqual(report.held_out.cases, 12)
        self.assertEqual(report.threshold.cases + report.held_out.cases, report.reviewed_cases)

    def test_thresholds_come_only_from_the_threshold_set(self) -> None:
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}], held_out_ratio=0.0)
        report = self.evaluate()
        self.assertEqual(report.held_out.cases, 0)
        inflated = replace(report, held_out=replace(report.held_out, true_positive=999,
                                                    false_negative=999))
        self.assertEqual(derive_thresholds(report).to_record(),
                         derive_thresholds(inflated).to_record())
        self.assertEqual(derive_thresholds(report).precision, 1.0)


class PreRegistrationTest(EvaluationFixtureTestCase):
    """Soft Thresholds are pre-registered with the corpus and baseline (AC4)."""

    def setUp(self) -> None:
        super().setUp()
        self.registry = ThresholdRegistry(self.fixture.root / "thresholds")
        self.stage([{"content": "draft prose one", "outcome": ALIASED.id},
                    {"content": "edit prose two", "outcome": ALIASED.id},
                    {"content": "just chatting", "outcome": NO_SKILL}])

    def test_registration_binds_the_thresholds_to_the_corpus_and_baseline(self) -> None:
        report = self.evaluate()
        record = self.registry.register(report, registered_at=NOW)
        self.assertEqual(record["corpus_sha256"], report.corpus_sha256)
        self.assertEqual(record["thresholds"]["precision"], report.threshold.precision)
        self.assertEqual(record["baseline"]["threshold"]["recall"], report.threshold.recall)
        self.assertEqual(record["baseline"]["held_out"], report.held_out.to_record())
        self.assertTrue(record["held_out_not_used_for_tuning"])
        self.assertEqual(self.registry.get(report.profile), record)

    def test_the_same_corpus_cannot_be_registered_twice(self) -> None:
        report = self.evaluate()
        self.registry.register(report, registered_at=NOW)
        with self.assertRaises(EvaluationError):
            self.registry.register(report, registered_at=NOW)

    def test_a_moved_threshold_needs_an_explicit_larger_re_derivation(self) -> None:
        report = self.evaluate()
        self.registry.register(report, registered_at=NOW)
        moved = replace(report, corpus_sha256="other-corpus",
                        reviewed_cases=report.reviewed_cases)
        with self.assertRaises(EvaluationError):
            self.registry.register(moved, registered_at=NOW)
        with self.assertRaises(EvaluationError):
            self.registry.register(moved, registered_at=NOW, allow_re_derivation=True)
        larger = replace(moved, reviewed_cases=report.reviewed_cases + 5)
        record = self.registry.register(larger, registered_at=NOW, allow_re_derivation=True)
        self.assertEqual(record["corpus_sha256"], "other-corpus")

    def test_a_thin_profile_is_reported_in_shadow_and_is_not_registered(self) -> None:
        report = self.evaluate(minimum=99)
        self.assertTrue(report.below_minimum)
        self.assertEqual(report.stage, "shadow")
        with self.assertRaises(EvaluationError):
            self.registry.register(report)

    def test_a_gateable_profile_is_reported_and_registered(self) -> None:
        report = self.evaluate(minimum=3)
        self.assertFalse(report.below_minimum)
        self.assertEqual(report.stage, "gateable")
        self.registry.register(report)


class EvaluationCliTest(EvaluationFixtureTestCase):
    """The CLI evaluates and registers without importing repo internals by hand."""

    def setUp(self) -> None:
        super().setUp()
        import evaluate as evaluate_cli

        self.cli = evaluate_cli
        self.stage([{"content": "draft prose", "outcome": ALIASED.id}])

    def run_cli(self, *argv: str) -> dict:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.cli.main(list(argv))
        self.assertEqual(code, 0, buffer.getvalue())
        return json.loads(buffer.getvalue())

    def args(self) -> list[str]:
        return ["--profile", self.store.profile, "--store", str(self.store.root),
                "--corpus", str(self.fixture.corpus), "--labels", str(self.fixture.labels),
                "--recordings", str(self.fixture.recordings)]

    def test_evaluate_reports_and_register_writes_the_pre_registration(self) -> None:
        report = self.run_cli("evaluate", *self.args(), "--minimum", "1")
        self.assertEqual(report["threshold"]["cases"], 1)
        self.assertEqual(report["thresholds"]["precision"], 1.0)
        thresholds = self.fixture.root / "thresholds"
        record = self.run_cli("register", *self.args(), "--minimum", "1",
                              "--thresholds", str(thresholds), "--registered-at", NOW)
        self.assertEqual(record["thresholds"]["precision"], 1.0)
        self.assertTrue((thresholds / f"{self.store.profile}.json").exists())


if __name__ == "__main__":
    unittest.main()
