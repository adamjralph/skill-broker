"""The Skill Broker: the deterministic intervention layer over the Skill Store.

This package is the broker's home in the broker repository (ticket #45), beside the store
tooling in ``scripts/``. It is deliberately importable by the existing stdlib-``unittest``
runner without a packaging change: the suites already put ``scripts/`` on ``sys.path``, so
``import broker`` works, and broker modules import the store tooling (``store_manifest``,
``profile_policy``) from the same path with no shim.

The single external seam is ``Broker.prepare_turn(request, profile, session_context)``
(ADR-0001). The internal replaceable seams are the Judgment Source and the Hermes Adapter.
See the accepted spec (issue #44) and ``PROJECT-OUTLINE.md``.
"""

from .broker import Broker
from .evidence import EvidenceLog, JsonlEvidenceLog, SessionLedger
from .judgment import (
    JudgmentError,
    JudgmentSource,
    RecordedJudgmentSource,
    Recording,
    RecordingMismatch,
    StubJudgmentSource,
)
from .pack import Budget, HookConfig, Pack, SkillContent
from .retrieval import CANDIDATE_LIMIT
from .types import (
    Candidate,
    InterventionResult,
    Judgment,
    PackDelivery,
    ResolvedSkillVersion,
    RouteDecision,
    TurnOutcome,
)

__all__ = [
    "Broker",
    "Budget",
    "CANDIDATE_LIMIT",
    "Candidate",
    "EvidenceLog",
    "HookConfig",
    "InterventionResult",
    "JsonlEvidenceLog",
    "Judgment",
    "JudgmentError",
    "JudgmentSource",
    "Pack",
    "PackDelivery",
    "RecordedJudgmentSource",
    "Recording",
    "RecordingMismatch",
    "ResolvedSkillVersion",
    "RouteDecision",
    "SessionLedger",
    "SkillContent",
    "StubJudgmentSource",
    "TurnOutcome",
]
