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

from .adapter import (
    Adapter,
    ApiRequestEvidence,
    DeliveryPath,
    JsonlRequestEvidenceLog,
    RequestEvidenceLog,
    classify_delivery_path,
)
from .broker import Broker
from .corpus import (
    Case,
    ConsentError,
    CorpusError,
    CorpusStore,
    ExtractionReport,
    LabelError,
    LabelStore,
    ProfileStatus,
    RecordingStore,
    Review,
    ReviewedCase,
    extract_cases,
    load_reviewed_cases,
    profile_status,
    redact,
    split_for,
)
from .evidence import EvidenceLog, JsonlEvidenceLog, SessionLedger
from .jev import DEFAULT_TIMEOUT as JEV_DEFAULT_TIMEOUT
from .jev import JevJudgmentSource, live_judgment_source
from .judgment import (
    FallbackJudgmentSource,
    FirstCandidateJudgmentSource,
    JudgmentCall,
    JudgmentError,
    JudgmentSource,
    RecordedJudgmentSource,
    Recording,
    RecordingMismatch,
    StubJudgmentSource,
    no_skill_claim,
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
    "Adapter",
    "ApiRequestEvidence",
    "Broker",
    "Budget",
    "CANDIDATE_LIMIT",
    "Candidate",
    "Case",
    "ConsentError",
    "CorpusError",
    "CorpusStore",
    "DeliveryPath",
    "EvidenceLog",
    "ExtractionReport",
    "FallbackJudgmentSource",
    "FirstCandidateJudgmentSource",
    "HookConfig",
    "InterventionResult",
    "JEV_DEFAULT_TIMEOUT",
    "JevJudgmentSource",
    "JsonlEvidenceLog",
    "JsonlRequestEvidenceLog",
    "Judgment",
    "JudgmentCall",
    "JudgmentError",
    "JudgmentSource",
    "LabelError",
    "LabelStore",
    "Pack",
    "PackDelivery",
    "ProfileStatus",
    "RecordedJudgmentSource",
    "Recording",
    "RecordingMismatch",
    "RecordingStore",
    "RequestEvidenceLog",
    "ResolvedSkillVersion",
    "Review",
    "ReviewedCase",
    "RouteDecision",
    "SessionLedger",
    "SkillContent",
    "StubJudgmentSource",
    "TurnOutcome",
    "classify_delivery_path",
    "extract_cases",
    "live_judgment_source",
    "load_reviewed_cases",
    "no_skill_claim",
    "profile_status",
    "redact",
    "split_for",
]
