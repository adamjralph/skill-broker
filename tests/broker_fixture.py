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


class ScriptedJudgmentSource:
    """A deterministic ``JudgmentSource`` returning one scripted claim per turn, in order."""

    def __init__(self, *claims) -> None:
        self.claims = list(claims)

    def judge(self, request, candidates):
        return self.claims.pop(0)


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
