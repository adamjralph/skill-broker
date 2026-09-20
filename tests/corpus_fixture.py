"""A synthetic Hermes ``state.db`` plus machine-local corpus roots for the corpus suite (#54).

Companion to :mod:`store_fixture`. The DB carries only the columns the extractor reads, so a
test stages sessions and turns directly instead of standing up Hermes. The corpus, label and
recording roots are temporary directories, so no test writes into the repo's committed
``corpus/`` or the operator's ``~/.config/skill-broker/corpus``.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_fixture import StoreFixture, make_store  # noqa: E402

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    profile_name TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    title TEXT,
    cwd TEXT,
    origin_json TEXT,
    message_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    display_kind TEXT,
    _compressed_summary INTEGER NOT NULL DEFAULT 0
);
"""


def create_db(path: Path) -> None:
    """Create an empty Hermes-shaped ``state.db`` at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def add_session(
    db: Path,
    session_id: str,
    source: str,
    *,
    profile_name: str | None = None,
    started_at: float = 0.0,
    title: str | None = None,
    cwd: str | None = None,
) -> None:
    """Insert one session row."""
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "INSERT INTO sessions (id, source, profile_name, started_at, title, cwd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, source, profile_name, started_at, title, cwd),
        )
        connection.commit()
    finally:
        connection.close()


def add_turn(
    db: Path,
    session_id: str,
    content: str,
    *,
    role: str = "user",
    timestamp: float = 0.0,
    active: int = 1,
    display_kind: str | None = None,
    compressed: int = 0,
) -> int:
    """Insert one message row; returns its id (the turn id)."""
    connection = sqlite3.connect(db)
    try:
        cursor = connection.execute(
            "INSERT INTO messages "
            "(session_id, role, content, timestamp, active, display_kind, _compressed_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, timestamp, active, display_kind, compressed),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


@dataclass
class CorpusFixture:
    """A temporary Hermes DB, Skill Store and the three corpus-side roots."""

    root: Path
    db: Path
    store: StoreFixture
    corpus: Path
    labels: Path
    recordings: Path
    _tmp: tempfile.TemporaryDirectory

    def close(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def __enter__(self) -> "CorpusFixture":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def make_corpus_fixture(*, profile: str | None = None, **store_overrides) -> CorpusFixture:
    """Build a temporary fixture; ``profile`` overrides the store fixture's policy profile."""
    tmp = tempfile.TemporaryDirectory(prefix="skill-broker-corpus-")
    root = Path(tmp.name)
    store = make_store(**(store_overrides if profile is None else {"profile": profile,
                                                                   **store_overrides}))
    db = root / "state.db"
    create_db(db)
    return CorpusFixture(
        root=root,
        db=db,
        store=store,
        corpus=root / "corpus",
        labels=root / "labels",
        recordings=root / "recordings",
        _tmp=tmp,
    )
