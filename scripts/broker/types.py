"""The broker's value types: the Intervention Result and the Route Decision (CONTEXT.md).

These are the shapes a caller sees at the external seam and the shapes the Evidence Log
persists. They carry hashes and identifiers, never request text and never Skill bodies
(ADR-0015), so secrecy is structural rather than a discipline.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class RouteDecision:
    """The complete record of one routing outcome (CONTEXT.md), minus anything textual.

    Tickets #46-#48 record the request identity, the profile, the resolved Authorised Closure,
    the ranked Candidate set, the Judgment and the Grants — all metadata or hashes. Ticket #49
    adds the Pack Delivery mode, the Pack's size and hash, and the effective spill budget, so a
    reviewer can tell whether a turn delivered under a bounded or an unbounded cap. Ticket #50
    adds the Judgment Source that answered, its latency and its token usage. Timing and model
    usage elsewhere follow in later stages.
    """

    profile: str
    session_id: str | None
    outcome: TurnOutcome
    reasons: tuple[str, ...]
    request_sha256: str
    request_chars: int
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

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "session_id": self.session_id,
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

    @property
    def failed(self) -> bool:
        return self.outcome is TurnOutcome.FAILURE
