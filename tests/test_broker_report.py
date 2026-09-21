#!/usr/bin/env python3
"""The Shadow Report and its batch review (ticket #56, ADR-0019).

Drives the report at its real seam: recorded Route Decisions from the Evidence Log plus the
skill use observed in a Hermes ``state.db``, over a bounded window. Asserts the ticket's
acceptance criteria — the window and its assembly, the named batch, the Hard-Gate status and
Soft-Threshold movement, the attributed disagreements, and the review that is the only thing
that enables a batch.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import (  # noqa: E402
    APPROVE,
    APPROVE_NARROWER,
    REJECT,
    Broker,
    HardGate,
    InjectionBatch,
    InjectionGate,
    JsonlEvidenceLog,
    ReportError,
    ShadowReportStore,
    StubJudgmentSource,
    apply_review,
    build_shadow_report,
    load_route_decisions,
)
from broker.evaluation import CORRECT, JUDGMENT_MISS, RETRIEVAL_MISS  # noqa: E402
from broker_fixture import judgment_claim  # noqa: E402
from corpus_fixture import add_session, add_skill_call, add_turn, make_corpus_fixture  # noqa: E402
from store_fixture import ALIASED, DISTINCT, Identity  # noqa: E402

TONE = Identity(owner="local", slug="tone-guide", name="tone-guide",
                body="# Tone guide\n\nVoice for this profile.\n")
REVIEW_REQUEST = "Please use writing-method for this piece."
GRANT = judgment_claim([DISTINCT.id, ALIASED.id, TONE.id], primary=ALIASED.id)


class ReportTestCase(unittest.TestCase):
    """A fixture store (one Foundation, two Brokered Skills) and its recorded decisions."""

    def setUp(self) -> None:
        self.fixture = make_corpus_fixture(identities=(DISTINCT, ALIASED, TONE),
                                           brokered=(ALIASED.id, TONE.id))
        self.addCleanup(self.fixture.close)
        self.profile = self.fixture.store.profile
        self.claims = []
        self.evidence = self.fixture.root / "route_decisions.jsonl"

    def record(self, *, claim=None, turns=("1", "2"), session="s1"):
        source = StubJudgmentSource(claim or GRANT)
        broker = Broker(store=self.fixture.store.root,
                        evidence_log=JsonlEvidenceLog(self.evidence),
                        judgment_source=source)
        for turn_id in turns:
            broker.prepare_turn(REVIEW_REQUEST, self.profile,
                                {"session_id": session, "turn_id": turn_id})

    def build(self, **overrides):
        return build_shadow_report(
            profile=self.profile, store=self.fixture.store.root,
            evidence_path=self.evidence, db=self.fixture.db, **overrides)

    def enable_gate(self, tmp: str) -> InjectionGate:
        return InjectionGate(state_path=Path(tmp) / "gate.json")


class AssemblyTest(ReportTestCase):
    """The report is assembled from Decisions plus observed use over a bounded window (AC1)."""

    def test_a_turn_outside_the_window_is_left_out(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        second = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=200.0)
        third = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=300.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=110.0)
        add_skill_call(self.fixture.db, "s1", "tone-guide", timestamp=210.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=310.0)
        self.record(turns=(str(first), str(second), str(third)))

        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.turns, 2)
        self.assertEqual([entry.turn_id for entry in report.disagreements], [str(second)])
        self.assertEqual(self.build(since=0.0, until=350.0).turns, 3)

    def test_a_decision_whose_turn_is_missing_when_windowed_is_a_recorded_problem(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        self.record(turns=(str(first), "missing"))

        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.turns, 1)
        self.assertIn("turn_not_in_window", " ".join(report.problems))

    def test_the_decisions_are_read_back_from_the_evidence_log(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        self.record(turns=(str(first),))

        decisions = load_route_decisions(self.evidence, profile=self.profile)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].grants[0].name, ALIASED.name)
        self.assertEqual(len(decisions[0].authorised_closure), 3)


class LiveJoinTest(ReportTestCase):
    """A live Decision joins its turn by request content, not the ephemeral hook turn id (#60)."""

    def prepare(self, *, session="s1", turn_id="s1:task:deadbeef"):
        broker = Broker(store=self.fixture.store.root,
                        evidence_log=JsonlEvidenceLog(self.evidence),
                        judgment_source=StubJudgmentSource(GRANT))
        broker.prepare_turn(REVIEW_REQUEST, self.profile,
                            {"session_id": session, "turn_id": turn_id})

    def test_a_live_decision_joins_by_request_hash(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        self.prepare()

        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.turns, 1)
        self.assertEqual(report.problems, ())

    def test_a_repeated_request_resolves_to_each_of_its_turns(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        second = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=200.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=110.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=210.0)
        self.prepare(turn_id="s1:task:first")
        self.prepare(turn_id="s1:task:second")

        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.turns, 2)
        self.assertEqual(report.metrics.true_positive, 2)
        self.assertEqual({entry.turn_id for entry in report.disagreements}, set())
        self.assertEqual(len({str(first), str(second)}), 2)

    def test_a_live_decision_with_no_matching_turn_is_a_recorded_problem(self) -> None:
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        add_turn(self.fixture.db, "s1", "a different request", timestamp=100.0)
        self.prepare()

        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.turns, 0)
        self.assertIn("turn_not_in_window", " ".join(report.problems))


class BatchNamingTest(ReportTestCase):
    """The report names the profile and the exact batch enabling would cover (AC2)."""

    def test_the_batch_is_the_profile_brokered_allowlist(self) -> None:
        self.record(turns=())

        report = self.build()

        self.assertEqual(report.profile, self.profile)
        self.assertEqual(report.batch.name, f"{self.profile}-brokered")
        self.assertEqual(report.batch.skills, (ALIASED.id, TONE.id))

    def test_a_profile_without_a_policy_cannot_be_reported(self) -> None:
        with self.assertRaises(ReportError):
            build_shadow_report(profile="ghost", store=self.fixture.store.root,
                                evidence_path=self.evidence, db=self.fixture.db)


class MeasurementTest(ReportTestCase):
    """Actual use and human review measure the would-have-selected (AC3, AC4)."""

    def setUp(self) -> None:
        super().setUp()
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        self.first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        self.second = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=200.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=110.0)
        add_skill_call(self.fixture.db, "s1", "tone-guide", timestamp=210.0)
        self.record(turns=(str(self.first), str(self.second)))

    def test_agreement_and_disagreement_are_measured_against_actual_use(self) -> None:
        report = self.build(since=0.0, until=250.0)

        self.assertEqual(report.metrics.true_positive, 1)
        self.assertEqual(report.metrics.false_positive, 1)
        self.assertEqual(report.metrics.precision, 0.5)
        self.assertEqual(len(report.disagreements), 1)
        self.assertEqual(report.disagreements[0].would_have_selected, ALIASED.name)
        self.assertEqual(report.disagreements[0].observed, (TONE.name,))
        self.assertEqual(report.disagreements[0].attribution, JUDGMENT_MISS)

    def test_a_human_review_overrides_actual_use_for_the_disagreement(self) -> None:
        report = self.build(since=0.0, until=250.0,
                            human_review={("s1", str(self.second)): TONE.id})

        self.assertEqual(len(report.disagreements), 1)
        self.assertEqual(report.disagreements[0].reviewed, TONE.id)

    def test_a_broker_agreeing_with_the_review_is_correct_despite_actual_use(self) -> None:
        report = self.build(since=0.0, until=250.0,
                            human_review={("s1", str(self.second)): ALIASED.id})

        self.assertEqual(len(report.disagreements), 1)
        self.assertEqual(report.disagreements[0].attribution, CORRECT)

    def test_a_review_for_a_ghost_skill_is_attributed_to_retrieval(self) -> None:
        missing = self.build(since=0.0, until=250.0,
                             human_review={("s1", str(self.second)): "local.ghost"})

        self.assertEqual(missing.disagreements[0].attribution, RETRIEVAL_MISS)

    def test_gate_status_reports_a_prompt_schema_incident_from_the_gate_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.fail_closed(self.profile, (HardGate.PROMPT_SCHEMA_MUTATION.value,))

            report = self.build(since=0.0, until=250.0, gate=gate)

            statuses = {status.gate: status for status in report.gate_status}
            self.assertEqual(statuses[HardGate.PROMPT_SCHEMA_MUTATION.value].status, "breach")

    def test_gate_status_reports_a_breach_named_by_the_decision(self) -> None:
        invalid = self.fixture.root / "invalid.jsonl"
        broker = Broker(store=self.fixture.store.root, evidence_log=JsonlEvidenceLog(invalid),
                        judgment_source=StubJudgmentSource({"primary": ALIASED.id}))
        broker.prepare_turn(REVIEW_REQUEST, self.profile,
                            {"session_id": "s1", "turn_id": str(self.first)})

        report = build_shadow_report(profile=self.profile, store=self.fixture.store.root,
                                     evidence_path=invalid, db=self.fixture.db,
                                     since=0.0, until=250.0)

        statuses = {status.gate: status for status in report.gate_status}
        self.assertTrue(statuses[HardGate.JUDGMENT_INVALID.value].passed is False)
        self.assertFalse(report.gates_passed)

    def test_the_prompt_schema_gate_is_not_checked_without_the_gate_log(self) -> None:
        report = self.build(since=0.0, until=250.0)

        statuses = {status.gate: status for status in report.gate_status}
        unchecked = statuses[HardGate.PROMPT_SCHEMA_MUTATION.value]
        self.assertEqual(unchecked.status, "not_checked")
        self.assertFalse(unchecked.checked)
        self.assertTrue(report.gates_passed)

    def test_threshold_movement_is_reported_when_preregistered(self) -> None:
        regressed = self.build(since=0.0, until=250.0,
                               thresholds={"thresholds": {"precision": 0.9, "recall": 0.5}})
        clear = self.build(since=0.0, until=250.0,
                           thresholds={"thresholds": {"precision": 0.4, "recall": 0.5}})

        self.assertTrue(regressed.thresholds_pre_registered)
        self.assertTrue(any("precision regressed" in reason
                            for reason in regressed.threshold_movement))
        self.assertEqual(clear.threshold_movement, ())

    def test_without_a_preregistration_there_is_no_movement_to_report(self) -> None:
        report = self.build(since=0.0, until=250.0)

        self.assertFalse(report.thresholds_pre_registered)
        self.assertEqual(report.threshold_movement, ())


class ReviewTest(ReportTestCase):
    """The review is the only thing that enables a batch (AC5, AC6)."""

    def setUp(self) -> None:
        super().setUp()
        add_session(self.fixture.db, "s1", "cli", profile_name=self.profile, started_at=0.0)
        self.first = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=100.0)
        add_skill_call(self.fixture.db, "s1", "writing-method", timestamp=110.0)
        self.record(turns=(str(self.first),))
        self.report = self.build(since=0.0, until=250.0,
                                 thresholds={"thresholds": {"precision": 0.5, "recall": 0.5}})

    def test_building_a_report_enables_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)

            self.assertFalse(gate.injecting(self.profile))

    def test_an_approval_enables_the_named_batch_bound_to_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)

            reviewed = apply_review(self.report, outcome=APPROVE, reviewer="adam", gate=gate)

            self.assertTrue(gate.injecting(self.profile))
            self.assertEqual(gate.enabled_batch(self.profile).name, self.report.batch.name)
            self.assertEqual(reviewed.review["outcome"], APPROVE)
            self.assertEqual(reviewed.review["report_sha256"], self.report.report_sha256)
            self.assertEqual(reviewed.report_sha256, self.report.report_sha256)

    def test_an_approval_refuses_a_batch_that_is_not_the_reviewed_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            narrower = InjectionBatch(name=f"{self.profile}-narrowed", profile=self.profile,
                                      skills=(ALIASED.id,))

            with self.assertRaises(ReportError):
                apply_review(self.report, outcome=APPROVE, reviewer="adam", gate=gate,
                             batch=narrower)

            self.assertFalse(gate.injecting(self.profile))

    def test_a_rejection_enables_nothing_and_records_the_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)

            reviewed = apply_review(self.report, outcome=REJECT, reviewer="adam", gate=gate)

            self.assertFalse(gate.injecting(self.profile))
            self.assertEqual(gate.status(self.profile)["last_review"]["outcome"], REJECT)
            self.assertEqual(reviewed.review["outcome"], REJECT)

    def test_a_narrower_approval_enables_only_the_narrowed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            narrower = InjectionBatch(name=f"{self.profile}-narrowed", profile=self.profile,
                                      skills=(ALIASED.id,))

            apply_review(self.report, outcome=APPROVE_NARROWER, reviewer="adam", gate=gate,
                         batch=narrower)

            self.assertEqual(gate.enabled_batch(self.profile).skills, (ALIASED.id,))
            self.assertEqual(gate.enabled_batch(self.profile).name, narrower.name)

    def test_an_approval_without_a_pre_registration_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            plain = self.build(since=0.0, until=250.0)

            with self.assertRaises(ReportError):
                apply_review(plain, outcome=APPROVE, reviewer="adam", gate=gate)
            self.assertFalse(gate.injecting(self.profile))

    def test_an_approval_on_a_breached_report_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            gate.fail_closed(self.profile, (HardGate.JUDGMENT_INVALID.value,))
            breached = self.build(since=0.0, until=250.0,
                                  thresholds={"thresholds": {"precision": 0.5}}, gate=gate)

            with self.assertRaises(ReportError):
                apply_review(breached, outcome=APPROVE, reviewer="adam", gate=gate)

    def test_a_soft_threshold_regression_blocks_expansion(self) -> None:
        second = add_turn(self.fixture.db, "s1", REVIEW_REQUEST, timestamp=200.0)
        add_skill_call(self.fixture.db, "s1", "tone-guide", timestamp=210.0)
        self.record(turns=(str(second),))
        regressed = self.build(since=0.0, until=250.0,
                               thresholds={"thresholds": {"precision": 0.9}})

        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)

            with self.assertRaises(ReportError):
                apply_review(regressed, outcome=APPROVE, reviewer="adam", gate=gate)

            self.assertTrue(gate.expansion_blocked(self.profile))
            self.assertFalse(gate.injecting(self.profile))

    def test_an_unknown_outcome_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)

            with self.assertRaises(ReportError):
                apply_review(self.report, outcome="maybe", reviewer="adam", gate=gate)

    def test_a_narrower_approval_needs_a_proper_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            whole = InjectionBatch(name="whole", profile=self.profile,
                                   skills=self.report.batch.skills)

            with self.assertRaises(ReportError):
                apply_review(self.report, outcome=APPROVE_NARROWER, reviewer="adam", gate=gate)
            with self.assertRaises(ReportError):
                apply_review(self.report, outcome=APPROVE_NARROWER, reviewer="adam", gate=gate,
                             batch=whole)
            self.assertFalse(gate.injecting(self.profile))


class StoreTest(ReportTestCase):
    """The report round-trips through its store, digest stable across the review (AC5)."""

    def setUp(self) -> None:
        super().setUp()
        self.record(turns=())
        self.report = self.build(thresholds={"thresholds": {"precision": 0.5}})

    def test_a_report_round_trips_and_keeps_its_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ShadowReportStore(tmp)
            path = store.save(self.report)

            loaded = store.load(path)

            self.assertEqual(loaded.to_record(), self.report.to_record())
            self.assertEqual(loaded.report_sha256, self.report.report_sha256)
            self.assertNotIn("request text", json.dumps(loaded.to_record()))

    def test_the_digest_does_not_move_when_a_review_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = self.enable_gate(tmp)
            reviewed = apply_review(self.report, outcome=APPROVE, reviewer="adam", gate=gate)

            self.assertEqual(reviewed.report_sha256, self.report.report_sha256)


class ShadowReportCliTest(ReportTestCase):
    """The operator CLI builds, shows and reviews the same artifact the module does."""

    def setUp(self) -> None:
        super().setUp()
        self.record(turns=())
        self.report = self.build(thresholds={"thresholds": {"precision": 0.5}})

    def run_cli(self, *argv) -> tuple[int, dict]:
        import contextlib
        import io

        import shadow_report

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = shadow_report.main(list(argv))
        return code, json.loads(out.getvalue())

    def test_show_prints_the_report_and_review_records_the_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ShadowReportStore(Path(tmp) / "reports")
            path = store.save(self.report)

            code, shown = self.run_cli("show", "--report", str(path))
            self.assertEqual(code, 0)
            self.assertEqual(shown["profile"], self.profile)

            code, rejected = self.run_cli("review", "--report", str(path), "--outcome",
                                          "reject", "--reviewer", "adam", "--evidence-dir", tmp)
            self.assertEqual(code, 0)
            self.assertFalse(rejected["gate"]["injecting"])

            code, approved = self.run_cli("review", "--report", str(path), "--outcome",
                                          "approve", "--reviewer", "adam", "--evidence-dir", tmp)
            self.assertEqual(code, 0)
            self.assertTrue(approved["gate"]["injecting"])
            self.assertEqual(approved["review"]["report_sha256"], self.report.report_sha256)


class SkillUseReaderTest(unittest.TestCase):
    """The observed-use reader attributes a call to the user turn that preceded it."""

    def test_skill_views_are_attributed_to_the_latest_earlier_turn(self) -> None:
        fixture = make_corpus_fixture()
        self.addCleanup(fixture.close)
        add_session(fixture.db, "s1", "cli", started_at=0.0)
        first = add_turn(fixture.db, "s1", "one", timestamp=10.0)
        second = add_turn(fixture.db, "s1", "two", timestamp=20.0)
        add_skill_call(fixture.db, "s1", "writing-method", timestamp=15.0)
        add_skill_call(fixture.db, "s1", "writing-method", timestamp=25.0,
                       file_path="references/voice.md")
        add_skill_call(fixture.db, "s1", "tone-guide", timestamp=26.0)

        from broker.corpus import read_skill_use

        uses = read_skill_use(fixture.db)

        self.assertEqual([(use.turn_id, use.skill) for use in uses],
                         [(str(first), "writing-method"), (str(second), "writing-method"),
                          (str(second), "tone-guide")])


if __name__ == "__main__":
    unittest.main()
