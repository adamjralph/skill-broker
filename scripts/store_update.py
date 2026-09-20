#!/usr/bin/env python3
"""Update one Skill Store identity from its upstream, reconciling Local Patches.

Implements the `store update <id>` flow of
[Decide how upstream updates and local patches reconcile inside the store](ADR-0009/0016;
wayfinder #19), now that the store and its committed Store Manifest exist. This is the
"library repair" half of the Stage 2-3 store-build fog: it restores update paths for
vendored content without touching any original source or Consumer (ADR-0012).

Per identity:

- resolve the target commit from the supplied checkout (the newest commit on ``--ref``
  touching the Skill's upstream path);
- no Local Patch -> replace the canonical content with the target and record the new
  provenance commit;
- Local Patch present -> a three-way merge (base = ``local_patch.upstream_commit``,
  ours = current store content, theirs = target); a clean merge mints a new Version, moves
  ``local_patch.upstream_commit`` to the target, and re-applies the patch;
- no-op when the target content already matches the store (the recorded ``provenance.commit``
  is often the upstream repo HEAD, not the last commit touching the Skill, so a content
  comparison, not a commit comparison, decides);
- a conflict fails closed: store content, metadata, and manifest are left unchanged, a
  conflict report listing the hunks is written for Adam's resolution, and no commit is made;
- a successful update re-generates and verifies the Store Manifest and makes exactly one
  store commit.

The upstream path is ``provenance.path`` when recorded, else discovered at ``--ref`` by a
unique frontmatter-name match, else supplied with ``--path``. Pseudo-owner identities (no
upstream repo) are unsupported. ``--checkout REPO=PATH`` supplies the read-only git checkout
for each provenance repo; ``--fetch`` refreshes it first.

Recorded implementation defaults (reversible):

- upstream trees are materialized with ``git archive`` so ``.gitattributes`` eol/text
  normalization matches the working tree the content was vendored from; upstreams relying on
  ``export-ignore``/``export-subst`` inside a Skill package fail closed via the divergence
  check rather than silently dropping files;
- a non-patch identity whose store content diverges from its recorded upstream commit fails
  closed, rather than silently overwriting an unrecorded local edit;
- the store must be a clean git working tree, so a failed update cannot sweep unrelated
  changes into its commit and one commit is exactly one update;
- conflict reports default to ``$XDG_STATE_HOME/skill-broker/conflicts/<owner>.<name>.patch``
  (machine-local, outside the store) and can be overridden with ``--report``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_audit import hash_package, package_files  # noqa: E402
import store_manifest as manifest  # noqa: E402
import store_vendor as vendor  # noqa: E402

METADATA_DIR = ".skill-broker"
PATCH_PATH = f"{METADATA_DIR}/local.patch"
DEFAULT_REPORT_DIR = (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                      / "skill-broker" / "conflicts")
_NAME_RE = re.compile(r"^name:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


class UpdateError(Exception):
    """The update cannot proceed safely (bad input, drift, or an unsupported identity)."""


@dataclass
class UpdateResult:
    id: str
    status: str  # "no-op" | "updated" | "conflict"
    old_version: str
    new_version: str | None
    old_commit: str | None
    new_commit: str | None
    upstream_path: str | None
    patch: bool
    report: Path | None = None
    commit: str | None = None


# --------------------------------------------------------------------------- content


def _content_files(skill_dir: Path) -> list[Path]:
    """Normalized package files except the reserved metadata directory."""
    return [f for f in package_files(skill_dir)
            if f.relative_to(skill_dir).parts[0] != METADATA_DIR]


def copy_content(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for file in _content_files(source):
        dest = target / file.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)


def _replace_tree(work: Path, source: Path) -> None:
    for child in list(work.iterdir()):
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    copy_content(source, work)


def _publish(destination: Path, content: Path) -> None:
    """Replace an identity directory with a fully-built package tree."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for file in package_files(content):
        dest = destination / file.relative_to(content)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)


# --------------------------------------------------------------------------- git


def _commit(work: Path, message: str) -> None:
    vendor.git(work, "-c", "user.name=Skill Store", "-c", "user.email=store@localhost",
               "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
               "commit", "--quiet", "--allow-empty", "-m", message)


def _rev(work: Path, ref: str) -> str:
    return vendor.git(work, "rev-parse", ref).decode().strip()


def _frontmatter_name(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    match = _NAME_RE.search(text[3:end])
    return match.group(1).strip() if match else ""


def _skill_names(checkout: Path, ref: str) -> dict[str, list[str]]:
    """frontmatter name -> upstream directory paths holding a matching SKILL.md at ``ref``."""
    listing = vendor.git(checkout, "ls-tree", "-r", ref).decode(errors="replace")
    entries: list[tuple[str, str]] = []
    for line in listing.splitlines():
        if not line:
            continue
        meta_part, path = line.split("\t", 1)
        _mode, kind, oid = meta_part.split()
        if kind == "blob" and path.endswith("SKILL.md"):
            entries.append((oid, path))
    names: dict[str, list[str]] = {}
    if not entries:
        return names
    requested = "".join(f"{oid}\n" for oid, _ in entries).encode()
    proc = subprocess.run(vendor.git_command(checkout, "cat-file", "--batch"),
                          input=requested, capture_output=True, env=vendor.git_environment())
    if proc.returncode:
        raise UpdateError(proc.stderr.decode(errors="replace"))
    out, pos = proc.stdout, 0
    for _oid, path in entries:
        end = out.index(b"\n", pos)
        _name, _type, size = out[pos:end].split()
        size = int(size)
        body = out[end + 1:end + 1 + size]
        pos = end + 1 + size + 1
        name = _frontmatter_name(body.decode("utf-8", "replace"))
        if name:
            names.setdefault(name, []).append(str(PurePosixPath(path).parent))
    return names


def resolve_upstream_path(checkout: Path, ref: str, name: str,
                          declared: str | None) -> str:
    if declared:
        vendor.relative_path(declared)
        return declared
    matches = sorted(set(_skill_names(checkout, ref).get(name, [])))
    if len(matches) != 1:
        raise UpdateError(
            f"cannot resolve upstream path for {name!r} at {ref}: {len(matches)} matches; "
            f"record provenance.path or pass --path")
    return matches[0]


def target_commit(checkout: Path, ref: str, upstream_path: str) -> str:
    commit = vendor.git(checkout, "rev-list", "-1", ref, "--", upstream_path).decode().strip()
    if not commit:
        raise UpdateError(f"no commit on {ref} touches upstream path {upstream_path!r}")
    return commit


def materialize(checkout: Path, commit: str, upstream_path: str, destination: Path) -> None:
    """Materialize an upstream package tree at ``commit`` as a git checkout would.

    ``git archive`` honours ``.gitattributes`` eol/text normalization, so the result
    matches how the content was vendored from a working tree (unlike raw ``cat-file``
    blobs). Non-regular entries fail closed, as in vendoring.
    """
    vendor.relative_path(upstream_path)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise UpdateError("upstream commits require a full commit hash")
    proc = subprocess.run(
        vendor.git_command(checkout, "archive", "--format=tar", commit, "--", upstream_path),
        capture_output=True, env=vendor.git_environment())
    if proc.returncode:
        raise UpdateError(proc.stderr.decode(errors="replace").strip())
    prefix = PurePosixPath(upstream_path)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise UpdateError(f"non-regular upstream input: {member.name!r}")
            rel = PurePosixPath(member.name)
            if prefix not in rel.parents:
                raise UpdateError(f"archive entry outside {upstream_path!r}: {member.name!r}")
            file = destination / str(rel.relative_to(prefix))
            file.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source:
                file.write_bytes(source.read())
            file.chmod(0o755 if member.mode & 0o111 else 0o644)
    if not (destination / "SKILL.md").is_file():
        raise UpdateError(f"upstream package has no SKILL.md: {upstream_path}")


def merge_content(base: Path, ours: Path, theirs: Path, work: Path) -> tuple[list[str], str]:
    """Three-way merge of package content; returns (conflicted files, combined diff)."""
    work.mkdir(parents=True, exist_ok=True)
    vendor.git(work, "init", "--quiet", "--template=")
    attributes = work / ".git" / "info" / "attributes"
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text("* -text -filter -ident -working-tree-encoding !diff\n")

    _replace_tree(work, base)
    vendor.git(work, "add", "--all", "--force")
    _commit(work, "base")
    base_sha = _rev(work, "HEAD")

    _replace_tree(work, ours)
    vendor.git(work, "add", "--all", "--force")
    _commit(work, "ours")
    ours_sha = _rev(work, "HEAD")

    vendor.git(work, "checkout", "--quiet", "--detach", base_sha)
    _replace_tree(work, theirs)
    vendor.git(work, "add", "--all", "--force")
    _commit(work, "theirs")
    theirs_sha = _rev(work, "HEAD")

    vendor.git(work, "checkout", "--quiet", "--detach", ours_sha)
    proc = subprocess.run(
        vendor.git_command(work, "-c", "user.name=Skill Store",
                           "-c", "user.email=store@localhost",
                           "-c", "core.hooksPath=/dev/null",
                           "-c", "commit.gpgSign=false",
                           "merge", "--no-ff", "--no-edit", theirs_sha),
        capture_output=True, env=vendor.git_environment())
    if proc.returncode:
        conflicted = vendor.git(work, "diff", "--name-only", "--diff-filter=U").decode().split()
        if not conflicted:
            raise UpdateError(f"merge failed: {proc.stderr.decode(errors='replace').strip()}")
        diff = vendor.git(work, "diff", "--cc").decode(errors="replace")
        if not diff:
            diff = vendor.git(work, "diff").decode(errors="replace")
        return conflicted, diff
    return [], ""


# --------------------------------------------------------------------------- flow


def _write_report(path: Path, identity: str, base_commit: str, target: str,
                  old_version: str, conflicted: list[str], diff: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"Local Patch conflict: {identity}\n"
        f"base (upstream_commit): {base_commit}\n"
        f"ours (store Version):   {old_version}\n"
        f"theirs (target commit): {target}\n\n"
        f"conflicted files:\n" + "".join(f"  - {name}\n" for name in conflicted)
        + "\n--- combined diff ---\n" + diff)
    return path


def _store_changes(store: Path) -> str:
    return vendor.git(store, "status", "--porcelain").decode()


def update(store: Path, identity: str, checkouts: dict[str, Path], *,
           upstream_path: str | None = None, ref: str = "HEAD",
           report: Path | None = None, fetch: bool = False) -> UpdateResult:
    store = Path(store).expanduser().resolve()
    if (store / ".git").exists() and _store_changes(store):
        raise UpdateError("store has uncommitted changes; update requires a clean tree")
    rows = manifest.collect(store)
    row = next((r for r in rows if r["id"] == identity), None)
    if row is None:
        raise UpdateError(f"unknown identity: {identity}")
    rel = row["path"]
    meta = manifest.load_meta(store)
    entry = meta.get(rel, {})

    provenance = row["provenance"]
    repo = provenance.get("repo")
    if not repo:
        raise UpdateError(f"{identity} has no upstream provenance (pseudo-owner); unsupported")
    if repo not in checkouts:
        raise UpdateError(f"missing checkout for {repo!r}; pass --checkout {repo}=<path>")
    checkout = Path(checkouts[repo]).expanduser()
    if fetch:
        vendor.git(checkout, "fetch", "--prune", "origin")

    path = resolve_upstream_path(checkout, ref, row["name"], upstream_path or provenance.get("path"))
    target = target_commit(checkout, ref, path)
    patch = bool(row["local_patch"]["bool"])
    old_version = row["package_sha256"]
    old_commit = provenance.get("commit")
    base_commit = row["local_patch"].get("upstream_commit") if patch else None
    if patch and target == base_commit:
        return UpdateResult(identity, "no-op", old_version, old_version, old_commit,
                            target, path, True)
    if not patch and target == old_commit:
        return UpdateResult(identity, "no-op", old_version, old_version, old_commit,
                            target, path, False)

    with tempfile.TemporaryDirectory(prefix="skill-update-") as tmp_name:
        tmp = Path(tmp_name)
        theirs = tmp / "theirs"
        materialize(checkout, target, path, theirs)
        report_path = None
        if patch:
            base = tmp / "base"
            materialize(checkout, base_commit, path, base)
            ours = tmp / "ours"
            copy_content(store / rel, ours)
            merged = tmp / "merged"
            conflicted, diff = merge_content(base, ours, theirs, merged)
            if conflicted:
                report_path = report or (DEFAULT_REPORT_DIR / f"{rel.replace('/', '.')}.patch")
                _write_report(report_path, identity, base_commit, target, old_version,
                              conflicted, diff)
                return UpdateResult(identity, "conflict", old_version, None, old_commit,
                                    target, path, True, report_path)
            if hash_package(merged)[0] == hash_package(ours)[0]:
                return UpdateResult(identity, "no-op", old_version, old_version, old_commit,
                                    target, path, True)
            final = tmp / "final"
            copy_content(merged, final)
            patch_bytes = vendor.make_patch(theirs, merged)
        else:
            current = tmp / "current"
            copy_content(store / rel, current)
            baseline = tmp / "baseline"
            materialize(checkout, old_commit, path, baseline)
            if hash_package(baseline)[0] != old_version:
                raise UpdateError(
                    f"{identity} store content diverges from {repo}@{old_commit[:12]} "
                    f"({path}); record a Local Patch or correct provenance")
            if hash_package(theirs)[0] == hash_package(current)[0]:
                return UpdateResult(identity, "no-op", old_version, old_version, old_commit,
                                    target, path, False)
            final = tmp / "final"
            copy_content(theirs, final)
            patch_bytes = None
        if patch_bytes is not None:
            artifact = final / PATCH_PATH
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(patch_bytes)
        new_version = hash_package(final)[0]
        if new_version == old_version:
            raise UpdateError(f"{identity} target {target} yields no content change")

        new_entry = json.loads(json.dumps(entry))
        new_entry.setdefault("provenance", {})["commit"] = target
        if patch_bytes is not None:
            local = new_entry.setdefault("local_patch", {})
            local["bool"] = True
            local["upstream_commit"] = target
            local["path"] = PATCH_PATH

        destination = store / rel
        backup_dir = tmp / "backup-dir"
        if destination.exists():
            shutil.copytree(destination, backup_dir)
        meta_bytes = (store / manifest.META_NAME).read_bytes()
        manifest_bytes = (store / manifest.MANIFEST_NAME).read_bytes()
        try:
            _publish(destination, final)
            meta[rel] = new_entry
            (store / manifest.META_NAME).write_text(manifest.serialize(meta))
            manifest.generate(store)
            problems = manifest.verify(store)
            if problems:
                raise UpdateError(f"post-update manifest verification failed: {problems}")
        except BaseException:
            if destination.exists():
                shutil.rmtree(destination)
            if backup_dir.exists():
                shutil.copytree(backup_dir, destination)
            (store / manifest.META_NAME).write_bytes(meta_bytes)
            (store / manifest.MANIFEST_NAME).write_bytes(manifest_bytes)
            raise

        commit = None
        if (store / ".git").exists():
            vendor.git(store, "add", "-A")
            vendor.git(store, "-c", "user.name=Skill Store",
                       "-c", "user.email=store@localhost",
                       "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                       "commit", "--quiet", "-m", f"Update {identity} to {target}")
            commit = _rev(store, "HEAD")
    return UpdateResult(identity, "updated", old_version, new_version, old_commit, target,
                        path, patch, None, commit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("update",))
    parser.add_argument("--store", default=str(Path.home() / "skill-store"))
    parser.add_argument("--id", required=True, help="identity to update, <owner>.<name>")
    parser.add_argument("--checkout", action="append", default=[], metavar="REPO=PATH")
    parser.add_argument("--path", default=None, help="override the upstream Skill path")
    parser.add_argument("--ref", default="HEAD", help="upstream ref to resolve against")
    parser.add_argument("--report", type=Path, default=None, help="conflict report path")
    parser.add_argument("--fetch", action="store_true", help="fetch the checkout first")
    args = parser.parse_args(argv)
    try:
        checkouts: dict[str, Path] = {}
        for value in args.checkout:
            repo, path = value.split("=", 1)
            checkouts[repo] = Path(path)
        result = update(Path(args.store), args.id, checkouts, upstream_path=args.path,
                        ref=args.ref, report=args.report, fetch=args.fetch)
    except (UpdateError, vendor.VendorError, manifest.ManifestError,
            OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    payload = {k: v for k, v in vars(result).items() if v is not None}
    print(json.dumps(payload, indent=2, default=str))
    return 2 if result.status == "conflict" else 0


if __name__ == "__main__":
    raise SystemExit(main())
