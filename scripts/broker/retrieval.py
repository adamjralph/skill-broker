"""Deterministic, model-free Candidate Retrieval over the Authorised Closure (ADR-0013).

Tier 0 is an exact ID, Name or Alias mention in the request; it always ranks first and lexical
scoring cannot displace it. Every other Candidate is then ordered by Okapi BM25 over its Name,
Aliases, description and Hermes tags — never its body. The set is capped at ``CANDIDATE_LIMIT``
so the Judgment sees a small shortlist, and the ordering is total and stable: exact first, then
descending score, then ID.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

from .types import Candidate

CANDIDATE_LIMIT = 12
_K1 = 1.2
_B = 0.75
_WORD = re.compile(r"[a-z0-9]+")
_BOUNDARY = r"(?<![a-z0-9]){}(?![a-z0-9])"


@dataclass(frozen=True)
class CandidateProfile:
    """The metadata Retrieval may read for one Candidate; never the Skill body."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    tags: tuple[str, ...] = ()

    @property
    def handles(self) -> tuple[str, ...]:
        return (self.id, self.name, *self.aliases)

    @property
    def lexical_text(self) -> str:
        return " ".join((self.name, *self.aliases, self.description, *self.tags))


def rank(request: str, profiles: Sequence[CandidateProfile], *,
         limit: int = CANDIDATE_LIMIT) -> tuple[Candidate, ...]:
    """The ranked, capped Candidate set for one request over these profiles."""
    lowered = request.lower()
    documents = [_tokens(profile.lexical_text) for profile in profiles]
    scores = _bm25(_tokens(request), documents)
    candidates = [
        Candidate(id=profile.id, name=profile.name, score=round(scores[index], 6),
                  exact=any(_mentions(lowered, handle) for handle in profile.handles))
        for index, profile in enumerate(profiles)
    ]
    candidates.sort(key=lambda candidate: (not candidate.exact, -candidate.score, candidate.id))
    return tuple(candidates[:limit])


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _mentions(request_lower: str, handle: str) -> bool:
    return re.search(_BOUNDARY.format(re.escape(handle.lower())), request_lower) is not None


def _bm25(query: Sequence[str], documents: Sequence[Sequence[str]]) -> list[float]:
    """Okapi BM25 with the usual parameters. Terms are walked in sorted order so the float
    summation — and therefore the recorded scores — is identical across processes."""
    scores = [0.0] * len(documents)
    if not documents:
        return scores
    average_length = sum(len(document) for document in documents) / len(documents)
    for term in sorted(set(query)):
        frequency = sum(1 for document in documents if term in document)
        if not frequency:
            continue
        inverse_document_frequency = math.log(
            1 + (len(documents) - frequency + 0.5) / (frequency + 0.5))
        for index, document in enumerate(documents):
            occurrences = document.count(term)
            if not occurrences:
                continue
            length_ratio = len(document) / average_length if average_length else 0.0
            denominator = occurrences + _K1 * (1 - _B + _B * length_ratio)
            scores[index] += (inverse_document_frequency * occurrences * (_K1 + 1)
                              / denominator)
    return scores
