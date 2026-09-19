#!/usr/bin/env python3
"""Manifest-driven symlink-farm generator — wayfinder ticket #7.

Reads ``manifest.json`` (the desired per-consumer exposure state) and the store
catalog, then emits one symlink farm per consumer:

    farms/<consumer>/skills/<name> -> ../../store/objects/<package_sha>/

The generator does no discovery: the manifest is the single source of truth.
Re-running is idempotent; ``--prune`` removes links the manifest no longer asks
for.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(manifest_path: Path) -> tuple[dict, dict]:
    manifest = json.loads((HERE / manifest_path).read_text())
    catalog = json.loads((HERE / manifest["catalog"]).read_text())["skills"]
    return manifest, catalog


def desired_links(manifest: dict, catalog: dict) -> dict[str, dict[str, Path]]:
    """consumer -> {exposed name -> object dir}. Validates every exposure."""
    objects_root = HERE / manifest["store"]
    result: dict[str, dict[str, Path]] = {}
    for consumer, spec in manifest["consumers"].items():
        links: dict[str, Path] = {}
        for name, skill_id in spec["exposures"].items():
            if skill_id not in catalog:
                raise SystemExit(f"manifest error: {consumer} exposes unknown id {skill_id!r}")
            obj = objects_root / catalog[skill_id]["package_sha"]
            if not obj.is_dir():
                raise SystemExit(f"store error: object missing for {skill_id}: {obj}")
            links[name] = obj
        result[consumer] = links
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifest.json"))
    parser.add_argument("--prune", action="store_true", help="remove farm links not in the manifest")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest, catalog = load(args.manifest)
    desired = desired_links(manifest, catalog)
    farms_root = HERE / manifest["farms_root"]
    verb = "would " if args.dry_run else ""

    created = replaced = unchanged = pruned = 0
    for consumer, links in desired.items():
        farm = farms_root / consumer / "skills"
        if not args.dry_run:
            farm.mkdir(parents=True, exist_ok=True)

        if args.prune and farm.is_dir():
            for entry in sorted(farm.iterdir()):
                if entry.name not in links:
                    print(f"  {verb}prune  {consumer}/skills/{entry.name}")
                    if not args.dry_run:
                        entry.unlink()
                    pruned += 1

        for name, obj in sorted(links.items()):
            link = farm / name
            target = os.path.relpath(obj, link.parent)
            if link.is_symlink() and os.readlink(link) == target:
                unchanged += 1
                continue
            action = "replace" if link.is_symlink() else "create"
            print(f"  {verb}{action} {consumer}/skills/{name} -> {target}")
            if not args.dry_run:
                if link.is_symlink():
                    link.unlink()
                    replaced += 1
                else:
                    created += 1
                link.symlink_to(target)

    print(f"\nlinks: {created} created, {replaced} replaced, {unchanged} unchanged, {pruned} pruned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
