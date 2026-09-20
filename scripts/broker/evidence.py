"""The broker's evidence collaborators: the durable Evidence Log and the Session Ledger.

Neither grants anything. The Evidence Log is append-only and holds metadata and hashes
(ADR-0015); the Session Ledger records content hashes already supplied in one conversation and
exists only to suppress duplicate intervention (ADR-0014).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from .types import RouteDecision


def append_jsonl(path: Path | str, record: dict) -> None:
    """Append one JSON record to a ``.jsonl`` file, creating parents; never rewrites a line."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False))
        handle.write("\n")


@runtime_checkable
class EvidenceLog(Protocol):
    """Where a turn's Route Decision is durably appended."""

    def append(self, decision: RouteDecision) -> None: ...


class JsonlEvidenceLog:
    """The durable Evidence Log: one Route Decision per line, appended and never rewritten."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, decision: RouteDecision) -> None:
        append_jsonl(self.path, decision.to_record())


class SessionLedger:
    """In-session content hashes already supplied; evidence for dedup, never permission."""

    def __init__(self) -> None:
        self._supplied: dict[str, set[str]] = {}

    def supplied(self, session_id: str) -> frozenset[str]:
        return frozenset(self._supplied.get(session_id, ()))

    def remember(self, session_id: str, hashes: Iterable[str]) -> None:
        self._supplied.setdefault(session_id, set()).update(hashes)
