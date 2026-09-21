#!/usr/bin/env python3
"""Measured expansion: the reviewed-batch procedure (ticket #58, ADR-0019/0022).

Drives the procedure at the ``InjectionGate`` seam and the external ``Broker.prepare_turn`` seam
it guards: fail-closed on an open Incident, fresh evidence, an additive batch, thresholds held or
re-derived against a larger corpus, a rollback rehearsal before enabling, and the post-batch
foundation-resolution/hash checks. The gate is operator state, so it is driven directly.

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

from broker.evaluation import (  # noqa: E402
    EvaluationError,
    EvaluationReport,
    SplitMetrics,
    ThresholdRegistry,
)
from broker.expansion import (  # noqa: E402
    ExpansionError,
    assert_expandable,
    assert_fresh_review,
    assert_superset,
    check_after_batch,
    expand,
    read_expansions,
    rehearse_rollback,
    verify_thresholds,
)
from broker.gate import (  # noqa: E402
    APPROVE,
    REJECT,
    GateReview,
    HardGate,
    InjectionBatch,
    InjectionGate,
)
from broker.report import ShadowReport  # noqa: E402
from broker.types import ResolvedSkillVersion, RouteDecision, TurnOutcome  # noqa: E402
from store_fixture import ALIASED, DISTINCT, make_store  # noqa: E402

NOW = "2026-09-21T00:00:00+00:00"
LATER = "2026-09-22T00:00:00+00:00"
PROFILE = "expansion-test"
COLD = "coreyhaines31.cold-email"
COPY = "coreyhaines31.copywriting"
HUMANIZER = "nousresearch.humanizer"


def grant(ident: str, version: str = "v1") -> ResolvedSkillVersion:
    return ResolvedSkillVersion(id=ident, name=ident.split(".")[-1], version=version)


def decision(*, grants=(), closure=(), reasons=(), profile: str = PROFILE) -> RouteDecision:
    return RouteDecision(
        profile=profile, session_id="s", outcome=TurnOutcome.GRANTED, reasons=tuple(reasons),
        request_sha256="0" * 64, request_chars=1,
        authorised_closure=tuple(closure), grants=tuple(grants))


def review(batch: InjectionBatch, *, digest: str = "report-1", at: str = LATER) -> GateReview:
    return GateReview(profile=batch.profile, batch=batch.name, reviewer="adam",
                      outcome=APPROVE, reviewed_at=at, report_sha256=digest)


def evaluation_report(profile: str = PROFILE, *, corpus: str = "corpus-1", cases: int = 20,
                      precision: float = 0.9) -> EvaluationReport:
    metrics = SplitMetrics(cases=cases, true_positive=int(precision * cases),
                           false_positive=cases - int(precision * cases), grants=cases,
                           hash_agreements=cases)
    return EvaluationReport(profile=profile, minimum=20, below_minimum=False, stage="gateable",
                            corpus_sha256=corpus, reviewed_cases=cases, threshold=metrics,
                            held_out=SplitMetrics(), attribution={}, attribution_by_stage={})


def shadow_report(batch: InjectionBatch, *, generated_at: str = LATER) -> ShadowReport:
    return ShadowReport(
        profile=batch.profile, batch=batch, window_start=None, window_end=None,
        generated_at=generated_at, turns=1, metrics=SplitMetrics(cases=1, true_positive=1),
        gate_status=(), threshold_movement=(), threshold_regressions=(),
        thresholds_pre_registered=True, disagreements=())


class ExpansionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="skill-broker-expansion-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.gate = InjectionGate.in_evidence_dir(root / "gate", clock=lambda: NOW)
        self.registry = ThresholdRegistry(root / "thresholds")
        self.registry.register(evaluation_report(), registered_at=NOW)
        self.first = InjectionBatch(name=f"{PROFILE}-brokered", profile=PROFILE, skills=(COLD,))
        self.second = InjectionBatch(name=f"{PROFILE}-brokered-2", profile=PROFILE,
                                     skills=(COLD, COPY, HUMANIZER))
        self.gate.enable(self.first, review(self.first, digest="pilot-report",
                                            at="2026-09-21T12:00:00+00:00"))


class AssertExpandableTest(ExpansionTestCase):
    def test_a_running_batch_with_no_open_incident_may_expand(self) -> None:
        assert_expandable(self.gate, PROFILE)  # does not raise

    def test_an_open_incident_fails_expansion_closed(self) -> None:
        self.gate.fail_closed(PROFILE, (HardGate.UNAUTHORISED_GRANT.value,),
                              reasons=("unauthorised grant",))
        with self.assertRaises(ExpansionError):
            assert_expandable(self.gate, PROFILE)

    def test_a_later_review_closes_the_incident(self) -> None:
        self.gate.fail_closed(PROFILE, (HardGate.UNAUTHORISED_GRANT.value,))
        self.gate.enable(self.first, review(self.first, digest="recovery", at=LATER))
        self.assertEqual(self.gate.open_incidents(PROFILE), [])
        assert_expandable(self.gate, PROFILE)

    def test_a_profile_with_no_running_batch_cannot_expand(self) -> None:
        self.gate.disable(PROFILE)
        with self.assertRaises(ExpansionError):
            assert_expandable(self.gate, PROFILE)

    def test_a_soft_threshold_block_holds_expansion(self) -> None:
        self.gate.note_expansion_regression(PROFILE, ("precision regressed: 0.1 < 0.9",))
        with self.assertRaises(ExpansionError):
            assert_expandable(self.gate, PROFILE)


class FreshReviewTest(ExpansionTestCase):
    def test_a_new_report_digest_after_the_previous_review_is_fresh(self) -> None:
        assert_fresh_review(self.gate, PROFILE, review(self.second, digest="fresh", at=LATER))

    def test_the_same_report_digest_is_refused(self) -> None:
        with self.assertRaises(ExpansionError):
            assert_fresh_review(self.gate, PROFILE,
                                review(self.second, digest="pilot-report", at=LATER))

    def test_a_review_before_the_previous_one_is_refused(self) -> None:
        with self.assertRaises(ExpansionError):
            assert_fresh_review(self.gate, PROFILE,
                                review(self.second, digest="fresh", at=NOW))


class SupersetTest(ExpansionTestCase):
    def test_a_superset_preserves_the_first_batch(self) -> None:
        assert_superset(self.gate, PROFILE, self.second)

    def test_dropping_a_skill_from_the_running_batch_is_refused(self) -> None:
        narrower = InjectionBatch(name="narrow", profile=PROFILE, skills=(COPY,))
        with self.assertRaises(ExpansionError):
            assert_superset(self.gate, PROFILE, narrower)


class RollbackRehearsalTest(ExpansionTestCase):
    def test_the_rehearsal_restores_the_running_batch(self) -> None:
        rehearsal = rehearse_rollback(self.gate, PROFILE)
        self.assertTrue(rehearsal.foundation_only)
        self.assertTrue(rehearsal.restored)
        self.assertTrue(self.gate.injecting(PROFILE))
        self.assertEqual(self.gate.enabled_batch(PROFILE), self.first)

    def test_a_profile_with_nothing_enabled_cannot_rehearse(self) -> None:
        self.gate.disable(PROFILE)
        with self.assertRaises(ExpansionError):
            rehearse_rollback(self.gate, PROFILE)

    def test_the_rehearsal_restores_a_batch_the_last_review_did_not_name(self) -> None:
        self.gate.record_review(GateReview(profile=PROFILE, batch="some-other-batch",
                                           reviewer="adam", outcome=REJECT, reviewed_at=LATER,
                                           report_sha256="other"))

        rehearsal = rehearse_rollback(self.gate, PROFILE)

        self.assertTrue(rehearsal.restored)
        self.assertEqual(self.gate.enabled_batch(PROFILE), self.first)


class ThresholdsTest(ExpansionTestCase):
    def test_unchanged_thresholds_are_returned_without_a_report(self) -> None:
        result = verify_thresholds(self.registry, PROFILE)
        self.assertTrue(result.unchanged)
        self.assertEqual(result.registration, self.registry.get(PROFILE))

    def test_a_re_derivation_must_be_explicit(self) -> None:
        with self.assertRaises(ExpansionError):
            verify_thresholds(self.registry, PROFILE,
                              report=evaluation_report(corpus="corpus-2"))

    def test_a_re_derivation_on_a_larger_corpus_records_the_corpus_and_baseline(self) -> None:
        larger = evaluation_report(corpus="corpus-2", cases=25)
        result = verify_thresholds(self.registry, PROFILE, report=larger,
                                   allow_re_derivation=True)
        self.assertFalse(result.unchanged)
        self.assertEqual(result.registration["corpus_sha256"], "corpus-2")
        self.assertEqual(result.registration["reviewed_cases"], 25)
        self.assertEqual(result.previous_reviewed_cases, 20)
        self.assertIn("baseline", result.registration)

    def test_a_re_derivation_on_the_same_or_smaller_corpus_is_refused(self) -> None:
        with self.assertRaises(EvaluationError):
            verify_thresholds(self.registry, PROFILE, report=evaluation_report(cases=20),
                              allow_re_derivation=True)

    def test_no_registration_cannot_expand(self) -> None:
        with self.assertRaises(ExpansionError):
            verify_thresholds(self.registry, "unregistered")


class PostBatchTest(unittest.TestCase):
    def test_a_clean_window_agrees_on_every_hash(self) -> None:
        closed = grant(COLD)
        report = check_after_batch(profile=PROFILE, batch="b",
                                   decisions=[decision(grants=(closed,), closure=(closed,))],
                                   foundation_ids=(COLD,))
        self.assertTrue(report.ok)
        self.assertEqual(report.hash_agreements, 1)
        self.assertEqual(report.hash_disagreements, 0)

    def test_a_foundation_regression_is_reported(self) -> None:
        report = check_after_batch(profile=PROFILE, batch="b", decisions=[decision()],
                                   foundation_ids=("nousresearch.hermes-agent",))
        self.assertFalse(report.ok)
        self.assertEqual(report.foundation_regressions, 1)
        self.assertIn(HardGate.FOUNDATION_REGRESSION.value, report.gates_breached)

    def test_a_hash_disagreement_is_reported(self) -> None:
        report = check_after_batch(
            profile=PROFILE, batch="b",
            decisions=[decision(grants=(grant(COLD, "v2"),), closure=(grant(COLD, "v1"),))])
        self.assertFalse(report.ok)
        self.assertEqual(report.hash_disagreements, 1)


class ExpandTest(ExpansionTestCase):
    def test_expansion_enables_the_second_batch_and_records_the_step(self) -> None:
        ledger = Path(self._tmp.name) / "expansions.jsonl"
        cold = grant(COLD)
        step = expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
                      reviewed_at=LATER, foundation_ids=(COLD,),
                      post_probe=lambda: [decision(grants=(cold,), closure=(cold,))],
                      registry=self.registry, ledger_path=ledger)
        self.assertTrue(step.post.ok)
        self.assertEqual(step.previous_batch["name"], self.first.name)
        self.assertEqual(self.gate.enabled_batch(PROFILE), self.second)
        self.assertTrue(self.gate.injecting(PROFILE))
        self.assertEqual(len(read_expansions(ledger)), 1)
        self.assertTrue(step.rollback.restored)
        self.assertTrue(step.thresholds["unchanged"])
        self.assertEqual(step.review["report_sha256"], shadow_report(self.second).report_sha256)

    def test_expansion_fails_closed_on_an_open_incident(self) -> None:
        self.gate.fail_closed(PROFILE, (HardGate.UNAUTHORISED_GRANT.value,))
        with self.assertRaises(ExpansionError):
            expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
                   reviewed_at=LATER, registry=self.registry, post_probe=lambda: [])
        self.assertTrue(self.gate.failed_closed(PROFILE))

    def test_expansion_refuses_a_report_the_shadow_review_would_reject(self) -> None:
        rejected = ShadowReport(
            profile=PROFILE, batch=self.second, window_start=None, window_end=None,
            generated_at=LATER, turns=1, metrics=SplitMetrics(), gate_status=(),
            threshold_movement=(), threshold_regressions=(), thresholds_pre_registered=False,
            disagreements=())
        with self.assertRaises(ExpansionError):
            expand(gate=self.gate, report=rejected, reviewer="adam", reviewed_at=LATER,
                   registry=self.registry, post_probe=lambda: [])
        self.assertEqual(self.gate.enabled_batch(PROFILE), self.first)

    def test_a_post_batch_breach_fails_the_profile_closed(self) -> None:
        expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
               reviewed_at=LATER, foundation_ids=(HUMANIZER,), post_probe=lambda: [decision()],
               registry=self.registry)
        self.assertTrue(self.gate.failed_closed(PROFILE))
        self.assertFalse(self.gate.injecting(PROFILE))

    def test_expansion_does_not_move_thresholds(self) -> None:
        before = self.registry.get(PROFILE)["corpus_sha256"]
        cold = grant(COLD)
        expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
               reviewed_at=LATER, registry=self.registry,
               post_probe=lambda: [decision(grants=(cold,), closure=(cold,))])
        self.assertEqual(self.registry.get(PROFILE)["corpus_sha256"], before)

    def test_an_empty_post_batch_window_rolls_back_and_refuses(self) -> None:
        with self.assertRaises(ExpansionError):
            expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
                   reviewed_at=LATER, registry=self.registry, post_probe=lambda: [])
        self.assertEqual(self.gate.enabled_batch(PROFILE), self.first)

    def test_a_failing_post_batch_probe_restores_the_previous_exposure(self) -> None:
        def boom():
            raise RuntimeError("probe exploded")

        with self.assertRaises(ExpansionError):
            expand(gate=self.gate, report=shadow_report(self.second), reviewer="adam",
                   reviewed_at=LATER, registry=self.registry, post_probe=boom)

        self.assertEqual(self.gate.enabled_batch(PROFILE), self.first)
        self.assertTrue(self.gate.injecting(PROFILE))


class ExpansionCliTest(unittest.TestCase):
    """The operator path: status, rehearse, and apply over a reviewed report (ticket #58)."""

    def setUp(self) -> None:
        import expand as expand_cli

        self.cli = expand_cli
        self._tmp = tempfile.TemporaryDirectory(prefix="skill-broker-expand-cli-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.store = make_store(profile="broker-test", foundation=[DISTINCT.id],
                                brokered=[ALIASED.id])
        self.addCleanup(self.store.close)
        self.evidence = root / "evidence"
        self.thresholds = root / "thresholds"
        registry = ThresholdRegistry(self.thresholds)
        registry.register(evaluation_report("broker-test"), registered_at=NOW)
        self.gate = InjectionGate.in_evidence_dir(self.evidence)
        self.first = InjectionBatch(name="broker-test-brokered", profile="broker-test",
                                    skills=(ALIASED.id,))
        self.gate.enable(self.first, GateReview(profile="broker-test",
                                                batch=self.first.name, reviewer="adam",
                                                outcome=APPROVE, reviewed_at=NOW,
                                                report_sha256="pilot-report"))

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.cli.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def args(self) -> list[str]:
        return ["--evidence-dir", str(self.evidence), "--thresholds-root", str(self.thresholds)]

    def test_status_reports_a_running_batch_as_expandable(self) -> None:
        code, status = self.run_cli("status", *self.args(), "--profile", "broker-test")
        self.assertEqual(code, 0)
        self.assertTrue(status["expandable"])
        self.assertTrue(status["thresholds_registered"])
        self.assertEqual(status["open_incidents"], [])

    def test_rehearse_restores_the_running_batch(self) -> None:
        code, rehearsal = self.run_cli("rehearse", *self.args(), "--profile", "broker-test")
        self.assertEqual(code, 0)
        self.assertTrue(rehearsal["foundation_only"])
        self.assertTrue(rehearsal["restored"])
        self.assertEqual(self.gate.enabled_batch("broker-test"), self.first)

    def test_apply_runs_the_procedure_for_a_reviewed_report(self) -> None:
        report = shadow_report(self.first)
        path = Path(self._tmp.name) / "shadow-report.json"
        path.write_text(json.dumps(report.to_record()) + "\n")

        code, step = self.run_cli("apply", *self.args(), "--report", str(path),
                                  "--store", str(self.store.root), "--reviewer", "adam",
                                  "--probe-request", "Please use writing-method for this piece.")

        self.assertEqual(code, 0, step)
        self.assertTrue(step["post"]["ok"])
        self.assertTrue(step["rollback"]["restored"])
        self.assertEqual(step["review"]["report_sha256"], report.report_sha256)
        self.assertTrue((self.evidence / "expansions.jsonl").exists())

    def test_status_fails_closed_for_an_open_incident(self) -> None:
        self.gate.fail_closed("broker-test", (HardGate.UNAUTHORISED_GRANT.value,))
        code, status = self.run_cli("status", *self.args(), "--profile", "broker-test")
        self.assertEqual(code, 0)
        self.assertFalse(status["expandable"])
        self.assertEqual(len(status["open_incidents"]), 1)


if __name__ == "__main__":
    unittest.main()
