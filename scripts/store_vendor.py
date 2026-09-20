#!/usr/bin/env python3
"""Stage an audited Skill Store in a NEW directory; never alter sources or consumers.

The reviewed JSON plan supplies notices, licence/provenance overrides, and pinned patch
bases. Git checkouts are read-only inputs supplied as --checkout REPO=PATH. The complete
store is built and verified in a temporary sibling before publishing it. Existing output
paths are refused (including symlinks); installation into a live skeleton is a separate,
reviewed copy step. No fetching, committing, consumer wiring, or deletion is performed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_audit import hash_package, package_files
import store_manifest as manifest


class VendorError(Exception):
    """An ambiguous, stale, or incomplete vendoring input."""


def git_command(checkout: Path, *args: str) -> list[str]:
    return ["git", "-C", str(checkout), "-c", "core.autocrlf=false",
            "-c", "core.attributesFile=/dev/null", *args]


def git_environment() -> dict[str, str]:
    # Do not inherit injected repositories, index files, templates or global filters.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(checkout: Path, *args: str) -> bytes:
    result = subprocess.run(git_command(checkout, *args), capture_output=True,
                            env=git_environment())
    if result.returncode:
        raise VendorError(result.stderr.decode(errors="replace"))
    return result.stdout


def relative_path(value: str) -> Path:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or str(p) != value:
        raise VendorError(f"unsafe relative path: {value!r}")
    return Path(value)


def select_versions(rows: list[dict]) -> list[dict]:
    """ADR-0016: Fork patches win; superseded profile copies never win."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["id"]].append(row)
    selected = []
    for identity, versions in sorted(groups.items()):
        current = [r for r in versions if r.get("version_status") != "superseded-stale-profile-copy"]
        patches = [r for r in current if r["local_patch"]]
        candidates = patches or current
        if len(candidates) != 1:
            raise VendorError(f"ambiguous canonical Version: {identity}")
        selected.append(candidates[0])
    return selected


def copy_package(source: Path, target: Path) -> None:
    """Materialize the audit's normalized file set, never retain external symlinks."""
    target.mkdir(parents=True, exist_ok=True)
    for file in package_files(source):
        dest = target / file.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)


def extract_base(checkout: Path, commit: str, subpath: str, target: Path) -> None:
    """Read pinned git objects, not a checkout's possibly edited working tree."""
    relative_path(subpath)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise VendorError("patch bases require a full commit hash")
    # Raw objects avoid git archive's export-ignore/export-subst transformations,
    # including overrides from a checkout's uncommitted .git/info/attributes.
    tree = git(checkout, "ls-tree", "-rz", "--full-tree", commit, "--", subpath)
    prefix = PurePosixPath(subpath)
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, filename = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        rel = PurePosixPath(os.fsdecode(filename)).relative_to(prefix)
        path = relative_path(str(rel))
        if kind != "blob" or mode not in ("100644", "100755"):
            raise VendorError(f"non-regular upstream patch input: {filename!r}")
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git(checkout, "cat-file", "blob", oid))
        dest.chmod(0o755 if mode == "100755" else 0o644)
    if not (target / "SKILL.md").is_file():
        raise VendorError(f"patch base has no SKILL.md: {subpath}")


def make_patch(base: Path, current: Path) -> bytes:
    """Produce a binary-capable, package-relative diff and prove it reconstructs ours."""
    with tempfile.TemporaryDirectory(prefix="skill-patch-") as tmp:
        work = Path(tmp)
        copy_package(base, work)
        git(work, "init", "--quiet", "--template=")
        attributes = work / ".git" / "info" / "attributes"
        attributes.parent.mkdir(exist_ok=True)
        attributes.write_text("* -text -filter -ident -working-tree-encoding !diff\n")
        git(work, "add", "--all", "--force")
        git(work, "-c", "user.name=Skill Vendor", "-c", "user.email=vendor@localhost",
            "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
            "commit", "--quiet", "-m", "patch base")
        # Only the disposable reconstruction tree is cleared, never either input.
        for child in work.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        copy_package(current, work)
        git(work, "add", "--all", "--force")
        patch = git(work, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff",
                    "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/")
        if not patch:
            raise VendorError("declared Local Patch is empty")
        git(work, "reset", "--hard", "--quiet", "HEAD")
        result = subprocess.run(git_command(work, "apply", "--binary", "-"),
                                input=patch, capture_output=True, env=git_environment())
        if result.returncode or hash_package(work)[0] != hash_package(current)[0]:
            raise VendorError("patch reconstruction failed")
        return patch


def populate(index: dict, plan: dict, store: Path, checkouts: dict[str, Path]) -> None:
    rows = select_versions(index["identities"])
    if len(rows) != plan["expected_identities"]:
        raise VendorError("identity count differs from reviewed plan")
    ids = {r["id"] for r in rows}
    for field in ("overrides", "patches"):
        if set(plan.get(field, {})) - ids:
            raise VendorError(f"unknown identities in {field}")
    metadata, receipt, paths = {}, [], set()
    for row in rows:
        identity = row["id"]
        owner, name = row["owner"], row["name"]
        if identity != f"{owner}.{name}" or not re.fullmatch(r"[a-z0-9_-]+", owner):
            raise VendorError(f"invalid admitted identity: {identity}")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            raise VendorError(f"empty slug: {identity}")
        rel = f"{owner}/{slug}"
        if rel in paths:
            raise VendorError(f"directory collision: {rel}")
        paths.add(rel)
        source = Path(row["canonical_source"])
        if hash_package(source)[0] != row["package_sha256"]:
            raise VendorError(f"source drift: {identity}")
        target = store / rel
        copy_package(source, target)
        if hash_package(target)[0] != row["package_sha256"]:
            raise VendorError(f"copy hash mismatch: {identity}")
        if (target / ".skill-broker").exists():
            raise VendorError(f"reserved metadata directory in source: {identity}")
        override = plan.get("overrides", {}).get(identity, {})
        provenance = {**row["provenance"], **override.get("provenance", {})}
        license_id = override.get("license", row.get("license"))
        if not license_id:
            raise VendorError(f"unresolved licence: {identity}")
        notice_key = override.get("notice", owner)
        if license_id != "LicenseRef-Proprietary" and notice_key not in plan["notices"]:
            raise VendorError(f"missing upstream notice: {identity}")
        patch_spec = plan.get("patches", {}).get(identity)
        if row["local_patch"] and not patch_spec:
            raise VendorError(f"missing patch base: {identity}")
        local_patch = {"bool": bool(patch_spec), "upstream_commit": None}
        # All aliases of the selected identity survive, including the base Version's.
        aliases = sorted({a for r in index["identities"] if r["id"] == identity
                          for a in r.get("aliases", [])})
        if patch_spec:
            repo, commit = patch_spec["repo"], patch_spec["commit"]
            if repo not in checkouts:
                raise VendorError(f"missing checkout: {repo}")
            # Apache modifications are prominently identified inside the modified file.
            if override.get("modification_notice"):
                md = target / "SKILL.md"
                md.write_bytes(md.read_bytes() + ("\n<!-- " + override["modification_notice"]
                                                 + " -->\n").encode())
            with tempfile.TemporaryDirectory(prefix="skill-base-") as tmp:
                base = Path(tmp)
                extract_base(checkouts[repo], commit, patch_spec["path"], base)
                patch = make_patch(base, target)
            artifact = target / ".skill-broker" / "local.patch"
            artifact.parent.mkdir()
            artifact.write_bytes(patch)
            local_patch = {"bool": True, "upstream_commit": commit,
                           "path": ".skill-broker/local.patch"}
        metadata[rel] = {"name": name, "aliases": aliases, "provenance": provenance,
                         "local_patch": local_patch, "dependencies": row.get("dependencies", []),
                         "license": license_id}
        receipt.append({"id": identity, "source": str(source),
                        "source_package_sha256": row["package_sha256"],
                        "store_package_sha256": hash_package(target)[0]})
    (store / manifest.META_NAME).write_text(manifest.serialize(metadata))
    notices = ["Skill Store — upstream notices\n\nAuthored content: LicenseRef-Proprietary.\n"
               "Admitted IDs are unchanged; verified provenance can correct the audit.\n"]
    for key, notice in sorted(plan["notices"].items()):
        notices.append(f"\n=== {key} ===\nSource: {notice['source']}\n\n{notice['text']}\n")
    (store / "NOTICE").write_text("".join(notices).rstrip() + "\n")
    control = store / ".skill-broker"
    control.mkdir()
    (control / "vendor-plan.json").write_text(manifest.serialize(plan))
    (control / "vendor-receipt.json").write_text(manifest.serialize(receipt))
    manifest.generate(store)
    errors = manifest.verify(store)
    if errors or len(manifest.collect(store)) != len(rows):
        raise VendorError(f"manifest verification failed: {errors}")


def vendor(index_path: Path, plan_path: Path, destination: Path,
           checkouts: dict[str, Path]) -> None:
    if os.path.lexists(destination):
        raise VendorError(f"output must not already exist: {destination}")
    index_bytes = index_path.read_bytes()
    plan = json.loads(plan_path.read_text())
    if hashlib.sha256(index_bytes).hexdigest() != plan["audit_sha256"]:
        raise VendorError("audit differs from reviewed plan")
    with tempfile.TemporaryDirectory(prefix=".skill-vendor-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "store"
        stage.mkdir()
        populate(json.loads(index_bytes), plan, stage, checkouts)
        if os.path.lexists(destination):
            raise VendorError("output appeared during staging")
        stage.rename(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--checkout", action="append", default=[], metavar="REPO=PATH")
    args = parser.parse_args(argv)
    try:
        checkouts = {}
        for value in args.checkout:
            repo, path = value.split("=", 1)
            checkouts[repo] = Path(path).expanduser()
        vendor(args.index, args.plan, args.store, checkouts)
    except (VendorError, manifest.ManifestError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Staged and verified {args.store}; no consumers changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
