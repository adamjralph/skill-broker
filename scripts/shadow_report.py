#!/usr/bin/env python3
"""Build, show, and review a Shadow Report (ADR-0019, ticket #56).

The Shadow Report is the review artifact that ends Shadow Mode for one profile: recorded Route
Decisions plus what the agent actually used over a bounded window, with each Hard Gate's status,
movement against the pre-registered Soft Thresholds, and every attributed disagreement. Its
review — approve, reject, or approve a narrower batch — is the only thing that enables an
Injection Batch.

    python3 scripts/shadow_report.py build --profile stillroom-signal-generator \\
        --since 1789000000 --until 1790000000
    python3 scripts/shadow_report.py review --report <path> --outcome approve --reviewer adam
    python3 scripts/shadow_report.py review --report <path> --outcome reject --reviewer adam

Injection is off by default and a rejection leaves Shadow Mode running (ADR-0019).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker.corpus import (  # noqa: E402
    DEFAULT_CORPUS_ROOT,
    CorpusStore,
    LabelStore,
    load_reviewed_cases,
    sessions_db,
)
from broker.evaluation import DEFAULT_THRESHOLDS_ROOT, ThresholdRegistry  # noqa: E402
from broker.gate import InjectionBatch, InjectionGate  # noqa: E402
from broker.report import (  # noqa: E402
    ReportError,
    ShadowReportStore,
    apply_review,
    build_shadow_report,
    human_review_map,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE_DIR = Path.home() / ".hermes" / "skill-broker"
DEFAULT_REPORTS = Path.home() / ".config" / "skill-broker" / "reports"
DEFAULT_LABELS = _REPO_ROOT / "corpus" / "labels"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="assemble a Shadow Report over an observation window")
    build.add_argument("--profile", required=True)
    build.add_argument("--store", type=Path, default=Path.home() / "skill-store")
    build.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    build.add_argument("--db", type=Path, default=None,
                       help="the profile's Hermes state.db (defaults to its sessions db)")
    build.add_argument("--since", type=float, required=True, help="window start, epoch seconds")
    build.add_argument("--until", type=float, required=True, help="window end, epoch seconds")
    build.add_argument("--thresholds-root", type=Path, default=DEFAULT_THRESHOLDS_ROOT)
    build.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    build.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT,
                       help="machine-local Reviewed Cases to compare against, if present")
    build.add_argument("--labels", type=Path, default=DEFAULT_LABELS)

    show = sub.add_parser("show", help="print a Report's record")
    show.add_argument("--report", type=Path, required=True)

    review = sub.add_parser("review", help="record the review and, on approval, enable the batch")
    review.add_argument("--report", type=Path, required=True)
    review.add_argument("--outcome", required=True,
                        choices=["approve", "reject", "approve_narrower"])
    review.add_argument("--reviewer", required=True)
    review.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR,
                        help="where the injection gate state lives")
    review.add_argument("--batch-name", default=None, help="the narrower batch's name")
    review.add_argument("--skill", action="append", default=[],
                        help="a store ID the narrower batch covers; repeatable")
    return parser


def _human_review(args: argparse.Namespace) -> dict:
    if not Path(args.corpus).exists():
        return {}
    try:
        reviewed = load_reviewed_cases(CorpusStore(args.corpus), LabelStore(args.labels),
                                       args.profile)
    except Exception:  # noqa: BLE001 — an absent corpus is simply no human-review signal
        return {}
    return human_review_map(reviewed)


def _run(args: argparse.Namespace) -> dict:
    if args.command == "show":
        return ShadowReportStore(args.report.parent).load(args.report).to_record()

    if args.command == "build":
        report = build_shadow_report(
            profile=args.profile, store=args.store,
            evidence_path=args.evidence_dir / "route_decisions.jsonl",
            db=args.db or sessions_db(args.profile),
            since=args.since, until=args.until,
            thresholds=ThresholdRegistry(args.thresholds_root).get(args.profile),
            gate=InjectionGate.in_evidence_dir(args.evidence_dir),
            human_review=_human_review(args),
        )
        path = ShadowReportStore(args.reports).save(report)
        return {**report.to_record(), "report_path": str(path)}

    store = ShadowReportStore(args.report.parent)
    report = store.load(args.report)
    batch = None
    if args.outcome == "approve_narrower":
        name = args.batch_name or f"{report.profile}-narrowed"
        batch = InjectionBatch(name=name, profile=report.profile, skills=tuple(args.skill))
    reviewed = apply_review(
        report, outcome=args.outcome, reviewer=args.reviewer,
        gate=InjectionGate.in_evidence_dir(args.evidence_dir), batch=batch)
    store.save(reviewed)
    return {"profile": reviewed.profile, "review": reviewed.review,
            "gate": InjectionGate.in_evidence_dir(args.evidence_dir).status(reviewed.profile)}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(json.dumps(_run(args), indent=2, sort_keys=True))
    except ReportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
