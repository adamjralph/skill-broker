"""Offline routing evaluation and pre-registered Soft Thresholds (ADR-0019, spec B13, ticket #55).

The evaluation turns hand-reviewed Cases into the numbers the Stage 5 gate needs. For each
Reviewed Case it replays the real :class:`~broker.broker.Broker` pipeline against the Case's
SHA-bound **Recording** — no network and no model — and compares the deterministic result with
the hand-corrected ground truth. It reports, per profile and per split, selection precision and
recall, the correct no-skill rate, the unauthorised-grant rate, closure failures, the average
Candidate count, the average Pack size, the duplicate-injection rate, and broker/native hash
agreement. The threshold-setting set and the disjoint held-out set are reported separately, and
only the threshold set may derive thresholds, so the gate is measured on data it was not tuned
on (ADR-0020).

Each turn is attributed to the stage that decided it — deterministic **retrieval**, the semantic
**Judgment**, or the code-enforced **Grant** — so a wrong outcome can be explained (AC5). Soft
Thresholds are derived from the measured threshold-set baseline and **pre-registered** alongside
the corpus digest and the baseline they came from, before any Shadow Report is reviewed
(ADR-0019); a profile below the Stage 5 minimum is reported as staying in Shadow Mode rather
than gated on thin data.

The registration artifact is the pre-registration record — the thresholds plus the corpus digest
and the baseline they were derived from. ADR-0019 has the values ultimately living in the Profile
Policy (`thresholds.*`, `limits.*`); wiring them into the store's `policies/<profile>.json` is a
reviewed store change (ADR-0018) at the Stage 5/6 boundary, deliberately not something this
offline harness performs on its own.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path

from .broker import Broker
from .corpus import (
    DEFAULT_STAGE5_MINIMUM,
    HELD_OUT_SPLIT,
    CorpusError,
    CorpusStore,
    LabelStore,
    RecordingStore,
    ReviewedCase,
    load_reviewed_cases,
    profile_status,
    write_canonical,
)
from .evidence import SessionLedger
# ``soft_threshold_regression`` is defined in ``gate`` (the module that enforces the expansion
# block) and re-exported here, where the Soft Thresholds themselves live, for evaluation callers.
from .gate import closure_failure_kind, soft_threshold_regression
from .judgment import RecordingMismatch
from .types import RouteDecision, TurnOutcome

#: The committed pre-registration root; the CLI defaults here too.
DEFAULT_THRESHOLDS_ROOT = Path(__file__).resolve().parents[2] / "corpus" / "thresholds"

#: Attribution labels: which stage of the pipeline decided the turn's outcome.
CORRECT = "correct"
CORRECT_NO_SKILL = "correct_no_skill"
RETRIEVAL_MISS = "retrieval_miss"
JUDGMENT_MISS = "judgment_miss"
GRANT_MISS = "grant_miss"
FALSE_INTERVENTION = "false_intervention"
FAILURE = "failure"
ATTRIBUTIONS = (CORRECT, CORRECT_NO_SKILL, RETRIEVAL_MISS, JUDGMENT_MISS, GRANT_MISS,
                FALSE_INTERVENTION, FAILURE)

#: Attribution rolled up to the pipeline stage a wrong outcome belongs to.
_ATTRIBUTION_STAGE = {
    CORRECT: "correct",
    CORRECT_NO_SKILL: "correct",
    RETRIEVAL_MISS: "retrieval",
    JUDGMENT_MISS: "judgment",
    FALSE_INTERVENTION: "judgment",
    GRANT_MISS: "grant",
    FAILURE: "failure",
}
STAGE_NAMES = ("correct", "retrieval", "judgment", "grant", "failure")

THRESHOLD_VERSION = 1


class EvaluationError(CorpusError):
    """The evaluation or the threshold registration cannot proceed."""


def attribute(*, failed: bool, ground_truth: str | None, selected: str | None,
              candidates: Sequence[str],
              judgment_primary: str | None) -> str:
    """Attribute one turn's outcome to retrieval, Judgment or Grant (AC5).

    ``ground_truth`` is the hand-reviewed Primary Skill or ``None`` for a No-Skill Outcome;
    ``selected`` is the Skill the deterministic Grant supplied, or ``None``. A ground truth the
    Candidate set never surfaced is a **retrieval** miss; one the Judgment passed over is a
    **judgment** miss; one the Judgment chose but the Grant did not supply is a **grant** miss.
    """
    if failed:
        return FAILURE
    if ground_truth is None:
        return CORRECT_NO_SKILL if selected is None else FALSE_INTERVENTION
    if selected == ground_truth:
        return CORRECT
    if ground_truth not in candidates:
        return RETRIEVAL_MISS
    if judgment_primary != ground_truth:
        return JUDGMENT_MISS
    return GRANT_MISS


#: The stored integer fields of :class:`SplitMetrics`, named once for ``from_record``.
_SPLIT_METRIC_FIELDS = (
    "cases", "granted", "no_skill", "failures", "true_positive", "false_positive",
    "false_negative", "true_negative", "grants", "unauthorised_grants", "closure_failures",
    "dependency_failures", "cycle_failures", "candidate_total", "pack_deliveries",
    "pack_chars_total", "duplicate_suppressions", "hash_agreements", "hash_disagreements",
)


def _rate(numerator: int, denominator: int) -> float | None:
    """A rounded rate, or ``None`` when the denominator is zero (undefined, not zero)."""
    return None if denominator == 0 else round(numerator / denominator, 6)


@dataclass(frozen=True)
class SplitMetrics:
    """One split's measured baseline — counts plus the rates the gate is read from."""
    cases: int = 0
    granted: int = 0
    no_skill: int = 0
    failures: int = 0
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    grants: int = 0
    unauthorised_grants: int = 0
    closure_failures: int = 0
    dependency_failures: int = 0
    cycle_failures: int = 0
    candidate_total: int = 0
    pack_deliveries: int = 0
    pack_chars_total: int = 0
    duplicate_suppressions: int = 0
    hash_agreements: int = 0
    hash_disagreements: int = 0

    @property
    def precision(self) -> float | None:
        return _rate(self.true_positive, self.true_positive + self.false_positive)

    @property
    def recall(self) -> float | None:
        return _rate(self.true_positive, self.true_positive + self.false_negative)

    @property
    def correct_no_skill_rate(self) -> float | None:
        return _rate(self.true_negative, self.true_negative + self.false_positive)

    @property
    def unauthorised_grant_rate(self) -> float | None:
        return _rate(self.unauthorised_grants, self.grants)

    @property
    def duplicate_injection_rate(self) -> float | None:
        return _rate(self.duplicate_suppressions, self.cases)

    @property
    def average_candidates(self) -> float | None:
        return _rate(self.candidate_total, self.cases)

    @property
    def average_pack_chars(self) -> float | None:
        return _rate(self.pack_chars_total, self.pack_deliveries)

    @property
    def hash_agreement_rate(self) -> float | None:
        return _rate(self.hash_agreements, self.grants)

    def to_record(self) -> dict:
        return {
            "cases": self.cases,
            "granted": self.granted,
            "no_skill": self.no_skill,
            "failures": self.failures,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "true_negative": self.true_negative,
            "precision": self.precision,
            "recall": self.recall,
            "correct_no_skill_rate": self.correct_no_skill_rate,
            "grants": self.grants,
            "unauthorised_grants": self.unauthorised_grants,
            "unauthorised_grant_rate": self.unauthorised_grant_rate,
            "closure_failures": self.closure_failures,
            "dependency_failures": self.dependency_failures,
            "cycle_failures": self.cycle_failures,
            "average_candidates": self.average_candidates,
            "pack_deliveries": self.pack_deliveries,
            "average_pack_chars": self.average_pack_chars,
            "duplicate_suppressions": self.duplicate_suppressions,
            "duplicate_injection_rate": self.duplicate_injection_rate,
            "hash_agreements": self.hash_agreements,
            "hash_disagreements": self.hash_disagreements,
            "hash_agreement_rate": self.hash_agreement_rate,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "SplitMetrics":
        """Rebuild the stored counts from a record; the dataclass fields are the one field list."""
        return cls(**{field.name: int(record.get(field.name) or 0) for field in fields(cls)})


@dataclass(frozen=True)
class EvaluationReport:
    """One profile's offline evaluation over its Reviewed Cases, split in two (AC2, AC3)."""

    profile: str
    minimum: int
    below_minimum: bool
    stage: str
    corpus_sha256: str
    reviewed_cases: int
    threshold: SplitMetrics
    held_out: SplitMetrics
    attribution: dict[str, int]
    attribution_by_stage: dict[str, int]
    problems: tuple[str, ...] = ()

    def to_record(self) -> dict:
        return {
            "ok": True,
            "profile": self.profile,
            "minimum": self.minimum,
            "below_minimum": self.below_minimum,
            "stage": self.stage,
            "corpus_sha256": self.corpus_sha256,
            "reviewed_cases": self.reviewed_cases,
            "threshold": self.threshold.to_record(),
            "held_out": self.held_out.to_record(),
            "attribution": dict(sorted(self.attribution.items())),
            "attribution_by_stage": dict(sorted(self.attribution_by_stage.items())),
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class SoftThresholds:
    """The routing-quality baseline pre-registered from the threshold set (AC4).

    Each value is the measured threshold-set baseline. The quality rates (``precision``,
    ``recall``, ``correct_no_skill_rate``) are floors a future run may not fall below; the cost
    rates (``duplicate_injection_rate``, ``average_candidates``, ``average_pack_chars``) are the
    measured reference a later run is compared against. The registration moves only by
    re-derivation against a larger corpus (ADR-0019). ``None`` means the baseline was undefined
    (no cases of that kind), so there is nothing to hold.
    """

    precision: float | None = None
    recall: float | None = None
    correct_no_skill_rate: float | None = None
    duplicate_injection_rate: float | None = None
    average_candidates: float | None = None
    average_pack_chars: float | None = None

    def to_record(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "correct_no_skill_rate": self.correct_no_skill_rate,
            "duplicate_injection_rate": self.duplicate_injection_rate,
            "average_candidates": self.average_candidates,
            "average_pack_chars": self.average_pack_chars,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "SoftThresholds":
        """Rebuild the pre-registered thresholds from their record."""
        return cls(
            precision=record.get("precision"),
            recall=record.get("recall"),
            correct_no_skill_rate=record.get("correct_no_skill_rate"),
            duplicate_injection_rate=record.get("duplicate_injection_rate"),
            average_candidates=record.get("average_candidates"),
            average_pack_chars=record.get("average_pack_chars"),
        )


@dataclass(frozen=True)
class _Measurement:
    """One replayed Reviewed Case's result, the unit the split metrics aggregate."""

    reviewed: ReviewedCase
    selected: str | None
    outcome: TurnOutcome
    attribution: str
    duplicate: bool
    unauthorised: int
    hash_ok: bool
    dependency_failure: bool
    cycle_failure: bool
    candidates: int
    pack_chars: int | None
    recording_sha256: str

    @property
    def split(self) -> str:
        return self.reviewed.case.split

    @property
    def failed(self) -> bool:
        return self.outcome is TurnOutcome.FAILURE


class _NullEvidenceLog:
    """A sink: the evaluation reads each decision from the Intervention Result, not the log."""

    def append(self, decision: RouteDecision) -> None:
        del decision


def evaluate_profile(*, corpus: CorpusStore, labels: LabelStore, recordings: RecordingStore,
                     store: Path | str, profile: str,
                     minimum: int = DEFAULT_STAGE5_MINIMUM) -> EvaluationReport:
    """Replay every Reviewed Case offline and measure the profile's baseline.

    No network and no model: every turn's Judgment is its Recording, bound by content hash, and
    a missing or drifted Recording is recorded as a problem and a failed turn rather than falling
    back to a live call (AC1). The threshold-setting and held-out sets are measured separately;
    nothing here consults the held-out set for anything but its own report.
    """
    reviewed = load_reviewed_cases(corpus, labels, profile)
    ordered = sorted(reviewed, key=lambda entry: (entry.case.started_at, entry.case.turn_id))
    ledger = SessionLedger()
    problems: list[str] = []
    measurements: list[_Measurement] = []

    for entry in ordered:
        case = entry.case
        try:
            source = recordings.bind(case)
        except RecordingMismatch:
            problems.append(f"{case.case_sha256}: recording_missing")
            measurements.append(_Measurement(
                reviewed=entry, selected=None, outcome=TurnOutcome.FAILURE,
                attribution=FAILURE, duplicate=False, unauthorised=0, hash_ok=False,
                dependency_failure=False, cycle_failure=False, candidates=0, pack_chars=None,
                recording_sha256=""))
            continue
        broker = Broker(store=store, evidence_log=_NullEvidenceLog(),
                        judgment_source=source, session_ledger=ledger)
        result = broker.prepare_turn(
            case.request, profile,
            {"session_id": case.session_id, "turn_id": case.turn_id})
        decision = result.decision
        if decision is None:  # pragma: no cover — prepare_turn always records one
            raise EvaluationError(f"{case.case_sha256}: prepare_turn recorded no decision")
        measurements.append(_measure(entry, decision, recordings))

    corpus_sha256 = _corpus_digest(measurements)
    threshold = _summarize([m for m in measurements if m.split != HELD_OUT_SPLIT])
    held_out = _summarize([m for m in measurements if m.split == HELD_OUT_SPLIT])
    status = profile_status(corpus, labels, profile, minimum=minimum)
    # The Stage 5 minimum guards the data thresholds are tuned on, not just the total: a corpus
    # with one threshold-set case and the rest held out is thin for tuning however many it has
    # (ADR-0020: a profile too thin stays in Shadow Mode rather than gating on thin data).
    below_minimum = status.below_minimum or threshold.cases < minimum
    attribution_counts = {name: 0 for name in ATTRIBUTIONS}
    for measurement in measurements:
        attribution_counts[measurement.attribution] += 1
    stage_counts = {name: 0 for name in STAGE_NAMES}
    for measurement in measurements:
        stage_counts[_ATTRIBUTION_STAGE[measurement.attribution]] += 1

    return EvaluationReport(
        profile=profile,
        minimum=minimum,
        below_minimum=below_minimum,
        stage="shadow" if below_minimum else status.stage,
        corpus_sha256=corpus_sha256,
        reviewed_cases=len(reviewed),
        threshold=threshold,
        held_out=held_out,
        attribution=attribution_counts,
        attribution_by_stage=stage_counts,
        problems=tuple(problems),
    )


def _measure(entry: ReviewedCase, decision: RouteDecision,
             recordings: RecordingStore) -> _Measurement:
    """Turn one replayed Route Decision into a Measurement against its hand-reviewed truth."""
    case = entry.case
    closure_versions = {value.id: value.version for value in case.authorised_closure}
    selected = decision.grants[0].id if decision.grants else None
    failed = decision.outcome is TurnOutcome.FAILURE
    judgment_primary = decision.judgment.primary if decision.judgment is not None else None
    closure_kind = closure_failure_kind(decision.reasons) if failed else None
    recording = recordings.get(case.profile, case.case_sha256)
    return _Measurement(
        reviewed=entry,
        selected=selected,
        outcome=decision.outcome,
        attribution=attribute(
            failed=failed, ground_truth=entry.primary, selected=selected,
            candidates=[candidate.id for candidate in decision.candidates],
            judgment_primary=judgment_primary),
        duplicate="duplicate_suppressed" in decision.reasons,
        unauthorised=sum(1 for grant in decision.grants if grant.id not in closure_versions),
        # The grant must match the canonical hash the corpus reviewed the Skill at; a mismatch
        # is Store drift since extraction, the signal an offline run can carry for the gate's
        # broker/native hash-agreement item (the live farm comparison lives at the seam).
        hash_ok=all(grant.version == closure_versions.get(grant.id)
                    for grant in decision.grants),
        dependency_failure=closure_kind == "dependency",
        cycle_failure=closure_kind == "cycle",
        candidates=len(decision.candidates),
        pack_chars=decision.pack_chars,
        recording_sha256=_claim_digest(recording.claim) if recording is not None else "",
    )

def _summarize(measurements: Sequence[_Measurement]) -> SplitMetrics:
    """Aggregate one split's measurements into counts and rates."""
    metrics = SplitMetrics(
        cases=len(measurements),
        candidate_total=sum(m.candidates for m in measurements),
        duplicate_suppressions=sum(1 for m in measurements if m.duplicate),
        closure_failures=sum(1 for m in measurements if m.dependency_failure or m.cycle_failure),
        dependency_failures=sum(1 for m in measurements if m.dependency_failure),
        cycle_failures=sum(1 for m in measurements if m.cycle_failure),
        grants=sum(1 for m in measurements if m.selected is not None),
        pack_deliveries=sum(1 for m in measurements if m.pack_chars is not None),
        pack_chars_total=sum(m.pack_chars for m in measurements if m.pack_chars is not None),
        unauthorised_grants=sum(m.unauthorised for m in measurements),
    )
    counts = {
        "granted": sum(1 for m in measurements if m.outcome is TurnOutcome.GRANTED),
        "no_skill": sum(1 for m in measurements if m.outcome is TurnOutcome.NO_SKILL),
        "failures": sum(1 for m in measurements if m.outcome is TurnOutcome.FAILURE),
        "hash_agreements": sum(1 for m in measurements if m.selected is not None and m.hash_ok),
        "hash_disagreements": sum(1 for m in measurements
                                  if m.selected is not None and not m.hash_ok),
    }
    tp = fp = fn = tn = 0
    for measurement in measurements:
        if measurement.failed:
            continue  # a failure is counted separately, never as a correct silence
        truth = measurement.reviewed.primary
        if truth is None:
            if measurement.selected is None:
                tn += 1
            else:
                fp += 1
        elif measurement.selected == truth:
            tp += 1
        else:
            fn += 1
            if measurement.selected is not None:
                fp += 1
    return replace(metrics, true_positive=tp, false_positive=fp, false_negative=fn,
                   true_negative=tn, **counts)


def derive_thresholds(report: EvaluationReport) -> SoftThresholds:
    """Pre-register the Soft Thresholds from the threshold-set baseline only (AC3, AC4).

    The held-out split is never read: the floors come from the data the gate may tune on, so the
    gate is later reported on data it was not tuned on.
    """
    metrics = report.threshold
    return SoftThresholds(
        precision=metrics.precision,
        recall=metrics.recall,
        correct_no_skill_rate=metrics.correct_no_skill_rate,
        duplicate_injection_rate=metrics.duplicate_injection_rate,
        average_candidates=metrics.average_candidates,
        average_pack_chars=metrics.average_pack_chars,
    )


class ThresholdRegistry:
    """The committed pre-registration of one profile's Soft Thresholds (AC4).

    A registration binds the thresholds to the corpus digest and the measured baseline they were
    derived from, so the goalposts cannot move silently. Re-registration is refused for the same
    corpus, and for a different corpus it requires an explicitly requested re-derivation against
    a strictly larger reviewed set (ADR-0019).
    """

    def __init__(self, root: Path | str = DEFAULT_THRESHOLDS_ROOT) -> None:
        self.root = Path(root).expanduser()

    def path(self, profile: str) -> Path:
        return self.root / f"{profile}.json"

    def get(self, profile: str) -> dict | None:
        path = self.path(profile)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def register(self, report: EvaluationReport, *, registered_at: str | None = None,
                 allow_re_derivation: bool = False) -> dict:
        """Write the pre-registration, or refuse to move an already-registered threshold."""
        if report.below_minimum:
            raise EvaluationError(
                f"{report.profile} has {report.reviewed_cases} reviewed case(s), below the "
                f"Stage 5 minimum of {report.minimum}; it stays in Shadow Mode rather than "
                f"gating on thin data")
        existing = self.get(report.profile)
        if existing is not None:
            if existing.get("corpus_sha256") == report.corpus_sha256:
                raise EvaluationError(
                    f"{report.profile} is already registered for this corpus")
            if not allow_re_derivation:
                raise EvaluationError(
                    "re-registering needs --re-derive against a larger corpus")
            if report.reviewed_cases <= int(existing.get("reviewed_cases", 0)):
                raise EvaluationError(
                    "re-derivation must be against a strictly larger reviewed set")
        record = {
            "threshold_version": THRESHOLD_VERSION,
            "profile": report.profile,
            "registered_at": registered_at or _now(),
            "corpus_sha256": report.corpus_sha256,
            "reviewed_cases": report.reviewed_cases,
            "minimum": report.minimum,
            "stage": report.stage,
            "thresholds": derive_thresholds(report).to_record(),
            "baseline": {
                "threshold": report.threshold.to_record(),
                "held_out": report.held_out.to_record(),
            },
            "held_out_not_used_for_tuning": True,
        }
        write_canonical(self.path(report.profile), record)
        return record


def _corpus_digest(measurements: Sequence[_Measurement]) -> str:
    """A deterministic digest of the reviewed corpus, labels and Recordings evaluated.

    Binds the pre-registration to the exact data it was derived from without carrying any request
    text: each Case's content hash, its split, its hand-reviewed outcome, and its Recording claim.
    """
    material = "\n".join(
        "\x00".join((m.reviewed.case.case_sha256, m.split, m.reviewed.outcome,
                      m.recording_sha256))
        for m in sorted(measurements, key=lambda m: m.reviewed.case.case_sha256)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _claim_digest(claim: Mapping) -> str:
    blob = json.dumps(dict(claim), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "ATTRIBUTIONS",
    "CORRECT",
    "CORRECT_NO_SKILL",
    "DEFAULT_THRESHOLDS_ROOT",
    "EvaluationError",
    "EvaluationReport",
    "FALSE_INTERVENTION",
    "FAILURE",
    "GRANT_MISS",
    "JUDGMENT_MISS",
    "RETRIEVAL_MISS",
    "STAGE_NAMES",
    "SoftThresholds",
    "SplitMetrics",
    "THRESHOLD_VERSION",
    "ThresholdRegistry",
    "attribute",
    "derive_thresholds",
    "evaluate_profile",
    "soft_threshold_regression",
]
