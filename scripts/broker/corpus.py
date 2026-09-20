"""Replay Corpus extraction and Reviewed Cases (ADR-0020, spec B14, ticket #54).

The **Replay Corpus** is the reviewed set of real request Cases used to evaluate routing and to
pre-register Soft Thresholds. Raw request text is the one thing replay needs in full
(ADR-0015), so the corpus is assembled by **per-source opt-in** and kept **machine-local and
uncommitted** — redacted of secrets and identifiers at extraction, deletable on request. The
repository keeps only this harness, the hand-corrected labels, and the SHA-bound Recordings.

A :class:`Case` is one request turn with its provenance and the profile's Authorised Closure at
extraction. The agent pre-labels; Adam hand-reviews, and only a :class:`ReviewedCase` — whose
ground truth is a Primary Skill or an explicit No-Skill Outcome — counts for evaluation and
threshold setting. A :class:`Recording` freezes a Jev outcome and binds it to its Case by
content hash, so replay is deterministic and offline. Reviewed Cases split deterministically
into a threshold-setting set and a disjoint held-out set. A profile too thin to reach the Stage
5 minimum is reported as staying in Shadow Mode, never padded from another profile.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import profile_policy as pp
import store_manifest as sm

from .closure import authorised_closure
from .judgment import NO_SKILL, RecordedJudgmentSource, Recording, RecordingMismatch
from .types import ResolvedSkillVersion

#: Channels whose turns are the gated profile's own requests and the agent-authored traffic:
#: in by default (ADR-0020). Every other source — including ``kanban`` and ``acp`` — is opt-in.
_AGENT_AUTHORED_SOURCES = frozenset({"cli", "cron", "subagent", "tool"})

#: Sources ADR-0020 holds back as third-party chat: never extracted without explicit consent.
HELD_BACK_SOURCES = frozenset({"telegram", "desktop"})

#: The defaults never consent a held-back source, even if the agent-authored set changes.
DEFAULT_CONSENTED_SOURCES = _AGENT_AUTHORED_SOURCES - HELD_BACK_SOURCES

#: User-role rows that are injected notices rather than requests, and so are not Cases.
NON_REQUEST_DISPLAY_KINDS = frozenset({"internal_notification"})

#: Machine-local corpus root (ADR-0020): raw request text and nothing else lives here.
DEFAULT_CORPUS_ROOT = Path("~/.config/skill-broker/corpus")

#: A provisional Stage 5 floor. The number itself is set at the Stage 5/6 boundary; until then
#: a profile below it is reported as staying in Shadow Mode rather than gated on thin data.
DEFAULT_STAGE5_MINIMUM = 20

#: The share of extracted Cases assigned to the disjoint held-out split.
DEFAULT_HELD_OUT_RATIO = 0.25

CORPUS_VERSION = 1
LABEL_VERSION = 1
THRESHOLD_SPLIT = "threshold"
HELD_OUT_SPLIT = "held_out"
_REPO_ROOT = Path(__file__).resolve().parents[2]


class CorpusError(Exception):
    """The corpus cannot be safely assembled or read."""


class ConsentError(CorpusError):
    """A source has not opted in, so its turns may not enter the corpus (ADR-0020)."""


class LabelError(CorpusError):
    """A reviewed outcome is malformed or names a Skill outside the Authorised Closure."""


@dataclass(frozen=True)
class Turn:
    """One Hermes user turn read from a ``state.db`` — a Case before redaction or closure."""

    session_id: str
    source: str
    profile_name: str | None
    started_at: float
    title: str | None
    cwd: str | None
    turn_id: str
    content: str
    timestamp: float
    display_kind: str | None


@dataclass(frozen=True)
class SkillUse:
    """One observed ``skill_view`` call, attributed to the user turn that preceded it.

    This is what the agent *actually used* in a conversation, the signal the Shadow Report
    compares a would-have-selected Route Decision against (spec B13, ticket #56).
    """

    session_id: str
    turn_id: str
    skill: str
    timestamp: float


@dataclass(frozen=True)
class Case:
    """One request turn with provenance and the Authorised Closure at extraction (ADR-0020)."""

    case_sha256: str
    profile: str
    request: str
    source: str
    session_id: str
    turn_id: str
    started_at: float
    extracted_at: str
    split: str
    authorised_closure: tuple[ResolvedSkillVersion, ...]
    cwd: str | None = None
    title: str | None = None
    prelabel: str | None = None

    def verify(self) -> bool:
        """Whether the stored content hash still matches the request text (drift check)."""
        return self.case_sha256 == digest(self.request)

    def with_prelabel(self, outcome: str | None) -> "Case":
        return replace(self, prelabel=outcome)

    def to_record(self) -> dict:
        return {
            "case_version": 1,
            "case_sha256": self.case_sha256,
            "profile": self.profile,
            "request": self.request,
            "source": self.source,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "started_at": self.started_at,
            "extracted_at": self.extracted_at,
            "split": self.split,
            "cwd": self.cwd,
            "title": self.title,
            "prelabel": self.prelabel,
            "authorised_closure": [entry.to_record() for entry in self.authorised_closure],
        }

    @classmethod
    def from_record(cls, record: Mapping) -> "Case":
        return cls(
            case_sha256=str(record["case_sha256"]),
            profile=str(record["profile"]),
            request=str(record["request"]),
            source=str(record["source"]),
            session_id=str(record["session_id"]),
            turn_id=str(record["turn_id"]),
            started_at=float(record.get("started_at", 0.0)),
            extracted_at=str(record.get("extracted_at", "")),
            split=str(record.get("split", THRESHOLD_SPLIT)),
            authorised_closure=tuple(
                ResolvedSkillVersion(id=str(entry["id"]), name=str(entry["name"]),
                                     version=str(entry["version"]))
                for entry in record.get("authorised_closure", ())
            ),
            cwd=record.get("cwd"),
            title=record.get("title"),
            prelabel=record.get("prelabel"),
        )


@dataclass(frozen=True)
class Review:
    """A hand-corrected ground-truth outcome for one Case (ADR-0020). Carries no request text."""

    case_sha256: str
    profile: str
    outcome: str
    reviewer: str
    reviewed_at: str

    @property
    def primary(self) -> str | None:
        return None if self.outcome == NO_SKILL else self.outcome

    def to_record(self) -> dict:
        return {
            "case_sha256": self.case_sha256,
            "outcome": self.outcome,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_record(cls, profile: str, record: Mapping) -> "Review":
        return cls(
            case_sha256=str(record["case_sha256"]),
            profile=profile,
            outcome=str(record["outcome"]),
            reviewer=str(record.get("reviewer", "")),
            reviewed_at=str(record.get("reviewed_at", "")),
        )


@dataclass(frozen=True)
class ReviewedCase:
    """A Case whose ground truth has been corrected by hand — the only unit evaluation counts."""

    case: Case
    review: Review

    @property
    def outcome(self) -> str:
        return self.review.outcome

    @property
    def primary(self) -> str | None:
        return self.review.primary

    @property
    def split(self) -> str:
        return self.case.split


@dataclass(frozen=True)
class ExtractionReport:
    """What one profile's extraction did, including every source it refused."""

    profile: str
    extracted: int
    duplicates: int
    skipped: int
    skipped_other_profile: int
    redactions: int
    refused: dict[str, int]
    closure_size: int
    corpus: str

    def to_record(self) -> dict:
        return {
            "ok": True,
            "profile": self.profile,
            "extracted": self.extracted,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "skipped_other_profile": self.skipped_other_profile,
            "redactions": self.redactions,
            "refused": dict(sorted(self.refused.items())),
            "closure_size": self.closure_size,
            "corpus": self.corpus,
        }


@dataclass(frozen=True)
class ProfileStatus:
    """One profile's readiness for the Stage 5 gate: reviewed counts and the minimum report."""

    profile: str
    cases: int
    reviewed: int
    threshold_reviewed: int
    held_out_reviewed: int
    minimum: int
    below_minimum: bool
    stage: str
    message: str

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "cases": self.cases,
            "reviewed": self.reviewed,
            "threshold_reviewed": self.threshold_reviewed,
            "held_out_reviewed": self.held_out_reviewed,
            "minimum": self.minimum,
            "below_minimum": self.below_minimum,
            "stage": self.stage,
            "message": self.message,
        }


@dataclass(frozen=True)
class RetentionReport:
    """What raw request text the machine-local corpus still holds, for the Stage 5/6 review."""

    now: str
    profiles: tuple[dict, ...]

    def to_record(self) -> dict:
        return {"ok": True, "now": self.now, "profiles": list(self.profiles)}


# --------------------------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------------------------

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "<private-key>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), "<jwt>"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}\b"), "<secret>"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}\b"), "<secret>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "<secret>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "<secret>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<secret>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "<secret>"),
    (re.compile(r"\bya29\.[0-9A-Za-z_-]{10,}\b"), "<secret>"),
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password"
                r"|passwd|pwd)\b\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?"), r"\1=<redacted>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
)
_PHONE = re.compile(r"(?<!\d)\+?\d[\d ().-]{6,}\d(?!\d)")
_LONG_ID = re.compile(r"(?<!\d)\d{7,}(?!\d)")


def redact(text: str) -> str:
    """Remove secrets and identifiers from one request's text (ADR-0020)."""
    return _redact(text)[0]


def _redact(text: str) -> tuple[str, int]:
    """Return ``(redacted, redaction_count)``. Deterministic and order-independent."""
    count = 0
    for pattern, replacement in _REDACTIONS:
        text, found = pattern.subn(replacement, text)
        count += found
    text, found = _LONG_ID.subn("<id>", text)
    count += found
    text, found = _PHONE.subn(_phone_replacement, text)
    count += found
    return text, count


def _phone_replacement(match: re.Match[str]) -> str:
    """Redact a phone-shaped run only when it holds enough digits to be one, not a date."""
    candidate = match.group(0)
    digits = sum(1 for character in candidate if character.isdigit())
    return candidate if digits < 9 else "<phone>"


# --------------------------------------------------------------------------------------------
# Splits and digests
# --------------------------------------------------------------------------------------------


def digest(text: str) -> str:
    """The content hash a Case and its Recording share (sha256 over the request text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_for(case_sha256: str, *, held_out_ratio: float = DEFAULT_HELD_OUT_RATIO) -> str:
    """Assign a Case to ``threshold`` or ``held_out`` deterministically from its hash.

    The agent assigns the split at extraction and records it (ADR-0020), so the same Case always
    lands in the same split and the held-out set is disjoint from the threshold-setting set.
    """
    ratio = min(1.0, max(0.0, float(held_out_ratio)))
    bucket = int(case_sha256[:8], 16) / 0xFFFFFFFF
    return HELD_OUT_SPLIT if bucket < ratio else THRESHOLD_SPLIT


# --------------------------------------------------------------------------------------------
# Reading Hermes sessions
# --------------------------------------------------------------------------------------------

_TURN_QUERY = """
SELECT m.id AS turn_id, m.session_id, m.content, m.timestamp, m.display_kind,
       s.source, s.profile_name, s.started_at, s.title, s.cwd
FROM messages m JOIN sessions s ON s.id = m.session_id
WHERE m.role = 'user' AND m.active = 1 AND m._compressed_summary = 0
ORDER BY s.started_at, m.timestamp, m.id
"""

_USER_TURN_IDS_QUERY = """
SELECT m.id, m.session_id, m.timestamp
FROM messages m
WHERE m.role = 'user' AND m.active = 1 AND m._compressed_summary = 0
ORDER BY m.session_id, m.timestamp, m.id
"""

_SKILL_CALL_QUERY = """
SELECT session_id, timestamp, tool_calls
FROM messages
WHERE tool_calls IS NOT NULL AND tool_calls != '' AND active = 1
ORDER BY session_id, timestamp, id
"""


def sessions_db(profile: str, hermes_home: Path | str = Path("~/.hermes")) -> Path:
    """Where a profile's Hermes sessions live: its own ``state.db``, or the root for default."""
    home = Path(hermes_home).expanduser()
    if not profile or profile == "default":
        return home / "state.db"
    return home / "profiles" / profile / "state.db"


def read_turns(db: Path | str) -> list[Turn]:
    """Every request-candidate user turn in a Hermes ``state.db``, in time order.

    Injected notices and compaction summaries are not requests and are left out here; the
    per-source opt-in and profile attribution are applied by :func:`extract_cases`.
    """
    path = Path(db)
    if not path.exists():
        raise CorpusError(f"sessions database not found: {path}")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CorpusError(f"cannot open sessions database {path}: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(_TURN_QUERY).fetchall()
    except sqlite3.Error as exc:
        raise CorpusError(f"cannot read sessions database {path}: {exc}") from exc
    finally:
        connection.close()
    turns: list[Turn] = []
    for row in rows:
        if row["display_kind"] in NON_REQUEST_DISPLAY_KINDS:
            continue
        turns.append(Turn(
            session_id=str(row["session_id"]),
            source=str(row["source"]),
            profile_name=row["profile_name"],
            started_at=float(row["started_at"]),
            title=row["title"],
            cwd=row["cwd"],
            turn_id=str(row["turn_id"]),
            content=row["content"] or "",
            timestamp=float(row["timestamp"]),
            display_kind=row["display_kind"],
        ))
    return turns


def read_skill_use(db: Path | str) -> list[SkillUse]:
    """Every ``skill_view`` call in a Hermes ``state.db``, attributed to its user turn.

    A call belongs to the most recent user turn in its session at or before the call's time, so
    a Route Decision keyed by ``session_id``/``turn_id`` can be compared with what the agent
    actually loaded. The skill is the Name the call passed, deduplicated per turn.
    """
    path = Path(db)
    if not path.exists():
        raise CorpusError(f"sessions database not found: {path}")
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CorpusError(f"cannot open sessions database {path}: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        turns = connection.execute(_USER_TURN_IDS_QUERY).fetchall()
        calls = connection.execute(_SKILL_CALL_QUERY).fetchall()
    except sqlite3.Error as exc:
        raise CorpusError(f"cannot read sessions database {path}: {exc}") from exc
    finally:
        connection.close()

    by_session: dict[str, list[tuple[float, str]]] = {}
    for row in turns:
        by_session.setdefault(str(row["session_id"]), []).append(
            (float(row["timestamp"]), str(row["id"])))
    for session_turns in by_session.values():
        session_turns.sort()

    uses: list[SkillUse] = []
    seen: set[tuple[str, str, str]] = set()
    for row in calls:
        session_id = str(row["session_id"])
        timestamp = float(row["timestamp"])
        for skill in _skill_names(row["tool_calls"]):
            turn_id = _turn_at_or_before(by_session.get(session_id, ()), timestamp)
            if turn_id is None or (session_id, turn_id, skill) in seen:
                continue
            seen.add((session_id, turn_id, skill))
            uses.append(SkillUse(session_id=session_id, turn_id=turn_id, skill=skill,
                                 timestamp=timestamp))
    return uses


def _skill_names(blob: object) -> list[str]:
    """The Skill Names one assistant message's ``tool_calls`` JSON loaded through ``skill_view``."""
    try:
        calls = json.loads(blob) if isinstance(blob, str) else blob
    except ValueError:
        return []
    if not isinstance(calls, list):
        return []
    names: list[str] = []
    for call in calls:
        function = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(function, Mapping) or function.get("name") != "skill_view":
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            continue
        name = arguments.get("name") if isinstance(arguments, Mapping) else None
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _turn_at_or_before(turns: Sequence[tuple[float, str]], timestamp: float) -> str | None:
    """The id of the latest turn at or before ``timestamp``, or ``None`` when there is none."""
    found: str | None = None
    for turn_timestamp, turn_id in turns:
        if turn_timestamp > timestamp:
            break
        found = turn_id
    return found


def _belongs_to(turn: Turn, profile: str) -> bool:
    """Whether a turn is this profile's own request.

    A named ``profile_name`` must match, so a shared or root ``state.db`` can never lend its
    default-profile turns to a named profile (ADR-0020: no pooled corpus). A turn that Hermes
    left untagged belongs only to the default profile.
    """
    name = (turn.profile_name or "").strip()
    if name:
        return name == profile
    return not profile or profile == "default"


def _authorised_closure(store: Path | str, profile: str) -> tuple[ResolvedSkillVersion, ...]:
    """The profile's Authorised Closure at extraction, or a fail-closed :class:`CorpusError`."""
    store_path = Path(store)
    problems = sm.verify(store_path)
    if problems:
        raise CorpusError("store manifest does not verify: " + "; ".join(problems))
    identities = {row["id"]: row for row in sm.build(store_path)["identities"]}
    path = store_path / pp.POLICIES_DIR / f"{profile}.json"
    if not path.exists():
        raise CorpusError(f"policy_missing for profile {profile!r}: {path}")
    try:
        policy = pp.load(path)
    except (OSError, ValueError, pp.PolicyError) as exc:
        raise CorpusError(f"policy_invalid for profile {profile!r}: {exc}") from exc
    problems = pp.validate(policy, identities, filename=profile)
    if problems:
        raise CorpusError(f"policy_invalid for profile {profile!r}: " + "; ".join(problems))
    closure, problems = authorised_closure(policy, identities)
    if problems:
        raise CorpusError(
            f"authorised closure does not resolve for {profile!r}: " + "; ".join(problems))
    return closure


def extract_cases(
    profile: str,
    *,
    store: Path | str,
    db: Path | str,
    corpus: "CorpusStore",
    consent: Collection[str] = DEFAULT_CONSENTED_SOURCES,
    held_out_ratio: float = DEFAULT_HELD_OUT_RATIO,
    now: str | None = None,
) -> ExtractionReport:
    """Extract one profile's opted-in request turns into the machine-local corpus.

    A source that has not opted in is refused and counted, never extracted (ADR-0020). Request
    text is redacted before it is stored, the profile's Authorised Closure is recorded on every
    Case, and the split is assigned at extraction. Any failure to resolve the closure fails the
    whole extraction closed, so no Case is stored without the profile's policy behind it.
    """
    timestamp = now or _now()
    consent = frozenset(consent)
    closure = _authorised_closure(store, profile)
    corpus.record_consent(consent, created_at=timestamp)

    extracted = duplicates = skipped = skipped_other = redactions = 0
    refused: dict[str, int] = {}
    for turn in read_turns(db):
        if not _belongs_to(turn, profile):
            skipped_other += 1
            continue
        if turn.source not in consent:
            refused[turn.source] = refused.get(turn.source, 0) + 1
            continue
        text, redacted = _redact(turn.content)
        if not text.strip():
            skipped += 1
            continue
        case_sha256 = digest(text)
        if corpus.get(profile, case_sha256) is not None:
            duplicates += 1
            continue
        case = Case(
            case_sha256=case_sha256,
            profile=profile,
            request=text,
            source=turn.source,
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            started_at=turn.started_at,
            extracted_at=timestamp,
            split=split_for(case_sha256, held_out_ratio=held_out_ratio),
            authorised_closure=closure,
            cwd=turn.cwd,
            title=turn.title,
        )
        corpus.add(case)
        extracted += 1
        redactions += redacted

    return ExtractionReport(
        profile=profile,
        extracted=extracted,
        duplicates=duplicates,
        skipped=skipped,
        skipped_other_profile=skipped_other,
        redactions=redactions,
        refused=refused,
        closure_size=len(closure),
        corpus=str(corpus.root),
    )


# --------------------------------------------------------------------------------------------
# The machine-local Case store
# --------------------------------------------------------------------------------------------


class CorpusStore:
    """The machine-local home of raw request text: ``cases/<profile>/<sha>.json`` (ADR-0020).

    The index records the opted-in source set and the date consent was given. Adding a Case
    whose source is not in that set is refused here as well as at extraction, so the consent
    boundary holds even if a caller drives the store directly.
    """

    def __init__(self, root: Path | str = DEFAULT_CORPUS_ROOT) -> None:
        resolved = Path(root).expanduser()
        if _inside(resolved, _REPO_ROOT):
            raise CorpusError(
                "the corpus root holds raw request text and must stay outside the repository "
                "(ADR-0020); labels and Recordings live in corpus/ instead")
        self.root = resolved

    # -- index -------------------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.root / "corpus.json"

    def index(self) -> dict:
        if not self.index_path.exists():
            return {"corpus_version": CORPUS_VERSION, "created_at": None, "consented_at": None,
                    "consented_sources": []}
        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CorpusError(f"corpus index is not valid JSON: {exc}") from exc
        if not isinstance(index, dict):
            raise CorpusError("corpus index must be a JSON object")
        return index

    def record_consent(self, sources: Iterable[str],
                       *, created_at: str | None = None) -> dict:
        """Widen the recorded opted-in source set and stamp the consent date. Never narrows."""
        index = self.index()
        consented = sorted(set(index.get("consented_sources", ())) | set(sources))
        index.update({
            "corpus_version": CORPUS_VERSION,
            "consented_sources": consented,
            "consented_at": created_at or index.get("consented_at") or _now(),
            "created_at": index.get("created_at") or created_at or _now(),
        })
        self._write_json(self.index_path, index)
        return index

    # -- cases -------------------------------------------------------------------------

    def case_path(self, profile: str, case_sha256: str) -> Path:
        return self.root / "cases" / profile / f"{case_sha256}.json"

    def add(self, case: Case) -> Path:
        """Store one Case, refusing an unconsented source or a Case whose hash does not hold."""
        if not case.verify():
            raise CorpusError(f"case {case.case_sha256} does not match its request text")
        if case.source not in self.index().get("consented_sources", ()):
            raise ConsentError(
                f"source {case.source!r} has not opted in; refusing to store its request text")
        path = self.case_path(case.profile, case.case_sha256)
        self._write_json(path, case.to_record())
        return path

    def get(self, profile: str, case_sha256: str) -> Case | None:
        path = self.case_path(profile, case_sha256)
        if not path.exists():
            return None
        case = Case.from_record(json.loads(path.read_text(encoding="utf-8")))
        if not case.verify():
            raise CorpusError(f"case {case_sha256} has drifted from its request text")
        return case

    def profiles(self) -> list[str]:
        cases = self.root / "cases"
        if not cases.is_dir():
            return []
        return sorted(path.name for path in cases.iterdir() if path.is_dir())

    def cases(self, profile: str | None = None) -> list[Case]:
        cases_dir = self.root / "cases"
        if not cases_dir.is_dir():
            return []
        profiles = [profile] if profile is not None else self.profiles()
        found: list[Case] = []
        for name in profiles:
            for path in sorted((cases_dir / name).glob("*.json")):
                found.append(Case.from_record(json.loads(path.read_text(encoding="utf-8"))))
        return found

    def count(self, profile: str) -> int:
        """How many Cases the profile has, without reading any request text."""
        directory = self.root / "cases" / profile
        return len(list(directory.glob("*.json"))) if directory.is_dir() else 0

    def set_prelabel(self, profile: str, case_sha256: str, outcome: str | None) -> Case:
        """Record the agent's suggested outcome on the machine-local Case (never ground truth)."""
        case = self.get(profile, case_sha256)
        if case is None:
            raise CorpusError(f"no case {case_sha256} for profile {profile!r}")
        updated = case.with_prelabel(outcome)
        self._write_json(self.case_path(profile, case_sha256), updated.to_record())
        return updated

    # -- deletion ----------------------------------------------------------------------

    def purge_case(self, profile: str, case_sha256: str) -> bool:
        path = self.case_path(profile, case_sha256)
        if not path.exists():
            return False
        path.unlink()
        return True

    def purge_source(self, source: str) -> int:
        removed = 0
        for profile in self.profiles():
            for case in self.cases(profile):
                if case.source == source:
                    removed += int(self.purge_case(profile, case.case_sha256))
        return removed

    def revoke_source(self, source: str) -> int:
        """Delete a source's Cases and remove its consent, as revocation requires (ADR-0020)."""
        removed = self.purge_source(source)
        index = self.index()
        index["consented_sources"] = sorted(set(index.get("consented_sources", ())) - {source})
        self._write_json(self.index_path, index)
        return removed

    def purge_profile(self, profile: str) -> int:
        directory = self.root / "cases" / profile
        if not directory.is_dir():
            return 0
        removed = len(list(directory.glob("*.json")))
        for path in directory.glob("*.json"):
            path.unlink()
        directory.rmdir()
        return removed

    def _write_json(self, path: Path, record: Mapping) -> None:
        write_canonical(path, record)


# --------------------------------------------------------------------------------------------
# The committed reviewed-label store
# --------------------------------------------------------------------------------------------


class LabelStore:
    """Hand-corrected ground truth, committed to the repository as ``<profile>.json``.

    A label names a Case by content hash and its outcome by Primary Skill ID or ``no_skill``;
    it never carries request text (ADR-0020). Only these labels let a Case count for evaluation.
    """

    def __init__(self, root: Path | str = _REPO_ROOT / "corpus" / "labels") -> None:
        self.root = Path(root).expanduser()

    def path(self, profile: str) -> Path:
        return self.root / f"{profile}.json"

    def reviews(self, profile: str) -> dict[str, Review]:
        path = self.path(profile)
        if not path.exists():
            return {}
        record = json.loads(path.read_text(encoding="utf-8"))
        return {str(entry["case_sha256"]): Review.from_record(profile, entry)
                for entry in record.get("reviews", ())}

    def review(self, case: Case, outcome: str, *, reviewer: str = "adam",
               reviewed_at: str | None = None) -> Review:
        """Record the hand-corrected outcome for one Case, validating it against the closure."""
        if outcome != NO_SKILL and outcome not in {entry.id for entry in case.authorised_closure}:
            raise LabelError(
                f"outcome {outcome!r} is neither {NO_SKILL!r} nor in the Case's "
                f"Authorised Closure")
        review = Review(case_sha256=case.case_sha256, profile=case.profile, outcome=outcome,
                        reviewer=reviewer, reviewed_at=reviewed_at or _now())
        reviews = self.reviews(case.profile)
        reviews[case.case_sha256] = review
        self._write(case.profile, reviews)
        return review

    def forget(self, profile: str, case_sha256: str) -> bool:
        reviews = self.reviews(profile)
        if case_sha256 not in reviews:
            return False
        del reviews[case_sha256]
        self._write(profile, reviews)
        return True

    def _write(self, profile: str, reviews: Mapping[str, Review]) -> None:
        record = {
            "label_version": LABEL_VERSION,
            "profile": profile,
            "reviews": [reviews[sha].to_record() for sha in sorted(reviews)],
        }
        path = self.path(profile)
        write_canonical(path, record)


def load_reviewed_cases(corpus: CorpusStore, labels: LabelStore, profile: str,
                        *, split: str | None = None) -> list[ReviewedCase]:
    """The profile's Reviewed Cases, optionally only one split, ordered by content hash."""
    cases = {case.case_sha256: case for case in corpus.cases(profile)}
    reviews = labels.reviews(profile)
    reviewed: list[ReviewedCase] = []
    for case_sha256 in sorted(reviews):
        case = cases.get(case_sha256)
        if case is None:
            continue  # a deleted case leaves its committed label harmless
        if split is not None and case.split != split:
            continue
        reviewed.append(ReviewedCase(case=case, review=reviews[case_sha256]))
    return reviewed


def profile_status(corpus: CorpusStore, labels: LabelStore, profile: str, *,
                   minimum: int = DEFAULT_STAGE5_MINIMUM) -> ProfileStatus:
    """Report one profile's reviewed supply against the Stage 5 minimum.

    A profile below the minimum is reported as staying in Shadow Mode. Nothing is borrowed from
    another profile: only this profile's Cases and labels are read (ADR-0020).
    """
    reviewed = load_reviewed_cases(corpus, labels, profile)
    threshold = sum(1 for entry in reviewed if entry.split == THRESHOLD_SPLIT)
    held_out = sum(1 for entry in reviewed if entry.split == HELD_OUT_SPLIT)
    below = len(reviewed) < minimum
    if below:
        message = (f"{profile}: {len(reviewed)} reviewed case(s), below the Stage 5 minimum of "
                   f"{minimum}; stays in Shadow Mode rather than gated on thin data")
    else:
        message = (f"{profile}: {len(reviewed)} reviewed case(s) meet the Stage 5 minimum of "
                   f"{minimum}")
    return ProfileStatus(
        profile=profile,
        cases=corpus.count(profile),
        reviewed=len(reviewed),
        threshold_reviewed=threshold,
        held_out_reviewed=held_out,
        minimum=minimum,
        below_minimum=below,
        stage="shadow" if below else "gateable",
        message=message,
    )


# --------------------------------------------------------------------------------------------
# The committed, SHA-bound Recording store
# --------------------------------------------------------------------------------------------


class RecordingStore:
    """Frozen Jev outcomes committed as ``recordings/<profile>/<sha>.json`` (ADR-0020).

    A Recording is bound to its Case by content hash, so replay is deterministic and offline; a
    Recording whose Case has drifted is refused rather than replayed.
    """

    def __init__(self, root: Path | str = _REPO_ROOT / "corpus" / "recordings") -> None:
        self.root = Path(root).expanduser()

    def path(self, profile: str, case_sha256: str) -> Path:
        return self.root / profile / f"{case_sha256}.json"

    def write(self, case: Case, claim: Mapping) -> Recording:
        """Freeze a Jev claim against one Case, binding it by content hash."""
        if not case.verify():
            raise CorpusError(f"case {case.case_sha256} does not match its request text")
        recording = Recording.of(case.request, claim)
        if recording.case_sha256 != case.case_sha256:
            raise CorpusError(f"recording does not bind to case {case.case_sha256}")
        path = self.path(case.profile, case.case_sha256)
        write_canonical(path, recording.to_record())
        return recording

    def get(self, profile: str, case_sha256: str) -> Recording | None:
        path = self.path(profile, case_sha256)
        if not path.exists():
            return None
        return Recording.from_record(json.loads(path.read_text(encoding="utf-8")))

    def bind(self, case: Case) -> RecordedJudgmentSource:
        """A Source that replays this Case's Recording, or refuses a missing or drifted one."""
        if not case.verify():
            raise RecordingMismatch(
                f"case {case.case_sha256} has drifted from its request text; refusing replay")
        recording = self.get(case.profile, case.case_sha256)
        if recording is None:
            raise RecordingMismatch(f"no recording for case {case.case_sha256}")
        if recording.case_sha256 != case.case_sha256 or not recording.matches(case.request):
            raise RecordingMismatch(f"recording does not bind to case {case.case_sha256}")
        return RecordedJudgmentSource(recording)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_canonical(path: Path, record: Mapping) -> None:
    """Write one JSON record deterministically: sorted keys, stable indent, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")


def retention_report(corpus: CorpusStore, *, now: str | None = None) -> RetentionReport:
    """Report raw-text retention per profile, the input to the Stage 5/6 retention review."""
    stamp = now or _now()
    profiles: list[dict] = []
    for profile in corpus.profiles():
        cases = corpus.cases(profile)
        sources: dict[str, int] = {}
        for case in cases:
            sources[case.source] = sources.get(case.source, 0) + 1
        profiles.append({
            "profile": profile,
            "cases": len(cases),
            "sources": dict(sorted(sources.items())),
            "oldest_extracted_at": min((case.extracted_at for case in cases), default=None),
        })
    return RetentionReport(now=stamp, profiles=tuple(profiles))


def _inside(path: Path, parent: Path) -> bool:
    """Whether ``path`` sits at or under ``parent``, resolving both without requiring existence."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


__all__ = [
    "Case",
    "ConsentError",
    "CorpusError",
    "CorpusStore",
    "DEFAULT_CONSENTED_SOURCES",
    "DEFAULT_CORPUS_ROOT",
    "DEFAULT_HELD_OUT_RATIO",
    "DEFAULT_STAGE5_MINIMUM",
    "ExtractionReport",
    "HELD_BACK_SOURCES",
    "HELD_OUT_SPLIT",
    "LABEL_VERSION",
    "LabelError",
    "LabelStore",
    "NO_SKILL",
    "NON_REQUEST_DISPLAY_KINDS",
    "ProfileStatus",
    "RecordedJudgmentSource",
    "Recording",
    "RecordingStore",
    "RetentionReport",
    "Review",
    "ReviewedCase",
    "THRESHOLD_SPLIT",
    "Turn",
    "digest",
    "extract_cases",
    "load_reviewed_cases",
    "profile_status",
    "read_turns",
    "redact",
    "retention_report",
    "sessions_db",
    "split_for",
    "write_canonical",
]
