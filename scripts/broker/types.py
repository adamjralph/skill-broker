"""The broker's value types: the Intervention Result and the Route Decision (CONTEXT.md).

These are the shapes a caller sees at the external seam and the shapes the Evidence Log
persists. They carry hashes and identifiers, never request text and never Skill bodies
(ADR-0015), so secrecy is structural rather than a discipline.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class TurnOutcome(str, Enum):
    """How one turn's intervention ended.

    ``NO_SKILL`` is a valid routing outcome, including the degraded foundation-only behaviour
    a missing or malformed Profile Policy produces. ``GRANTED`` means authority was granted for
    this turn (delivery itself is Pack assembly). ``FAILURE`` is an infrastructure or
    verification problem that must never be delivered.
    """

    NO_SKILL = "no_skill"
    GRANTED = "granted"
    FAILURE = "failure"


class PackDelivery(str, Enum):
    """How a Skill Pack's content reaches the turn (CONTEXT.md "Pack Delivery", ADR-0010).

    ``INLINE`` means the whole Pack rides the Intervention itself; ``BY_REFERENCE`` means the
    Pack is over the effective inline budget and Hermes's native spill supplies a head/tail
    preview plus the path to the full text. Independent of which Skills the Pack contains: size
    never changes the grants.
    """

    INLINE = "inline"
    BY_REFERENCE = "by_reference"


@dataclass(frozen=True)
class ResolvedSkillVersion:
    """One Skill at one Version: its ID plus the canonical package hash (CONTEXT.md)."""

    id: str
    name: str
    version: str

    def to_record(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version}

    @classmethod
    def from_record(cls, record: Mapping) -> "ResolvedSkillVersion":
        return cls(id=str(record["id"]), name=str(record["name"]),
                   version=str(record["version"]))


@dataclass(frozen=True)
class Candidate:
    """One Skill surfaced by Retrieval as potentially relevant — an input to Judgment, never
    authority (CONTEXT.md). ``exact`` marks a Tier-0 ID/Name/Alias mention."""

    id: str
    name: str
    score: float
    exact: bool

    def to_record(self) -> dict:
        return {"id": self.id, "name": self.name, "score": self.score, "exact": self.exact}

    @classmethod
    def from_record(cls, record: Mapping) -> "Candidate":
        return cls(id=str(record["id"]), name=str(record["name"]),
                   score=float(record.get("score", 0.0)), exact=bool(record.get("exact", False)))


@dataclass(frozen=True)
class Judgment:
    """A validated Judgment: a nullable Primary Skill, a confidence, the full probability
    distribution and the echoed Candidate set (ADR-0013). It grants nothing by itself."""

    primary: str | None
    confidence: float
    distribution: dict[str, float]
    candidates: tuple[str, ...]

    @property
    def no_skill(self) -> bool:
        return self.primary is None

    def to_record(self) -> dict:
        return {
            "primary": self.primary,
            "confidence": self.confidence,
            "distribution": dict(self.distribution),
            "candidates": list(self.candidates),
            "no_skill": self.no_skill,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "Judgment":
        return cls(
            primary=record.get("primary"),
            confidence=float(record.get("confidence", 0.0)),
            distribution={str(key): float(value)
                          for key, value in (record.get("distribution") or {}).items()},
            candidates=tuple(str(candidate) for candidate in record.get("candidates", ())),
        )


@dataclass(frozen=True)
class RouteDecision:
    """The complete record of one routing outcome (CONTEXT.md), minus anything textual.

    Tickets #46-#48 record the request identity, the profile, the resolved Authorised Closure,
    the ranked Candidate set, the Judgment and the Grants — all metadata or hashes. Ticket #49
    adds the Pack Delivery mode, the Pack's size and hash, and the effective spill budget, so a
    reviewer can tell whether a turn delivered under a bounded or an unbounded cap. Ticket #50
    adds the Judgment Source that answered, its latency and its token usage. Ticket #51 adds the
    Hermes correlation ids (``task_id``/``turn_id``) and the classified delivery path, so the
    Adapter's per-request evidence handle can locate the Route Decision it belongs to.
    """

    profile: str
    session_id: str | None
    outcome: TurnOutcome
    reasons: tuple[str, ...]
    request_sha256: str
    request_chars: int
    task_id: str | None = None
    turn_id: str | None = None
    delivery_path: str | None = None
    authorised_closure: tuple[ResolvedSkillVersion, ...] = ()
    candidate_limit: int | None = None
    candidates: tuple[Candidate, ...] = ()
    judgment: Judgment | None = None
    judgment_source: str | None = None
    judgment_latency_ms: float | None = None
    judgment_usage: dict | None = None
    grants: tuple[ResolvedSkillVersion, ...] = ()
    delivery: PackDelivery | None = None
    pack_chars: int | None = None
    pack_sha256: str | None = None
    budget: dict | None = None

    @property
    def route_decision_id(self) -> str:
        """A stable locator for this decision, so a request's evidence handle can name it.

        Deterministic over the routing identity — the profile, the turn id and the request hash —
        so the same Recording replays to the same locator and no session, counter or clock leaks
        into it. The Adapter's evidence record also carries ``session_id``/``turn_id``, so the join
        holds durably even without this id.
        """
        material = "\x00".join((self.profile or "", self.turn_id or "", self.request_sha256))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_record(self) -> dict:
        return {
            "route_decision_id": self.route_decision_id,
            "profile": self.profile,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "delivery_path": self.delivery_path,
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "request": {"sha256": self.request_sha256, "chars": self.request_chars},
            "authorised_closure": [entry.to_record() for entry in self.authorised_closure],
            "candidate_limit": self.candidate_limit,
            "candidates": [candidate.to_record() for candidate in self.candidates],
            "judgment": self.judgment.to_record() if self.judgment is not None else None,
            "judgment_source": self.judgment_source,
            "judgment_latency_ms": self.judgment_latency_ms,
            "judgment_usage": self.judgment_usage,
            "grants": [entry.to_record() for entry in self.grants],
            "delivery": self.delivery.value if self.delivery is not None else None,
            "pack_chars": self.pack_chars,
            "pack_sha256": self.pack_sha256,
            "budget": self.budget,
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "RouteDecision":
        """Rebuild a Decision from its recorded form, so a log line can be re-checked (B12).

        Only the routing identity, the closure, the Candidate set, the Judgment and the Grants
        are reconstructed: the request text was never recorded (ADR-0015), so the record carries
        its hash and length instead.
        """
        request = record.get("request") or {}
        delivery = record.get("delivery")
        judgment = record.get("judgment")
        return cls(
            profile=str(record.get("profile", "")),
            session_id=record.get("session_id"),
            outcome=TurnOutcome(str(record.get("outcome", TurnOutcome.NO_SKILL.value))),
            reasons=tuple(str(reason) for reason in record.get("reasons", ())),
            request_sha256=str(request.get("sha256", "")),
            request_chars=int(request.get("chars", 0)),
            task_id=record.get("task_id"),
            turn_id=record.get("turn_id"),
            delivery_path=record.get("delivery_path"),
            authorised_closure=tuple(
                ResolvedSkillVersion.from_record(entry)
                for entry in record.get("authorised_closure", ())),
            candidate_limit=record.get("candidate_limit"),
            candidates=tuple(Candidate.from_record(candidate)
                             for candidate in record.get("candidates", ())),
            judgment=Judgment.from_record(judgment) if judgment else None,
            judgment_source=record.get("judgment_source"),
            judgment_latency_ms=record.get("judgment_latency_ms"),
            judgment_usage=record.get("judgment_usage"),
            grants=tuple(ResolvedSkillVersion.from_record(entry)
                         for entry in record.get("grants", ())),
            delivery=PackDelivery(str(delivery)) if delivery else None,
            pack_chars=record.get("pack_chars"),
            pack_sha256=record.get("pack_sha256"),
            budget=record.get("budget"),
        )


@dataclass(frozen=True)
class InterventionResult:
    """One turn's outcome at the external seam (CONTEXT.md).

    ``pack`` is falsy whenever the broker supplies nothing, which is what the Hermes Adapter
    gates on. An explicit no-intervention outcome is still a valid Intervention Result.
    """

    outcome: TurnOutcome
    reasons: tuple[str, ...] = ()
    grants: tuple[ResolvedSkillVersion, ...] = ()
    pack: str | None = None
    delivery: PackDelivery | None = None
    decision: RouteDecision | None = None
    failed_closed: bool = False

    @property
    def failed(self) -> bool:
        return self.outcome is TurnOutcome.FAILURE
