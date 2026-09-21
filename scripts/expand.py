#!/usr/bin/env python3
"""Operate the measured-expansion procedure for one profile (ADR-0019/0022, ticket #58).

Expansion is one reviewed batch at a time, on fresh evidence, and it fails closed: a profile
with an open Incident cannot expand. This CLI is the operator's path: ``status`` is read-only,
``rehearse`` exercises the running batch's rollback, and ``apply`` runs the whole procedure for
one reviewed Shadow Report — the same guards that enable the pilot batch, plus the additive,
rehearsal, threshold and post-batch checks.

    python3 scripts/expand.py status  --profile stillroom-signal-generator
    python3 scripts/expand.py rehearse --profile stillroom-signal-generator
    python3 scripts/expand.py apply --report <shadow-report.json> --reviewer adam \\
        --probe-request "run the stillroom-research-workflow for this topic"

``apply`` replays the probe requests through the deterministic broker after enabling the batch,
so the post-batch foundation-resolution and hash checks run without a model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker.evaluation import DEFAULT_THRESHOLDS_ROOT, ThresholdRegistry  # noqa: E402
from broker.expansion import ExpansionError, assert_expandable, expand, rehearse_rollback  # noqa: E402
from broker.gate import InjectionGate  # noqa: E402

DEFAULT_EVIDENCE_DIR = Path.home() / ".hermes" / "skill-broker"
DEFAULT_STORE = Path.home() / "skill-store"


class _Sink:
    """An Evidence Log sink: the procedure reads the decisions from the probe's results."""

    def append(self, decision) -> None:
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR,
                        help="where gate state, incidents and the expansion ledger live")
    common.add_argument("--thresholds-root", type=Path, default=DEFAULT_THRESHOLDS_ROOT,
                        help="where the pre-registered Soft Thresholds live")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", parents=[common],
                            help="show whether the profile may take another batch")
    status.add_argument("--profile", required=True)

    rehearse = sub.add_parser("rehearse", parents=[common],
                              help="rehearse the running batch's rollback")
    rehearse.add_argument("--profile", required=True)

    apply = sub.add_parser("apply", parents=[common],
                           help="run the procedure for one reviewed Shadow Report")
    apply.add_argument("--report", type=Path, required=True)
    apply.add_argument("--reviewer", required=True)
    apply.add_argument("--store", type=Path, default=DEFAULT_STORE)
    apply.add_argument("--profile", default=None, help="defaults to the report's profile")
    apply.add_argument("--reviewed-at", default="")
    apply.add_argument("--probe-request", action="append", default=[],
                       help="a request replayed after enabling; repeatable")
    return parser


def _load_report(path: Path):
    from broker.report import ShadowReport

    return ShadowReport.from_record(json.loads(path.read_text(encoding="utf-8")))


def _apply(args: argparse.Namespace) -> dict:
    from broker.broker import Broker
    from broker.judgment import FirstCandidateJudgmentSource
    from broker.report import profile_metadata

    report = _load_report(args.report)
    profile = args.profile or report.profile
    store = args.store.expanduser()
    _, foundation_ids, _ = profile_metadata(store, profile)
    gate = InjectionGate.in_evidence_dir(args.evidence_dir)
    broker = Broker(store=store, evidence_log=_Sink(),
                    judgment_source=FirstCandidateJudgmentSource())

    def post_probe():
        return [broker.prepare_turn(request, profile,
                                    {"session_id": f"expand-probe-{index}",
                                     "turn_id": f"probe-{index}"}).decision
                for index, request in enumerate(args.probe_request)]

    step = expand(gate=gate, report=report, reviewer=args.reviewer, profile=profile,
                  reviewed_at=args.reviewed_at, foundation_ids=foundation_ids,
                  post_probe=post_probe, registry=ThresholdRegistry(args.thresholds_root),
                  ledger_path=args.evidence_dir / "expansions.jsonl")
    return step.to_record()


def _run(args: argparse.Namespace) -> dict:
    if args.command == "apply":
        return _apply(args)
    gate = InjectionGate.in_evidence_dir(args.evidence_dir)
    if args.command == "rehearse":
        return rehearse_rollback(gate, args.profile).to_record()
    registry = ThresholdRegistry(args.thresholds_root)
    problems: list[str] = []
    try:
        assert_expandable(gate, args.profile)
    except ExpansionError as exc:
        problems.append(str(exc))
    registration = registry.get(args.profile)
    return {
        "profile": args.profile,
        "expandable": not problems,
        "problems": problems,
        "gate": gate.status(args.profile),
        "open_incidents": [incident.to_record()
                           for incident in gate.open_incidents(args.profile)],
        "thresholds_registered": registration is not None,
        "thresholds_reviewed_cases": (registration or {}).get("reviewed_cases"),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(_run(args), indent=2, sort_keys=True))
    except ExpansionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
