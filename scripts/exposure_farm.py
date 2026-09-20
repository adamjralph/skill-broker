#!/usr/bin/env python3
"""Generate and verify machine-local Exposure Farms (ADR-0011).

Exposure Manifest v1 (explicit input; Hermes policy derivation is separate)::

    {"manifest_version": 1, "consumer": "example",
     "exposures": [{"name": "writing", "id": "local.writing"}]}

Usage::

    python3 scripts/exposure_farm.py validate --store ~/skill-store --manifest exposures.json
    python3 scripts/exposure_farm.py generate --store ~/skill-store --manifest exposures.json --farm /tmp/example
    python3 scripts/exposure_farm.py verify --store ~/skill-store --manifest exposures.json --farm /tmp/example

Reversible implementation defaults: flat, case-sensitive exposure names match
[A-Za-z0-9][A-Za-z0-9_.-]* (at most 255 bytes); IDs are not restricted to that spelling.
List rows preserve collisions for diagnostics. Multiple aliases for one ID are allowed.
The generated receipt pins IDs, absolute canonical targets, and store package Versions.
Every command re-verifies the whole Store Manifest. No policy or loader configuration
is inferred. validate is read-only and needs no farm (suitable for store-repo CI).

Farms must be new paths or directories owned by this generator. Unknown entries,
modified links, symlink farm roots, and consumer/store mismatches fail closed, even
with --prune. Removing any previously generated exposure requires --prune; without
it generation fails rather than silently retaining now-withheld skills. No original
Skill paths are adopted, overwritten, or pruned.

Linux renameat2 atomically publishes a sibling staging directory (RENAME_NOREPLACE
initially, RENAME_EXCHANGE on updates); unsupported systems/filesystems fail closed.
A sibling advisory flock serializes cooperating generators and verifiers. Readers see
an old or new directory, not a partially built farm; multi-file reader transactions
and concurrent external edits of farms/the store are not supported. This is atomic
publication, not a power-loss durability guarantee. The hidden lock file persists.
After publication only generated old links/receipt are removed; cleanup failure is a
warning and leaves a hidden staging directory, never rolls back the published farm.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_manifest as sm  # noqa: E402

RECEIPT = ".skill-broker-farm.json"
GENERATOR = "scripts/exposure_farm.py"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class FarmError(Exception):
    """Input or farm state cannot safely be used."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FarmError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path):
    return json.loads(path.read_text(), object_pairs_hook=_object)


def _name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value) or len(value.encode()) > 255:
        raise FarmError(f"unsafe exposure name: {value!r}")
    return value


def _plan(store: Path, manifest: Path) -> dict:
    if not store.is_dir():
        raise FarmError(f"store not found: {store}")
    committed = _load(store / sm.MANIFEST_NAME)
    # Full equality also rejects duplicate rows/extra fields that a keyed comparison loses.
    expected = sm.build(store)
    if committed != expected:
        raise FarmError("Store Manifest drift; regenerate and review it first")
    identities = {row["id"]: row for row in expected["identities"]}
    data = _load(manifest)
    if (not isinstance(data, dict) or set(data) != {"manifest_version", "consumer", "exposures"}
            or type(data["manifest_version"]) is not int or data["manifest_version"] != 1
            or not isinstance(data["consumer"], str) or not data["consumer"].strip()
            or not isinstance(data["exposures"], list)):
        raise FarmError("expected Exposure Manifest v1 with consumer and exposures list")
    entries = {}
    for row in data["exposures"]:
        if not isinstance(row, dict) or set(row) != {"name", "id"}:
            raise FarmError("each exposure must contain exactly name and id")
        name, ident = _name(row["name"]), row["id"]
        if not isinstance(ident, str) or ident not in identities:
            raise FarmError(f"unknown ID: {ident!r}")
        if name in entries:
            raise FarmError(f"exposure collision {name!r}: {entries[name]['id']} and {ident}")
        skill = identities[ident]
        target = store / skill["path"]
        if target.resolve() != target or not target.is_relative_to(store):
            raise FarmError(f"canonical identity directory must not be a symlink: {target}")
        entries[name] = {"id": ident, "target": str(target),
                         "package_sha256": skill["package_sha256"]}
    return {"manifest_version": 1, "generator": GENERATOR,
            "consumer": data["consumer"], "store": str(store), "entries": entries}


def _owned(farm: Path, plan: dict) -> dict | None:
    if not os.path.lexists(farm):
        return None
    if farm.is_symlink() or not farm.is_dir():
        raise FarmError(f"refusing unmanaged farm path: {farm}")
    receipt = farm / RECEIPT
    if receipt.is_symlink() or not receipt.is_file():
        raise FarmError(f"missing generator ownership receipt: {farm}")
    old = _load(receipt)
    if (not isinstance(old, dict) or set(old) != set(plan)
            or old.get("manifest_version") != 1 or old.get("generator") != GENERATOR
            or old.get("consumer") != plan["consumer"] or old.get("store") != plan["store"]
            or not isinstance(old.get("entries"), dict)):
        raise FarmError("farm ownership/consumer/store mismatch")
    for name, row in old["entries"].items():
        _name(name)
        if (not isinstance(row, dict) or set(row) != {"id", "target", "package_sha256"}
                or not all(isinstance(value, str) for value in row.values())
                or not re.fullmatch(r"[0-9a-f]{64}", row["package_sha256"])):
            raise FarmError(f"invalid ownership row: {name}")
        target = Path(row["target"])
        if not target.is_absolute() or ".." in target.parts or not target.is_relative_to(Path(plan["store"])):
            raise FarmError(f"invalid owned target: {name}")
        link = farm / name
        if not link.is_symlink() or os.readlink(link) != row["target"]:
            raise FarmError(f"modified or missing generated link: {name}")
    actual = {p.name for p in farm.iterdir()}
    if actual != set(old["entries"]) | {RECEIPT}:
        raise FarmError("unmanaged entries in farm; refusing to modify or prune them")
    return old


@contextmanager
def _lock(farm: Path, exclusive: bool):
    lock = farm.parent / f".{farm.name}.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


def _publish(stage: Path, farm: Path, existing: bool):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise FarmError("atomic directory publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    flag = 2 if existing else 1  # RENAME_EXCHANGE / RENAME_NOREPLACE
    if rename(-100, os.fsencode(stage), -100, os.fsencode(farm), flag):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(farm))


def _discard(stage: Path):
    # Never recurse into a link target or remove unexpected content.
    for path in stage.iterdir():
        if path.is_symlink() or (path.name == RECEIPT and path.is_file()):
            path.unlink()
        else:
            raise FarmError(f"unexpected staging content left untouched: {path}")
    stage.rmdir()


def run(command: str, store: Path, manifest: Path, farm: Path | None = None,
        *, prune: bool = False) -> dict:
    """Validate inputs, generate atomically, or verify exact declared links and Versions."""
    if command not in {"validate", "generate", "verify"}:
        raise FarmError(f"unknown command: {command}")
    if prune and command != "generate":
        raise FarmError("--prune is only valid with generate")
    store, manifest = store.expanduser().resolve(), manifest.expanduser().resolve()
    plan = _plan(store, manifest)
    result = {"ok": True, "consumer": plan["consumer"], "exposures": len(plan["entries"])}
    if command == "validate":
        return result
    if farm is None:
        raise FarmError("--farm is required for generate/verify")
    # Resolve parents, not the final component: a farm-root symlink must be refused.
    farm = farm.expanduser().absolute()
    farm = farm.parent.resolve() / farm.name
    if (farm == farm.parent or farm.is_relative_to(store) or store.is_relative_to(farm)
            or manifest.is_relative_to(farm)):
        raise FarmError("farm must be separate from the store and input manifest")
    with _lock(farm, command == "generate"):
        old = _owned(farm, plan)
        if command == "verify":
            if old != plan:
                raise FarmError("farm differs from declared exposures/Versions; generate it first")
            return result
        stale = set(old["entries"]) - set(plan["entries"]) if old else set()
        if stale and not prune:
            raise FarmError(f"removing exposures requires --prune: {', '.join(sorted(stale))}")
        if old == plan:
            return {**result, "changed": False}
        stage = Path(tempfile.mkdtemp(prefix=f".{farm.name}.stage-", dir=farm.parent))
        try:
            for name, row in plan["entries"].items():
                (stage / name).symlink_to(row["target"], target_is_directory=True)
            (stage / RECEIPT).write_text(sm.serialize(plan))
            if _owned(stage, plan) != plan or _plan(store, manifest) != plan:
                raise FarmError("inputs changed during generation")
            if _owned(farm, plan) != old:
                raise FarmError("farm changed during generation")
            _publish(stage, farm, old is not None)
        finally:
            if stage.exists():
                try:
                    _discard(stage)
                except (OSError, FarmError) as exc:
                    print(f"warning: staging cleanup failed: {exc}", file=sys.stderr)
        return {**result, "changed": True, "pruned": sorted(stale)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("validate", "generate", "verify"))
    parser.add_argument("--store", type=Path, default=Path.home() / "skill-store")
    parser.add_argument("--manifest", type=Path, required=True, help="explicit Exposure Manifest JSON")
    parser.add_argument("--farm", type=Path, help="machine-local farm path (parent must exist)")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.command, args.store, args.manifest, args.farm, prune=args.prune)
    except (FarmError, sm.ManifestError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
