#!/usr/bin/env python3
"""The expansion proof's acceptance logic, tested without the real Skill Store (ticket #58).

``run_expansion_proof.py`` is the executed evidence for #58 and is excluded from unittest
discovery because it routes over the real store. Its *assertions* are ordinary pure functions
over the report the harness collects, so they are tested here against a well-formed report and
deliberately broken ones: a checker that silently passed would turn a failed expansion green.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROOF = HERE / "hermes_adapter" / "run_expansion_proof.py"

_spec = importlib.util.spec_from_file_location("run_expansion_proof", PROOF)
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)

PROFILE = ep.PROFILE
COLD = ep.COLD_EMAIL_ID
NEW = ep.NEW_SKILL_ID
PILOT = ["local.adam-content-writing", "nousresearch.humanizer", COLD]
EXPANDED = PILOT + list(ep.ADDITIONS)


def observable(grants, *, pack="pack") -> dict:
    return {"outcome": "granted", "grants": list(grants), "pack_sha256": pack, "pack_chars": 10}


def report(**overrides) -> dict:
    base = {
        "profile": PROFILE,
        "pilot_brokered": PILOT,
        "expanded_brokered": EXPANDED,
        "batch_one": {"name": f"{PROFILE}-brokered", "profile": PROFILE, "skills": PILOT},
        "batch_two": {"name": f"{PROFILE}-brokered-2", "profile": PROFILE, "skills": EXPANDED},
        "report_one_sha256": "one",
        "report_two_sha256": "two",
        "report_two_thresholds_pre_registered": True,
        "report_two_gates_passed": True,
        "step": {
            "batch": {"name": f"{PROFILE}-brokered-2", "profile": PROFILE, "skills": EXPANDED},
            "previous_batch": {"name": f"{PROFILE}-brokered", "profile": PROFILE,
                               "skills": PILOT},
            "review": {"outcome": "approve", "report_sha256": "two"},
            "thresholds": {"unchanged": True},
            "rollback": {"foundation_only": True, "restored": True},
            "post": {"ok": True, "foundation_regressions": 0, "hash_disagreements": 0,
                     "hash_agreements": 2, "gates_breached": []},
        },
        "thresholds_before": {"corpus_sha256": "c1"},
        "thresholds_after": {"corpus_sha256": "c1"},
        "cold_before": observable([COLD]),
        "cold_after": observable([COLD]),
        "new_grant": observable([NEW]),
        "rollback": {"foundation_only": True, "first_batch_restored": True},
        "fail_closed": {"refused": True, "failed_closed": True},
        "ledger_records": 1,
    }
    base.update(overrides)
    return base


def run(**overrides) -> list[str]:
    return ep.assert_proof(report(**overrides))


class ExpansionProofAssertionsTest(unittest.TestCase):
    def test_the_well_formed_expansion_passes(self) -> None:
        self.assertEqual(run(), [])

    def test_first_batch_checks(self) -> None:
        self.assertTrue(run(batch_two={"name": "b", "profile": PROFILE,
                                       "skills": [COLD]}))
        self.assertTrue(run(batch_one={"name": "b", "profile": PROFILE, "skills": []}))
        self.assertTrue(run(pilot_brokered=PILOT + ["local.extra"]))
        self.assertTrue(run(cold_after=observable(["other"])))
        self.assertTrue(run(cold_after=observable([COLD], pack="different")))
        self.assertTrue(run(cold_before=None))
        self.assertTrue(run(new_grant=observable(["other"])))

    def test_second_review_checks(self) -> None:
        self.assertTrue(run(report_two_thresholds_pre_registered=False))
        self.assertTrue(run(report_two_gates_passed=False))
        self.assertTrue(run(report_one_sha256="two", report_two_sha256="two"))
        broken = report()
        broken["step"] = {**broken["step"], "review": {"outcome": "reject",
                                                       "report_sha256": "two"}}
        self.assertTrue(ep.assert_proof(broken))
        bound = report()
        bound["step"] = {**bound["step"], "review": {"outcome": "approve",
                                                     "report_sha256": "other"}}
        self.assertTrue(ep.assert_proof(bound))

    def test_expansion_checks(self) -> None:
        enabled = report()
        enabled["step"] = {**enabled["step"], "batch": {"name": "other"}}
        self.assertTrue(ep.assert_proof(enabled))
        previous = report()
        previous["step"] = {**previous["step"], "previous_batch": None}
        self.assertTrue(ep.assert_proof(previous))
        post = report()
        post["step"] = {**post["step"],
                        "post": {"ok": False, "foundation_regressions": 1,
                                 "hash_disagreements": 0, "hash_agreements": 2,
                                 "gates_breached": ["foundation_regression"]}}
        self.assertTrue(ep.assert_proof(post))
        hashes = report()
        hashes["step"] = {**hashes["step"],
                          "post": {"ok": False, "foundation_regressions": 0,
                                   "hash_disagreements": 1, "hash_agreements": 1,
                                   "gates_breached": ["hash_disagreement"]}}
        self.assertTrue(ep.assert_proof(hashes))
        noagreement = report()
        noagreement["step"] = {**noagreement["step"],
                               "post": {"ok": True, "foundation_regressions": 0,
                                        "hash_disagreements": 0, "hash_agreements": 0,
                                        "gates_breached": []}}
        self.assertTrue(ep.assert_proof(noagreement))
        self.assertTrue(run(ledger_records=0))

    def test_threshold_checks(self) -> None:
        self.assertTrue(run(thresholds_after={"corpus_sha256": "c2"}))
        moved = report()
        moved["step"] = {**moved["step"], "thresholds": {"unchanged": False}}
        self.assertTrue(ep.assert_proof(moved))

    def test_rollback_checks(self) -> None:
        self.assertTrue(run(rollback={"foundation_only": False, "first_batch_restored": True}))
        self.assertTrue(run(rollback={"foundation_only": True, "first_batch_restored": False}))
        rehearsal = report()
        rehearsal["step"] = {**rehearsal["step"],
                             "rollback": {"foundation_only": False, "restored": True}}
        self.assertTrue(ep.assert_proof(rehearsal))
        notrestored = report()
        notrestored["step"] = {**notrestored["step"],
                               "rollback": {"foundation_only": True, "restored": False}}
        self.assertTrue(ep.assert_proof(notrestored))

    def test_fail_closed_checks(self) -> None:
        self.assertTrue(run(fail_closed={"refused": False, "failed_closed": True}))
        self.assertTrue(run(fail_closed={"refused": True, "failed_closed": False}))


if __name__ == "__main__":
    unittest.main()
