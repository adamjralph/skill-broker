"""The live Jev Judgment Source: one TypeSafe System One call per turn (ticket #50).

Jev answers exactly one ``Choice`` over the ranked Candidates plus the reserved ``no_skill``
label, from the request as state. The answer is converted into the same claim the recorded and
stub paths produce, so the shared validator and the deterministic Grant treat every source
identically; nothing outside the bounded, already-authorised Candidate set is ever sent.

The Source is deliberately thin and untuned — Jev prompt and criteria engineering is carried
fog. It enforces a timeout well under Hermes's hook-callback timeout (30s default) and raises on
anything unusable, so the Adapter can compose it with a recorded fallback and a No-Skill floor
rather than letting a model outage block a turn (ADR-0004, ``FallbackJudgmentSource``).

The live client is a direct, single-attempt HTTP call to TypeSafe System One — no SDK dependency —
so the Hermes runtime needs no package a venv rebuild would prune. The seam (``client_factory``)
stays injectable, so the suites and an offline Consumer never make a network call.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
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
#: TypeSafe's API root; ``TYPESAFE_BASE_URL`` overrides it.
DEFAULT_BASE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"
API_KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL_ENV = "TYPESAFE_BASE_URL"
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
        self._client_factory = client_factory or _http_client

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


class _HttpJevClient:
    """A minimal TypeSafe System One client: one bounded POST, no SDK dependency.

    A direct call keeps the Hermes runtime free of an extra package a venv rebuild would prune
    (``uv sync --locked``), while the seam (``client_factory``) stays injectable for tests. The
    response is adapted to the same attribute shape the SDK returns, so :meth:`JevJudgmentSource.judge`
    treats every client identically.
    """

    def __init__(self, *, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float,
                 opener: Callable[..., Any] | None = None) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def system_one(self, *, state: str, questions: Mapping, model: str) -> Any:
        body = json.dumps({"state": state, "model": model, "questions": questions}).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{SYSTEM_ONE_PATH}", data=body, method="POST",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json", "Accept": "application/json"})
        with self._opener(request, timeout=self._timeout) as response:
            return _as_response(json.loads(response.read().decode("utf-8")))

    def close(self) -> None:
        return None


def _as_response(payload: Mapping) -> Any:
    """Adapt the wire JSON to the SDK's ``.answers``/``.usage`` attribute shape."""
    answers = {str(name): SimpleNamespace(**value)
               for name, value in (payload.get("answers") or {}).items()}
    usage = payload.get("usage") or {}
    return SimpleNamespace(answers=answers, usage=SimpleNamespace(**usage))


def _http_client(timeout: float) -> _HttpJevClient:
    """The default live client: the API key from the environment, one bounded HTTP call.

    Raises :class:`JudgmentError` when the key is unset, so the composed source falls back to its
    Recording and then to No-Skill rather than making an unauthenticated call (ADR-0004).
    """
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise JudgmentError(f"{API_KEY_ENV} is not set; a live Jev call needs an API key")
    base_url = os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL
    return _HttpJevClient(api_key=api_key, base_url=base_url, timeout=timeout)


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
