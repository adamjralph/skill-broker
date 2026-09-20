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


class RecordingEvidenceLog:
    """An in-memory ``EvidenceLog``: keeps every Route Decision it is handed, in order."""

    def __init__(self) -> None:
        self.decisions: list = []

    def append(self, decision) -> None:
        self.decisions.append(decision)

    @property
    def records(self) -> list[dict]:
        return [decision.to_record() for decision in self.decisions]


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
