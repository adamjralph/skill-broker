"""Measured expansion: one reviewed batch at a time (ADR-0019, ADR-0022, spec B13, ticket #58).

The bounded pilot ends Shadow Mode for one profile and one batch. Expansion is the **routine**
that grows that exposure without re-opening the gate: each batch is enabled on fresh replayed or
shadow evidence, with its own review, and the previous batch's behaviour is preserved rather than
replaced. This module encodes the routine so the operator drives it instead of re-deriving it by
hand, and so the same checks that guard the first batch guard every later one.

The procedure, in order:

1. **Fail closed.** A profile with an open Incident — one no later review closed — cannot
   expand. Neither can a profile that is failed closed or whose expansion is blocked by a
   Soft-Threshold regression.
2. **A fresh review.** The approval must be recorded after the previous batch's review and must
   bind a Report digest the profile has not already reviewed, so the same evidence cannot admit
   two batches.
3. **A superset.** The new batch must cover every Skill the currently enabled batch covers, so
   enabling it cannot withdraw a Skill the first batch was already delivering.
4. **Thresholds held or re-derived.** The pre-registered Soft Thresholds are carried forward
   unchanged unless the caller asks for a re-derivation, and a re-derivation is only accepted
   against a strictly larger reviewed corpus (delegated to the committed
   :class:`~broker.evaluation.ThresholdRegistry`, which records the corpus digest and baseline).
5. **Rollback rehearsed.** The currently enabled batch is disabled, the profile is observed
   foundation-only, and the batch is restored, all before the new batch is enabled.
6. **Enabled, then checked.** The new batch is enabled and the post-batch window is checked for
   a foundation-resolution regression and broker/native hash disagreement; a breach fails the
   profile closed and is recorded.

Every step is appended — metadata and hashes only, no request text and no Skill body (ADR-0015)
— so an expansion is as replayable as a turn.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .evaluation import ThresholdRegistry
from .evidence import append_jsonl
from .gate import (
    APPROVE,
    GateError,
    GateReview,
    HardGate,
    InjectionBatch,
    InjectionGate,
    check_hard_gates,
)
from .report import ReportError, ShadowReport, apply_review
from .types import RouteDecision

EXPANSION_VERSION = 1


class ExpansionError(RuntimeError):
    """An expansion the procedure forbids: an open Incident, stale evidence, a narrower batch."""


@dataclass(frozen=True)
class ThresholdDecision:
    """Whether expansion carried the Soft Thresholds forward or re-derived them (AC3)."""

    unchanged: bool
    registration: dict
    previous_corpus_sha256: str | None = None
    previous_reviewed_cases: int | None = None

    def to_record(self) -> dict:
        return {
            "unchanged": self.unchanged,
            "registration": self.registration,
            "previous_corpus_sha256": self.previous_corpus_sha256,
            "previous_reviewed_cases": self.previous_reviewed_cases,
        }


@dataclass(frozen=True)
class RollbackRehearsal:
    """The proof that the current exposure can be withdrawn and restored before expanding (AC4)."""

    profile: str
    previous_batch: dict | None
    foundation_only: bool
    restored: bool

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "previous_batch": self.previous_batch,
            "foundation_only": self.foundation_only,
            "restored": self.restored,
        }


@dataclass(frozen=True)
class PostBatchReport:
    """The foundation-resolution and hash checks over the window after the batch (AC5)."""

    profile: str
    batch: str
    turns_checked: int
    foundation_regressions: int
    hash_agreements: int
    hash_disagreements: int
    gates_breached: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return (self.turns_checked > 0 and self.foundation_regressions == 0
                and self.hash_disagreements == 0 and not self.gates_breached)

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "batch": self.batch,
            "turns_checked": self.turns_checked,
            "foundation_regressions": self.foundation_regressions,
            "hash_agreements": self.hash_agreements,
            "hash_disagreements": self.hash_disagreements,
            "gates_breached": list(self.gates_breached),
            "ok": self.ok,
        }


@dataclass(frozen=True)
class ExpansionStep:
    """One recorded expansion: the batch, its review, the rehearsal and the post-batch checks."""

    profile: str
    batch: dict
    previous_batch: dict | None
    review: dict
    thresholds: dict
    rollback: RollbackRehearsal
    post: PostBatchReport
    recorded_at: str
    version: int = EXPANSION_VERSION

    def to_record(self) -> dict:
        return {
            "expansion_version": self.version,
            "profile": self.profile,
            "batch": self.batch,
            "previous_batch": self.previous_batch,
            "review": self.review,
            "thresholds": self.thresholds,
            "rollback": self.rollback.to_record(),
            "post": self.post.to_record(),
            "recorded_at": self.recorded_at,
        }


def assert_expandable(gate: InjectionGate, profile: str) -> None:
    """Fail closed unless the profile may take another batch (AC6).

    An open Incident is the hard stop: expansion is not the recovery path for a breach. Recovery
    is a review recorded after the breach; only once no Incident is open does growth resume.
    """
    open_incidents = gate.open_incidents(profile)
    if open_incidents:
        gates = sorted({gate_name for incident in open_incidents
                        for gate_name in incident.gates})
        raise ExpansionError(
            f"{profile} has {len(open_incidents)} open Incident(s) ({', '.join(gates)}); "
            "expansion fails closed until a later review closes them")
    status = gate.status(profile)
    if status["failed_closed"]:
        raise ExpansionError(f"{profile} is failed closed; expansion fails closed")
    if status["expansion_blocked"]:
        raise ExpansionError(
            f"{profile} expansion is blocked by a Soft-Threshold regression: "
            + "; ".join(status["expansion_reasons"]))
    if status["enabled_batch"] is None:
        raise ExpansionError(f"{profile} has no enabled batch to expand from")


def assert_fresh_review(gate: InjectionGate, profile: str, review: GateReview) -> None:
    """A batch needs evidence the profile has not already reviewed (AC1).

    The review must bind a non-empty Report digest the profile has not seen before, and must be
    recorded after the previous batch's review. A review that reuses the previous digest re-admits
    the same window, which is exactly the bespoke, unreviewed growth ADR-0019 forbids.
    """
    if not review.report_sha256:
        raise ExpansionError("expansion needs a review bound to a Shadow Report digest")
    last = gate.last_review(profile)
    if review.report_sha256 == str(last.get("report_sha256", "")):
        raise ExpansionError(
            "expansion needs fresh evidence: the review binds the same Report digest")
    previous_at = str(last.get("reviewed_at", ""))
    if review.reviewed_at and previous_at and review.reviewed_at <= previous_at:
        raise ExpansionError(
            f"expansion needs a review recorded after {previous_at}; "
            f"this one is {review.reviewed_at}")


def assert_superset(gate: InjectionGate, profile: str, batch: InjectionBatch) -> None:
    """The new batch must preserve the first batch's exposure (AC2).

    Every Skill the running batch covers must still be covered, so enabling the new batch cannot
    withdraw guidance the first batch was already delivering. Growth is additive by construction.
    An empty running batch means "the whole Brokered Allowlist" and cannot be checked additively,
    so it is refused rather than guessed at.
    """
    previous = gate.enabled_batch(profile)
    if previous is None:
        raise ExpansionError(f"{profile} has no enabled batch to expand from")
    if not previous.skills:
        raise ExpansionError(
            f"the running batch {previous.name!r} names no Skills (it covers the whole "
            "Brokered Allowlist); expansion needs an explicit Skill set to grow additively")
    missing = sorted(set(previous.skills) - set(batch.skills))
    if missing:
        raise ExpansionError(
            f"the new batch drops {missing} from the running batch {previous.name!r}; "
            "expansion is additive")


def rehearse_rollback(gate: InjectionGate, profile: str) -> RollbackRehearsal:
    """Disable the running batch, observe foundation-only, restore it — before expanding (AC4).

    This is the same discipline as the Cutover rehearsal: the rollback path is exercised while
    the good state is still known, so a later rollback is a revert rather than a rebuild. A
    profile with nothing enabled has no exposure to restore and is refused, not silently skipped.
    """
    previous = gate.enabled_batch(profile)
    if previous is None:
        raise ExpansionError(f"{profile} has no enabled batch to rehearse rollback")
    gate.disable(profile, reason="expansion rollback rehearsal")
    foundation_only = not gate.injecting(profile)
    _re_enable(gate, profile, previous)
    restored = gate.injecting(profile) and gate.enabled_batch(profile) == previous
    return RollbackRehearsal(profile=profile, previous_batch=previous.to_record(),
                             foundation_only=foundation_only, restored=restored)


def _re_enable(gate: InjectionGate, profile: str, batch: InjectionBatch) -> None:
    """Restore a running batch after a rehearsal or an empty-window rollback.

    The stored review may name a different batch (a later REJECT, or a differently-named batch),
    and ``enable`` validates the batch name, so the restoration review is rebuilt to name the
    batch actually being restored while keeping the recorded reviewer, digest and timestamp.
    """
    stored = gate.last_review(profile)
    gate.enable(batch, GateReview(
        profile=profile,
        batch=batch.name,
        reviewer=str(stored.get("reviewer") or "expansion rollback"),
        outcome=APPROVE,
        reviewed_at=str(stored.get("reviewed_at") or ""),
        report_sha256=str(stored.get("report_sha256") or ""),
    ))


def verify_thresholds(registry: ThresholdRegistry | None, profile: str, *,
                      report=None, allow_re_derivation: bool = False,
                      registered_at: str | None = None) -> ThresholdDecision:
    """Carry the pre-registered thresholds forward, or accept a larger-corpus re-derivation (AC3).

    With no ``report`` the thresholds are unchanged and the existing registration is returned.
    With a ``report`` the caller must explicitly request a re-derivation; the committed registry
    refuses the same corpus and any reviewed set that is not strictly larger, then records the
    new corpus digest and measured baseline alongside the thresholds.
    """
    if registry is None:
        raise ExpansionError("thresholds cannot be checked without a ThresholdRegistry")
    existing = registry.get(profile)
    if existing is None:
        raise ExpansionError(f"{profile} has no pre-registered Soft Thresholds (ADR-0019)")
    if report is None:
        return ThresholdDecision(unchanged=True, registration=existing)
    if not allow_re_derivation:
        raise ExpansionError(
            "a re-derivation must be explicit; thresholds otherwise stay unchanged (ADR-0019)")
    updated = registry.register(report, registered_at=registered_at, allow_re_derivation=True)
    return ThresholdDecision(
        unchanged=False,
        registration=updated,
        previous_corpus_sha256=existing.get("corpus_sha256"),
        previous_reviewed_cases=existing.get("reviewed_cases"),
    )


def check_after_batch(*, profile: str, batch: str, decisions: Sequence[RouteDecision],
                      foundation_ids: Sequence[str] = ()) -> PostBatchReport:
    """The foundation-resolution regression and hash-agreement checks after the batch (AC5).

    The same data-independent :func:`~broker.gate.check_hard_gates` the broker runs per turn is
    read over the post-batch window, so an expansion is held to the identical standard as a turn.
    """
    regressions = 0
    agreements = 0
    disagreements = 0
    breached: set[str] = set()
    for decision in decisions:
        breaches = check_hard_gates(decision, foundation_ids=foundation_ids)
        breached.update(breaches)
        if HardGate.FOUNDATION_REGRESSION.value in breaches:
            regressions += 1
        closure = {entry.id: entry.version for entry in decision.authorised_closure}
        for grant in decision.grants:
            if grant.id not in closure:
                continue  # an unauthorised Grant is the unauthorised_grant gate, not a hash
            if grant.version == closure[grant.id]:
                agreements += 1
            else:
                disagreements += 1
    return PostBatchReport(
        profile=profile,
        batch=batch,
        turns_checked=len(decisions),
        foundation_regressions=regressions,
        hash_agreements=agreements,
        hash_disagreements=disagreements,
        gates_breached=tuple(sorted(breached)),
    )


def expand(*, gate: InjectionGate, report: ShadowReport, reviewer: str, post_probe:
           Callable[[], Sequence[RouteDecision]], profile: str | None = None,
           outcome: str = APPROVE, reviewed_at: str = "", batch: InjectionBatch | None = None,
           foundation_ids: Sequence[str] = (), registry: ThresholdRegistry | None = None,
           evaluation_report=None, allow_re_derivation: bool = False,
           ledger_path: Path | str | None = None,
           clock: Callable[[], str] | None = None) -> ExpansionStep:
    """Run the whole expansion procedure and record it (AC1-AC6).

    The reviewed :class:`~broker.report.ShadowReport` is the input, and its review is recorded by
    :func:`~broker.report.apply_review` — the same guards that enable the pilot batch (thresholds
    pre-registered, every checked Hard Gate passed, no quality floor regressed) — so the procedure
    cannot enable a batch the Shadow Report itself would refuse. The pre-flight checks run before
    anything is touched; the rollback rehearsal runs while the current exposure is still known;
    only then is the new batch enabled. ``post_probe`` is called immediately after enabling and
    must return the post-batch window's Route Decisions. A breach fails the profile closed, so an
    expansion that goes wrong reverts rather than persists.
    """
    profile = profile or report.profile
    batch = batch or report.batch
    review = GateReview(profile=profile, batch=batch.name, reviewer=reviewer, outcome=outcome,
                        reviewed_at=reviewed_at, report_sha256=report.report_sha256)
    assert_expandable(gate, profile)
    assert_fresh_review(gate, profile, review)
    assert_superset(gate, profile, batch)
    previous = gate.enabled_batch(profile)
    rehearsal = rehearse_rollback(gate, profile)
    if not rehearsal.restored:
        # The safe state is whatever the rehearsal left: never enable on an unrestorable batch.
        raise ExpansionError("the rollback rehearsal did not restore the previous exposure")
    thresholds = verify_thresholds(registry, profile, report=evaluation_report,
                                   allow_re_derivation=allow_re_derivation)
    try:
        reviewed = apply_review(report, outcome=outcome, reviewer=reviewer, gate=gate,
                                reviewed_at=reviewed_at, batch=batch)
    except (ReportError, GateError) as exc:
        raise ExpansionError(str(exc)) from exc
    # The durable review the gate stored (its clock may have stamped an empty ``reviewed_at``).
    recorded_review = gate.last_review(profile) or reviewed.review or {}
    decisions = tuple(post_probe())
    if not decisions:
        # Nothing was checked, so nothing may be reported as passing: withdraw the new batch and
        # restore the previous exposure rather than claim a clean window.
        gate.disable(profile, reason="empty post-batch window")
        if previous is not None:
            _re_enable(gate, profile, previous)
        raise ExpansionError("the post-batch window has no turns to check")
    post = check_after_batch(profile=profile, batch=batch.name, decisions=decisions,
                             foundation_ids=foundation_ids)
    step = ExpansionStep(
        profile=profile,
        batch=batch.to_record(),
        previous_batch=previous.to_record() if previous is not None else None,
        review=dict(recorded_review),
        thresholds=thresholds.to_record(),
        rollback=rehearsal,
        post=post,
        recorded_at=(clock or _now)(),
    )
    if ledger_path is not None:
        append_jsonl(ledger_path, step.to_record())
    if not post.ok:
        gate.fail_closed(profile, post.gates_breached
                         or (HardGate.FOUNDATION_REGRESSION.value,),
                         reasons=tuple(f"post_batch:{gate_name}"
                                       for gate_name in post.gates_breached)
                         or ("post_batch_failed",))
    return step


def read_expansions(path: Path | str) -> list[dict]:
    """The recorded expansion steps, oldest first; a malformed line is skipped."""
    file = Path(path)
    if not file.exists():
        return []
    steps: list[dict] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            steps.append(json.loads(line))
        except ValueError:
            continue
    return steps


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "EXPANSION_VERSION",
    "ExpansionError",
    "ExpansionStep",
    "PostBatchReport",
    "RollbackRehearsal",
    "ThresholdDecision",
    "assert_expandable",
    "assert_fresh_review",
    "assert_superset",
    "check_after_batch",
    "expand",
    "read_expansions",
    "rehearse_rollback",
    "verify_thresholds",
]
