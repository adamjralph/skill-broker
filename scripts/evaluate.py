#!/usr/bin/env python3
"""Run the offline routing evaluation and pre-register Soft Thresholds (ticket #55).

Offline and model-free: every Reviewed Case is replayed against its SHA-bound Recording, so a
run needs only the machine-local corpus, the committed labels and Recordings, and the Skill
Store. Nothing here calls Jev or the network.

    python3 scripts/evaluate.py evaluate --profile stillroom-signal-generator --minimum 20
    python3 scripts/evaluate.py register --profile stillroom-signal-generator --minimum 20

``evaluate`` prints the per-profile report: the threshold-setting and held-out baselines, the
retrieval/Judgment/Grant attribution, and whether the profile is below the Stage 5 minimum (and
so stays in Shadow Mode). ``register`` writes the pre-registration under ``corpus/thresholds/``,
bound to the corpus digest and baseline it was derived from. Moving an already-registered
threshold needs ``--re-derive`` against a strictly larger reviewed set (ADR-0019).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from broker.corpus import (  # noqa: E402
    DEFAULT_CORPUS_ROOT,
    DEFAULT_STAGE5_MINIMUM,
    CorpusStore,
    LabelStore,
    RecordingStore,
)
from broker.evaluation import (  # noqa: E402
    DEFAULT_THRESHOLDS_ROOT,
    ThresholdRegistry,
    derive_thresholds,
    evaluate_profile,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = _REPO_ROOT / "corpus" / "labels"
DEFAULT_RECORDINGS = _REPO_ROOT / "corpus" / "recordings"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("evaluate", "report the routing baseline over Reviewed Cases"),
                            ("register", "pre-register the Soft Thresholds from the baseline")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--profile", required=True)
        command.add_argument("--store", type=Path, default=Path.home() / "skill-store")
        command.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
        command.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
        command.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
        command.add_argument("--minimum", type=int, default=DEFAULT_STAGE5_MINIMUM)
        if name == "register":
            command.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS_ROOT)
            command.add_argument("--re-derive", action="store_true",
                                 help="allow moving a registered threshold to a larger corpus")
            command.add_argument("--registered-at", default=None,
                                 help="the registration timestamp (for a deterministic record)")
    return parser


def _run(args: argparse.Namespace) -> dict:
    report = evaluate_profile(
        corpus=CorpusStore(args.corpus), labels=LabelStore(args.labels),
        recordings=RecordingStore(args.recordings), store=args.store, profile=args.profile,
        minimum=args.minimum)
    if args.command == "evaluate":
        result = report.to_record()
        result["thresholds"] = derive_thresholds(report).to_record()
        return result
    record = ThresholdRegistry(args.thresholds).register(
        report, registered_at=args.registered_at, allow_re_derivation=args.re_derive)
    return {**record, "ok": True}


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
