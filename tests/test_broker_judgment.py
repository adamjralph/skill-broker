#!/usr/bin/env python3
"""Judgment, validation and the deterministic Grant, observed at ``prepare_turn`` (ticket #48).

A Judgment is one Choice over the ranked Candidates plus a reserved no-skill option. Only
deterministic code grants: a Grant is the intersection of a validated Judgment with the Profile
Policy for the current turn, and nothing — not a prior Grant, a Preferred flag or an explicit
invocation — carries authority forward.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import Broker, Recording, RecordedJudgmentSource, TurnOutcome  # noqa: E402
from broker_fixture import (  # noqa: E402
    RecordingEvidenceLog,
    ScriptedJudgmentSource,
    judgment_claim,
)
from store_fixture import ALIASED, DISTINCT, Identity, StoreFixtureTestCase  # noqa: E402

REQUEST = "Please use writing-method for this piece."
CANDIDATES = (DISTINCT.id, ALIASED.id)


class JudgmentTest(StoreFixtureTestCase):
    def turn(self, source, *, request=REQUEST, session_id="s-1", profile=None):
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence, judgment_source=source)
        result = broker.prepare_turn(request, profile or self.store.profile,
                                     {"session_id": session_id})
        return result, evidence.decisions[-1]

    def test_a_no_skill_judgment_is_a_valid_outcome_with_no_grant(self) -> None:
        result, decision = self.turn(ScriptedJudgmentSource(
            judgment_claim(CANDIDATES, primary=None, no_skill=0.7)))

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertIsNotNone(decision.judgment)
        self.assertIsNone(decision.judgment.primary)

    def test_a_valid_primary_produces_exactly_one_grant_of_that_resolved_version(self) -> None:
        result, decision = self.turn(ScriptedJudgmentSource(
            judgment_claim(CANDIDATES, primary=ALIASED.id, confidence=0.8)))

        self.assertIs(result.outcome, TurnOutcome.GRANTED)
        self.assertEqual([grant.id for grant in result.grants], [ALIASED.id])
        self.assertEqual(result.grants[0].version,
                         self.store.identities()[ALIASED.id]["package_sha256"])
        self.assertEqual(decision.grants, result.grants)
        self.assertIsNotNone(result.pack)
        self.assertIsNotNone(decision.delivery)

    def test_a_non_candidate_primary_is_rejected_and_grants_nothing(self) -> None:
        result, decision = self.turn(ScriptedJudgmentSource(
            judgment_claim(CANDIDATES + ("local.ghost",), primary="local.ghost")))

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIn("judgment_invalid", result.reasons)
        self.assertEqual(result.grants, ())
        self.assertIsNone(decision.judgment)

    def test_an_unauthorised_primary_is_rejected_and_grants_nothing(self) -> None:
        outsider = Identity(owner="local", slug="outsider", name="outsider", body="# Outsider\n")
        fixture = self.make_store(identities=(DISTINCT, ALIASED, outsider))
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence,
                        judgment_source=ScriptedJudgmentSource(
                            judgment_claim(CANDIDATES, primary=outsider.id)))

        result = broker.prepare_turn(REQUEST, fixture.profile, {"session_id": "s-2"})

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertEqual(result.grants, ())
        self.assertNotIn(outsider.id, {grant.id for grant in result.grants})

    def test_a_malformed_judgment_is_rejected_never_partially_applied(self) -> None:
        for broken in ({"primary": ALIASED.id},
                       {"primary": ALIASED.id, "confidence": "high"},
                       "not a judgment at all",
                       None):
            with self.subTest(broken=broken):
                result, decision = self.turn(ScriptedJudgmentSource(broken))
                self.assertIs(result.outcome, TurnOutcome.FAILURE)
                self.assertIn("judgment_invalid", result.reasons)
                self.assertEqual(result.grants, ())
                self.assertIsNone(decision.judgment)

    def test_an_incomplete_distribution_is_rejected(self) -> None:
        claim = judgment_claim(CANDIDATES, primary=ALIASED.id)
        del claim["distribution"][DISTINCT.id]

        result, _ = self.turn(ScriptedJudgmentSource(claim))

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIn("judgment_invalid", result.reasons)
        self.assertEqual(result.grants, ())

    def test_a_judgment_whose_echoed_candidates_are_invented_is_rejected(self) -> None:
        claim = judgment_claim(CANDIDATES, primary=ALIASED.id)
        claim["candidates"] = [ALIASED.id]

        result, _ = self.turn(ScriptedJudgmentSource(claim))

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIn("judgment_invalid", result.reasons)

    def test_a_source_that_raises_is_recorded_and_grants_nothing(self) -> None:
        class Exploding:
            def judge(self, request, candidates):
                raise RuntimeError("Jev unavailable")

        result, _ = self.turn(Exploding())

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIn("judgment_source_error", result.reasons)
        self.assertEqual(result.grants, ())

    def test_a_grant_is_always_inside_the_profile_policy(self) -> None:
        result, decision = self.turn(ScriptedJudgmentSource(
            judgment_claim(CANDIDATES, primary=ALIASED.id)))

        closure = {entry.id for entry in decision.authorised_closure}
        self.assertTrue(result.grants)
        for grant in result.grants:
            self.assertIn(grant.id, closure)

    def test_authority_never_carries_over_to_a_later_turn(self) -> None:
        source = ScriptedJudgmentSource(
            judgment_claim(CANDIDATES, primary=ALIASED.id),
            judgment_claim(CANDIDATES, primary=None, no_skill=0.9))
        first, _ = self.turn(source, session_id="s-3")
        second, _ = self.turn(source, session_id="s-3")

        self.assertIs(first.outcome, TurnOutcome.GRANTED)
        self.assertIs(second.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(second.grants, ())

    def test_a_preferred_skill_is_not_force_supplied(self) -> None:
        fixture = self.make_store(identities=(DISTINCT, ALIASED), brokered=[],
                                  preferred=(ALIASED.id,))
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence,
                        judgment_source=ScriptedJudgmentSource(
                            judgment_claim(CANDIDATES, primary=None, no_skill=0.9)))

        result = broker.prepare_turn(REQUEST, fixture.profile, {"session_id": "s-4"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(result.grants, ())


class RecordingReplayTest(StoreFixtureTestCase):
    def decisions_for(self, source, request=REQUEST) -> list[dict]:
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence, judgment_source=source)
        for session_id in ("s-5", "s-6"):
            broker.prepare_turn(request, self.store.profile, {"session_id": session_id})
        return [record for record in evidence.records]

    def test_two_replays_of_one_recording_are_identical(self) -> None:
        recording = Recording.of(REQUEST, judgment_claim(CANDIDATES, primary=ALIASED.id))

        first, second = self.decisions_for(RecordedJudgmentSource(recording))

        self.assertEqual({k: v for k, v in first.items() if k != "session_id"},
                         {k: v for k, v in second.items() if k != "session_id"})
        self.assertIsNotNone(first["judgment"])

    def test_a_recording_bound_to_another_request_is_rejected(self) -> None:
        recording = Recording.of("a different request entirely",
                                 judgment_claim(CANDIDATES, primary=ALIASED.id))

        first, _ = self.decisions_for(RecordedJudgmentSource(recording))

        self.assertEqual(first["outcome"], TurnOutcome.FAILURE.value)
        self.assertIn("judgment_source_error", first["reasons"])

    def test_a_recording_survives_a_json_round_trip(self) -> None:
        recording = Recording.of(REQUEST, judgment_claim(CANDIDATES, primary=ALIASED.id))
        replayed = Recording.from_record(recording.to_record())

        first, second = self.decisions_for(RecordedJudgmentSource(recording))[0], \
            self.decisions_for(RecordedJudgmentSource(replayed))[0]

        self.assertEqual(first["judgment"], second["judgment"])
        self.assertEqual(first["grants"], second["grants"])
