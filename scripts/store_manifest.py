#!/usr/bin/env python3
"""Skill Store Manifest generator and verifier (ADR-0011).

Scans the Skill Store and writes the canonically-serialized **Store Manifest**: one row per
Skill identity carrying the canonical package hash over the normalized file set
(``package_sha256``), a separate ``SKILL.md`` hash, provenance, the local-patch marker,
dependencies, and licence.

An *identity directory* is a ``<owner>/<slug>/`` directory containing a ``SKILL.md``. Empty
placeholder directories (the store skeleton) are ignored, so the CLI runs against an
unpopulated store.

Fields the generator cannot derive are read from an optional authored
``<store>/store-meta.json`` keyed by the identity's ``path`` (``<owner>/<slug>``): ``name``,
``aliases``, ``provenance``, ``local_patch``, ``dependencies``, ``license``. Absent entries
fall back to defaults; ``license`` defaults to ``LicenseRef-Proprietary`` for the authored
pseudo-owners (ADR-0017) and ``null`` otherwise.

Deterministic and read-only against the Skill Store; writes ``<store>/store-manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_audit import frontmatter_name, hash_package  # noqa: E402

MANIFEST_NAME = "store-manifest.json"
META_NAME = "store-meta.json"
MANIFEST_VERSION = 1
AUTHORED_OWNERS = {"local", "hermes_engineer", "life-os", "stillroom"}
DEFAULT_LICENSE = "LicenseRef-Proprietary"


class ManifestError(Exception):
    """The store is not in a manifest-verifiable state (e.g. two dirs for one identity)."""


def identity_dirs(store: Path) -> list[Path]:
    """The populated identity directories: ``<owner>/<slug>/`` holding a ``SKILL.md``."""
    if not store.is_dir():
        return []
    return sorted(
        md.parent for md in store.glob("*/*/SKILL.md")
        if not md.parent.name.startswith(".") and not md.parent.parent.name.startswith(".")
    )


def load_meta(store: Path) -> dict:
    path = store / META_NAME
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ManifestError(f"{META_NAME} must be a JSON object keyed by identity path")
    return data


def identity_row(store: Path, skill_dir: Path, meta: dict) -> dict:
    owner = skill_dir.parent.name
    rel_path = f"{owner}/{skill_dir.name}"
    m = meta.get(rel_path, {})
    name = m.get("name") or frontmatter_name(skill_dir / "SKILL.md") or skill_dir.name
    package_sha, skill_md_sha, file_count, _ = hash_package(skill_dir)
    return {
        "id": f"{owner}.{name}",
        "name": name,
        "owner": owner,
        "aliases": sorted(m.get("aliases", [])),
        "path": rel_path,
        "package_sha256": package_sha,
        "skill_md_sha256": skill_md_sha,
        "file_count": file_count,
        "provenance": {"repo": None, "commit": None, **m.get("provenance", {})},
        "local_patch": {"bool": False, "upstream_commit": None, **m.get("local_patch", {})},
        "dependencies": sorted(m.get("dependencies", [])),
        "license": m.get("license") or (DEFAULT_LICENSE if owner in AUTHORED_OWNERS else None),
    }


def collect(store: Path) -> list[dict]:
    """Build one row per populated identity directory, asserting the store is unambiguous."""
    meta = load_meta(store)
    rows: list[dict] = []
    by_id: dict[str, str] = {}
    by_hash: dict[str, str] = {}
    for skill_dir in identity_dirs(store):
        row = identity_row(store, skill_dir, meta)
        if row["id"] in by_id:
            raise ManifestError(
                f"duplicate identity {row['id']}: {by_id[row['id']]} and {row['path']}"
            )
        if row["package_sha256"] in by_hash:
            raise ManifestError(
                f"identical content in two identity directories "
                f"({by_hash[row['package_sha256']]} and {row['path']}); "
                f"identical content is one identity with aliases (ADR-0009)"
            )
        by_id[row["id"]] = row["path"]
        by_hash[row["package_sha256"]] = row["path"]
        rows.append(row)
    return sorted(rows, key=lambda r: r["id"])


def build(store: Path) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "generator": "scripts/store_manifest.py",
        "identities": collect(store),
    }


def serialize(manifest: dict) -> str:
    """Canonical serialization: sorted keys, stable indent, no timestamps."""
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate(store: Path, manifest_path: Path | None = None) -> Path:
    out = manifest_path or (store / MANIFEST_NAME)
    out.write_text(serialize(build(store)))
    return out


def verify(store: Path, manifest_path: Path | None = None) -> list[str]:
    path = manifest_path or (store / MANIFEST_NAME)
    problems: list[str] = []
    if not path.exists():
        return [f"manifest not found: {path}"]
    try:
        committed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"manifest is not valid JSON: {exc}"]
    try:
        expected = build(store)
    except ManifestError as exc:
        return [str(exc)]

    have = {r["id"]: r for r in committed.get("identities", [])}
    want = {r["id"]: r for r in expected["identities"]}
    for missing in sorted(set(want) - set(have)):
        problems.append(f"identity missing from manifest: {missing}")
    for extra in sorted(set(have) - set(want)):
        problems.append(f"manifest row has no identity directory: {extra}")
    for ident in sorted(set(have) & set(want)):
        if have[ident] != want[ident]:
            problems.append(f"manifest drift for {ident}: {want[ident]['path']}")
    if committed.get("manifest_version") != MANIFEST_VERSION:
        problems.append(f"manifest_version is {committed.get('manifest_version')!r}, "
                        f"expected {MANIFEST_VERSION}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--store", default=str(Path.home() / "skill-store"),
                        help="Skill Store root (default: ~/skill-store)")
    parser.add_argument("--manifest", default=None, help=f"manifest path (default: <store>/{MANIFEST_NAME})")
    args = parser.parse_args(argv)
    store = Path(args.store).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser() if args.manifest else None

    if args.command == "generate":
        out = generate(store, manifest_path)
        rows = json.loads(out.read_text())["identities"]
        print(f"wrote {out} ({len(rows)} identities)")
        return 0

    problems = verify(store, manifest_path)
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, indent=2))
        return 1
    print(json.dumps({"ok": True, "store": str(store)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
