"""The Judgment Source seam, the typed Judgment contract, and the deterministic Grant.

A Judgment is exactly one batched Choice over the ranked Candidates plus a reserved ``no_skill``
option, validated into a nullable Primary Skill, a confidence, the full probability distribution
and the echoed Candidate set (ADR-0013). The Source is interchangeable — live Jev (ticket #50),
a recorded Recording, or the deterministic stub here — because a model returns text, not
authority: only ``grant`` turns a validated Judgment into a Grant, and it can only ever narrow
what the Profile Policy already allows (ADR-0004).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .types import Candidate, Judgment, ResolvedSkillVersion

NO_SKILL = "no_skill"
CHOICE_KEYS = frozenset({"primary", "confidence", "distribution", "candidates"})


class JudgmentError(Exception):
    """A Judgment could not be produced."""


class RecordingMismatch(JudgmentError):
    """A Recording was asked to judge a request that is not its Case."""


@runtime_checkable
class JudgmentSource(Protocol):
    """Anything that answers one Choice for a request over these Candidates."""

    def judge(self, request: str, candidates: Sequence[Candidate]) -> Mapping: ...


@dataclass(frozen=True)
class Recording:
    """A Jev outcome frozen and bound to its Case by content hash (ADR-0013, ADR-0020).

    Replay is deterministic and offline because the claim is stored, never recomputed.
    """

    case_sha256: str
    claim: dict

    @classmethod
    def of(cls, request: str, claim: Mapping) -> "Recording":
        return cls(case_sha256=_digest(request), claim=dict(claim))

    def matches(self, request: str) -> bool:
        return _digest(request) == self.case_sha256

    def to_record(self) -> dict:
        return {"case_sha256": self.case_sha256, "claim": self.claim}

    @classmethod
    def from_record(cls, record: Mapping) -> "Recording":
        return cls(case_sha256=str(record["case_sha256"]), claim=dict(record["claim"]))


class StubJudgmentSource:
    """The deterministic stub: always answers the claim it was constructed with."""

    def __init__(self, claim: Mapping) -> None:
        self._claim = dict(claim)

    def judge(self, request: str, candidates: Sequence[Candidate]) -> Mapping:
        return dict(self._claim)


class RecordedJudgmentSource:
    """Replays one Recording, refusing a request that is not that Recording's Case."""

    def __init__(self, recording: Recording) -> None:
        self._recording = recording

    def judge(self, request: str, candidates: Sequence[Candidate]) -> Mapping:
        if not self._recording.matches(request):
            raise RecordingMismatch("the request is not this Recording's Case")
        return dict(self._recording.claim)


def validate(claim: object, candidates: Sequence[Candidate],
             closure: Sequence[ResolvedSkillVersion]) -> tuple[Judgment | None, tuple[str, ...]]:
    """Validate one Choice into a Judgment, or return ``(None, problems)``. Rejects whole."""
    if not isinstance(claim, Mapping):
        return None, (f"judgment is not a Choice object: {type(claim).__name__}",)

    candidate_ids = [candidate.id for candidate in candidates]
    closure_ids = {entry.id for entry in closure}
    problems: list[str] = []

    if set(claim) != CHOICE_KEYS:
        problems.append(f"Choice keys must be {sorted(CHOICE_KEYS)}; got "
                        f"{sorted(str(key) for key in claim)}")

    primary = claim.get("primary")
    confidence = claim.get("confidence")
    distribution = claim.get("distribution")
    echoed = claim.get("candidates")

    if primary is not None and not isinstance(primary, str):
        problems.append("primary must be a Candidate ID or null")
    if not _number(confidence) or not 0.0 <= float(confidence) <= 1.0:
        problems.append("confidence must be a number between 0 and 1")
    if not isinstance(distribution, Mapping):
        problems.append("distribution must be an object covering the Candidates and no_skill")
        distribution = {}
    if not isinstance(echoed, (list, tuple)) or not all(isinstance(i, str) for i in echoed):
        problems.append("candidates must be a list of Candidate IDs")
        echoed = []

    if set(echoed) != set(candidate_ids):
        problems.append("the echoed Candidate set does not match the Candidates judged")
    if primary is not None:
        if primary not in candidate_ids:
            problems.append(f"primary {primary!r} is not a Candidate")
        elif primary not in closure_ids:
            problems.append(f"primary {primary!r} is not authorised by the Profile Policy")

    expected = set(candidate_ids) | {NO_SKILL}
    if set(distribution) != expected:
        problems.append("distribution must cover every Candidate and the reserved no_skill")
    else:
        for key, value in distribution.items():
            if not _number(value) or not 0.0 <= float(value) <= 1.0:
                problems.append(f"distribution[{key!r}] must be a number between 0 and 1")
        total = sum(float(value) for value in distribution.values() if _number(value))
        if abs(total - 1.0) > 1e-6:
            problems.append(f"distribution must sum to 1 (got {total})")

    if problems:
        return None, tuple(sorted(problems))
    return Judgment(
        primary=primary,
        confidence=float(confidence),
        distribution={key: float(value) for key, value in sorted(distribution.items())},
        candidates=tuple(candidate_ids),
    ), ()


def grant(judgment: Judgment,
          closure: Sequence[ResolvedSkillVersion]) -> tuple[ResolvedSkillVersion, ...]:
    """The Grant: one validated Judgment intersected with the Policy's Authorised Closure.

    Authority is per turn, so this is the whole of it — there is no lease and nothing to carry
    over (ADR-0014). v1 is strictly single-primary (ADR-0006).
    """
    if judgment.primary is None:
        return ()
    return tuple(entry for entry in closure if entry.id == judgment.primary)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _digest(request: str) -> str:
    return hashlib.sha256(request.encode("utf-8")).hexdigest()
