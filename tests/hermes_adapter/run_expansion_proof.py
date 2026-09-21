#!/usr/bin/env python3
"""Measured expansion, rehearsed deterministically (ticket #58, ADR-0019/0022).

Run it with the repository's Python (it uses only the broker seam)::

    python3 tests/hermes_adapter/run_expansion_proof.py

This is the **rehearsal** for the second batch. It touches no live Consumer state: it routes over
the *real* Skill Store through a symlink overlay, so the identities and content are the ones the
live profile resolves, and it authors the expanded policy in that overlay the way the operator
would author it in the store.

It rehearses the whole expansion procedure and asserts its acceptance criteria:

1. **The first batch** — the pilot batch (the stillroom Brokered Allowlist) is enabled on a
   reviewed report over a fresh window, exactly as #57 enabled it.
2. **Fresh evidence and its own review** — the policy grows by three newly-brokered stillroom
   workflows; a second report is assembled from a fresh window and reviewed with its own digest.
3. **The expansion** — the reviewed-batch procedure rehearses the running batch's rollback,
   confirms the new batch preserves every Skill the first batch covered, holds the pre-registered
   thresholds unchanged, enables the second batch, and checks the post-batch window.
4. **The first batch is unchanged** — the same request replayed after the expansion grants the
   same Skill and delivers the byte-identical Pack with the same hash.
5. **Rollback** — disabling the second batch and restoring the first returns the profile to the
   pilot exposure without data loss.
6. **Fail closed** — a profile failed closed by an Incident cannot expand.

The harness is not part of the repo's stdlib ``unittest`` discovery because it needs the real
Skill Store; its acceptance logic is tested in ``tests/test_expansion_proof.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
STORE = Path.home() / "skill-store"
PROFILE = "stillroom-signal-generator"
COLD_EMAIL_ID = "coreyhaines31.cold-email"
NEW_SKILL_ID = "life-os.stillroom-research-workflow"
NEW_SKILL_NAME = "stillroom-research-workflow"
ADDITIONS = (
    "life-os.stillroom-signal-workflow",
    NEW_SKILL_ID,
    "life-os.stillroom-content-scout-workflow",
)
COLD_REQUEST = "write a cold email to a prospect introducing our new service"
NEW_REQUEST = "run the stillroom-research-workflow for this topic"
FIRST_REVIEWED_AT = "2026-09-21T00:00:00+00:00"
SECOND_REVIEWED_AT = "2026-09-22T00:00:00+00:00"


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- the store overlay: real identities, an authored second policy --------------------------


def build_overlay(store: Path, overlay: Path) -> Path:
    """A verifying store that symlinks the real store's identities and copies its policies.

    The overlay lets the rehearsal author the expanded policy without touching the store, while
    every Skill it routes over is the real vendored content.
    """
    overlay.mkdir(parents=True, exist_ok=True)
    for owner in sorted(path for path in store.iterdir() if path.is_dir()
                        and path.name not in {"policies", ".git"}):
        target = overlay / owner.name
        if not target.exists():
            target.symlink_to(owner.resolve(), target_is_directory=True)
    for name in ("store-manifest.json", "store-meta.json"):
        shutil.copy2(store / name, overlay / name)
    policies = overlay / "policies"
    policies.mkdir(exist_ok=True)
    for policy in (store / "policies").glob("*.json"):
        shutil.copy2(policy, policies / policy.name)
    return overlay


def brokered(overlay: Path, profile: str) -> list[str]:
    policy = json.loads((overlay / "policies" / f"{profile}.json").read_text())
    return [str(ident) for ident in policy.get("brokered", ())]


def foundation(overlay: Path, profile: str) -> list[str]:
    policy = json.loads((overlay / "policies" / f"{profile}.json").read_text())
    return [entry["id"] for entry in policy.get("foundation", ()) if "id" in entry]


def expand_policy(overlay: Path, profile: str, additions: tuple[str, ...]) -> list[str]:
    """Add newly-brokered Skills to the policy, preserving the pilot's allowlist exactly."""
    path = overlay / "policies" / f"{profile}.json"
    policy = json.loads(path.read_text())
    for ident in additions:
        if ident not in policy["brokered"]:
            policy["brokered"].append(ident)
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n")
    return [str(ident) for ident in policy["brokered"]]


# --- fresh evidence: replay through the external seam ---------------------------------------


class Replay:
    """One replay over the overlay, plus the turn/skill-use rows a report windows over."""

    def __init__(self, overlay: Path, profile: str, gate) -> None:
        sys.path.insert(0, str(REPO / "scripts"))
        from broker.broker import Broker
        from broker.evidence import SessionLedger
        from broker.judgment import FirstCandidateJudgmentSource

        self.profile = profile
        self.overlay = overlay
        self.gate = gate
        self._ledger = SessionLedger()
        self.broker = Broker(store=overlay, evidence_log=_NullLog(),
                             judgment_source=FirstCandidateJudgmentSource(),
                             session_ledger=self._ledger, gate=gate)
        self.decisions = []

    def turn(self, request: str, *, session_id: str, turn_id: str):
        result = self.broker.prepare_turn(request, self.profile,
                                          {"session_id": session_id, "turn_id": turn_id})
        self.decisions.append(result.decision)
        return result


class _NullLog:
    """An in-memory sink for a replay's decisions; the decisions are read from the results."""

    def append(self, decision) -> None:  # noqa: D401
        return None


def record_evidence(path: Path, decisions) -> None:
    from broker.evidence import append_jsonl

    for decision in decisions:
        append_jsonl(path, decision.to_record())


def create_state_db(db: Path) -> None:
    """Create the Hermes-shaped ``state.db`` once; turns are staged per replay window."""
    sys.path.insert(0, str(REPO / "tests"))
    from corpus_fixture import add_session, create_db

    create_db(db)
    add_session(db, "sess-expansion", "cli", profile_name=PROFILE, started_at=0.0)


def stage_turn(db: Path, session_id: str, request: str, skill: str | None,
               *, timestamp: float) -> str:
    """Stage one user turn and its ``skill_view``; return the turn id the report keys on."""
    sys.path.insert(0, str(REPO / "tests"))
    from corpus_fixture import add_session, add_skill_call, add_turn

    if not _session_exists(db, session_id):
        add_session(db, session_id, "cli", profile_name=PROFILE, started_at=timestamp)
    turn_id = add_turn(db, session_id, request, timestamp=timestamp, active=1)
    if skill:
        add_skill_call(db, session_id, skill, timestamp=timestamp + 0.5)
    return str(turn_id)


def _session_exists(db: Path, session_id: str) -> bool:
    import sqlite3

    connection = sqlite3.connect(db)
    try:
        row = connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None
    finally:
        connection.close()


def register_rehearsal_thresholds(registry, profile: str, metrics, *, corpus: str,
                                  registered_at: str) -> dict:
    """Pre-register the rehearsal's measured rates as floors (ADR-0019, as in #57).

    A rehearsal has no hand-reviewed corpus, so the window's measured rates become the floors;
    the same artifact shape — thresholds, corpus digest and baseline — is what a real
    pre-registration carries.
    """
    from broker.evaluation import EvaluationReport, SplitMetrics

    report = EvaluationReport(
        profile=profile, minimum=1, below_minimum=False, stage="gateable",
        corpus_sha256=corpus, reviewed_cases=max(metrics.cases, 1), threshold=metrics,
        held_out=SplitMetrics(), attribution={}, attribution_by_stage={})
    return registry.register(report, registered_at=registered_at)


# --- the parent: the expansion lifecycle ----------------------------------------------------


def _build_report(*, overlay: Path, profile: str, evidence: Path, db: Path, registry, gate,
                  generated_at: str):
    from broker.report import build_shadow_report

    thresholds = registry.get(profile)
    probe = build_shadow_report(profile=profile, store=overlay, evidence_path=evidence, db=db,
                                gate=gate, generated_at=f"{generated_at}-probe")
    if thresholds is None:
        thresholds = register_rehearsal_thresholds(
            registry, profile, probe.metrics, corpus=_sha_text(evidence.read_text()),
            registered_at=generated_at)
    return build_shadow_report(profile=profile, store=overlay, evidence_path=evidence, db=db,
                               thresholds=thresholds, gate=gate, generated_at=generated_at)


def _run_suite(work: Path) -> int:
    if not (STORE / "store-manifest.json").is_file():
        print(f"Skill Store not found at {STORE}")
        return 1

    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "tests"))
    from broker.evaluation import ThresholdRegistry
    from broker.expansion import ExpansionError, expand
    from broker.gate import APPROVE, GateReview, InjectionBatch, InjectionGate
    from broker.report import apply_review

    overlay = build_overlay(STORE, work / "store")
    gate = InjectionGate.in_evidence_dir(work / "gate")
    registry = ThresholdRegistry(work / "thresholds")
    db = work / "state.db"
    create_state_db(db)

    print("=== Measured expansion (rehearsal) ===")
    pilot_brokered = brokered(overlay, PROFILE)

    # 1. The first batch: fresh replay evidence, a report, and its review (as #57 enabled it).
    first = Replay(overlay, PROFILE, gate)
    first_turn = stage_turn(db, "sess-first", COLD_REQUEST, COLD_EMAIL_ID, timestamp=0.0)
    first.turn(COLD_REQUEST, session_id="sess-first", turn_id=first_turn)
    evidence_one = work / "evidence" / "first.jsonl"
    record_evidence(evidence_one, first.decisions)
    report_one = _build_report(overlay=overlay, profile=PROFILE, evidence=evidence_one, db=db,
                               registry=registry, gate=gate, generated_at=FIRST_REVIEWED_AT)
    reviewed_one = apply_review(report_one, outcome=APPROVE, reviewer="adam (rehearsal)",
                                reviewed_at=FIRST_REVIEWED_AT, gate=gate)
    batch_one = gate.enabled_batch(PROFILE)
    pilot_review = dict(reviewed_one.review)
    print(f"  first     batch={batch_one.name} skills={len(batch_one.skills)} "
          f"injecting={gate.injecting(PROFILE)}")

    # The first batch's behaviour, before anything grows.
    cold_before = first.turn(COLD_REQUEST, session_id="sess-cold-before", turn_id="cb").decision

    # 2. The store grows, and a fresh window supplies the second review.
    expanded_brokered = expand_policy(overlay, PROFILE, ADDITIONS)
    second = Replay(overlay, PROFILE, gate)
    second_turn = stage_turn(db, "sess-second", NEW_REQUEST, NEW_SKILL_NAME, timestamp=1.0)
    second_cold = stage_turn(db, "sess-second-cold", COLD_REQUEST, COLD_EMAIL_ID, timestamp=2.0)
    second.turn(NEW_REQUEST, session_id="sess-second", turn_id=second_turn)
    second.turn(COLD_REQUEST, session_id="sess-second-cold", turn_id=second_cold)
    evidence_two = work / "evidence" / "second.jsonl"
    record_evidence(evidence_two, second.decisions)
    report_two = _build_report(overlay=overlay, profile=PROFILE, evidence=evidence_two, db=db,
                               registry=registry, gate=gate, generated_at=SECOND_REVIEWED_AT)
    batch_two = InjectionBatch(name=f"{PROFILE}-brokered-2", profile=PROFILE,
                               skills=tuple(expanded_brokered))

    # 3. The procedure: fail-closed, fresh review, additive, thresholds held, rollback rehearsed.
    thresholds_before = registry.get(PROFILE)
    post = {}

    def post_probe():
        new_turn = second.turn(NEW_REQUEST, session_id="sess-post-new", turn_id="d4").decision
        cold_turn = second.turn(COLD_REQUEST, session_id="sess-post-cold", turn_id="d5").decision
        post["new"] = new_turn
        post["cold"] = cold_turn
        return [new_turn, cold_turn]

    ledger = work / "expansions.jsonl"
    step = expand(gate=gate, report=report_two, batch=batch_two, reviewer="adam (rehearsal)",
                  reviewed_at=SECOND_REVIEWED_AT, foundation_ids=foundation(overlay, PROFILE),
                  post_probe=post_probe, registry=registry, ledger_path=ledger)
    thresholds_after = registry.get(PROFILE)
    print(f"  expansion batch={step.batch['name']} previous={step.previous_batch['name']} "
          f"post_ok={step.post.ok} rollback_restored={step.rollback.restored}")

    # 4. Rollback: disable the second batch, restore the first, without data loss.
    gate.disable(PROFILE, reason="expansion rehearsal complete")
    foundation_only = not gate.injecting(PROFILE)
    gate.enable(batch_one, GateReview.from_record(pilot_review))
    rollback_ok = gate.injecting(PROFILE) and gate.enabled_batch(PROFILE) == batch_one
    print(f"  rollback  foundation_only={foundation_only} first_batch_restored={rollback_ok}")

    # 5. Fail closed: a profile with an open Incident cannot expand.
    gate.fail_closed(PROFILE, ("unauthorised_grant",), reasons=("rehearsal incident",))
    refused = False
    try:
        expand(gate=gate, report=report_two, batch=batch_two, reviewer="adam (rehearsal)",
               reviewed_at=SECOND_REVIEWED_AT, registry=registry, post_probe=lambda: [])
    except ExpansionError:
        refused = True
    print(f"  fail_closed refused_expansion={refused} failed_closed={gate.failed_closed(PROFILE)}")

    report = {
        "profile": PROFILE,
        "pilot_brokered": pilot_brokered,
        "expanded_brokered": expanded_brokered,
        "batch_one": batch_one.to_record(),
        "batch_two": batch_two.to_record(),
        "report_one_sha256": reviewed_one.report_sha256,
        "report_two_sha256": report_two.report_sha256,
        "report_two_thresholds_pre_registered": report_two.thresholds_pre_registered,
        "report_two_gates_passed": report_two.gates_passed,
        "step": step.to_record(),
        "thresholds_before": thresholds_before,
        "thresholds_after": thresholds_after,
        "cold_before": _observable(cold_before),
        "cold_after": _observable(post.get("cold")),
        "new_grant": _observable(post.get("new")),
        "rollback": {"foundation_only": foundation_only, "first_batch_restored": rollback_ok},
        "fail_closed": {"refused": refused, "failed_closed": gate.failed_closed(PROFILE)},
        "ledger_records": len(_read_lines(ledger)),
    }
    failures = assert_proof(report)
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        print("VERDICT: FAIL")
        return 1
    print(f"  PASS  second batch enabled on fresh evidence: {step.batch['name']} "
          f"({len(step.batch['skills'])} Brokered Skills)")
    print("  PASS  the first batch still grants the same Skill and byte-identical Pack")
    print("  PASS  thresholds unchanged; rollback rehearsed and restored")
    print("  PASS  foundation-resolution and hash agreement checked after the batch")
    print("  PASS  an open Incident fails expansion closed")
    print("VERDICT: PASS")
    return 0


def _observable(decision) -> dict | None:
    if decision is None:
        return None
    return {
        "outcome": decision.outcome.value,
        "grants": [grant.id for grant in decision.grants],
        "pack_sha256": decision.pack_sha256,
        "pack_chars": decision.pack_chars,
    }


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


# --- the assertions: pure functions over the collected report --------------------------------


def assert_proof(report: dict) -> list[str]:
    """Return the failures; empty means every expansion invariant held."""
    failures: list[str] = []
    failures += _check_first_batch(report)
    failures += _check_second_review(report)
    failures += _check_expansion(report)
    failures += _check_thresholds(report)
    failures += _check_rollback(report)
    failures += _check_fail_closed(report)
    return failures


def _check_first_batch(report: dict) -> list[str]:
    """The first batch keeps every Skill and the same behaviour after the expansion (AC2)."""
    failures: list[str] = []
    batch_one = report["batch_one"]
    batch_two = report["batch_two"]
    dropped = sorted(set(batch_one["skills"]) - set(batch_two["skills"]))
    if dropped:
        failures.append(f"the second batch drops {dropped} from the first batch")
    if not batch_one["skills"]:
        failures.append("the first batch covers no Skills")
    if not set(report["pilot_brokered"]) <= set(batch_two["skills"]):
        failures.append("the expanded batch no longer covers the pilot's Brokered Allowlist")
    before, after = report["cold_before"], report["cold_after"]
    if not before or not after:
        return failures + ["the first batch's request was not replayed"]
    if before["grants"] != after["grants"] or before["grants"][:1] != [COLD_EMAIL_ID]:
        failures.append("the first batch's grant changed after the expansion")
    if not after["pack_sha256"] or before["pack_sha256"] != after["pack_sha256"]:
        failures.append("the first batch's Pack hash changed after the expansion")
    if not report["new_grant"] or report["new_grant"]["grants"][:1] != [NEW_SKILL_ID]:
        failures.append("the newly-brokered Skill was not granted after the expansion")
    return failures


def _check_second_review(report: dict) -> list[str]:
    """The second batch has its own fresh review bound to the fresh report (AC1)."""
    failures: list[str] = []
    step = report["step"]
    if step["review"]["outcome"] != "approve":
        failures.append("the second report was not approved")
    if step["review"]["report_sha256"] != report["report_two_sha256"]:
        failures.append("the review is not bound to the second report")
    if report["report_one_sha256"] == report["report_two_sha256"]:
        failures.append("the second review reused the first report's evidence")
    if not report["report_two_thresholds_pre_registered"]:
        failures.append("the second report has no pre-registered Soft Thresholds")
    if not report["report_two_gates_passed"]:
        failures.append("the second report has a Hard-Gate breach")
    return failures


def _check_expansion(report: dict) -> list[str]:
    """The procedure enabled the second batch without changing the first (AC2, AC5)."""
    failures: list[str] = []
    step = report["step"]
    if step["batch"]["name"] != report["batch_two"]["name"]:
        failures.append("the enabled batch is not the reviewed second batch")
    if step["previous_batch"] is None or step["previous_batch"]["name"] != \
            report["batch_one"]["name"]:
        failures.append("the expansion did not record the first batch it expanded from")
    post = step["post"]
    if not post["ok"]:
        failures.append("the post-batch checks did not pass")
    if post["foundation_regressions"]:
        failures.append(f"{post['foundation_regressions']} foundation-resolution regressions")
    if post["hash_disagreements"]:
        failures.append(f"{post['hash_disagreements']} broker/native hash disagreements")
    if not post["hash_agreements"]:
        failures.append("the post-batch window recorded no hash agreement")
    if report["ledger_records"] != 1:
        failures.append("the expansion was not recorded exactly once")
    return failures


def _check_thresholds(report: dict) -> list[str]:
    """Thresholds stayed put; the procedure reports whether a re-derivation happened (AC3)."""
    failures: list[str] = []
    if report["thresholds_before"] != report["thresholds_after"]:
        failures.append("the expansion moved the pre-registered thresholds")
    if not report["step"]["thresholds"]["unchanged"]:
        failures.append("the expansion did not report the thresholds as unchanged")
    return failures


def _check_rollback(report: dict) -> list[str]:
    """Rollback was rehearsed before enabling and restores the first exposure (AC4)."""
    failures: list[str] = []
    rehearsal = report["step"]["rollback"]
    if not rehearsal["foundation_only"]:
        failures.append("the rollback rehearsal did not reach foundation-only")
    if not rehearsal["restored"]:
        failures.append("the rollback rehearsal did not restore the first batch")
    if not report["rollback"]["foundation_only"]:
        failures.append("disabling the second batch did not reach foundation-only")
    if not report["rollback"]["first_batch_restored"]:
        failures.append("restoring the first batch did not restore the pilot exposure")
    return failures


def _check_fail_closed(report: dict) -> list[str]:
    """A profile with an open Incident cannot expand (AC6)."""
    failures: list[str] = []
    if not report["fail_closed"]["refused"]:
        failures.append("expansion was not refused while failed closed")
    if not report["fail_closed"]["failed_closed"]:
        failures.append("the refused expansion did not leave the profile failed closed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()
    work = args.root or Path(tempfile.mkdtemp(prefix="sb_expansion_proof_"))
    try:
        return _run_suite(work)
    finally:
        if args.root is None:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
