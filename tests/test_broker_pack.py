#!/usr/bin/env python3
"""Skill Pack assembly, Pack Delivery modes and duplicate suppression (ticket #49).

Drives ``Broker.prepare_turn`` and asserts on the Intervention Result and the recorded Route
Decision: the Pack is the granted Resolved Skill Versions plus their Dependency Closure,
delivered complete — inline within the budget, by reference above it — never partially
truncated, never repeating content already supplied in the conversation.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import (  # noqa: E402
    Broker,
    HookConfig,
    PackDelivery,
    SessionLedger,
    StubJudgmentSource,
    TurnOutcome,
)
from broker_fixture import (  # noqa: E402
    RecordingEvidenceLog,
    ScriptedJudgmentSource,
    TamperingJudgmentSource,
    add_progressive_disclosure,
    append_invalid_utf8,
    judgment_claim,
)
from store_fixture import ALIASED, DISTINCT, Identity, StoreFixtureTestCase  # noqa: E402

REQUEST = "Please use writing-method for this piece."
DEFAULT_CANDIDATES = (DISTINCT.id, ALIASED.id)

DEPENDENCY = Identity(owner="local", slug="house-style", name="house-style",
                      body="# House style\n\nPreferred spelling and casing.\n")
BIG = Identity(owner="local", slug="big-method", name="big-method",
               description="Use when writing something long.",
               tags=("writing",),
               body="# Big method\n\n" + "P" * 800 + "MIDDLE-CANARY" + "Q" * 800 + "\n")


def hermes_preview(pack: str, *, head: int = 500, tail: int = 500) -> str:
    """Emulate the head/tail excerpt Hermes substitutes for an oversize hook payload."""
    return pack[:head] + pack[-tail:]


class PackTestCase(StoreFixtureTestCase):
    """Helpers: one granted turn over a store, with the collaborators injected."""

    def granted_turn(self, *, fixture=None, source=None, hook=None, session_id="s-1",
                     request=REQUEST, ledger=None):
        fixture = fixture or self.store
        evidence = RecordingEvidenceLog()
        source = source or ScriptedJudgmentSource(
            judgment_claim(DEFAULT_CANDIDATES, primary=ALIASED.id))
        broker = Broker(store=fixture.root, evidence_log=evidence, judgment_source=source,
                        hook=hook, session_ledger=ledger)
        result = broker.prepare_turn(request, fixture.profile, {"session_id": session_id})
        return result, evidence.decisions[-1], evidence

    def big_fixture(self, **policy_extra):
        return self.make_store(identities=(DISTINCT, BIG), foundation=(DISTINCT.id,),
                               brokered=(BIG.id,), **policy_extra)

    def big_source(self):
        return ScriptedJudgmentSource(
            judgment_claim((DISTINCT.id, BIG.id), primary=BIG.id))


class PackAssemblyTest(PackTestCase):
    def test_a_grant_delivers_the_complete_skill_md_inline(self) -> None:
        result, decision, _ = self.granted_turn()

        self.assertIs(result.outcome, TurnOutcome.GRANTED)
        self.assertIsNotNone(result.pack)
        self.assertIs(result.delivery, PackDelivery.INLINE)
        self.assertIn(ALIASED.body.strip(), result.pack)
        self.assertIs(decision.delivery, PackDelivery.INLINE)
        self.assertEqual(decision.pack_chars, len(result.pack))

    def test_the_pack_frames_the_primary_at_the_head(self) -> None:
        result, _, _ = self.granted_turn()

        self.assertTrue(result.pack.startswith("SKILL-BROKER PACK"))
        head = result.pack[:500]
        self.assertIn(ALIASED.name, head)
        self.assertIn(ALIASED.id, head)

    def test_the_pack_tail_carries_identifiers_versions_paths_and_the_closure(self) -> None:
        result, _, _ = self.granted_turn()
        version = self.store.identities()[ALIASED.id]["package_sha256"]

        self.assertIn(f"{ALIASED.id}@{version}", result.pack)
        self.assertIn(str(self.store.root / ALIASED.path), result.pack)
        self.assertIn(f"closure: {ALIASED.id}", result.pack)

    def test_the_pack_carries_the_primary_and_its_whole_dependency_closure(self) -> None:
        primary = replace(ALIASED, dependencies=(DEPENDENCY.id,))
        fixture = self.make_store(identities=(DISTINCT, primary, DEPENDENCY),
                                  brokered=(primary.id,))
        source = ScriptedJudgmentSource(
            judgment_claim((DISTINCT.id, primary.id, DEPENDENCY.id), primary=primary.id))

        result, decision, _ = self.granted_turn(fixture=fixture, source=source)

        self.assertIn(primary.body.strip(), result.pack)
        self.assertIn(DEPENDENCY.body.strip(), result.pack)
        self.assertIn(DEPENDENCY.id, {entry.id for entry in decision.authorised_closure})

    def test_a_pack_is_never_truncated(self) -> None:
        long = Identity(owner="local", slug="long-skill", name="long-skill",
                        body="# Long skill\n\n" + "L" * 5_000 + "\n")
        fixture = self.make_store(identities=(DISTINCT, long), brokered=(long.id,))
        source = ScriptedJudgmentSource(
            judgment_claim((DISTINCT.id, long.id), primary=long.id))

        result, _, _ = self.granted_turn(fixture=fixture, source=source)

        self.assertIn("L" * 5_000, result.pack)

    def test_progressive_disclosure_material_is_never_in_a_pack(self) -> None:
        files = add_progressive_disclosure(self.store, ALIASED)

        result, _, _ = self.granted_turn()

        self.assertIn(ALIASED.body.strip(), result.pack)
        for path in files:
            self.assertNotIn(path.read_text().strip(), result.pack)
        for subdir in ("references", "scripts", "templates"):
            self.assertNotIn(f"PROGRESSIVE-DISCLOSURE-CANARY:{subdir}", result.pack)


class PackDeliveryTest(PackTestCase):
    def test_an_over_budget_pack_is_delivered_by_reference(self) -> None:
        fixture = self.big_fixture()
        hook = HookConfig(hook_cap=1_000, reserve=500)

        result, decision, _ = self.granted_turn(fixture=fixture, source=self.big_source(),
                                                hook=hook)

        self.assertIs(result.delivery, PackDelivery.BY_REFERENCE)
        self.assertIs(decision.delivery, PackDelivery.BY_REFERENCE)
        self.assertGreater(decision.pack_chars, hook.inline_budget)

    def test_a_spilled_preview_carries_the_head_the_tail_and_not_the_middle(self) -> None:
        fixture = self.big_fixture()
        result, _, _ = self.granted_turn(fixture=fixture, source=self.big_source(),
                                         hook=HookConfig(hook_cap=1_000, reserve=500))

        preview = hermes_preview(result.pack)
        version = fixture.identities()[BIG.id]["package_sha256"]

        self.assertIn("SKILL-BROKER PACK", preview)
        self.assertIn(BIG.id, preview)
        self.assertIn(f"{BIG.id}@{version}", preview)
        self.assertIn(f"closure: {BIG.id}", preview)
        self.assertNotIn("MIDDLE-CANARY", preview)
        self.assertIn("MIDDLE-CANARY", result.pack)

    def test_a_policy_lowered_budget_is_honoured(self) -> None:
        fixture = self.big_fixture(limits={"max_injected_chars": 500})

        result, decision, _ = self.granted_turn(fixture=fixture, source=self.big_source())

        self.assertIs(result.delivery, PackDelivery.BY_REFERENCE)
        self.assertEqual(decision.budget["policy_limit"], 500)
        self.assertEqual(decision.budget["effective_inline_chars"], 500)

    def test_a_policy_raising_the_runtime_budget_is_refused(self) -> None:
        for limit in (9_800, 50_000):
            with self.subTest(limit=limit):
                fixture = self.big_fixture(limits={"max_injected_chars": limit})
                result, decision, _ = self.granted_turn(fixture=fixture, source=self.big_source())

                self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
                self.assertIn("policy_budget_invalid", result.reasons)
                self.assertIsNone(result.pack)
                self.assertEqual(result.grants, ())
                self.assertEqual(decision.budget["policy_limit"], limit)

    def test_with_spill_disabled_the_pack_is_inline_in_full_and_unbounded(self) -> None:
        fixture = self.big_fixture()

        result, decision, _ = self.granted_turn(fixture=fixture, source=self.big_source(),
                                                hook=HookConfig(spill=False))

        self.assertIs(result.delivery, PackDelivery.INLINE)
        self.assertIn("MIDDLE-CANARY", result.pack)
        self.assertTrue(decision.budget["unbounded"])
        self.assertIsNone(decision.budget["effective_inline_chars"])

    def test_the_route_decision_records_the_effective_budget(self) -> None:
        _, decision, _ = self.granted_turn()

        self.assertEqual(decision.budget, {
            "spill": True,
            "unbounded": False,
            "hook_cap": 10_000,
            "reserve": 500,
            "policy_limit": None,
            "effective_inline_chars": 9_500,
        })


class DuplicateSuppressionTest(PackTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = SessionLedger()
        self.evidence = RecordingEvidenceLog()
        self.broker = Broker(
            store=self.store.root, evidence_log=self.evidence,
            session_ledger=self.ledger,
            judgment_source=StubJudgmentSource(
                judgment_claim(DEFAULT_CANDIDATES, primary=ALIASED.id)))

    def turn(self, session_id, *, request=REQUEST):
        return self.broker.prepare_turn(request, self.store.profile, {"session_id": session_id})

    def test_a_repeat_turn_supplies_no_duplicate_injection(self) -> None:
        first = self.turn("s-repeat")
        second = self.turn("s-repeat")

        self.assertIsNotNone(first.pack)
        self.assertIsNone(second.pack)
        self.assertIn("duplicate_suppressed", second.reasons)
        self.assertIs(second.outcome, TurnOutcome.GRANTED)
        self.assertEqual([grant.id for grant in second.grants], [ALIASED.id])

    def test_the_suppression_is_recorded_on_the_route_decision(self) -> None:
        self.turn("s-record")
        self.turn("s-record")
        decision = self.evidence.decisions[-1]

        self.assertIn("duplicate_suppressed", decision.reasons)
        self.assertIsNone(decision.delivery)
        self.assertIsNone(decision.pack_chars)
        self.assertEqual([grant.id for grant in decision.grants], [ALIASED.id])

    def test_content_supplied_in_one_session_does_not_suppress_another(self) -> None:
        first = self.turn("s-a")
        second = self.turn("s-b")

        self.assertIsNotNone(first.pack)
        self.assertIsNotNone(second.pack)

    def test_the_ledger_is_evidence_not_a_lease(self) -> None:
        source = ScriptedJudgmentSource(
            judgment_claim(DEFAULT_CANDIDATES, primary=ALIASED.id),
            judgment_claim(DEFAULT_CANDIDATES, primary=None, no_skill=0.9))
        broker = Broker(store=self.store.root, evidence_log=RecordingEvidenceLog(),
                        session_ledger=self.ledger, judgment_source=source)

        first = broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-lease"})
        second = broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-lease"})

        self.assertIsNotNone(first.pack)
        self.assertIs(second.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(second.grants, ())
        self.assertIsNone(second.pack)


class PackFailureTest(PackTestCase):
    def test_a_missing_dependency_fails_the_pack_closed(self) -> None:
        primary = replace(ALIASED, dependencies=("local.ghost",))
        fixture = self.make_store(identities=(DISTINCT, primary), brokered=(primary.id,))
        source = ScriptedJudgmentSource(
            judgment_claim((DISTINCT.id, primary.id), primary=primary.id))

        result, _, _ = self.granted_turn(fixture=fixture, source=source)

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIsNone(result.pack)
        self.assertTrue(any("local.ghost" in reason for reason in result.reasons), result.reasons)

    def test_a_denied_dependency_fails_the_pack_closed(self) -> None:
        primary = replace(ALIASED, dependencies=(DEPENDENCY.id,))
        fixture = self.make_store(identities=(DISTINCT, primary, DEPENDENCY),
                                  brokered=(primary.id,), denied=(DEPENDENCY.id,))
        source = ScriptedJudgmentSource(
            judgment_claim((DISTINCT.id, primary.id), primary=primary.id))

        result, _, _ = self.granted_turn(fixture=fixture, source=source)

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIsNone(result.pack)
        self.assertTrue(any(DEPENDENCY.id in reason for reason in result.reasons),
                        result.reasons)

    def test_a_dependency_cycle_fails_the_pack_closed(self) -> None:
        first = Identity(owner="local", slug="one", name="one", body="# One\n",
                         dependencies=("local.two",))
        second = Identity(owner="local", slug="two", name="two", body="# Two\n",
                          dependencies=("local.one",))
        fixture = self.make_store(identities=(first, second), foundation=(first.id,),
                                  brokered=())
        source = ScriptedJudgmentSource(
            judgment_claim((first.id, second.id), primary=first.id))

        result, _, _ = self.granted_turn(fixture=fixture, source=source)

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIsNone(result.pack)
        self.assertTrue(any("cycle" in reason for reason in result.reasons), result.reasons)

    def test_a_content_hash_mismatch_rejects_the_pack_and_flags_the_source(self) -> None:
        skill_md = self.store.root / ALIASED.path / "SKILL.md"
        source = TamperingJudgmentSource(
            judgment_claim(DEFAULT_CANDIDATES, primary=ALIASED.id), skill_md)

        result, decision, _ = self.granted_turn(source=source)

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIsNone(result.pack)
        self.assertTrue(any("content hash mismatch" in reason and ALIASED.id in reason
                            and str(skill_md.parent) in reason for reason in result.reasons),
                        result.reasons)
        self.assertIsNone(decision.delivery)

    def test_a_skill_md_that_is_not_utf8_rejects_the_pack(self) -> None:
        append_invalid_utf8(self.store, ALIASED)

        result, _, _ = self.granted_turn()

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIsNone(result.pack)
        self.assertTrue(any("UTF-8" in reason for reason in result.reasons), result.reasons)


if __name__ == "__main__":
    unittest.main()
