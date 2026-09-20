#!/usr/bin/env python3
"""Operate and inspect the injection gate (ADR-0019, ticket #52).

Injection is off by default and turned on per batch, on a recorded review. This CLI reads and
writes the gate's durable state; it never reaches into the broker. ``status`` is what makes the
gate observable: what is enabled, for which batch, whether a profile is failed closed, and how
many Incidents it carries.

    python3 scripts/gate.py status --profile stillroom-signal-generator
    python3 scripts/gate.py enable --profile stillroom-signal-generator --batch pilot \\
        --skill local.adam-content-writing --reviewer adam
    python3 scripts/gate.py disable --profile stillroom-signal-generator --reason rollback
    python3 scripts/gate.py regression --profile stillroom-signal-generator \\
        --reason "precision regressed: 0.5 < 0.9"
    python3 scripts/gate.py incidents --profile stillroom-signal-generator

``enable`` needs a review that post-dates any breach; a profile failed closed cannot be re-enabled
by the turn that breached it. A Soft-Threshold regression blocks expansion and leaves any running
injection in place, so ``regression`` never disables a batch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker.gate import (  # noqa: E402
    APPROVE,
    InjectionBatch,
    GateError,
    GateReview,
    InjectionGate,
)

DEFAULT_EVIDENCE_DIR = Path.home() / ".hermes" / "skill-broker"


def _gate(args: argparse.Namespace) -> InjectionGate:
    return InjectionGate.in_evidence_dir(args.evidence_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR,
                        help="where gate state and incidents live")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show what is enabled and whether a profile is closed")
    status.add_argument("--profile", required=True)

    enable = sub.add_parser("enable", help="enable one batch on a recorded review")
    enable.add_argument("--profile", required=True)
    enable.add_argument("--batch", required=True)
    enable.add_argument("--skill", action="append", default=[],
                        help="a store ID the batch covers; repeatable")
    enable.add_argument("--reviewer", required=True)
    enable.add_argument("--report-sha256", default="")

    disable = sub.add_parser("disable", help="turn injection off for a profile")
    disable.add_argument("--profile", required=True)
    disable.add_argument("--reason", default="")

    regression = sub.add_parser("regression", help="block expansion on a Soft-Threshold regression")
    regression.add_argument("--profile", required=True)
    regression.add_argument("--reason", action="append", required=True)

    clear = sub.add_parser("clear-regression", help="clear an expansion block after re-derivation")
    clear.add_argument("--profile", required=True)

    incidents = sub.add_parser("incidents", help="list a profile's recorded Incidents")
    incidents.add_argument("--profile", default=None)
    return parser


def _run(args: argparse.Namespace) -> dict:
    gate = _gate(args)
    if args.command == "status":
        return gate.status(args.profile)
    if args.command == "enable":
        batch = InjectionBatch(name=args.batch, profile=args.profile, skills=tuple(args.skill))
        review = GateReview(profile=args.profile, batch=args.batch, reviewer=args.reviewer,
                            outcome=APPROVE, reviewed_at="", report_sha256=args.report_sha256)
        return gate.enable(batch, review)
    if args.command == "disable":
        return gate.disable(args.profile, reason=args.reason)
    if args.command == "regression":
        return gate.note_expansion_regression(args.profile, tuple(args.reason))
    if args.command == "clear-regression":
        return gate.clear_expansion_block(args.profile)
    return {"profile": args.profile,
            "incidents": [incident.to_record() for incident in gate.incidents(args.profile)]}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(_run(args), indent=2, sort_keys=True))
    except GateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
