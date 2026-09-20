"""Broker-side test doubles and helpers (ticket #46).

Companion to :mod:`store_fixture`: the store fixture provides the real artifacts, this module
provides the collaborators a broker test injects so it can observe the Route Decision without a
durable log, plus the small ways a test breaks a store on purpose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_fixture import StoreFixture  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import store_manifest as sm  # noqa: E402
from broker.judgment import JudgmentCall  # noqa: E402


class RecordingEvidenceLog:
    """An in-memory ``EvidenceLog``: keeps every Route Decision it is handed, in order."""

    def __init__(self) -> None:
        self.decisions: list = []

    def append(self, decision) -> None:
        self.decisions.append(decision)

    @property
    def records(self) -> list[dict]:
        return [decision.to_record() for decision in self.decisions]


class ScriptedJudgmentSource:
    """A deterministic ``JudgmentSource`` returning one scripted claim per turn, in order."""

    name = "scripted"

    def __init__(self, *claims) -> None:
        self.claims = list(claims)

    def judge(self, request, candidates):
        return JudgmentCall(claim=self.claims.pop(0), source=self.name)


class TamperingJudgmentSource:
    """Answers one claim, corrupting a Skill's ``SKILL.md`` first.

    The Judgment Source is the one collaborator called between the Store Manifest verification
    and Pack assembly, so it is where a test can stage delivery-time drift.
    """

    name = "tampering"

    def __init__(self, claim, skill_md: Path, text: str = "\nTampered after verification.\n") -> None:
        self.claim = claim
        self.skill_md = Path(skill_md)
        self.text = text

    def judge(self, request, candidates):
        self.skill_md.write_text(self.skill_md.read_text() + self.text)
        return JudgmentCall(claim=self.claim, source=self.name)


def judgment_claim(candidate_ids, *, primary, confidence=0.9, no_skill=0.1):
    """A well-formed Choice over these Candidates, echoing them and the reserved no-skill.

    ``confidence`` (or ``no_skill`` when the Choice is no-skill) is the chosen option's
    probability; the remainder is spread evenly across the other options.
    """
    ids = list(candidate_ids)
    if primary is None:
        others = list(ids)
        share = round((1.0 - no_skill) / len(others), 6) if others else 0.0
        distribution = {ident: share for ident in others}
        distribution["no_skill"] = round(1.0 - share * len(others), 6)
        chosen = distribution["no_skill"]
    else:
        others = [ident for ident in ids if ident != primary]
        share = round((1.0 - confidence) / (len(others) + 1), 6)
        distribution = {ident: share for ident in others}
        distribution["no_skill"] = share
        distribution[primary] = round(1.0 - share * (len(others) + 1), 6)
        chosen = distribution[primary]
    return {"primary": primary, "confidence": chosen, "distribution": distribution,
            "candidates": ids}


def stale_manifest(fixture: StoreFixture) -> None:
    """Make the committed Store Manifest disagree with the store on disk."""
    skill_md = next(fixture.root.glob("*/*/SKILL.md"))
    skill_md.write_text(skill_md.read_text() + "\nDrifted.\n")


def remove_policy(fixture: StoreFixture) -> None:
    fixture.policy_path.unlink()


def corrupt_policy(fixture: StoreFixture) -> None:
    """Write a policy that is valid JSON but fails the policy validator."""
    fixture.policy_path.write_text(json.dumps({"policy_version": 1, "profile": fixture.profile,
                                               "foundation": [], "surprise": True}))


def add_progressive_disclosure(fixture: StoreFixture, identity, *,
                               content: str = "PROGRESSIVE-DISCLOSURE-CANARY") -> list[Path]:
    """Give one identity references/, scripts/ and templates/ files, then regenerate the
    manifest so the store still verifies (the package hash now covers these files).

    Returns the files written, so a test can name exactly what must never enter a Pack.
    """
    skill_dir = fixture.root / identity.path
    written: list[Path] = []
    for subdir, filename in (("references", "notes.md"), ("scripts", "run.py"),
                             ("templates", "template.md")):
        path = skill_dir / subdir
        path.mkdir(exist_ok=True)
        file = path / filename
        file.write_text(f"{content}:{subdir}\n")
        written.append(file)
    sm.generate(fixture.root)
    return written


def append_invalid_utf8(fixture: StoreFixture, identity, *, suffix: bytes = b"\xff\xfe\n") -> Path:
    """Make a skill's ``SKILL.md`` invalid UTF-8 and regenerate the manifest, so the store
    verifies but the delivered bytes cannot be faithfully decoded into a Pack body."""
    path = fixture.root / identity.path / "SKILL.md"
    path.write_bytes(path.read_bytes() + suffix)
    sm.generate(fixture.root)
    return path
