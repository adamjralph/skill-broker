#!/usr/bin/env python3
"""Compare the control and treatment proof reports (wayfinder ticket #6).

Asserts the cache-safety and non-mutation invariants and prints a verdict.
Exits non-zero if any invariant fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACK_MARKER = "SKILL-BROKER-PACK v0"


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def check(self, condition: bool, label: str) -> None:
        (self.passes if condition else self.failures).append(label)

    def report(self) -> int:
        for line in self.passes:
            print(f"  PASS  {line}")
        for line in self.failures:
            print(f"  FAIL  {line}")
        print()
        if self.failures:
            print(f"VERDICT: FAIL ({len(self.failures)} invariant(s) broken)")
            return 1
        print(f"VERDICT: PASS ({len(self.passes)} invariants hold)")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("treatment", type=Path)
    args = parser.parse_args()

    control = json.loads(args.control.read_text())
    treatment = json.loads(args.treatment.read_text())
    c_reqs, t_reqs = control["requests"], treatment["requests"]

    checker = Checker()

    # 0. Both runs actually produced the two turns we expect.
    checker.check(len(c_reqs) == 2, f"control captured 2 chat requests (got {len(c_reqs)})")
    checker.check(len(t_reqs) == 2, f"treatment captured 2 chat requests (got {len(t_reqs)})")
    checker.check(
        all(len(r["tools"]) > 0 for r in c_reqs),
        "control request carried a non-empty tool schema",
    )

    # 1. The intervention happened, and only in treatment.
    control_has_marker = any(
        PACK_MARKER in (content or "") for r in c_reqs for content in r["user_messages"]
    )
    checker.check(not control_has_marker, "control: no Skill Pack on the wire")
    checker.check(
        all(any(PACK_MARKER in (content or "") for content in r["user_messages"]) for r in t_reqs),
        "treatment: every turn's wire carried a Skill Pack",
    )

    # 2. The plugin did not mutate the system prompt.
    checker.check(
        [r["system_prompt_sha256"] for r in c_reqs] == [r["system_prompt_sha256"] for r in t_reqs],
        "system prompt bytes identical control vs treatment, per turn",
    )

    # 3. The plugin did not mutate the tool schema.
    checker.check(
        [r["tools"] for r in c_reqs] == [r["tools"] for r in t_reqs],
        "tool schema identical control vs treatment, per turn",
    )

    # 4. Prompt-cache prefix: system prompt is byte-stable across turns.
    checker.check(
        len({r["system_prompt_sha256"] for r in t_reqs}) == 1,
        "treatment: system prompt byte-identical across turns (cache prefix stable)",
    )

    # 5. Replay: turn 2 resends turn 1's injected user bytes exactly.
    if len(t_reqs) == 2:
        checker.check(
            t_reqs[1]["user_messages"][0] == t_reqs[0]["user_messages"][0],
            "treatment: turn 2 replays turn 1's injected user message byte-for-byte",
        )
        checker.check(
            t_reqs[1]["user_messages"][-1] != t_reqs[0]["user_messages"][0],
            "treatment: turn 2's own user message is a distinct, freshly-injected turn",
        )

    # 6. Evidence handle: the pre_api_request observer saw the same system prompt.
    evidence = treatment["evidence"]
    checker.check(len(evidence) == len(t_reqs), "treatment: one evidence record per request")
    checker.check(
        [e["system_prompt_sha256"] for e in evidence] == [r["system_prompt_sha256"] for r in t_reqs],
        "evidence handle records the same system-prompt hash as the wire",
    )

    # Summary numbers for the resolution comment.
    print("=== summary ===")
    for label, reqs in (("control", c_reqs), ("treatment", t_reqs)):
        for i, r in enumerate(reqs):
            users = len(r["user_messages"])
            injected = sum(PACK_MARKER in (content or "") for content in r["user_messages"])
            print(
                f"  {label} req{i}: system_prompt={r['system_prompt_sha256']} "
                f"chars={len(r['system_prompt'] or '')} tools={len(r['tools'])} "
                f"tools_sha={r['tools_sha256']} messages={len(r['messages'])} "
                f"users={users} injected={injected}"
            )
    print(f"  treatment evidence: {json.dumps(evidence, indent=None)}")
    print()

    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
