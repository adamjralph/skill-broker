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

    Later stages extend it with limits, timing and model usage; tickets #46-#48 record the
    request identity, the profile, the resolved Authorised Closure, the ranked Candidate set,
    the Judgment and the Grants — all of which are metadata or hashes.
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
    grants: tuple[ResolvedSkillVersion, ...] = ()

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
            "grants": [entry.to_record() for entry in self.grants],
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
    delivery: str | None = None
    decision: RouteDecision | None = None

    @property
    def failed(self) -> bool:
        return self.outcome is TurnOutcome.FAILURE
