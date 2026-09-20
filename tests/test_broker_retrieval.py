#!/usr/bin/env python3
"""Candidate Retrieval over the Authorised Closure, observed at ``prepare_turn`` (ticket #47).

Deterministic, model-free narrowing: exact ID/Name/Alias first, then lexical scoring over Name,
Aliases, description and Hermes tags — never a Skill body. Nothing here judges or grants; the
ranked set is read from the recorded Route Decision.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import Broker, TurnOutcome  # noqa: E402
from broker_fixture import RecordingEvidenceLog  # noqa: E402
from store_fixture import ALIASED, DISTINCT, Identity, StoreFixtureTestCase  # noqa: E402

CANARY = "zebrafish"


class RetrievalTest(StoreFixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.evidence = RecordingEvidenceLog()
        self.broker = Broker(store=self.store.root, evidence_log=self.evidence)

    def turn(self, request: str):
        result = self.broker.prepare_turn(request, self.store.profile, {"session_id": "s"})
        return result, self.evidence.decisions[-1]

    def test_an_exact_name_or_alias_match_ranks_first(self) -> None:
        _, decision = self.turn("Please use prose-method for this piece.")

        self.assertTrue(decision.candidates[0].exact)
        self.assertEqual(decision.candidates[0].id, ALIASED.id)

    def test_an_exact_id_match_ranks_first(self) -> None:
        _, decision = self.turn("Run local.writing-method now.")

        self.assertEqual(decision.candidates[0].id, ALIASED.id)
        self.assertTrue(decision.candidates[0].exact)

    def test_exact_matches_are_never_displaced_by_lexical_scoring(self) -> None:
        _, decision = self.turn("hermes agent prose-method")

        exact = [candidate for candidate in decision.candidates if candidate.exact]
        self.assertEqual([candidate.id for candidate in exact], [ALIASED.id])
        self.assertEqual(decision.candidates[0].id, ALIASED.id)

    def test_candidates_never_leave_the_authorised_closure(self) -> None:
        _, decision = self.turn("anything about hermes and prose")

        closure = {entry.id for entry in decision.authorised_closure}
        self.assertTrue(decision.candidates)
        for candidate in decision.candidates:
            self.assertIn(candidate.id, closure)

    def test_a_skill_body_is_never_scored(self) -> None:
        body_only = Identity(owner="local", slug="body-trap", name="body-trap",
                             description="Use when nothing in particular.",
                             tags=("unrelated",),
                             body=f"# Body trap\n\nThis body alone mentions {CANARY}.\n")
        fixture = self.make_store(identities=(DISTINCT, ALIASED, body_only),
                                  brokered=[ALIASED.id, body_only.id])
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence)

        broker.prepare_turn(f"Please deal with {CANARY}.", fixture.profile, {"session_id": "s"})

        for candidate in evidence.decisions[-1].candidates:
            self.assertEqual(candidate.score, 0.0, candidate)

    def test_the_ranked_set_its_limit_and_every_score_are_recorded(self) -> None:
        _, decision = self.turn("writing prose")

        self.assertEqual(decision.candidate_limit, 12)
        self.assertTrue(decision.candidates)
        for record in decision.to_record()["candidates"]:
            self.assertEqual(set(record), {"id", "name", "score", "exact"})
            self.assertIsInstance(record["score"], float)

    def test_retrieval_is_deterministic(self) -> None:
        _, first = self.turn("hermes agent prose-method writing")
        _, second = self.turn("hermes agent prose-method writing")

        self.assertEqual(first.candidates, second.candidates)

    def test_an_explicit_invocation_is_a_candidate_never_a_grant(self) -> None:
        result, decision = self.turn("Use writing-method.")

        self.assertIn(ALIASED.id, [candidate.id for candidate in decision.candidates])
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)

    def test_the_candidate_set_is_bounded_by_the_limit(self) -> None:
        many = tuple(Identity(owner="local", slug=f"fixture-skill-{i}", name=f"fixture-skill-{i}",
                              body=f"# Fixture skill {i}\n",
                              description=f"Fixture skill number {i} for retrieval.",
                              tags=("fixture",))
                     for i in range(13))
        fixture = self.make_store(identities=many, foundation=(many[0].id,),
                                  brokered=[identity.id for identity in many[1:]])
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence)

        broker.prepare_turn("fixture skill number", fixture.profile, {"session_id": "s"})

        decision = evidence.decisions[-1]
        self.assertEqual(len(decision.candidates), 12)
        self.assertEqual(decision.candidate_limit, 12)
        self.assertEqual(len(decision.authorised_closure), 13)
