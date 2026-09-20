#!/usr/bin/env python3
"""The injection gate: Hard Gates, fail-closed, and the per-batch switch (ticket #52, ADR-0019).

Drives the gate machinery at its two real seams: ``check_hard_gates`` over a Route Decision, the
``Broker.prepare_turn`` seam where a breach must fail the profile closed within the turn, and the
Adapter's provider-request seam where the system prompt and tool schema are observed. The
per-batch switch is driven directly: it is operator state, not a turn effect.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import broker  # noqa: E402
from broker import Broker, RouteDecision, TurnOutcome  # noqa: E402
from broker.adapter import Adapter  # noqa: E402
from broker.evaluation import SoftThresholds, SplitMetrics, soft_threshold_regression  # noqa: E402
from broker.gate import (  # noqa: E402
    APPROVE,
    APPROVE_NARROWER,
    BROKER_GATES,
    HARD_GATES,
    REJECT,
    InjectionBatch,
    GateError,
    GateReview,
    HardGate,
    InjectionGate,
    check_hard_gates,
)
from broker.types import ResolvedSkillVersion  # noqa: E402
from broker_fixture import (  # noqa: E402
    RecordingEvidenceLog,
    ScriptedJudgmentSource,
    judgment_claim,
)
from store_fixture import ALIASED, DISTINCT, StoreFixtureTestCase  # noqa: E402

REQUEST = "Please use writing-method for this piece."
NOW = "2026-09-21T00:00:00+00:00"


def decision(*, grants=(), closure=(), reasons=(), outcome=TurnOutcome.GRANTED) -> RouteDecision:
    return RouteDecision(
        profile="p", session_id="s", outcome=outcome, reasons=tuple(reasons),
        request_sha256="0" * 64, request_chars=1,
        authorised_closure=tuple(closure), grants=tuple(grants))


class HardGateCheckTest(unittest.TestCase):
    """Every Hard Gate is a data-independent check over the Route Decision (AC1)."""

    def test_an_empty_decision_breaches_nothing(self) -> None:
        self.assertEqual(check_hard_gates(decision()), ())

    def test_an_unauthorised_grant_breaches(self) -> None:
        entry = ResolvedSkillVersion(id=DISTINCT.id, name=DISTINCT.name, version="a")
        rogue = ResolvedSkillVersion(id=ALIASED.id, name=ALIASED.name, version="a")

        self.assertEqual(check_hard_gates(decision(grants=(rogue,), closure=(entry,))),
                         (HardGate.UNAUTHORISED_GRANT.value,))

    def test_a_foundation_resolution_regression_breaches(self) -> None:
        entry = ResolvedSkillVersion(id=ALIASED.id, name=ALIASED.name, version="a")

        self.assertEqual(
            check_hard_gates(decision(closure=(entry,)), foundation_ids=(DISTINCT.id,)),
            (HardGate.FOUNDATION_REGRESSION.value,))

    def test_a_hash_disagreement_breaches(self) -> None:
        entry = ResolvedSkillVersion(id=ALIASED.id, name=ALIASED.name, version="store")
        grant = ResolvedSkillVersion(id=ALIASED.id, name=ALIASED.name, version="delivered")

        self.assertEqual(check_hard_gates(decision(grants=(grant,), closure=(entry,))),
                         (HardGate.HASH_DISAGREEMENT.value,))
        self.assertEqual(check_hard_gates(decision(reasons=("content hash mismatch for x",))),
                         (HardGate.HASH_DISAGREEMENT.value,))

    def test_a_judgment_validation_failure_breaches(self) -> None:
        decision_with_failure = decision(outcome=TurnOutcome.FAILURE,
                                         reasons=("judgment_invalid", "primary not a Candidate"))

        self.assertEqual(check_hard_gates(decision_with_failure),
                         (HardGate.JUDGMENT_INVALID.value,))

    def test_an_incomplete_dependency_closure_breaches(self) -> None:
        for reason in ("unknown dependency: local.ghost",
                       "dependency not authorised: local.ghost",
                       "dependency cycle: local.one -> local.two -> local.one",
                       "unknown ID in closure: local.ghost"):
            with self.subTest(reason=reason):
                self.assertEqual(check_hard_gates(decision(outcome=TurnOutcome.FAILURE,
                                                           reasons=(reason,))),
                                 (HardGate.INCOMPLETE_CLOSURE.value,))

    def test_the_check_order_is_canonical_and_deduplicated(self) -> None:
        rogue = ResolvedSkillVersion(id=ALIASED.id, name=ALIASED.name, version="a")
        breaches = check_hard_gates(decision(grants=(rogue,),
                                             reasons=("judgment_invalid",
                                                      "unknown dependency: x")))

        self.assertEqual(breaches, (HardGate.UNAUTHORISED_GRANT.value,
                                    HardGate.JUDGMENT_INVALID.value,
                                    HardGate.INCOMPLETE_CLOSURE.value))
        self.assertEqual(list(breaches), [gate for gate in HARD_GATES if gate in breaches])

    def test_the_prompt_schema_gate_is_adapter_owned_not_broker_owned(self) -> None:
        self.assertNotIn(HardGate.PROMPT_SCHEMA_MUTATION.value, BROKER_GATES)
        self.assertEqual(set(BROKER_GATES) | {HardGate.PROMPT_SCHEMA_MUTATION.value},
                         set(HARD_GATES))


class GateTestBase(StoreFixtureTestCase):
    def make_gate(self, *, tmp: str) -> InjectionGate:
        return InjectionGate(state_path=Path(tmp) / "gate.json", clock=_Clock())

    def make_broker(self, gate, source):
        self.evidence = RecordingEvidenceLog()
        return Broker(store=self.store.root, evidence_log=self.evidence,
                      judgment_source=source, gate=gate)


class _Clock:
    """A settable clock, so a review's ordering against a breach is deterministic in tests."""

    def __init__(self, now: str = NOW) -> None:
        self.now = now

    def __call__(self) -> str:
        return self.now


class FailClosedThroughTheSeamTest(GateTestBase):
    """A breach reverts the turn to foundation-only and records an Incident (AC2)."""

    def test_a_clean_granted_turn_passes_every_broker_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp=tmp)
            broker = self.make_broker(gate, ScriptedJudgmentSource(
                judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)))

            result = broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-1"})

            self.assertIs(result.outcome, TurnOutcome.GRANTED)
            self.assertTrue(result.pack)
            self.assertFalse(result.failed_closed)
            self.assertFalse(gate.failed_closed(self.store.profile))
            self.assertEqual(gate.incidents(self.store.profile), [])

    def test_a_judgment_validation_failure_fails_the_profile_closed_within_the_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp=tmp)
            broker = self.make_broker(gate, ScriptedJudgmentSource({"primary": ALIASED.id}))

            result = broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-1"})

            self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
            self.assertIsNone(result.pack)
            self.assertEqual(result.grants, ())
            self.assertTrue(result.failed_closed)
            self.assertIn("hard_gate_breach:judgment_invalid", result.reasons)
            self.assertTrue(gate.failed_closed(self.store.profile))
            self.assertFalse(gate.injecting(self.store.profile))
            incidents = gate.incidents(self.store.profile)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0].gates, (HardGate.JUDGMENT_INVALID.value,))
            self.assertEqual(incidents[0].turn_id, None)

    def test_an_incomplete_closure_fails_the_profile_closed_naming_the_gate(self) -> None:
        fixture = self.make_store(identities=(DISTINCT, self._aliased_with_ghost()))
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp=tmp)
            evidence = RecordingEvidenceLog()
            broker = Broker(store=fixture.root, evidence_log=evidence,
                            judgment_source=ScriptedJudgmentSource(
                                judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)), gate=gate)

            result = broker.prepare_turn(REQUEST, fixture.profile, {"session_id": "s-1"})

            self.assertIn("hard_gate_breach:incomplete_closure", result.reasons)
            self.assertEqual(gate.incidents(fixture.profile)[0].gates,
                             (HardGate.INCOMPLETE_CLOSURE.value,))

    @staticmethod
    def _aliased_with_ghost():
        from dataclasses import replace

        return replace(ALIASED, dependencies=("local.ghost",))

    def test_a_failed_closed_profile_is_foundation_only_on_later_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp=tmp)
            broker = self.make_broker(gate, ScriptedJudgmentSource(
                judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)))
            gate.fail_closed(self.store.profile, (HardGate.JUDGMENT_INVALID.value,))

            result = broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-1"})

            self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
            self.assertTrue(result.failed_closed)
            self.assertIn("injection_failed_closed", result.reasons)

    def test_an_incident_carries_no_request_text_and_no_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.make_gate(tmp=tmp)
            broker = self.make_broker(gate, ScriptedJudgmentSource({"primary": ALIASED.id}))

            broker.prepare_turn(REQUEST, self.store.profile, {"session_id": "s-1"})

            blob = Path(tmp, "gate_incidents.jsonl").read_text()
            self.assertNotIn("writing-method for this piece", blob)
            self.assertNotIn(ALIASED.body.strip(), blob)


class TheSwitchTest(unittest.TestCase):
    """Injection is off unless a batch is explicitly enabled, and enabling names it (AC4)."""

    def test_a_fresh_gate_injects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")

            self.assertFalse(gate.injecting("p"))
            self.assertIsNone(gate.enabled_batch("p"))

    def test_enabling_names_the_profile_and_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")

            gate.enable(InjectionBatch(name="pilot", profile="p", skills=(ALIASED.id,)),
                        GateReview(profile="p", batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))

            self.assertTrue(gate.injecting("p"))
            status = gate.status("p")
            self.assertEqual(status["enabled_batch"]["name"], "pilot")
            self.assertEqual(status["enabled_batch"]["skills"], [ALIASED.id])
            self.assertTrue(status["injecting"])

    def test_a_review_for_the_wrong_profile_or_batch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")

            with self.assertRaises(GateError):
                gate.enable(InjectionBatch(name="pilot", profile="p"),
                            GateReview(profile="other", batch="pilot", reviewer="adam",
                                       outcome=APPROVE, reviewed_at=NOW))
            with self.assertRaises(GateError):
                gate.enable(InjectionBatch(name="pilot", profile="p"),
                            GateReview(profile="p", batch="different", reviewer="adam",
                                       outcome=APPROVE, reviewed_at=NOW))

    def test_a_rejected_review_enables_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")

            with self.assertRaises(GateError):
                gate.enable(InjectionBatch(name="pilot", profile="p"),
                            GateReview(profile="p", batch="pilot", reviewer="adam",
                                       outcome=REJECT, reviewed_at=NOW))
            self.assertFalse(gate.injecting("p"))

    def test_an_operator_can_disable_a_running_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(InjectionBatch(name="pilot", profile="p"),
                        GateReview(profile="p", batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))

            gate.disable("p", reason="rollback")

            self.assertFalse(gate.injecting("p"))


class ReEnableAfterBreachTest(unittest.TestCase):
    """Re-enabling after a breach requires a review recorded after the breach (AC3)."""

    def test_the_turn_that_breached_cannot_flip_the_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock("2026-09-21T00:00:00+00:00")
            gate = InjectionGate(state_path=Path(tmp) / "gate.json", clock=clock)
            gate.enable(InjectionBatch(name="pilot", profile="p"),
                        GateReview(profile="p", batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))

            gate.fail_closed("p", (HardGate.UNAUTHORISED_GRANT.value,))

            # Same instant: a review that cannot post-date the breach is refused.
            with self.assertRaises(GateError):
                gate.enable(InjectionBatch(name="pilot", profile="p"),
                            GateReview(profile="p", batch="pilot", reviewer="adam",
                                       outcome=APPROVE, reviewed_at=clock.now))
            self.assertTrue(gate.failed_closed("p"))
            self.assertFalse(gate.injecting("p"))

    def test_a_review_recorded_after_the_breach_re_enables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock("2026-09-21T00:00:00+00:00")
            gate = InjectionGate(state_path=Path(tmp) / "gate.json", clock=clock)
            gate.fail_closed("p", (HardGate.JUDGMENT_INVALID.value,))
            clock.now = "2026-09-22T00:00:00+00:00"

            gate.enable(InjectionBatch(name="narrower", profile="p"),
                        GateReview(profile="p", batch="narrower", reviewer="adam",
                                   outcome=APPROVE_NARROWER, reviewed_at=clock.now))

            self.assertFalse(gate.failed_closed("p"))
            self.assertTrue(gate.injecting("p"))
            self.assertEqual(gate.enabled_batch("p").name, "narrower")

class SoftThresholdGateTest(unittest.TestCase):
    """A Soft-Threshold regression blocks expansion and leaves running injection on (AC5)."""

    def test_a_regression_is_detected_against_the_pre_registered_floor(self) -> None:
        baseline = SoftThresholds(precision=0.9, recall=0.5, correct_no_skill_rate=0.8)
        current = SplitMetrics(true_positive=1, false_positive=3, false_negative=1,
                               true_negative=1)

        reasons = soft_threshold_regression(baseline, current)

        self.assertTrue(any(reason.startswith("precision regressed") for reason in reasons))
        self.assertFalse(any(reason.startswith("recall regressed") for reason in reasons))

    def test_an_undefined_metric_is_not_a_regression(self) -> None:
        self.assertEqual(soft_threshold_regression(SoftThresholds(precision=0.9), SplitMetrics()),
                         ())

    def test_a_regression_blocks_expansion_but_not_running_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(InjectionBatch(name="pilot", profile="p"),
                        GateReview(profile="p", batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))

            gate.note_expansion_regression("p", ("precision regressed: 0.5 < 0.9",))

            self.assertTrue(gate.injecting("p"))
            self.assertTrue(gate.expansion_blocked("p"))
            self.assertEqual(gate.status("p")["expansion_reasons"],
                             ["precision regressed: 0.5 < 0.9"])
            with self.assertRaises(GateError):
                gate.enable(InjectionBatch(name="next", profile="p"),
                            GateReview(profile="p", batch="next", reviewer="adam",
                                       outcome=APPROVE, reviewed_at=NOW))

    def test_check_soft_thresholds_sets_the_block_from_a_measured_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")

            status = gate.check_soft_thresholds(
                "p", baseline=SoftThresholds(precision=0.9), current=SplitMetrics(
                    true_positive=1, false_positive=3))

            self.assertTrue(status["expansion_blocked"])
            self.assertFalse(status["injecting"])


class ObservabilityTest(unittest.TestCase):
    """The gate state is observable: what is enabled, for which batch, and failed-closed (AC6)."""

    def test_status_reports_the_full_gate_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(InjectionBatch(name="pilot", profile="p"),
                        GateReview(profile="p", batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))
            gate.fail_closed("p", (HardGate.HASH_DISAGREEMENT.value,),
                             reasons=("content hash mismatch for x",), turn_id="t-9")

            status = gate.status("p")

            self.assertEqual(set(status), {
                "profile", "injecting", "enabled_batch", "failed_closed",
                "failed_closed_gates", "failed_closed_at", "review_required",
                "expansion_blocked", "expansion_reasons", "disabled_reason",
                "previous_batch", "last_review", "incident_count"})
            self.assertFalse(status["injecting"])
            self.assertIsNone(status["enabled_batch"])
            self.assertTrue(status["failed_closed"])
            self.assertEqual(status["failed_closed_gates"],
                             [HardGate.HASH_DISAGREEMENT.value])
            self.assertTrue(status["review_required"])
            self.assertEqual(status["incident_count"], 1)

    def test_incidents_are_append_only_and_survive_a_re_enable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clock = _Clock()
            gate = InjectionGate(state_path=Path(tmp) / "gate.json", clock=clock)
            gate.fail_closed("p", (HardGate.JUDGMENT_INVALID.value,))
            clock.now = "2026-09-22T00:00:00+00:00"
            gate.enable(InjectionBatch(name="next", profile="p"),
                        GateReview(profile="p", batch="next", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=clock.now))

            self.assertTrue(gate.injecting("p"))
            self.assertEqual(len(gate.incidents("p")), 1)


class AdapterPromptSchemaGateTest(StoreFixtureTestCase):
    """The Adapter's prompt/tool-schema gate fails a mutated conversation closed (AC1, AC2)."""

    def make_adapter(self, gate):
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence,
                        judgment_source=ScriptedJudgmentSource(
                            judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)), gate=gate)
        return Adapter(broker=broker, profile=self.store.profile, gate=gate), evidence

    def request(self, adapter, *, system_prompt, tool_count, turn_id, session_id="s-1",
                tools=None):
        adapter.on_pre_llm_call(session_id=session_id, task_id="t", turn_id=turn_id,
                                user_message=REQUEST)
        payload = {"session_id": session_id, "task_id": "t", "turn_id": turn_id,
                   "api_request_id": f"{turn_id}:api:1", "system_prompt": system_prompt,
                   "tool_count": tool_count}
        if tools is not None:
            payload["request"] = {"method": "POST", "body": {"tools": tools}}
        adapter.on_pre_api_request(**payload)

    def test_a_stable_prompt_and_tool_schema_over_a_conversation_breach_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)

            self.request(adapter, system_prompt="S", tool_count=7, turn_id="1")
            self.request(adapter, system_prompt="S", tool_count=7, turn_id="2")

            self.assertFalse(gate.failed_closed(self.store.profile))
            self.assertEqual(gate.incidents(self.store.profile), [])

    def test_a_mutated_system_prompt_fails_the_profile_closed_naming_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)

            self.request(adapter, system_prompt="S", tool_count=7, turn_id="1")
            self.request(adapter, system_prompt="S plus a tool section", tool_count=7, turn_id="2")

            self.assertTrue(gate.failed_closed(self.store.profile))
            incident = gate.incidents(self.store.profile)[0]
            self.assertEqual(incident.gates, (HardGate.PROMPT_SCHEMA_MUTATION.value,))
            self.assertEqual(incident.turn_id, "2")

    def test_a_mutated_tool_schema_fails_the_profile_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)

            self.request(adapter, system_prompt="S", tool_count=7, turn_id="1")
            self.request(adapter, system_prompt="S", tool_count=9, turn_id="2")

            self.assertTrue(gate.failed_closed(self.store.profile))

    def test_a_tool_schema_edit_that_keeps_the_count_still_breaches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)
            original = [{"name": "read_file", "parameters": {"type": "object"}}]
            edited = [{"name": "read_file", "parameters": {"type": "object", "required": []}}]

            self.request(adapter, system_prompt="S", tool_count=1, turn_id="1", tools=original)
            self.request(adapter, system_prompt="S", tool_count=1, turn_id="2", tools=edited)

            self.assertTrue(gate.failed_closed(self.store.profile))

    def test_a_stable_tool_schema_hash_breaches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)
            tools = [{"name": "read_file", "parameters": {"type": "object"}}]

            self.request(adapter, system_prompt="S", tool_count=1, turn_id="1", tools=tools)
            self.request(adapter, system_prompt="S", tool_count=1, turn_id="2", tools=tools)

            self.assertFalse(gate.failed_closed(self.store.profile))

    def test_a_different_conversation_has_its_own_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, _ = self.make_adapter(gate)

            self.request(adapter, system_prompt="S", tool_count=7, turn_id="1", session_id="a")
            self.request(adapter, system_prompt="OTHER", tool_count=9, turn_id="1",
                         session_id="b")

            self.assertFalse(gate.failed_closed(self.store.profile))


class AdapterSwitchTest(StoreFixtureTestCase):
    """The Adapter delivers nothing until a batch is enabled, whatever ``inject`` says (AC4)."""

    def make_adapter(self, gate, *, inject):
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence,
                        judgment_source=ScriptedJudgmentSource(
                            judgment_claim([DISTINCT.id, ALIASED.id], primary=ALIASED.id)), gate=gate)
        return Adapter(broker=broker, profile=self.store.profile, inject=inject, gate=gate), evidence

    def test_a_gate_overrides_the_inject_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            adapter, evidence = self.make_adapter(gate, inject=True)

            self.assertIsNone(adapter.on_pre_llm_call(session_id="s", task_id="t", turn_id="1",
                                                      user_message=REQUEST))
            self.assertTrue(evidence.decisions[-1].grants)

    def test_an_enabled_batch_delivers_the_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(InjectionBatch(name="pilot", profile=self.store.profile),
                        GateReview(profile=self.store.profile, batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))
            adapter, _ = self.make_adapter(gate, inject=False)

            result = adapter.on_pre_llm_call(session_id="s", task_id="t", turn_id="1",
                                             user_message=REQUEST)

            self.assertIsInstance(result, dict)
            self.assertIn("SKILL-BROKER PACK", result["context"])

    def test_a_breach_within_the_turn_delivers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(InjectionBatch(name="pilot", profile=self.store.profile),
                        GateReview(profile=self.store.profile, batch="pilot", reviewer="adam",
                                   outcome=APPROVE, reviewed_at=NOW))
            evidence = RecordingEvidenceLog()
            broker = Broker(store=self.store.root, evidence_log=evidence,
                            judgment_source=ScriptedJudgmentSource({"primary": ALIASED.id}),
                            gate=gate)
            adapter = Adapter(broker=broker, profile=self.store.profile, gate=gate)

            result = adapter.on_pre_llm_call(session_id="s", task_id="t", turn_id="1",
                                             user_message=REQUEST)

            self.assertIsNone(result)
            self.assertTrue(gate.failed_closed(self.store.profile))


class PromptSchemaObservationTest(unittest.TestCase):
    """The durable baseline compares only what both observations carry (AC1)."""

    def test_a_tool_schema_appearing_later_does_not_spuriously_breach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.observe_request("p", "s", system_prompt_sha256="a", tool_count=1)

            ok, _ = gate.observe_request("p", "s", system_prompt_sha256="a", tool_count=1,
                                         tool_schema_sha256="h")

            self.assertTrue(ok)

    def test_a_changed_prompt_still_breaches_when_the_schema_hash_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.observe_request("p", "s", system_prompt_sha256="a", tool_count=1)

            ok, _ = gate.observe_request("p", "s", system_prompt_sha256="b", tool_count=1)

            self.assertFalse(ok)

    def test_in_evidence_dir_places_the_state_and_incidents_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate.in_evidence_dir(tmp)

            self.assertEqual(gate.state_path, Path(tmp) / "gate.json")
            self.assertEqual(gate.incidents_path, Path(tmp) / "gate_incidents.jsonl")


class GateCliTest(unittest.TestCase):
    """The operator CLI reads the same state the Broker and Adapter enforce (AC4, AC6)."""

    def run_cli(self, *argv) -> tuple[int, dict]:
        import gate as gate_cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate_cli.main(["--evidence-dir", self.tmp, *argv])
        return code, json.loads(out.getvalue())

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def test_status_names_what_is_enabled_and_the_closed_state(self) -> None:
        code, status = self.run_cli("status", "--profile", "p")
        self.assertEqual(code, 0)
        self.assertFalse(status["injecting"])
        self.assertIsNone(status["enabled_batch"])

        code, enabled = self.run_cli("enable", "--profile", "p", "--batch", "pilot",
                                     "--skill", ALIASED.id, "--reviewer", "adam")
        self.assertEqual(code, 0)
        self.assertTrue(enabled["injecting"])
        self.assertEqual(enabled["enabled_batch"]["name"], "pilot")

        code, disabled = self.run_cli("disable", "--profile", "p", "--reason", "rollback")
        self.assertEqual(code, 0)
        self.assertFalse(disabled["injecting"])

    def test_a_regression_blocks_enablement_with_a_recorded_reason(self) -> None:
        self.run_cli("enable", "--profile", "p", "--batch", "pilot", "--reviewer", "adam")
        self.run_cli("regression", "--profile", "p", "--reason", "precision regressed")

        code, result = self.run_cli("enable", "--profile", "p", "--batch", "next",
                                    "--reviewer", "adam")

        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"])
        _, status = self.run_cli("status", "--profile", "p")
        self.assertTrue(status["injecting"])

    def test_a_regression_can_be_cleared_after_re_derivation(self) -> None:
        self.run_cli("enable", "--profile", "p", "--batch", "pilot", "--reviewer", "adam")
        self.run_cli("regression", "--profile", "p", "--reason", "precision regressed")

        code, status = self.run_cli("clear-regression", "--profile", "p")

        self.assertEqual(code, 0)
        self.assertFalse(status["expansion_blocked"])


if __name__ == "__main__":
    unittest.main()
