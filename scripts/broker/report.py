"""The Shadow Report and its batch review (ADR-0019, spec B13, ticket #56).

The Shadow Report is the **review artifact** that ends Shadow Mode for one profile. It is not a
metrics dump: it is assembled from the recorded Route Decisions of real traffic plus what the
agent *actually used* over a bounded observation window, and it says, in reviewable form:

* the profile and the exact Injection Batch that enabling injection would cover;
* each Hard Gate's status over the window;
* movement against the pre-registered Soft Thresholds;
* every turn where the broker's would-have-selected differed from actual use or from human
  review, with the pipeline stage that decided it attributed.

Its **review** — approve, reject, or approve a narrower batch — is the only thing that enables an
Injection Batch. A rejected report enables nothing and leaves Shadow Mode running. Reports carry
metadata and hashes only, never request text and never a Skill body (ADR-0015).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import profile_policy as pp
import store_manifest as sm

from .corpus import Turn, digest, read_skill_use, read_turns
from .evaluation import SoftThresholds, SplitMetrics, attribute
from .gate import (
    APPROVE,
    APPROVE_NARROWER,
    HARD_GATES,
    REJECT,
    GateReview,
    HardGate,
    InjectionBatch,
    InjectionGate,
    check_hard_gates,
    closure_failure_kind,
    closure_versions,
    soft_threshold_regression,
)
from .types import RouteDecision, TurnOutcome

REPORT_VERSION = 1


class ReportError(RuntimeError):
    """A Shadow Report cannot be assembled or reviewed."""


def load_route_decisions(path: Path | str, *,
                         profile: str | None = None) -> list[RouteDecision]:
    """The recorded Route Decisions, oldest first, optionally narrowed to a profile.

    The Evidence Log is append-only JSONL (ADR-0015), so a malformed or partial trailing line is
    skipped rather than failing the read. There is no time filter here because a record carries
    no timestamp; :func:`build_shadow_report` windows on the session turn instead.
    """
    file = Path(path)
    if not file.exists():
        return []
    decisions: list[RouteDecision] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            decision = RouteDecision.from_record(record)
        except (ValueError, KeyError, TypeError):
            continue
        if profile is not None and decision.profile != profile:
            continue
        decisions.append(decision)
    return decisions


@dataclass(frozen=True)
class ShadowTurn:
    """One observed turn: the broker's Decision, the agent's actual use and any human review."""

    decision: RouteDecision
    timestamp: float | None
    observed: tuple[str, ...]
    reviewed: str | None = None

    @property
    def would_have_selected(self) -> str | None:
        """The Name of the Primary Grant the broker would have supplied, or ``None``."""
        return self.decision.grants[0].name if self.decision.grants else None


@dataclass(frozen=True)
class GateStatus:
    """One Hard Gate's status over the observation window (AC3).

    ``checked`` is False for a gate this seam cannot observe (the prompt/tool-schema gate is
    observed by the Adapter at the provider request, so a report built without the gate's
    Incident log can only say ``not_checked``). ``gates_passed`` ignores unchecked gates.
    """

    gate: str
    status: str
    breached: int
    turns_checked: int
    checked: bool = True

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_record(self) -> dict:
        return {"gate": self.gate, "status": self.status, "breached": self.breached,
                "turns_checked": self.turns_checked, "checked": self.checked}

    @classmethod
    def from_record(cls, record: Mapping) -> "GateStatus":
        return cls(gate=str(record["gate"]), status=str(record["status"]),
                   breached=int(record.get("breached", 0)),
                   turns_checked=int(record.get("turns_checked", 0)),
                   checked=bool(record.get("checked", True)))


@dataclass(frozen=True)
class Disagreement:
    """One turn where the broker's selection differed from use or review (AC4)."""

    session_id: str
    turn_id: str
    would_have_selected: str | None
    observed: tuple[str, ...]
    reviewed: str | None
    attribution: str

    def to_record(self) -> dict:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "would_have_selected": self.would_have_selected,
            "observed": list(self.observed),
            "reviewed": self.reviewed,
            "attribution": self.attribution,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "Disagreement":
        return cls(
            session_id=str(record.get("session_id", "")),
            turn_id=str(record.get("turn_id", "")),
            would_have_selected=record.get("would_have_selected"),
            observed=tuple(str(skill) for skill in record.get("observed", ())),
            reviewed=record.get("reviewed"),
            attribution=str(record.get("attribution", "")),
        )


@dataclass(frozen=True)
class ShadowReport:
    """The reviewed record of one profile's Shadow Mode window (AC1-AC4)."""

    profile: str
    batch: InjectionBatch
    window_start: float | None
    window_end: float | None
    generated_at: str
    turns: int
    metrics: SplitMetrics
    gate_status: tuple[GateStatus, ...]
    threshold_movement: tuple[str, ...]
    threshold_regressions: tuple[str, ...]
    thresholds_pre_registered: bool
    disagreements: tuple[Disagreement, ...]
    review: dict | None = None
    problems: tuple[str, ...] = ()

    @property
    def gates_passed(self) -> bool:
        return all(status.passed for status in self.gate_status if status.checked)

    @property
    def threshold_regressed(self) -> bool:
        """Whether a quality floor was breached, which holds expansion (ADR-0019)."""
        return bool(self.threshold_regressions)

    @property
    def report_sha256(self) -> str:
        """A stable digest of the report's reviewable content, binding any review to it.

        The review and any generated problems are excluded, so the digest is the digest of what
        the review actually reviewed.
        """
        return _digest(self._content_record())

    def _content_record(self) -> dict:
        return {
            "report_version": REPORT_VERSION,
            "profile": self.profile,
            "batch": self.batch.to_record(),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "generated_at": self.generated_at,
            "turns": self.turns,
            "metrics": self.metrics.to_record(),
            "gate_status": [status.to_record() for status in self.gate_status],
            "gates_passed": self.gates_passed,
            "thresholds_pre_registered": self.thresholds_pre_registered,
            "threshold_movement": list(self.threshold_movement),
            "threshold_regressions": list(self.threshold_regressions),
            "disagreements": [entry.to_record() for entry in self.disagreements],
        }

    def to_record(self) -> dict:
        return {
            **self._content_record(),
            "report_sha256": self.report_sha256,
            "review": self.review,
            "problems": list(self.problems),
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "ShadowReport":
        return cls(
            profile=str(record["profile"]),
            batch=InjectionBatch.from_record(record["batch"]),
            window_start=record.get("window_start"),
            window_end=record.get("window_end"),
            generated_at=str(record.get("generated_at", "")),
            turns=int(record.get("turns", 0)),
            metrics=SplitMetrics.from_record(record.get("metrics") or {}),
            gate_status=tuple(GateStatus.from_record(entry)
                              for entry in record.get("gate_status", ())),
            threshold_movement=tuple(str(reason)
                                     for reason in record.get("threshold_movement", ())),
            threshold_regressions=tuple(str(reason)
                                        for reason in record.get("threshold_regressions", ())),
            thresholds_pre_registered=bool(record.get("thresholds_pre_registered", False)),
            disagreements=tuple(Disagreement.from_record(entry)
                                for entry in record.get("disagreements", ())),
            review=dict(record["review"]) if record.get("review") else None,
            problems=tuple(str(problem) for problem in record.get("problems", ())),
        )


class ShadowReportStore:
    """Where built reports live: one JSON document per profile and window (machine-local)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    def path(self, profile: str, generated_at: str) -> Path:
        stamp = "".join(character if character.isalnum() else "-" for character in generated_at)
        return self.root / profile / f"shadow-{stamp}.json"

    def save(self, report: ShadowReport) -> Path:
        path = self.path(report.profile, report.generated_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_record(), indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return path

    def load(self, path: Path | str) -> ShadowReport:
        return ShadowReport.from_record(json.loads(Path(path).read_text(encoding="utf-8")))


def build_shadow_report(*, profile: str, store: Path | str, evidence_path: Path | str,
                        db: Path | str, since: float | None = None, until: float | None = None,
                        thresholds: Mapping | None = None, gate: InjectionGate | None = None,
                        human_review: Mapping[tuple[str, str], str | None] | None = None,
                        generated_at: str | None = None) -> ShadowReport:
    """Assemble the report over the window from recorded Decisions plus observed skill use.

    The window is applied to the session turn's timestamp, because a recorded Route Decision
    carries no clock of its own. A Decision whose turn is outside the window (or whose turn the
    database does not carry, when a window is given) is left out.
    """
    batch, foundation_ids, names_to_ids = profile_metadata(store, profile)
    if (since is None) != (until is None):
        raise ReportError("a bounded window needs both since and until")
    if since is not None and until is not None and since > until:
        raise ReportError(f"window start {since} is after its end {until}")
    human_review = dict(human_review or {})
    all_turns = read_turns(db)
    by_turn_id = {(turn.session_id, turn.turn_id): turn for turn in all_turns}
    # A live Route Decision's ``turn_id`` is Hermes's ephemeral per-turn correlation id, not the
    # state.db message id, so it never joins by id. The request text is the durable key both
    # records carry (as its content hash), and matching in log order resolves a repeated request
    # to its own turn rather than the first one.
    by_request: dict[tuple[str, str], list[Turn]] = {}
    for turn in all_turns:
        by_request.setdefault((turn.session_id, digest(turn.content)), []).append(turn)
    matched: set[tuple[str, str]] = set()
    observed: dict[tuple[str, str], list[str]] = {}
    for use in read_skill_use(db):
        observed.setdefault((use.session_id, use.turn_id), []).append(use.skill)

    def resolve(decision: RouteDecision) -> Turn | None:
        """The session turn a recorded Decision belongs to, by id or by request content."""
        session = decision.session_id or ""
        turn = by_turn_id.get((session, decision.turn_id or ""))
        if turn is not None:
            matched.add((turn.session_id, turn.turn_id))
            return turn
        for candidate in by_request.get((session, decision.request_sha256), ()):
            key = (candidate.session_id, candidate.turn_id)
            if key not in matched:
                matched.add(key)
                return candidate
        return None

    problems: list[str] = []
    shadow_turns: list[ShadowTurn] = []
    for decision in load_route_decisions(evidence_path, profile=profile):
        turn = resolve(decision)
        if since is not None or until is not None:
            if turn is None:
                problems.append(
                    f"{decision.session_id or ''}/{decision.turn_id or ''} "
                    f"({decision.request_sha256[:12]}): turn_not_in_window")
                continue
            if since is not None and turn.timestamp < since:
                continue
            if until is not None and turn.timestamp > until:
                continue
        key = ((turn.session_id, turn.turn_id) if turn is not None
               else (decision.session_id or "", decision.turn_id or ""))
        uses = tuple(_brokered_names(observed.get(key, ()), names_to_ids))
        shadow_turns.append(ShadowTurn(
            decision=decision,
            timestamp=turn.timestamp if turn is not None else None,
            observed=uses,
            reviewed=human_review.get(key),
        ))

    metrics = _summarize(shadow_turns, names_to_ids)
    gate_status = _gate_status(shadow_turns, foundation_ids, gate, profile)
    movement, regressions, pre_registered = _threshold_movement(thresholds, metrics)
    disagreements = _disagreements(shadow_turns, names_to_ids)

    return ShadowReport(
        profile=profile,
        batch=batch,
        window_start=since,
        window_end=until,
        generated_at=generated_at or _now(),
        turns=len(shadow_turns),
        metrics=metrics,
        gate_status=gate_status,
        threshold_movement=movement,
        threshold_regressions=regressions,
        thresholds_pre_registered=pre_registered,
        disagreements=tuple(disagreements),
        problems=tuple(problems),
    )


def apply_review(report: ShadowReport, *, outcome: str, reviewer: str, gate: InjectionGate,
                 reviewed_at: str = "", batch: InjectionBatch | None = None) -> ShadowReport:
    """Record the review and, for an approval, enable the batch. A rejection enables nothing.

    The review is bound to the report by ``report_sha256``, so it names exactly what was
    reviewed. ``approve_narrower`` enables the ``batch`` supplied (defaulting to the report's
    batch with the same skills), and only an approval reaches :meth:`InjectionGate.enable`; a
    rejection is recorded and Shadow Mode keeps running. An approval supplied with a ``batch``
    must cover exactly the reviewed batch's Skills, so a review cannot enable a different set.
    """
    if outcome not in (APPROVE, APPROVE_NARROWER, REJECT):
        raise ReportError(f"review outcome {outcome!r} is not approve/reject/approve_narrower")
    if outcome == APPROVE_NARROWER:
        if batch is None or not batch.skills:
            raise ReportError("approve_narrower needs the narrower batch's Skills")
        if not set(batch.skills) < set(report.batch.skills):
            raise ReportError(
                "the narrower batch must be a proper subset of the report's Brokered Allowlist")
    if outcome != REJECT:
        if not report.thresholds_pre_registered:
            raise ReportError("an approval needs pre-registered Soft Thresholds (ADR-0019)")
        if not report.gates_passed:
            raise ReportError("a Report with a Hard-Gate breach cannot authorise injection")
        if report.threshold_regressed:
            gate.note_expansion_regression(report.profile, report.threshold_regressions)
            raise ReportError("expansion is blocked by a Soft-Threshold regression: "
                              + "; ".join(report.threshold_regressions))
    target = batch or report.batch
    if target.profile != report.profile:
        raise ReportError(f"the reviewed batch is for {target.profile!r}, not {report.profile!r}")
    if outcome == APPROVE and batch is not None and set(batch.skills) != set(report.batch.skills):
        raise ReportError(
            "an approval enables exactly the reviewed batch; use approve_narrower to enable a "
            "proper subset of the report's Brokered Allowlist")
    review = GateReview(profile=report.profile, batch=target.name, reviewer=reviewer,
                        outcome=outcome, reviewed_at=reviewed_at,
                        report_sha256=report.report_sha256)
    if outcome == REJECT:
        gate.record_review(review)
    else:
        gate.enable(target, review)
    return replace(report, review=review.to_record())


def human_review_map(reviewed_cases: Iterable) -> dict[tuple[str, str], str | None]:
    """A Reviewed Case's hand-corrected truth keyed by the session turn the report windows on."""
    return {(entry.case.session_id, entry.case.turn_id): entry.primary
            for entry in reviewed_cases}


def profile_metadata(store: Path | str, profile: str) -> tuple[InjectionBatch, tuple[str, ...],
                                                              dict[str, str]]:
    """``(batch, foundation_ids, broker-name→id)`` for the profile's policy (B15, AC2).

    The batch is exactly the policy's Brokered Allowlist, so the report names the Skills that
    enabling injection would cover. The name map lets observed ``skill_view`` Names be compared
    with the Broker's store-ID Grants.
    """
    store_path = Path(store)
    problems = sm.verify(store_path)
    if problems:
        raise ReportError("store manifest does not verify: " + "; ".join(problems))
    identities = {row["id"]: row for row in sm.build(store_path)["identities"]}
    path = store_path / pp.POLICIES_DIR / f"{profile}.json"
    if not path.exists():
        raise ReportError(f"policy_missing for profile {profile!r}: {path}")
    try:
        policy = pp.load(path)
    except (OSError, ValueError, pp.PolicyError) as exc:
        raise ReportError(f"policy_invalid for profile {profile!r}: {exc}") from exc
    problems = pp.validate(policy, identities, filename=profile)
    if problems:
        raise ReportError(f"policy_invalid for profile {profile!r}: " + "; ".join(problems))
    foundation_ids = tuple(entry["id"] for entry in policy.get("foundation", ())
                           if isinstance(entry, dict) and "id" in entry)
    brokered = tuple(str(ident) for ident in policy.get("brokered", ()))
    names_to_ids: dict[str, str] = {}
    for ident in brokered:
        row = identities.get(ident)
        if row is None:
            continue
        for name in (row["name"], *row.get("aliases", ())):
            name = str(name)
            existing = names_to_ids.get(name)
            if existing is not None and existing != ident:
                # A Name Collision is legitimate (CONTEXT.md) and a Name is not identity, so an
                # ambiguous observed Name is left unmatched rather than attributed to one Skill.
                del names_to_ids[name]
                continue
            names_to_ids[name] = ident
    return (InjectionBatch(name=f"{profile}-brokered", profile=profile, skills=brokered),
            foundation_ids, names_to_ids)


def _brokered_names(names: Iterable[str], names_to_ids: Mapping[str, str]) -> tuple[str, ...]:
    """The observed Skill Names that enabling this batch would cover, in first-seen order."""
    seen: list[str] = []
    for name in names:
        if name in names_to_ids and name not in seen:
            seen.append(name)
    return tuple(seen)


def _summarize(turns: list[ShadowTurn], names_to_ids: Mapping[str, str]) -> SplitMetrics:
    """Measure precision, recall and the Hard-Gate counts against actual use and human review."""
    counts = dict(tp=0, fp=0, fn=0, tn=0, granted=0, no_skill=0, failures=0, grants=0,
                  unauthorised=0, hash_agreements=0, hash_disagreements=0,
                  closure_failures=0, candidate_total=0, pack_deliveries=0, pack_chars_total=0,
                  duplicate_suppressions=0)
    for turn in turns:
        decision = turn.decision
        closure = closure_versions(decision)
        selected = decision.grants[0].id if decision.grants else None
        actual = {names_to_ids.get(name, name) for name in turn.observed}
        if decision.outcome is TurnOutcome.GRANTED:
            counts["granted"] += 1
        elif decision.outcome is TurnOutcome.NO_SKILL:
            counts["no_skill"] += 1
        else:
            counts["failures"] += 1
        if selected is not None:
            counts["grants"] += 1
            counts["hash_agreements" if selected in closure
                   and decision.grants[0].version == closure[selected]
                   else "hash_disagreements"] += 1
        counts["unauthorised"] += sum(1 for grant in decision.grants if grant.id not in closure)
        counts["closure_failures"] += _closure_failure_count(decision)
        counts["candidate_total"] += len(decision.candidates)
        if decision.pack_chars is not None:
            counts["pack_deliveries"] += 1
            counts["pack_chars_total"] += decision.pack_chars
        if "duplicate_suppressed" in decision.reasons:
            counts["duplicate_suppressions"] += 1
        if selected is not None and actual:
            if selected in actual:
                counts["tp"] += 1
            else:
                counts["fp"] += 1
        elif selected is not None:
            counts["fp"] += 1
        elif actual:
            counts["fn"] += 1
        else:
            counts["tn"] += 1
    return SplitMetrics(
        cases=len(turns),
        granted=counts["granted"], no_skill=counts["no_skill"], failures=counts["failures"],
        true_positive=counts["tp"], false_positive=counts["fp"], false_negative=counts["fn"],
        true_negative=counts["tn"], grants=counts["grants"],
        unauthorised_grants=counts["unauthorised"],
        closure_failures=counts["closure_failures"],
        candidate_total=counts["candidate_total"], pack_deliveries=counts["pack_deliveries"],
        pack_chars_total=counts["pack_chars_total"],
        duplicate_suppressions=counts["duplicate_suppressions"],
        hash_agreements=counts["hash_agreements"],
        hash_disagreements=counts["hash_disagreements"],
    )


def _closure_failure_count(decision: RouteDecision) -> int:
    return 1 if closure_failure_kind(decision.reasons) else 0


def _gate_status(turns: list[ShadowTurn], foundation_ids: tuple[str, ...],
                 gate: InjectionGate | None, profile: str) -> tuple[GateStatus, ...]:
    """Per-gate status over the window, from the Decisions and the Incident log (AC3)."""
    breaches: dict[str, int] = {gate_name: 0 for gate_name in HARD_GATES}
    for turn in turns:
        for gate_name in check_hard_gates(turn.decision, foundation_ids=foundation_ids):
            breaches[gate_name] += 1
    if gate is not None:
        for incident in gate.incidents(profile):
            for gate_name in incident.gates:
                if gate_name in breaches:
                    breaches[gate_name] += 1
    unchecked = HardGate.PROMPT_SCHEMA_MUTATION.value if gate is None else None
    return tuple(
        GateStatus(gate=gate_name,
                   status="not_checked" if gate_name == unchecked
                          else ("breach" if breaches[gate_name] else "pass"),
                   breached=breaches[gate_name], turns_checked=len(turns),
                   checked=gate_name != unchecked)
        for gate_name in HARD_GATES
    )


def _threshold_movement(thresholds: Mapping | None, metrics: SplitMetrics
                        ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """``(all movement, quality-floor regressions, pre_registered)`` for the window (AC3).

    Quality floors a later run fell below are blocking regressions (via
    :func:`soft_threshold_regression`); the reference cost rates are reported as movement even
    when they are not conditions, because the review reads what actually moved. The regressions
    are separated out so :func:`apply_review` can hold expansion on them (ADR-0019).
    """
    if not thresholds or not isinstance(thresholds, Mapping):
        return (), (), False
    record = thresholds.get("thresholds")
    if not isinstance(record, Mapping):
        return (), (), False
    baseline = SoftThresholds.from_record(record)
    regressions = soft_threshold_regression(baseline, metrics)
    movement = list(regressions)
    for name in ("duplicate_injection_rate", "average_candidates", "average_pack_chars"):
        reference = getattr(baseline, name)
        measured = getattr(metrics, name)
        if reference is not None and measured is not None and measured != reference:
            movement.append(f"{name} moved: {measured} (baseline {reference})")
    return tuple(movement), regressions, True


def _disagreements(turns: list[ShadowTurn],
                   names_to_ids: Mapping[str, str]) -> list[Disagreement]:
    """Every turn where the broker differed from actual use or from human review (AC4).

    The two signals are independent: a turn is listed when the broker's selection was not among
    what the agent used, and also when it did not match a hand review. A selection that matches
    the human review is attributed ``correct`` even if actual use differed, because the review is
    the ground truth for whether the broker was right.
    """
    entries: list[Disagreement] = []
    for turn in turns:
        decision = turn.decision
        selected = decision.grants[0].id if decision.grants else None
        actual = {names_to_ids.get(name, name) for name in turn.observed}
        reviewed = turn.reviewed
        differs_from_actual = not ((selected is None and not actual)
                                   or (selected is not None and selected in actual))
        differs_from_review = reviewed is not None and selected != reviewed
        if not (differs_from_actual or differs_from_review):
            continue
        ground_truth = reviewed if reviewed is not None else (
            sorted(actual)[0] if actual else None)
        entries.append(Disagreement(
            session_id=decision.session_id or "",
            turn_id=decision.turn_id or "",
            would_have_selected=turn.would_have_selected,
            observed=turn.observed,
            reviewed=reviewed,
            attribution=attribute(
                failed=decision.outcome is TurnOutcome.FAILURE,
                ground_truth=ground_truth,
                selected=selected,
                candidates=[candidate.id for candidate in decision.candidates],
                judgment_primary=decision.judgment.primary if decision.judgment else None),
        ))
    return entries


def _digest(record: Mapping) -> str:
    blob = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "Disagreement",
    "GateStatus",
    "REPORT_VERSION",
    "ReportError",
    "ShadowReport",
    "ShadowReportStore",
    "ShadowTurn",
    "apply_review",
    "build_shadow_report",
    "human_review_map",
    "load_route_decisions",
    "profile_metadata",
]
