#!/usr/bin/env python3
"""The pilot proof's acceptance logic, tested without the Hermes interpreter (ticket #57).

`run_pilot_proof.py` is the executed evidence for #57 and is excluded from unittest discovery
because it drives Hermes. Its *assertions* are ordinary pure functions over the reports the
harness collects, so they are tested here against well-formed and deliberately broken inputs:
a checker that silently passed would turn a failed pilot into a green verdict.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROOF = HERE / "hermes_adapter" / "run_pilot_proof.py"

_spec = importlib.util.spec_from_file_location("run_pilot_proof", PROOF)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

PLUGIN = pp.PLUGIN_NAME
GRANT = {"id": "coreyhaines31.cold-email", "name": "cold-email", "version": "v1"}
CLOSURE = [GRANT]


def cutover(**overrides) -> dict:
    report = {
        "withheld": ["cold-email", "copywriting"],
        "expected_withheld": ["cold-email", "copywriting"],
        "external_dirs_present": True,
        "disabled_present": True,
        "gate_ok": True,
        "rollback_changed": True,
        "bytes_restored": True,
        "config_before_sha256": "abc",
    }
    report.update(overrides)
    return report


def decision(route_id: str, *, reasons=(), pack: str | None = "pack",
             candidates=None) -> dict:
    return {"route_decision_id": route_id, "grants": [GRANT], "authorised_closure": CLOSURE,
            "candidates": candidates if candidates is not None else [{"id": GRANT["id"]}],
            "reasons": list(reasons), "pack_sha256": pack, "pack_chars": 10 if pack else None}


def evidence(route_id: str, request_id: str = "r1") -> dict:
    return {"route_decision_id": route_id, "session_id": "s", "task_id": "t",
            "turn_id": "1", "api_request_id": request_id}


def request(last_user_message: str, history=None) -> dict:
    return {"system_prompt_sha256": "sys", "tools_sha256": "tools",
            "last_user_message": last_user_message,
            "user_messages": list(history) if history is not None else [last_user_message]}


def shadow(**overrides) -> dict:
    report = {
        "mode": "shadow",
        "loaded_plugins": [PLUGIN],
        "requests": [request("write a cold email"), request("write a cold email")],
        "evidence": [evidence("d1"), evidence("d2", "r2")],
        "decisions": [decision("d1"), decision("d2", reasons=("duplicate_suppressed",), pack=None)],
    }
    report.update(overrides)
    return report


def control(**overrides) -> dict:
    report = {"loaded_plugins": [],
              "requests": [request("write a cold email"), request("write a cold email")]}
    report.update(overrides)
    return report


def pilot(**overrides) -> dict:
    report = {
        "mode": "pilot",
        "loaded_plugins": [PLUGIN],
        "requests": [request(f"{pp.PACK_MARKER}\n{pp.COLD_EMAIL_BODY}"),
                     request("write a cold email",
                             history=[f"{pp.PACK_MARKER}\n{pp.COLD_EMAIL_BODY}",
                                      "write a cold email"])],
        "evidence": [evidence("d1"), evidence("d2", "r2")],
        "decisions": [decision("d1"), decision("d2", reasons=("duplicate_suppressed",), pack=None)],
    }
    report.update(overrides)
    return report


def review(**overrides) -> dict:
    report = {
        "batch": {"name": "stillroom-signal-generator-brokered", "profile": pp.PROFILE,
                  "skills": ["coreyhaines31.cold-email"]},
        "review": {"outcome": "approve"},
        "thresholds_pre_registered": True,
        "gates_passed": True,
        "hash_disagreements": 0,
        "hash_agreements": 1,
        "injecting": True,
        "enabled_batch": {"name": "stillroom-signal-generator-brokered", "profile": pp.PROFILE},
        "other_injecting": False,
        "gate_status": [{"gate": "unauthorised_grant", "status": "pass", "checked": True}],
    }
    report.update(overrides)
    return report


def run(*, cutover=None, review=None, control=None, shadow=None, pilot=None) -> list[str]:
    return pp.assert_proof(
        cutover=cutover if cutover is not None else globals()["cutover"](),
        review=review if review is not None else globals()["review"](),
        control=control if control is not None else globals()["control"](),
        shadow=shadow if shadow is not None else globals()["shadow"](),
        pilot=pilot if pilot is not None else globals()["pilot"](),
    )


class PilotProofAssertionsTest(unittest.TestCase):
    def test_the_well_formed_lifecycle_passes(self) -> None:
        self.assertEqual(run(), [])

    def test_cutover_checks(self) -> None:
        self.assertTrue(run(cutover=cutover(withheld=["cold-email"])))
        self.assertTrue(run(cutover=cutover(external_dirs_present=False)))
        self.assertTrue(run(cutover=cutover(disabled_present=False)))
        self.assertTrue(run(cutover=cutover(gate_ok=False)))
        self.assertTrue(run(cutover=cutover(bytes_restored=False)))
        self.assertTrue(run(cutover=cutover(rollback_changed=False)))

    def test_shadow_checks(self) -> None:
        self.assertTrue(run(shadow=shadow(loaded_plugins=[])))
        self.assertTrue(run(shadow=shadow(decisions=[])))
        self.assertTrue(run(shadow=shadow(
            requests=[request(f"{pp.PACK_MARKER} leaked"), request("x")])))

    def test_review_checks(self) -> None:
        self.assertTrue(run(review=review(review={"outcome": "reject"})))
        self.assertTrue(run(review=review(batch={"name": "b", "profile": "other",
                                                "skills": ["x"]})))
        self.assertTrue(run(review=review(batch={"name": "b", "profile": pp.PROFILE,
                                                "skills": []})))
        self.assertTrue(run(review=review(thresholds_pre_registered=False)))
        self.assertTrue(run(review=review(gates_passed=False)))
        self.assertTrue(run(review=review(hash_disagreements=1)))
        self.assertTrue(run(review=review(hash_agreements=0)))
        self.assertTrue(run(review=review(enabled_batch=None)))
        self.assertTrue(run(review=review(enabled_batch={"name": "other",
                                                         "profile": pp.PROFILE})))
        self.assertTrue(run(review=review(other_injecting=True)))
        self.assertTrue(run(review=review(injecting=False)))
        self.assertTrue(run(review=review(
            gate_status=[{"gate": "g", "status": "breach", "checked": True}])))
        self.assertEqual(run(review=review(
            gate_status=[{"gate": "g", "status": "not_checked", "checked": False}])), [])

    def test_injection_checks(self) -> None:
        self.assertTrue(run(control=control(loaded_plugins=[PLUGIN])))
        self.assertTrue(run(pilot=pilot(loaded_plugins=[])))
        self.assertTrue(run(pilot=pilot(
            requests=[request("plain"), request(f"{pp.PACK_MARKER} late")])))
        self.assertTrue(run(pilot=pilot(
            requests=[request(pp.PACK_MARKER), request("plain")])))
        self.assertTrue(run(pilot=pilot(decisions=[decision("d1"), decision("d1")])))
        self.assertTrue(run(pilot=pilot(
            decisions=[decision("d1"),
                       decision("d2", reasons=(), pack="p")])))
        self.assertTrue(run(pilot=pilot(
            decisions=[{"route_decision_id": "d1", "grants": [GRANT],
                        "authorised_closure": [], "pack_sha256": "p", "reasons": []},
                       decision("d2", reasons=("duplicate_suppressed",), pack=None)])))
        self.assertTrue(run(pilot=pilot(
            decisions=[{"route_decision_id": "d1",
                        "grants": [{"id": "other", "name": "other", "version": "v1"}],
                        "authorised_closure": [{"id": "other", "name": "other",
                                                "version": "v1"}],
                        "candidates": [{"id": "other"}], "pack_sha256": "p", "reasons": []},
                       decision("d2", reasons=("duplicate_suppressed",), pack=None)])))
        self.assertTrue(run(pilot=pilot(
            decisions=[decision("d1", candidates=[]),
                       decision("d2", reasons=("duplicate_suppressed",), pack=None)])))
        self.assertTrue(run(pilot=pilot(
            requests=[request(f"{pp.PACK_MARKER}\n{pp.COLD_EMAIL_BODY}"),
                      request("write a cold email")])))
        self.assertTrue(run(control=control(
            requests=[{"system_prompt_sha256": "other", "tools_sha256": "tools",
                       "last_user_message": "x"}, request("y")])))
        self.assertTrue(run(control=control(
            requests=[{"system_prompt_sha256": "sys", "tools_sha256": "other",
                       "last_user_message": "x"}, request("y")])))

    def test_replay_checks(self) -> None:
        self.assertTrue(run(pilot=pilot(evidence=[evidence("ghost"), evidence("d2", "r2")])))
        self.assertTrue(run(pilot=pilot(
            evidence=[{"route_decision_id": "d1", "session_id": "", "task_id": "t",
                       "turn_id": "1", "api_request_id": "r1"}, evidence("d2", "r2")])))
        self.assertTrue(run(pilot=pilot(evidence=[evidence("d1")])))

    def test_rehearsal_thresholds_are_measured_floors(self) -> None:
        class _Metrics:
            precision = 1.0
            recall = 0.5
            correct_no_skill_rate = 1.0

        record = pp._rehearsal_thresholds(pp.PROFILE, _Metrics())

        self.assertEqual(record["profile"], pp.PROFILE)
        self.assertEqual(record["thresholds"]["precision"], 1.0)
        self.assertEqual(record["thresholds"]["recall"], 0.5)


if __name__ == "__main__":
    unittest.main()
