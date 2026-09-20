"""The live Jev Judgment Source: one TypeSafe System One call per turn (ticket #50).

Jev answers exactly one ``Choice`` over the ranked Candidates plus the reserved ``no_skill``
label, from the request as state. The answer is converted into the same claim the recorded and
stub paths produce, so the shared validator and the deterministic Grant treat every source
identically; nothing outside the bounded, already-authorised Candidate set is ever sent.

The Source is deliberately thin and untuned — Jev prompt and criteria engineering is carried
fog. It enforces a timeout well under Hermes's hook-callback timeout (30s default) and raises on
anything unusable, so the Adapter can compose it with a recorded fallback and a No-Skill floor
rather than letting a model outage block a turn (ADR-0004, ``FallbackJudgmentSource``).

The TypeSafe SDK is imported lazily: the broker's suites and a Consumer without the SDK still
run, and only an actual live call needs it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .judgment import (
    NO_SKILL,
    FallbackJudgmentSource,
    JudgmentCall,
    JudgmentError,
    JudgmentSource,
    RecordedJudgmentSource,
    Recording,
)
from .types import Candidate

DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 8.0
QUESTION = "primary"
_INSTRUCTIONS = (
    "Select the single Skill whose guidance is warranted for the request. "
    "Choose no_skill when no listed Skill applies."
)
_NO_SKILL_CRITERION = "No Skill is warranted for this request."


class JevJudgmentSource:
    """Live Jev over the ranked Candidate set. ``client_factory(timeout)`` is injectable."""

    name = "jev"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        client_factory: Callable[[float], Any] | None = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._client_factory = client_factory or _typesafe_client

    def judge(self, request: str, candidates: Sequence[Candidate]) -> JudgmentCall:
        """Ask Jev one Choice and convert the answer into a validated-path claim."""
        question = _question(candidates)
        client = self._client_factory(self._timeout)
        try:
            response = client.system_one(state=request, questions={QUESTION: question},
                                         model=self._model)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        answer = _answer(response)
        labels = set(question["criteria"])
        if answer.choice not in labels:
            raise JudgmentError(f"Jev chose a label that was not offered: {answer.choice!r}")
        probabilities = dict(answer.probabilities)
        if set(probabilities) != labels:
            raise JudgmentError("Jev did not return a probability for every offered label")
        usage = getattr(response, "usage", None)
        return JudgmentCall(
            claim={
                "primary": None if answer.choice == NO_SKILL else answer.choice,
                "confidence": answer.confidence,
                "distribution": probabilities,
                "candidates": [candidate.id for candidate in candidates],
            },
            source=self.name,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def _question(candidates: Sequence[Candidate]) -> dict:
    """The one Choice: every Candidate by ID, plus the reserved no-skill option."""
    criteria: dict[str, str | None] = {candidate.id: candidate.name or None
                                       for candidate in candidates}
    criteria[NO_SKILL] = _NO_SKILL_CRITERION
    return {"type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}


def _answer(response: Any) -> Any:
    answer = getattr(response, "answers", {}).get(QUESTION)
    if answer is None:
        raise JudgmentError(f"Jev returned no {QUESTION!r} answer")
    return answer


def _typesafe_client(timeout: float) -> Any:
    from typesafe_sdk import RetryPolicy, TypeSafeClient  # only a live call needs the SDK

    # Exactly one attempt: the SDK's default policy retries twice inside a 30s budget, which
    # would put a live call at Hermes's hook-callback timeout instead of well under it.
    return TypeSafeClient(timeout=timeout, retry=RetryPolicy(max_retries=0, timeout=timeout))


def live_judgment_source(
    *,
    recording: Recording | None = None,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
    client_factory: Callable[[float], Any] | None = None,
) -> FallbackJudgmentSource:
    """The composed Source the Adapter injects: live Jev, then a Recording, then No-Skill.

    This is the runtime path for ticket #50's fallback: an expired, unavailable or misconfigured
    live source yields to the Recording, and a Recording that does not match the Case yields to
    an explicit No-Skill Outcome.
    """
    sources: list[JudgmentSource] = [JevJudgmentSource(model=model, timeout=timeout,
                                                      client_factory=client_factory)]
    if recording is not None:
        sources.append(RecordedJudgmentSource(recording))
    return FallbackJudgmentSource(*sources)
