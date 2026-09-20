"""The Broker: one deep module behind ``prepare_turn`` (ADR-0001, ticket #46).

Construct it with its collaborators — the Skill Store root, where the profile's policies live,
a Judgment Source, an Evidence Log and a Session Ledger — then call ``prepare_turn`` with a
request, a profile and session context. Everything else is private: callers learn nothing about
manifest layout, policy shape, retrieval, thresholds or content assembly.

Ticket #46 builds the seam, the Authorised Closure, the explicit No-Skill Outcome and the Route
Decision. Nothing is judged, granted or delivered yet, so every valid turn ends in a No-Skill
Outcome; retrieval (#47), Judgment and Grant (#48) and Pack assembly (#49) fill the pipeline in.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import profile_policy as pp
import store_manifest as sm

from .closure import authorised_closure
from .evidence import SessionLedger
from .judgment import grant, validate
from .metadata import candidate_profile
from .retrieval import CANDIDATE_LIMIT, rank
from .types import (
    Candidate,
    InterventionResult,
    Judgment,
    ResolvedSkillVersion,
    RouteDecision,
    TurnOutcome,
)


class Broker:
    """The Skill Broker over one Skill Store."""

    def __init__(
        self,
        *,
        store: Path | str,
        evidence_log,
        policies_dir: Path | str | None = None,
        judgment_source=None,
        session_ledger=None,
    ) -> None:
        self._store = Path(store)
        self._policies_dir = (Path(policies_dir) if policies_dir is not None
                              else self._store / pp.POLICIES_DIR)
        self._judgment_source = judgment_source
        self._evidence_log = evidence_log
        self._session_ledger = session_ledger if session_ledger is not None else SessionLedger()

    def prepare_turn(
        self,
        request: str,
        profile: str,
        session_context: Mapping[str, Any] | None = None,
    ) -> InterventionResult:
        """Decide this turn's intervention.

        The Authorised Closure is resolved and ranked into a Candidate set; exactly one Route
        Decision is always appended. Nothing is judged, granted or delivered in this ticket.
        """
        reasons: list[str] = []
        closure: tuple[ResolvedSkillVersion, ...] = ()
        candidates: tuple[Candidate, ...] = ()
        judgment: Judgment | None = None
        grants: tuple[ResolvedSkillVersion, ...] = ()
        outcome = TurnOutcome.NO_SKILL

        problems = sm.verify(self._store)
        if problems:
            outcome = TurnOutcome.FAILURE
            reasons.append("store_manifest_invalid")
            reasons.extend(problems)
        else:
            identities = {row["id"]: row for row in sm.build(self._store)["identities"]}
            policy, policy_problems = self._policy(profile, identities)
            if policy is None:
                reasons.append("policy_missing")
            elif policy_problems:
                reasons.append("policy_invalid")
                reasons.extend(policy_problems)
            else:
                closure, closure_problems = authorised_closure(policy, identities)
                reasons.extend(closure_problems)
                candidates = rank(request, [candidate_profile(self._store, identities[entry.id])
                                            for entry in closure])
                if self._judgment_source is not None:
                    outcome, judgment, grants, problems = self._judge(request, candidates, closure)
                    reasons.extend(problems)

        decision = RouteDecision(
            profile=profile,
            session_id=(session_context or {}).get("session_id"),
            outcome=outcome,
            reasons=tuple(reasons),
            request_sha256=hashlib.sha256(request.encode("utf-8")).hexdigest(),
            request_chars=len(request),
            authorised_closure=closure,
            candidate_limit=CANDIDATE_LIMIT,
            candidates=candidates,
            judgment=judgment,
            grants=grants,
        )
        self._evidence_log.append(decision)
        return InterventionResult(outcome=outcome, reasons=decision.reasons, grants=grants,
                                  decision=decision)

    def _judge(self, request: str, candidates: tuple[Candidate, ...],
               closure: tuple[ResolvedSkillVersion, ...]) -> tuple[
                   TurnOutcome, Judgment | None, tuple[ResolvedSkillVersion, ...],
                   tuple[str, ...]]:
        """Ask the Judgment Source and validate its answer. Invalid output never grants.

        The Source is an external seam, so anything it raises is a recorded failure rather than
        a crashed turn; a model returns text, not authority (ADR-0004).
        """
        try:
            claim = self._judgment_source.judge(request, candidates)
        except Exception as exc:  # noqa: BLE001 — the seam is outside our control
            return TurnOutcome.FAILURE, None, (), ("judgment_source_error", f"{exc}",)
        judgment, problems = validate(claim, candidates, closure)
        if judgment is None:
            return TurnOutcome.FAILURE, None, (), ("judgment_invalid", *problems)
        if judgment.no_skill:
            return TurnOutcome.NO_SKILL, judgment, (), ()
        grants = grant(judgment, closure)
        if not grants:
            return (TurnOutcome.FAILURE, judgment, (),
                    ("judgment_invalid", f"primary {judgment.primary!r} is not authorised"))
        return TurnOutcome.GRANTED, judgment, grants, ()

    def _policy(self, profile: str, identities: dict[str, dict]) -> tuple[dict | None, tuple[str, ...]]:
        """The profile's policy, or ``(None, ())`` when there is none, plus validation problems."""
        path = self._policies_dir / f"{profile}.json"
        if not path.exists():
            return None, ()
        try:
            policy = pp.load(path)
        except (OSError, ValueError, pp.PolicyError) as exc:
            return {}, (f"policy is not valid JSON: {exc}",)
        return policy, tuple(pp.validate(policy, identities, filename=profile))
