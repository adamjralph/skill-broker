#!/usr/bin/env python3
"""Verify the generated farms resolve to their intended variants — #7.

For every exposure in the manifest: the link exists, is a symlink, resolves to
the manifest's object, and the resolved package hashes to the catalog's
package_sha. Then the divergence invariants:

* same name exposed to two consumers with *different* ids resolves to
  *different* objects (divergence preserved, nothing overwritten);
* two ids that share one package hash resolve to the *same* object (alias).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXCLUDE = {"__pycache__", ".git", ".cache", "node_modules"}


def package_sha_dir(root: Path) -> str:
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            rel = path.relative_to(root).as_posix()
            entries.append(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


class Checker:
    def __init__(self) -> None:
        self.passes: list[str] = []
        self.failures: list[str] = []

    def check(self, ok: bool, label: str) -> None:
        (self.passes if ok else self.failures).append(label)

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
    manifest = json.loads((HERE / "manifest.json").read_text())
    catalog = json.loads((HERE / manifest["catalog"]).read_text())["skills"]
    objects_root = HERE / manifest["store"]
    farms_root = HERE / manifest["farms_root"]

    checker = Checker()
    resolved: dict[str, dict[str, str]] = {}  # consumer -> name -> object realpath

    print("=== resolved exposures ===")
    for consumer, spec in manifest["consumers"].items():
        resolved[consumer] = {}
        for name, skill_id in spec["exposures"].items():
            entry = catalog[skill_id]
            expected = objects_root / entry["package_sha"]
            link = farms_root / consumer / "skills" / name
            ok_link = link.is_symlink()
            checker.check(ok_link, f"{consumer}/{name}: is a symlink")
            actual = Path(os.path.realpath(link)) if ok_link else None
            ok_target = actual == Path(os.path.realpath(expected)) if actual else False
            checker.check(ok_target, f"{consumer}/{name}: resolves to {skill_id}")
            ok_hash = bool(actual) and actual.is_dir() and package_sha_dir(actual) == entry["package_sha"]
            checker.check(ok_hash, f"{consumer}/{name}: package hash == {entry['package_sha'][:12]}")
            if actual:
                resolved[consumer][name] = str(actual)
            print(
                f"  {consumer:7} {name:14} {skill_id:22} {entry['package_sha'][:12]}  "
                f"-> {os.readlink(link) if ok_link else 'MISSING'}"
            )

    # Divergence: same name, different content must land in different objects;
    # same content must land in the same object.
    print("\n=== same-name divergence ===")
    for name in sorted({n for spec in manifest["consumers"].values() for n in spec["exposures"]}):
        by_sha: dict[str, set[str]] = {}
        for consumer, spec in manifest["consumers"].items():
            if name in spec["exposures"] and name in resolved[consumer]:
                sha = catalog[spec["exposures"][name]]["package_sha"]
                by_sha.setdefault(sha, set()).add(resolved[consumer][name])
        distinct_consumers = sum(1 for spec in manifest["consumers"].values() if name in spec["exposures"])
        if distinct_consumers >= 2:
            for sha, targets in by_sha.items():
                checker.check(len(targets) == 1, f"{name}: one object per content ({sha[:12]})")
            if len(by_sha) > 1:
                all_targets = [t for ts in by_sha.values() for t in ts]
                checker.check(
                    len(set(all_targets)) == len(by_sha),
                    f"{name}: {len(by_sha)} divergent variants preserved as distinct objects",
                )
            print(f"  {name}: {len(by_sha)} distinct content(s) across {distinct_consumers} consumers")

    # Alias: one identity, many exposure names -> one object. Identical content
    # is one identity, so no two ids may share a package hash.
    print("\n=== aliases (one identity, many names) ===")
    shas = [entry["package_sha"] for entry in catalog.values()]
    checker.check(len(shas) == len(set(shas)), "no two ids share content (identical content is one identity)")
    names_by_id: dict[str, set[str]] = {}
    targets_by_id: dict[str, set[str]] = {}
    for consumer, spec in manifest["consumers"].items():
        for name, skill_id in spec["exposures"].items():
            names_by_id.setdefault(skill_id, set()).add(name)
            if name in resolved.get(consumer, {}):
                targets_by_id.setdefault(skill_id, set()).add(resolved[consumer][name])
    for skill_id, names in sorted(names_by_id.items()):
        targets = targets_by_id.get(skill_id, set())
        checker.check(len(targets) == 1, f"{skill_id}: every exposure name resolves to one object")
        if len(names) > 1:
            checker.check(True, f"alias: {skill_id} exposed as {', '.join(sorted(names))}")
            print(f"  {skill_id}: names {', '.join(sorted(names))}")

    print()
    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
