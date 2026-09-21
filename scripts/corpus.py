#!/usr/bin/env python3
"""Extract, review, record and purge the Stage 5 Replay Corpus (ADR-0020, ticket #54).

Raw request text is machine-local and uncommitted, so the corpus root defaults outside the
repository (``~/.config/skill-broker/corpus``); reviewed labels and SHA-bound Recordings default
to the committed ``corpus/`` tree. Every ``gh``-free, offline operation:

    python3 scripts/corpus.py extract --profile stillroom-signal-generator
    python3 scripts/corpus.py extract --profile pilot --consent telegram
    python3 scripts/corpus.py status  --minimum 20
    python3 scripts/corpus.py review  --profile pilot --case <sha> --outcome local.some-skill
    python3 scripts/corpus.py record  --profile pilot --case <sha> --claim claim.json
    python3 scripts/corpus.py record  --profile pilot --case <sha> \
        --from-evidence ~/.hermes/skill-broker/route_decisions.jsonl
    python3 scripts/corpus.py purge   --profile pilot --source telegram
    python3 scripts/corpus.py queue   --profile pilot --write-prelabels

``extract`` refuses any source that has not opted in (ADR-0020): the agent-authored channels are
consented by default, ``telegram`` and ``desktop`` need an explicit ``--consent``, and an
unlisted source such as ``kanban`` needs one too. ``review`` writes only a label — never request
text — and ``record`` freezes a Jev claim against its Case by content hash. ``record`` takes
either a ``--claim`` file or ``--from-evidence``, which freezes the Judgment the live broker
already recorded in the Evidence Log (matched to the Case by profile and correlation ids).
``queue`` writes a machine-local, pre-labelled review queue so hand-review starts from a
suggestion rather than a bare hash list.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker.corpus import (  # noqa: E402
    DEFAULT_CONSENTED_SOURCES,
    DEFAULT_CORPUS_ROOT,
    DEFAULT_HELD_OUT_RATIO,
    DEFAULT_STAGE5_MINIMUM,
    CorpusStore,
    LabelStore,
    RecordingStore,
    claim_from_decision,
    decision_for_case,
    extract_cases,
    profile_status,
    retention_report,
    sessions_db,
)
from broker.jev import JevJudgmentSource  # noqa: E402
from broker.report import load_route_decisions  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = _REPO_ROOT / "corpus" / "labels"
DEFAULT_RECORDINGS = _REPO_ROOT / "corpus" / "recordings"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="extract one or more profiles' opted-in turns")
    extract.add_argument("--profile", action="append", required=True,
                         help="profile whose state.db to extract (repeatable)")
    extract.add_argument("--store", type=Path, default=Path.home() / "skill-store")
    extract.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    extract.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    extract.add_argument("--sessions-db", type=Path,
                         help="one explicit state.db (single --profile only)")
    extract.add_argument("--consent", action="append", default=None,
                         help="opt a source in, on top of the agent-authored defaults (repeatable)")
    extract.add_argument("--held-out-ratio", type=float, default=DEFAULT_HELD_OUT_RATIO)

    status = sub.add_parser("status", help="report reviewed supply and the Stage 5 minimum")
    status.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    status.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    status.add_argument("--profile", action="append", default=None)
    status.add_argument("--minimum", type=int, default=DEFAULT_STAGE5_MINIMUM)

    retention = sub.add_parser("retention", help="report raw-text retention for the Stage 5/6 review")
    retention.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)

    review = sub.add_parser("review", help="record a hand-corrected Reviewed Case outcome")
    review.add_argument("--profile", required=True)
    review.add_argument("--case", required=True, help="Case content hash")
    review.add_argument("--outcome", required=True, help="a Primary Skill ID or no_skill")
    review.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    review.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    review.add_argument("--reviewer", default="adam")

    prelabel = sub.add_parser("prelabel", help="record the agent's suggested outcome on a Case")
    prelabel.add_argument("--profile", required=True)
    prelabel.add_argument("--case", required=True)
    prelabel.add_argument("--outcome", required=True, help="a Skill ID or no_skill")
    prelabel.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)

    record = sub.add_parser("record", help="freeze a Jev claim against its Case by content hash")
    record.add_argument("--profile", required=True)
    record.add_argument("--case", required=True)
    source = record.add_mutually_exclusive_group(required=True)
    source.add_argument("--claim", type=Path, help="a JSON Jev Choice")
    source.add_argument("--from-evidence", type=Path,
                        help="an Evidence Log; freeze the Judgment it recorded for this Case")
    record.add_argument("--any-source", action="store_true",
                        help="with --from-evidence, allow a Judgment not answered by live Jev")
    record.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    record.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)

    queue = sub.add_parser(
        "queue", help="write a machine-local review queue with a suggested outcome per Case")
    queue.add_argument("--profile", required=True)
    queue.add_argument("--store", type=Path, default=Path.home() / "skill-store")
    queue.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    queue.add_argument("--judgment", choices=("live", "first_candidate", "no_skill"),
                       default="live", help="the suggestion source (default: live Jev)")
    queue.add_argument("--write-prelabels", action="store_true",
                       help="also store each suggestion on its Case as the prelabel")

    purge = sub.add_parser("purge", help="delete raw request text on request")
    purge.add_argument("--profile", required=True)
    purge.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    purge.add_argument("--case", help="one Case content hash")
    purge.add_argument("--source", help="every Case from one source (kept consented)")
    purge.add_argument("--revoke", action="store_true",
                       help="with --source, also remove that source's consent")
    return parser


def _run(args: argparse.Namespace) -> dict:
    if args.command == "extract":
        return _extract(args)
    if args.command == "status":
        return _status(args)
    if args.command == "retention":
        return retention_report(CorpusStore(args.corpus)).to_record()
    if args.command == "review":
        return _review(args)
    if args.command == "prelabel":
        return _prelabel(args)
    if args.command == "record":
        return _record(args)
    if args.command == "queue":
        return _queue(args)
    return _purge(args)


def _extract(args: argparse.Namespace) -> dict:
    if args.sessions_db is not None and len(args.profile) != 1:
        raise ValueError("--sessions-db can only be used with a single --profile")
    consent = set(DEFAULT_CONSENTED_SOURCES)
    consent.update(args.consent or ())
    corpus = CorpusStore(args.corpus)
    reports = []
    for profile in args.profile:
        db = args.sessions_db or sessions_db(profile, args.hermes_home)
        report = extract_cases(profile, store=args.store, db=db, corpus=corpus,
                               consent=consent, held_out_ratio=args.held_out_ratio)
        reports.append(report.to_record())
    return {"ok": True, "reports": reports}


def _status(args: argparse.Namespace) -> dict:
    corpus = CorpusStore(args.corpus)
    labels = LabelStore(args.labels)
    profiles = args.profile or corpus.profiles()
    return {"ok": True,
            "profiles": [profile_status(corpus, labels, profile,
                                        minimum=args.minimum).to_record()
                         for profile in profiles]}


def _review(args: argparse.Namespace) -> dict:
    corpus = CorpusStore(args.corpus)
    case = corpus.get(args.profile, args.case)
    if case is None:
        raise ValueError(f"no case {args.case} for profile {args.profile!r}")
    review = LabelStore(args.labels).review(case, args.outcome, reviewer=args.reviewer)
    return {"ok": True, "review": review.to_record()}


def _prelabel(args: argparse.Namespace) -> dict:
    case = CorpusStore(args.corpus).set_prelabel(args.profile, args.case, args.outcome)
    return {"ok": True, "case_sha256": case.case_sha256, "prelabel": case.prelabel}


def _record(args: argparse.Namespace) -> dict:
    corpus = CorpusStore(args.corpus)
    case = corpus.get(args.profile, args.case)
    if case is None:
        raise ValueError(f"no case {args.case} for profile {args.profile!r}")
    judgment_source = "claim"
    if args.from_evidence is not None:
        decision = decision_for_case(
            case, load_route_decisions(args.from_evidence, profile=args.profile))
        judgment_source = decision.judgment_source or "none"
        if judgment_source != JevJudgmentSource.name and not args.any_source:
            raise ValueError(
                f"the Route Decision for {args.case} was answered by {judgment_source!r}, not "
                "live Jev; pass --any-source to freeze it anyway")
        claim = claim_from_decision(decision)
    else:
        claim = json.loads(args.claim.read_text(encoding="utf-8"))
    recording = RecordingStore(args.recordings).write(case, claim)
    return {"ok": True, "judgment_source": judgment_source, "recording": recording.to_record()}


def _queue(args: argparse.Namespace) -> dict:
    """Write a machine-local review queue: one row per Case with a suggested outcome.

    The suggestion is the Judgment Source's primary Skill (or ``no_skill``), so Adam reviews
    pre-labelled Cases rather than a bare hash list. Request text stays machine-local: the queue
    is written under the corpus root, never the repository. The broker's own outcome is shown
    alongside, so a policy denial or a ``judgment_source_error`` is visible at review time.
    """
    from broker.broker import Broker
    from broker.judgment import NO_SKILL, FallbackJudgmentSource, FirstCandidateJudgmentSource

    corpus = CorpusStore(args.corpus)
    cases = corpus.cases(args.profile)
    if not cases:
        raise ValueError(f"no Cases for profile {args.profile!r}; extract first")
    if args.judgment == "live":
        source = JevJudgmentSource()
    elif args.judgment == "first_candidate":
        source = FirstCandidateJudgmentSource()
    else:
        source = FallbackJudgmentSource()  # no sources: always the No-Skill floor
    broker = Broker(store=args.store, evidence_log=_NullEvidence(), judgment_source=source)

    rows: list[dict] = []
    suggestions: dict[str, int] = {}
    for case in sorted(cases, key=lambda entry: (entry.started_at, entry.case_sha256)):
        result = broker.prepare_turn(
            case.request, args.profile,
            {"session_id": case.session_id, "turn_id": case.turn_id})
        decision = result.decision
        primary = decision.judgment.primary if decision.judgment is not None else None
        suggestion = primary or NO_SKILL
        if args.write_prelabels:
            corpus.set_prelabel(args.profile, case.case_sha256, suggestion)
        suggestions[suggestion] = suggestions.get(suggestion, 0) + 1
        rows.append({
            "case_sha256": case.case_sha256,
            "source": case.source,
            "split": case.split,
            "suggestion": suggestion,
            "broker_outcome": decision.outcome.value,
            "judgment_source": decision.judgment_source,
            "candidates": len(decision.candidates),
            "reason": "; ".join(decision.reasons),
            "request": case.request,
        })

    path = Path(corpus.root) / "queues" / f"{args.profile}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_queue(args.profile, rows, args.judgment), encoding="utf-8")
    return {"ok": True, "profile": args.profile, "cases": len(rows), "queue": str(path),
            "judgment": args.judgment, "prelabels_written": bool(args.write_prelabels),
            "suggestions": dict(sorted(suggestions.items()))}


def _render_queue(profile: str, rows: list[dict], judgment: str) -> str:
    """Render the review queue: per Case, its identity, suggestion and redacted request."""
    lines = [
        f"# Review queue — {profile}",
        "",
        f"Suggestions from `{judgment}`. Review each Case, then record the outcome:",
        "",
        "```sh",
        f"python3 scripts/corpus.py review --profile {profile} --case <sha> --outcome <skill-id|no_skill>",
        "```",
        "",
        f"Cases: {len(rows)}",
        "",
    ]
    for row in rows:
        lines += [
            f"## {row['case_sha256']}",
            "",
            f"- source: {row['source']}  split: {row['split']}",
            f"- suggested: {row['suggestion']}  (broker: {row['broker_outcome']}, "
            f"source: {row['judgment_source']}, candidates: {row['candidates']})",
            f"- reason: {row['reason'] or '-'}",
            "- request:",
            "",
        ]
        lines += [f"> {line}" if line else ">" for line in row["request"].splitlines()]
        lines.append("")
    return "\n".join(lines)


class _NullEvidence:
    """An Evidence Log sink: the queue reads each Decision from the Intervention Result."""

    def append(self, decision) -> None:
        return None


def _purge(args: argparse.Namespace) -> dict:
    corpus = CorpusStore(args.corpus)
    if args.case:
        removed = corpus.purge_case(args.profile, args.case)
        return {"ok": True, "purged": {"case": args.case, "removed": int(removed)}}
    if args.source:
        removed = (corpus.revoke_source(args.source) if args.revoke
                   else corpus.purge_source(args.source))
        return {"ok": True, "purged": {"source": args.source, "removed": removed,
                                       "revoked": bool(args.revoke)}}
    removed = corpus.purge_profile(args.profile)
    return {"ok": True, "purged": {"profile": args.profile, "removed": removed}}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
