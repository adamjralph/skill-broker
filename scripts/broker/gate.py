"""The injection gate: data-independent Hard Gates, fail-closed, and the per-batch switch
(ADR-0019, spec B13, ticket #52).

The gate that ends Shadow Mode is a **procedure**, not a number. A small set of Hard Gates never
moves with data and is checked on every turn: an unauthorised Grant, a foundation-resolution
regression, broker/native content-hash disagreement, a Judgment validation failure, an incomplete
Dependency Closure, and any mutation of the system prompt or tool schema. A breach fails the
profile **closed** — the running injection is turned off, an Incident is recorded naming the gate,
and the profile is re-enabled only after a review recorded *after* the breach.

Injection is off by default. Turning it on is a per-batch operation: it names the profile and the
batch, and it takes a recorded review (the reviewed Shadow Report of ticket #56). A Soft-Threshold
regression is a different failure mode: it **blocks expansion** and leaves any running injection in
place, because a precision regression is a reason not to grow, not a correctness failure.

The gate holds no request text and no Skill body: an Incident carries gate names, reason markers,
correlation ids and hashes (ADR-0015). Everything here is observable through :meth:`status`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

from .evidence import append_jsonl
from .types import RouteDecision

STATE_VERSION = 1

#: The recorded review outcome that may enable a batch.
APPROVE = "approve"
APPROVE_NARROWER = "approve_narrower"
REJECT = "reject"
REVIEW_OUTCOMES = (APPROVE, APPROVE_NARROWER, REJECT)

#: Reason markers that mean a Dependency Closure did not resolve completely (B7).
_CLOSURE_MARKERS = ("unknown dependency", "dependency not authorised", "unknown ID in closure")

#: The Soft Thresholds that are floors: a later run below one is a regression (ADR-0019).
THRESHOLD_FLOORS = ("precision", "recall", "correct_no_skill_rate")


def soft_threshold_regression(baseline: _ThresholdMetric,
                              current: _ThresholdMetric) -> tuple[str, ...]:
    """Movement below a pre-registered quality floor, and nothing else (ADR-0019, AC5).

    ``baseline`` is a :class:`~broker.evaluation.SoftThresholds` and ``current`` a
    :class:`~broker.evaluation.SplitMetrics`; both are read by attribute, so the gate owns the
    enforcement without importing the evaluation module (which imports the broker in turn).

    Only the quality rates are floors: the cost rates are the measured reference a later run is
    compared against, not conditions. A regression blocks expansion and never disables running
    injection, because it is a reason not to grow rather than a correctness failure. A metric
    that is undefined on either side (no cases of that kind) cannot be compared and is skipped.
    """
    reasons: list[str] = []
    for name in THRESHOLD_FLOORS:
        floor = getattr(baseline, name)
        measured = getattr(current, name)
        if floor is None or measured is None:
            continue
        if measured < floor:
            reasons.append(f"{name} regressed: {measured} < {floor}")
    return tuple(reasons)


class GateError(RuntimeError):
    """An action the gate's current state forbids (e.g. expanding while failed closed or blocked)."""


class _ThresholdMetric(Protocol):
    """The quality rates a Soft-Threshold comparison reads, structurally (ADR-0019, AC5).

    Both :class:`~broker.evaluation.SoftThresholds` and :class:`~broker.evaluation.SplitMetrics`
    satisfy it, so the comparison is typed without the gate importing the evaluation module.
    """

    precision: float | None
    recall: float | None
    correct_no_skill_rate: float | None


class HardGate(str, Enum):
    """The data-independent conditions checked on every turn (ADR-0019)."""

    UNAUTHORISED_GRANT = "unauthorised_grant"
    FOUNDATION_REGRESSION = "foundation_regression"
    HASH_DISAGREEMENT = "hash_disagreement"
    JUDGMENT_INVALID = "judgment_invalid"
    INCOMPLETE_CLOSURE = "incomplete_closure"
    PROMPT_SCHEMA_MUTATION = "prompt_schema_mutation"


#: Canonical order, so a breach record is deterministic regardless of check order.
HARD_GATES = tuple(gate.value for gate in HardGate)

#: The gates the Broker can check on its own Route Decision. The prompt/tool-schema gate is
#: observed by the Adapter at the provider request, where the prompt and tools are assembled.
BROKER_GATES = tuple(gate.value for gate in HardGate
                     if gate is not HardGate.PROMPT_SCHEMA_MUTATION)


@dataclass(frozen=True)
class InjectionBatch:
    """A named group of Skills whose injection one review authorises for one profile.

    The batch is what an enablement names (AC4). ``skills`` lists the store IDs the batch covers;
    an empty list means the whole Brokered Allowlist, which is how a first pilot batch is drawn
    before a narrower approval prunes it.
    """

    name: str
    profile: str
    skills: tuple[str, ...] = ()

    def to_record(self) -> dict:
        return {"name": self.name, "profile": self.profile, "skills": list(self.skills)}

    @classmethod
    def from_record(cls, record: Mapping) -> "InjectionBatch":
        return cls(name=str(record["name"]), profile=str(record["profile"]),
                   skills=tuple(str(skill) for skill in record.get("skills", ())))


@dataclass(frozen=True)
class GateReview:
    """A recorded review that authorises enabling one batch (AC3, AC4; spec B13, ticket #56).

    The review is the *only* thing that flips the switch, and after a breach it must be recorded
    after the breach: ``reviewed_at`` is compared with the Incident's timestamp, so the turn that
    breached the profile cannot re-enable it. ``report_sha256`` binds the review to the Shadow
    Report it reviewed, when there is one.
    """

    profile: str
    batch: str
    reviewer: str
    outcome: str
    reviewed_at: str
    report_sha256: str = ""

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "batch": self.batch,
            "reviewer": self.reviewer,
            "outcome": self.outcome,
            "reviewed_at": self.reviewed_at,
            "report_sha256": self.report_sha256,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "GateReview":
        return cls(
            profile=str(record.get("profile", "")),
            batch=str(record.get("batch", "")),
            reviewer=str(record.get("reviewer", "")),
            outcome=str(record.get("outcome", "")),
            reviewed_at=str(record.get("reviewed_at", "")),
            report_sha256=str(record.get("report_sha256", "")),
        )


@dataclass(frozen=True)
class Incident:
    """One Hard-Gate breach, recorded durably (ADR-0019). Metadata and hashes only (ADR-0015)."""

    profile: str
    gates: tuple[str, ...]
    reasons: tuple[str, ...]
    recorded_at: str
    turn_id: str | None = None
    session_id: str | None = None
    route_decision_id: str | None = None

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "gates": list(self.gates),
            "reasons": list(self.reasons),
            "recorded_at": self.recorded_at,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "route_decision_id": self.route_decision_id,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "Incident":
        return cls(
            profile=str(record["profile"]),
            gates=tuple(str(gate) for gate in record.get("gates", ())),
            reasons=tuple(str(reason) for reason in record.get("reasons", ())),
            recorded_at=str(record.get("recorded_at", "")),
            turn_id=record.get("turn_id"),
            session_id=record.get("session_id"),
            route_decision_id=record.get("route_decision_id"),
        )


def closure_failure_kind(reasons: Sequence[str]) -> str | None:
    """Classify closure-failure reasons as a missing/denied Dependency or a cycle (B7).

    One home for the reason vocabulary both the gate and the offline evaluation read, so a
    reason reworded in one place cannot silently stop matching in another.
    """
    if any("dependency cycle" in reason for reason in reasons):
        return "cycle"
    if any(marker in reason for reason in reasons for marker in _CLOSURE_MARKERS):
        return "dependency"
    return None


def closure_versions(decision: RouteDecision) -> dict[str, str]:
    """The Decision's Authorised Closure keyed by store ID (spec B6).

    The one map a Grant is checked against, so every Hard-Gate check, the Shadow Report's
    metrics and the post-batch window read the same view of the closure.
    """
    return {entry.id: entry.version for entry in decision.authorised_closure}


def review_closes_incident(incident_at: str, reviewed_at: str) -> bool:
    """Whether a review recorded at ``reviewed_at`` closes an Incident at ``incident_at``.

    A review closes an Incident only when recorded **strictly after** it, so an Incident and the
    review at the same instant leave it open and the turn that breached can never flip the switch
    (ADR-0019). One home for the rule :meth:`InjectionGate.open_incidents` and
    :meth:`InjectionGate.enable` both enforce, so the two can never drift.
    """
    return bool(reviewed_at) and (not incident_at or reviewed_at > incident_at)


def check_hard_gates(decision: RouteDecision,
                     *, foundation_ids: Sequence[str] = ()) -> tuple[str, ...]:
    """The Hard Gates breached by one Route Decision, in canonical order (AC1).

    ``foundation_ids`` are the store-backed Foundation entries of the profile's policy. The
    remaining gates read the Decision itself, so a breach is never data-dependent: the same
    Decision always yields the same breaches, and an empty tuple means every check passed.

    The foundation check catches a Foundation entry the closure no longer resolves (denied or
    unknown), which is the data-independent signal available at this seam. Whether the native
    Hermes index still exposes the Foundation Set is the withholding gate placed by ticket #53.
    """
    closure = closure_versions(decision)
    breaches: list[str] = []

    if any(grant.id not in closure for grant in decision.grants):
        breaches.append(HardGate.UNAUTHORISED_GRANT.value)
    if any(ident not in closure for ident in foundation_ids):
        breaches.append(HardGate.FOUNDATION_REGRESSION.value)
    if (any(grant.id in closure and grant.version != closure[grant.id]
            for grant in decision.grants)
            or any("content hash mismatch" in reason for reason in decision.reasons)):
        breaches.append(HardGate.HASH_DISAGREEMENT.value)
    if "judgment_invalid" in decision.reasons:
        breaches.append(HardGate.JUDGMENT_INVALID.value)
    if any("dependency cycle" in reason for reason in decision.reasons
           ) or any(marker in reason for reason in decision.reasons
                    for marker in _CLOSURE_MARKERS):
        breaches.append(HardGate.INCOMPLETE_CLOSURE.value)
    return tuple(gate for gate in HARD_GATES if gate in breaches)


class InjectionGate:
    """The durable per-batch injection switch, fail-closed state and Incident log (AC2-AC6).

    One instance is shared by the Broker (which checks the Hard Gates and fails the profile
    closed) and the Adapter (which consults :meth:`injecting` before delivering a Pack). State is
    a small JSON document; Incidents are an append-only JSONL log, so a breach is evidence rather
    than a mutable flag. A ``clock`` is injectable so a review's ordering against a breach is
    deterministic in tests.
    """

    def __init__(self, *, state_path: Path | str, incidents_path: Path | str | None = None,
                 clock: Callable[[], str] | None = None) -> None:
        self.state_path = Path(state_path)
        self.incidents_path = (Path(incidents_path) if incidents_path is not None
                               else self.state_path.with_name("gate_incidents.jsonl"))
        self._clock = clock or _now

    @classmethod
    def in_evidence_dir(cls, directory: Path | str,
                        *, clock: Callable[[], str] | None = None) -> "InjectionGate":
        """The gate whose state and Incident log live under one evidence directory."""
        directory = Path(directory)
        return cls(state_path=directory / "gate.json",
                   incidents_path=directory / "gate_incidents.jsonl", clock=clock)

    # -- observability -------------------------------------------------------------------

    def status(self, profile: str) -> dict:
        """What is enabled, for which batch, and whether the profile is failed closed (AC6)."""
        record = self._profile_record(self._read(), profile)
        return {
            "profile": profile,
            "injecting": self.injecting(profile),
            "enabled_batch": record.get("enabled_batch"),
            "failed_closed": bool(record.get("failed_closed")),
            "failed_closed_gates": list(record.get("failed_closed_gates", ())),
            "failed_closed_at": record.get("failed_closed_at"),
            "review_required": bool(record.get("review_required")),
            "expansion_blocked": bool(record.get("expansion_blocked")),
            "expansion_reasons": list(record.get("expansion_reasons", ())),
            "disabled_reason": record.get("disabled_reason"),
            "previous_batch": record.get("previous_batch"),
            "last_review": record.get("last_review"),
            "incident_count": len(self.incidents(profile)),
        }

    def enabled_batch(self, profile: str) -> InjectionBatch | None:
        record = self._profile_record(self._read(), profile)
        batch = record.get("enabled_batch")
        return InjectionBatch.from_record(batch) if batch else None

    def injecting(self, profile: str) -> bool:
        """Injection is on only when a batch is enabled and the profile is not failed closed."""
        record = self._profile_record(self._read(), profile)
        return bool(record.get("enabled_batch")) and not bool(record.get("failed_closed"))

    def failed_closed(self, profile: str) -> bool:
        return bool(self._profile_record(self._read(), profile).get("failed_closed"))

    def expansion_blocked(self, profile: str) -> bool:
        return bool(self._profile_record(self._read(), profile).get("expansion_blocked"))

    def incidents(self, profile: str | None = None) -> list[Incident]:
        if not self.incidents_path.exists():
            return []
        incidents = []
        for line in self.incidents_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            incident = Incident.from_record(json.loads(line))
            if profile is None or incident.profile == profile:
                incidents.append(incident)
        return incidents

    def last_review(self, profile: str) -> dict:
        """The most recent recorded review for a profile, or an empty mapping (ticket #58)."""
        review = self._profile_record(self._read(), profile).get("last_review")
        return dict(review) if review else {}

    def open_incidents(self, profile: str) -> list[Incident]:
        """The profile's Incidents a later review has not closed (ticket #58).

        A review recorded *after* an Incident closes it: re-enabling a failed-closed profile is
        deliberate and reviewed (AC3, ADR-0019). The comparison is against the profile's last
        recorded review, so an Incident with no review after it (or one at the same instant) stays
        open and holds expansion.
        """
        reviewed_at = str(self.last_review(profile).get("reviewed_at", ""))
        return [incident for incident in self.incidents(profile)
                if not review_closes_incident(incident.recorded_at, reviewed_at)]

    # -- the switch ----------------------------------------------------------------------

    def enable(self, batch: InjectionBatch, review: GateReview) -> dict:
        """Enable one batch on a recorded review, naming the profile and the batch (AC3, AC4).

        Refused when the review does not match the batch or approves nothing, when a Soft
        Threshold regression is on record (expansion is blocked, AC5), and when the profile is
        failed closed by a breach the review does not post-date (the turn that breached cannot
        flip the switch, AC3).
        """
        self._validate(batch, review)
        if not review.reviewed_at:
            review = replace(review, reviewed_at=self._clock())
        state = self._read()
        record = self._profile_record(state, batch.profile)
        if record.get("expansion_blocked"):
            raise GateError(f"{batch.profile} expansion is blocked by a Soft-Threshold regression")
        if record.get("failed_closed"):
            failed_at = str(record.get("failed_closed_at", ""))
            if not review_closes_incident(failed_at, review.reviewed_at):
                raise GateError(
                    f"{batch.profile} is failed closed since {failed_at}; re-enabling needs a "
                    "review recorded after the breach")
        record["enabled_batch"] = batch.to_record()
        record["last_review"] = review.to_record()
        record["failed_closed"] = False
        record["failed_closed_gates"] = []
        record["failed_closed_at"] = None
        record["review_required"] = False
        self._write(state)
        return self.status(batch.profile)

    def disable(self, profile: str, *, reason: str = "") -> dict:
        """Turn injection off for a profile without touching its failed-closed state."""
        state = self._read()
        record = self._profile_record(state, profile)
        record["enabled_batch"] = None
        record["disabled_reason"] = reason
        self._write(state)
        return self.status(profile)

    def record_review(self, review: GateReview) -> dict:
        """Record a review outcome without enabling anything (a rejection, AC5).

        The review is durable evidence even when it approves nothing, so a rejected Shadow
        Report leaves a trace and Shadow Mode keeps running.
        """
        state = self._read()
        record = self._profile_record(state, review.profile)
        record["last_review"] = review.to_record()
        self._write(state)
        return self.status(review.profile)

    def fail_closed(self, profile: str, gates: Sequence[str], *, reasons: Sequence[str] = (),
                    turn_id: str | None = None, session_id: str | None = None,
                    route_decision_id: str | None = None) -> Incident:
        """Revert the profile to foundation-only with injection off and record the Incident.

        The Incident names the gate, and the state marks the profile failed closed with a review
        required, so nothing flips it back within the turn that breached it (AC2, AC3).
        """
        names = tuple(gate for gate in HARD_GATES if gate in set(gates)) or tuple(gates)
        incident = Incident(
            profile=profile,
            gates=names,
            reasons=tuple(reasons),
            recorded_at=self._clock(),
            turn_id=turn_id,
            session_id=session_id,
            route_decision_id=route_decision_id,
        )
        append_jsonl(self.incidents_path, incident.to_record())
        state = self._read()
        record = self._profile_record(state, profile)
        # A running injection is turned off; the batch it was is evidence for the review, not a
        # permission to keep injecting while failed closed.
        record["previous_batch"] = record.get("enabled_batch")
        record["enabled_batch"] = None
        record["failed_closed"] = True
        record["failed_closed_gates"] = list(names)
        record["failed_closed_at"] = incident.recorded_at
        record["review_required"] = True
        self._write(state)
        return incident

    # -- Soft Thresholds: block expansion, never injection -------------------------------

    def check_soft_thresholds(self, profile: str, *, baseline: _ThresholdMetric,
                              current: _ThresholdMetric) -> dict:
        """Set the expansion block from a measured run, leaving running injection on (AC5).

        This is the enforcement step the gate owns: the measurement may come from the offline
        evaluation or a Shadow Report, but the decision to hold expansion lives here. A run with
        no regression leaves the block as it is, so an explicit re-derivation clears it.
        """
        reasons = soft_threshold_regression(baseline, current)
        if reasons:
            return self.note_expansion_regression(profile, reasons)
        return self.status(profile)

    def note_expansion_regression(self, profile: str, reasons: Sequence[str]) -> dict:
        """Block expansion on a Soft-Threshold regression, leaving any running injection on (AC5)."""
        state = self._read()
        record = self._profile_record(state, profile)
        record["expansion_blocked"] = True
        record["expansion_reasons"] = list(reasons)
        self._write(state)
        return self.status(profile)

    def clear_expansion_block(self, profile: str) -> dict:
        state = self._read()
        record = self._profile_record(state, profile)
        record["expansion_blocked"] = False
        record["expansion_reasons"] = []
        self._write(state)
        return self.status(profile)

    # -- the prompt/tool-schema observation ----------------------------------------------

    def observe_request(self, profile: str, session_id: str, *, system_prompt_sha256: str,
                        tool_count: int, tool_schema_sha256: str = "") -> tuple[bool, dict | None]:
        """Compare a provider request's prompt/tool schema with its conversation's baseline.

        Returns ``(ok, baseline)``: ``ok`` is False on a mutation, and ``baseline`` is the first
        observation for the session (``None`` when this request established it). The tool schema
        is compared by hash when the hook supplies one (a same-count edit is still a mutation);
        ``tool_count`` is always compared. The gate records the baseline rather than a hash of
        the request, so the check is durable across restarts.
        """
        observation = {"system_prompt_sha256": system_prompt_sha256, "tool_count": tool_count}
        if tool_schema_sha256:
            observation["tool_schema_sha256"] = tool_schema_sha256
        state = self._read()
        baselines = state.setdefault("baselines", {})
        key = f"{profile}\x00{session_id}"
        baseline = baselines.get(key)
        if baseline is None:
            baselines[key] = observation
            self._write(state)
            return True, None
        return _same_observation(baseline, observation), baseline

    # -- internals -----------------------------------------------------------------------

    def _validate(self, batch: InjectionBatch, review: GateReview) -> None:
        if review.outcome not in REVIEW_OUTCOMES:
            raise GateError(f"review outcome {review.outcome!r} is not a recorded review outcome")
        if batch.profile != review.profile:
            raise GateError(
                f"review is for {review.profile!r}, not the batch's {batch.profile!r}")
        if batch.name != review.batch:
            raise GateError(f"review is for batch {review.batch!r}, not {batch.name!r}")
        if review.outcome not in (APPROVE, APPROVE_NARROWER):
            raise GateError(f"review outcome {review.outcome!r} does not approve the batch")

    def _read(self) -> dict:
        if not self.state_path.exists():
            return {"state_version": STATE_VERSION, "profiles": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt gate state is the safe default: nothing is enabled.
            return {"state_version": STATE_VERSION, "profiles": {}}
        if not isinstance(state, Mapping):
            return {"state_version": STATE_VERSION, "profiles": {}}
        state = dict(state)
        state.setdefault("profiles", {})
        state.setdefault("baselines", {})
        return state

    def _write(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(blob + "\n", encoding="utf-8")
        os.replace(temporary, self.state_path)

    @staticmethod
    def _profile_record(state: dict, profile: str) -> dict:
        return state.setdefault("profiles", {}).setdefault(profile, {})


def _same_observation(baseline: dict, observation: dict) -> bool:
    """Whether two provider observations agree. The tool-schema hash is compared only when both
    sides carry one, so a host that omits the full request on some calls cannot spuriously breach."""
    if any(baseline.get(key) != observation.get(key)
           for key in ("system_prompt_sha256", "tool_count")):
        return False
    if baseline.get("tool_schema_sha256") and observation.get("tool_schema_sha256"):
        return baseline["tool_schema_sha256"] == observation["tool_schema_sha256"]
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "APPROVE",
    "APPROVE_NARROWER",
    "BROKER_GATES",
    "InjectionBatch",
    "GateError",
    "GateReview",
    "HARD_GATES",
    "HardGate",
    "Incident",
    "InjectionGate",
    "REJECT",
    "REVIEW_OUTCOMES",
    "STATE_VERSION",
    "check_hard_gates",
    "closure_failure_kind",
    "closure_versions",
    "review_closes_incident",
    "soft_threshold_regression",
]
