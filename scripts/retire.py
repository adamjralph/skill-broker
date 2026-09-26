#!/usr/bin/env python3
"""Deterministic, archive-only Stage 9 retirement (ADR-0023).

First-batch safety boundary: one exact profile SKILL directory, never its parent.
No purge exists. All four proposal kinds are recognised; canonical store removal
and excluded-root withdrawal remain blocked pending a safe manifest/cutover plan.

propose requires --store, --audit, --evidence, --profile and --path. Evidence is
an input-location inventory, not a list of assertions: {"evidence_version": 1,
"consumers": [{"profile": "...", "skills_root": "/.../profiles/.../skills",
"config": "/.../config.yaml", "manifest": "/.../exposures.json",
"farm": "/.../farm", "usage": "/.../.usage.json", "cron": "/.../jobs.json",
"gate": "/.../gate.json", "incidents": "/.../gate_incidents.jsonl"}]}.
Every store policy must have exactly one consumer entry. Missing evidence blocks;
this inventory must be independently reviewed for machine-wide completeness.
Usage is the curator's name-keyed mapping; jobs use {"jobs": [...]}. Gate files
use the broker's native formats. Absent logs are not evidence of no incidents.

The manifest and audit must match Git HEAD. approve records an explicit Adam
review against --digest; it does not manufacture authorisation. Commit the JSON
and companion <batch>/report.md before apply. No command commits anything.
Artifacts are canonical JSON; artifact_sha256 covers everything except approval
and execution fields. apply re-derives the proposal and all live checks. Moves
use Linux renameat2(RENAME_NOREPLACE), never copy/delete, with a durable recovery
journal. rollback needs no healthy live audit and can recover a partial apply.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from datetime import datetime, timedelta, timezone

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cutover
import exposure_farm as ef
import profile_policy as pp
import skill_audit as audit
import store_manifest as sm
from broker.gate import InjectionGate

KINDS = ("redundant-copy", "dead-exposure", "excluded-root", "zero-use-stale")
FORBIDDEN = {".archive", ".curator_backups"}
MUTABLE = {"artifact_sha256", "approved_by", "approved_at", "approval_ref",
           "approved_digest", "applied", "rollback"}


class RetirementError(ValueError):
    """A safety condition could not be established."""


def require(condition, message):
    if not condition:
        raise RetirementError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def stamp(value):
    require(isinstance(value, str) and bool(value.strip()), "timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "timestamp must include timezone")
    return parsed


def now():
    return datetime.now(timezone.utc).isoformat()


def path(value):
    require(isinstance(value, str) and value.startswith("/"), "absolute exact path required")
    p = Path(value)
    require(str(p) == value and not ({"..", "."} & set(p.parts)), "noncanonical path refused")
    require(not (FORBIDDEN & set(p.parts)), "curator-owned archive/backup path refused")
    return p


def safe_parents(p):
    for part in (p, *p.parents):
        require(not part.is_symlink(), f"symlink path component refused: {part}")


def load(p):
    p = path(str(p))
    safe_parents(p)
    return pp.load(p)


def committed(p):
    """Require byte equality with HEAD, without modifying the index/worktree."""
    safe_parents(p)
    result = subprocess.run(["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True)
    require(result.returncode == 0, f"committed evidence required: {p}")
    root = Path(result.stdout.strip())
    blob = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{p.relative_to(root)}"],
                          capture_output=True)
    require(blob.returncode == 0 and blob.stdout == p.read_bytes(),
            f"evidence differs from Git HEAD: {p}")


def tree(p):
    """Complete byte/mode inventory, not the audit's cache-excluding hash."""
    safe_parents(p)
    require(p.is_dir(), f"skill directory missing: {p}")
    entries = []
    for current, dirs, files in os.walk(p, followlinks=False):
        dirs.sort(); files.sort()
        require(not FORBIDDEN.intersection(dirs + files), "curator-owned content refused")
        for item in [Path(current), *(Path(current) / f for f in files),
                     *(Path(current) / d for d in dirs)]:
            info = item.lstat()
            require(not stat.S_ISLNK(info.st_mode), f"symlink content refused: {item}")
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                    f"special file refused: {item}")
            require(info.st_dev == p.stat().st_dev, f"mount boundary refused: {item}")
            if item.is_file():
                require(info.st_nlink == 1, f"hard-linked content refused: {item}")
        info = Path(current).stat()
        entries.append({"path": str(Path(current).relative_to(p)), "kind": "directory",
                        "mode": stat.S_IMODE(info.st_mode)})
        for filename in files:
            f = Path(current) / filename
            entries.append({"path": str(f.relative_to(p)), "kind": "file",
                            "mode": stat.S_IMODE(f.stat().st_mode), "sha256": digest(f.read_bytes())})
    return sorted(entries, key=lambda e: e["path"])


def check_audit(index):
    require(index.get("artifact") == "stage-1-verified-audit", "not a Stage 1 audit")
    require(isinstance(index.get("identities"), list) and index["identities"], "empty audit")
    require(isinstance(index.get("conflicts"), list), "audit conflicts missing")
    require(not any(c.get("requires_decision") is not False for c in index["conflicts"]),
            "audit has unresolved decisions")
    seen = set()
    for row in index["identities"]:
        key = (row["id"], row["package_sha256"])
        require(key not in seen, "duplicate audit identity/version")
        seen.add(key)
        source = path(row["canonical_source"])
        # The audit helper silently skips unreadable files; tree validates them first.
        tree(source)
        require(audit.hash_package(source)[:3] == (row["package_sha256"],
                row["skill_md_sha256"], row["file_count"]), f"audit canonical hash drift: {source}")
        for exposure in row["exposures"]:
            p = path(exposure["path"])
            resolved = path(str(p.resolve(strict=True)))
            tree(resolved)
            require(audit.hash_package(resolved)[0] == row["package_sha256"],
                    f"audit exposure hash drift: {p}")


def usage_check(record, evaluated_at):
    if not isinstance(record, dict):
        return ["missing usage record (absence is not zero use)"]
    problems = []
    if record.get("pinned") is not False:
        problems.append("pinned or unknown pin state")
    if record.get("state") == "archived":
        problems.append("already withdrawn by curator; canonical copy must remain")
    times = [record[k] for k in ("last_used_at", "last_viewed_at", "last_patched_at", "created_at")
             if record.get(k)]
    for field in ("use_count", "view_count", "patch_count"):
        if type(record.get(field)) is not int or record[field] < 0:
            problems.append(f"missing/invalid usage counter: {field}")
    if not times:
        problems.append("usage retention cannot be established without activity/creation timestamp")
    elif max(stamp(t) for t in times) > evaluated_at - timedelta(days=30):
        problems.append("recorded activity inside 30-day retention floor")
    return problems


def consumer_resolution(consumers):
    """Cutover's public resolution model, over profile plus every configured root.

    This is not a substitute for a host-version-specific skill_view probe. In
    particular, depth-3 placements stay blocked rather than trusting this model.
    """
    result = {}
    for consumer in consumers:
        settings = cutover.yaml.safe_load(Path(consumer["config"]).read_text())
        skills = settings.get("skills") or {}
        roots = [path(consumer["skills_root"])]
        roots.extend(path(p) for p in skills.get("external_dirs", []))
        resolved = cutover.resolution(roots, disabled=skills.get("disabled", []))
        result[consumer["profile"]] = {name: row["package_sha256"]
                                      for name, row in sorted(resolved.items())}
    return result


def propose(*, batch, store, audit_path, evidence_path, profile, source, kind, generated_at):
    """Generate evidence from verified inputs, without moving or altering skills."""
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}", batch), "invalid batch name")
    require(kind in KINDS, "unknown retirement kind")
    evaluation = stamp(generated_at)
    require(evaluation <= datetime.now(timezone.utc), "generated_at is in the future")
    store = path(str(store)); source = path(str(source))
    audit_path = path(str(audit_path)); evidence_path = path(str(evidence_path))
    for p in (store, source, audit_path, evidence_path):
        safe_parents(p)
    manifest_path = store / sm.MANIFEST_NAME
    committed(manifest_path); committed(audit_path)
    identities = pp.identities(store)
    for identity in identities.values():
        tree(store / identity["path"])
    problems = pp.validate_dir(store)
    require(not problems, "policy validation failed: " + "; ".join(problems))
    index = load(audit_path)
    check_audit(index)
    inputs = load(evidence_path)
    require(inputs.get("evidence_version") == 1, "unknown evidence version")
    consumers = inputs["consumers"]
    policy_names = sorted(p.stem for p in (store / "policies").glob("*.json"))
    require(policy_names and sorted(c["profile"] for c in consumers) == policy_names,
            "consumer inventory must exactly cover every policy")
    require(profile in policy_names, "candidate profile not in consumer inventory")
    citations = {}

    def cite(p):
        p = path(str(p)); safe_parents(p)
        citations[str(p)] = digest(p.read_bytes())
        return p

    for p in (manifest_path, audit_path, evidence_path):
        cite(p)
    if (store / sm.META_NAME).exists():
        cite(store / sm.META_NAME)
    blockers = []
    consumer_records = []
    for c in sorted(consumers, key=lambda c: c["profile"]):
        name = c["profile"]
        policy_path = cite(store / "policies" / f"{name}.json")
        policy = load(policy_path)
        locations = {key: cite(path(c[key])) for key in
                     ("config", "manifest", "usage", "cron", "gate", "incidents")}
        skills_root = path(c["skills_root"])
        safe_parents(skills_root)
        require(skills_root.is_dir() and skills_root.name == "skills" and
                skills_root.parent.name == name and skills_root.parent.parent.name == "profiles",
                "skills_root must be the exact profile skills directory")
        farm = path(c["farm"]); safe_parents(farm)
        require(load(locations["manifest"]) == pp.derive(policy, identities),
                f"exposure manifest does not match policy: {name}")
        result = cutover.run("verify", config=locations["config"], store=store,
                             manifest=locations["manifest"], farm=farm, policy=policy_path,
                             roots=[skills_root])
        require(result["consumer"] == name, "consumer/farm mismatch")
        state = load(locations["gate"])
        require(state.get("state_version") == 1 and isinstance(state.get("profiles"), dict)
                and isinstance(state["profiles"].get(name), dict), "missing or malformed incident state")
        gate = InjectionGate(state_path=locations["gate"], incidents_path=locations["incidents"])
        if gate.failed_closed(name) or gate.open_incidents(name):
            blockers.append(f"open Incident or failed-closed profile: {name}")
        usages = load(locations["usage"])
        require(isinstance(usages, dict), "usage must be name-keyed mapping")
        jobs = load(locations["cron"])
        require(isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list), "cron jobs evidence missing")
        consumer_records.append((c, policy, usages, jobs))
    own = next(c for c in consumers if c["profile"] == profile)
    root = path(own["skills_root"])
    require(source != root and source.is_relative_to(root), "exact profile skill path required, not parent/root")
    if len(source.relative_to(root).parts) > 2:
        blockers.append("depth-3 or deeper skill requires a separate native placement review")
    require((source / "SKILL.md").is_file(), "candidate must be an exact skill directory")
    full_tree = tree(source)
    require(not any(e["path"].endswith("/SKILL.md") for e in full_tree),
            "nested skill would be moved with its parent; select the exact child")
    require(not source.is_relative_to(store) and not store.is_relative_to(source), "canonical store withdrawal refused")
    matches = [r for r in index["identities"] if any(e["path"] == str(source) for e in r["exposures"])]
    require(len(matches) == 1, "candidate must resolve to one exact audited exposure path")
    row = matches[0]
    canonical_row = identities.get(row["id"])
    require(canonical_row is not None and canonical_row["package_sha256"] == row["package_sha256"],
            "audit/store identity or hash mismatch; no same-name/owner remapping")
    target = store / canonical_row["path"]
    require(audit.hash_package(source)[0] == canonical_row["package_sha256"], "candidate hash unverified")
    aliases = sorted(set([row["id"], row["name"], *row.get("aliases", []),
                          canonical_row["name"], *canonical_row.get("aliases", [])]))
    references = []
    for other in index["identities"]:
        for exposure in other["exposures"]:
            exposed = Path(exposure["path"])
            resolved = exposed.resolve(strict=True)
            if exposed != source and (resolved == source or resolved.is_relative_to(source)):
                references.append(f"exposure link: {exposed}")
        if other is row:
            continue
        refs = set(other.get("references", []))
        for location in {other["canonical_source"], *(e["path"] for e in other["exposures"])}:
            resolved = Path(location).resolve()
            refs.update(audit.references(resolved))
            for file in audit.package_files(resolved):
                if file.suffix == ".md":
                    text = file.read_text(errors="strict")
                    if str(source) in text:
                        refs.add(str(source))
                    refs.update(re.findall(r"skill_view\(\s*(?:name\s*=\s*)?['\"]([^'\"]+)['\"]", text))
        if refs.intersection([*aliases, str(source)]):
            references.append(f"cross-skill: {other['id']}")
    for identity in identities.values():
        if set(identity["dependencies"]).intersection(aliases):
            references.append(f"dependency: {identity['id']}")
        if identity["id"] != row["id"]:
            directory = store / identity["path"]
            refs = set(audit.references(directory))
            for file in audit.package_files(directory):
                if file.suffix == ".md":
                    text = file.read_text(errors="strict")
                    refs.update(re.findall(r"skill_view\(\s*(?:name\s*=\s*)?['\"]([^'\"]+)['\"]", text))
                    if str(source) in text:
                        refs.add(str(source))
            if refs.intersection([*aliases, str(source)]):
                references.append(f"store cross-skill: {identity['id']}")
    usages = []
    names = set([row["name"], *row.get("aliases", []), canonical_row["name"], *canonical_row.get("aliases", [])])
    ambiguous = any(sum(n in {r["name"], *r.get("aliases", [])} for r in index["identities"]) > 1
                    for n in names)
    for c, policy, records, jobs in consumer_records:
        available = [(n, records[n]) for n in sorted(names) if n in records]
        if not available:
            blockers.append(f"missing usage evidence: {c['profile']}")
        for name, record in available:
            usages.append({"profile": c["profile"], "name": name, "record": record})
            blockers.extend(f"{c['profile']}/{name}: {p}" for p in usage_check(record, evaluation))
        if any(re.search(r"(?<![\w.-])" + re.escape(token) + r"(?![\w.-])", canonical(jobs))
               for token in [*aliases, str(source)]):
            references.append(f"cron: {c['profile']}")
        if str(source) in Path(c["config"]).read_text():
            references.append(f"config path: {c['profile']}")
    if references:
        blockers.append("candidate is referenced")
    if kind == "redundant-copy":
        require(str(source) in row.get("redundant_realpaths", []) and
                any(r["realpath"] == str(source) and r["of"] == row["id"] and
                    r["package_sha256"] == row["package_sha256"] for r in index["redundant_copies"]),
                "candidate not classified as an exact redundant copy")
        links = [Path(own["farm"]) / e["name"] for e in load(Path(own["manifest"]))["exposures"]
                 if e["id"] == row["id"]]
        require(links and all(p.resolve(strict=True) == target for p in links),
                "candidate replacement is not exposed by its Consumer farm")
    elif kind == "zero-use-stale":
        require(row.get("proposed_classification") == "retirement-candidate", "not audit-classified zero-use-stale")
        if ambiguous or any(u["record"].get("use_count") != 0 or u["record"].get("state") != "stale" for u in usages):
            blockers.append("zero-use-stale requires unambiguous stale zero-use evidence")
        blockers.append("canonical identity withdrawal requires a separately designed manifest transition")
    elif kind == "excluded-root":
        blockers.append("excluded-root withdrawal requires complete root/cutover evidence; exact-profile-only boundary")
    else:
        for c, policy, _, _ in consumer_records:
            if row["id"] in canonical(policy) or any(p.resolve() == source for p in Path(c["farm"]).iterdir()):
                blockers.append(f"dead-exposure still resolves in policy/farm: {c['profile']}")
    licence = canonical_row["license"]
    if not licence or row.get("license") != licence:
        blockers.append("licence missing or audit/store licence mismatch")
    notice_files = []
    for base in (source, *list(source.parents)[:len(source.relative_to(root).parts)]):
        for f in sorted(base.iterdir()):
            if f.name.upper().startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING")) and f.is_file():
                safe_parents(f)
                notice_files.append(f)
    target_legal = [f for f in target.rglob("*") if f.is_file() and
                    f.name.upper().startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING"))]
    if licence != "LicenseRef-Proprietary" and not target_legal:
        blockers.append("upstream licence/NOTICE preservation unproven in canonical package")
    for f in notice_files:
        cite(f)
        if not any(f.read_bytes() == t.read_bytes() for t in target_legal):
            blockers.append(f"licence/NOTICE not preserved canonically: {f}")
    candidate = {"path": str(source), "identity": row["id"], "kind": kind,
                 "resolves_to": str(target), "package_sha256": row["package_sha256"],
                 "tree": full_tree, "usage": {"record": usages, "ambiguous_same_name": ambiguous},
                 "references": sorted(set(references)), "license": licence,
                 "blockers": sorted(set(blockers))}
    result = {"retirement_version": 1, "batch": batch, "kind": kind, "generated_at": generated_at,
              "store_manifest_sha256": digest(manifest_path.read_bytes()),
              "audit_index_sha256": digest(audit_path.read_bytes()),
              "inputs": {"store": str(store), "audit": str(audit_path), "evidence": str(evidence_path),
                         "profile": profile, "path": str(source)},
              "evidence_sha256": dict(sorted(citations.items())), "retention_days": 30,
              "resolution_before": consumer_resolution(consumers),
              "candidates": [candidate], "retired_to": str(store / "retired" / batch),
              "approved_by": None, "approved_at": None, "approval_ref": None,
              "approved_digest": None, "applied": None, "rollback": None}
    result["artifact_sha256"] = artifact_digest(result)
    return result


def artifact_digest(data):
    return digest(canonical({k: v for k, v in data.items() if k not in MUTABLE}).encode())


def report(data):
    lines = [f"# Retirement review: {data['batch']}", "", f"Digest: `{data['artifact_sha256']}`", "",
             "Archive only; no purge. Exact-path first batch. Retention floor: 30 days.", ""]
    for c in data["candidates"]:
        lines += [f"- Path: `{c['path']}`", f"- Identity: `{c['identity']}`",
                  f"- Replacement: `{c['resolves_to']}`", f"- Package: `{c['package_sha256']}`",
                  f"- Blockers: {', '.join(c['blockers']) or 'none'}", "", "```json",
                  canonical(c).rstrip(), "```", ""]
    lines += ["## Evidence", "", "```json", canonical(data["evidence_sha256"]).rstrip(), "```", ""]
    return "\n".join(lines)


def save(p, data):
    """Durable same-directory replace; never follow a symlink output."""
    safe_parents(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".retire-", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, p)
        directory = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def checked_artifact(artifact, batch):
    data = load(artifact)
    require(data.get("retirement_version") == 1 and data.get("batch") == batch,
            "batch/version mismatch")
    require(data.get("artifact_sha256") == artifact_digest(data), "artifact digest mismatch")
    require(len(data["candidates"]) == 1, "first batch requires exactly one exact skill path")
    return data


def fresh(data):
    inputs = data["inputs"]
    regenerated = propose(batch=data["batch"], store=Path(inputs["store"]),
                          audit_path=Path(inputs["audit"]), evidence_path=Path(inputs["evidence"]),
                          profile=inputs["profile"], source=Path(inputs["path"]),
                          kind=data["kind"], generated_at=data["generated_at"])
    require(regenerated["artifact_sha256"] == data["artifact_sha256"],
            "proposal/evidence drift; generate a new reviewed batch")
    require(not regenerated["candidates"][0]["blockers"],
            "batch blocked: " + "; ".join(regenerated["candidates"][0]["blockers"]))
    return regenerated


def approval(data):
    require(data.get("approved_by") == "Adam" and data.get("approval_ref") and
            isinstance(data["approval_ref"], str) and data["approval_ref"].strip(),
            "explicit Adam approval and approval_ref required")
    require(data.get("approved_digest") == data["artifact_sha256"], "approval digest mismatch")
    require(stamp(data["generated_at"]) <= stamp(data["approved_at"]) <= datetime.now(timezone.utc),
            "approval must postdate proposal and not be in the future")


def movement(data):
    source = path(data["inputs"]["path"])
    require(str(source) == data["candidates"][0]["path"], "candidate/source mismatch")
    store = path(data["inputs"]["store"])
    retired = store / "retired" / data["batch"]
    require(str(retired) == data["retired_to"], "retired destination mismatch")
    destination = retired / "paths" / source.relative_to("/")
    return source, destination, retired / "manifest.json"


def rename_exact(source, destination):
    """No-overwrite atomic move; cross-device moves deliberately have no fallback."""
    safe_parents(source); safe_parents(destination)
    require(source.stat().st_dev == destination.parent.stat().st_dev,
            "cross-filesystem retirement refused (move-only)")
    libc = ctypes.CDLL(None, use_errno=True)
    require(hasattr(libc, "renameat2"), "safe no-overwrite renameat2 unavailable")
    function = libc.renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))
    for parent in {source.parent, destination.parent}:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def apply(artifact, data):
    require(not data.get("applied") and not data.get("rollback"), "batch already executed")
    approval(data)
    committed(artifact)
    review_path = artifact.parent / data["batch"] / "report.md"
    committed(review_path)
    require(review_path.read_text() == report(data), "review report differs from artifact")
    fresh(data)
    source, destination, journal_path = movement(data)
    safe_parents(destination)
    require(not journal_path.parent.exists(), "retired batch already exists; inspect/rollback its journal")
    expected = data["candidates"][0]["tree"]
    require(tree(source) == expected, "source byte inventory changed")
    require(source.stat().st_dev == Path(data["inputs"]["store"]).stat().st_dev,
            "cross-filesystem retirement refused (move-only)")
    moves = [{"source": str(source), "destination": str(destination), "tree": expected}]
    journal = {"retirement_version": 1, "batch": data["batch"],
               "artifact_sha256": data["artifact_sha256"], "approved_digest": data["approved_digest"],
               "created_at": now(), "state": "prepared", "moves": moves, "rollback": None}
    destination.parent.mkdir(parents=True, exist_ok=True)
    save(journal_path, canonical(journal))
    # All failure states from here are journal-backed. Never silently copy/delete
    # or overwrite to recover: the rollback command inspects both locations.
    rename_exact(source, destination)
    require(tree(destination) == expected and not source.exists(), "post-move verification failed; inspect journal")
    consumers = load(Path(data["inputs"]["evidence"]))["consumers"]
    try:
        after = consumer_resolution(consumers)
        require(after == data["resolution_before"], "post-move name/hash resolution regression")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rollback(artifact, data)
        raise RetirementError(f"post-move resolution failed; rollback restored exact bytes: {exc}") from exc
    journal["state"] = "applied"
    save(journal_path, canonical(journal))
    data["applied"] = {"at": now(), "manifest": str(journal_path), "moves": moves,
                       "resolution_after": after}
    save(artifact, canonical(data))
    return {"ok": True, "batch": data["batch"], "moves": moves, "journal": str(journal_path)}


def rollback(artifact, data):
    approval(data)
    source, destination, journal_path = movement(data)
    journal = load(journal_path)
    expected = data["candidates"][0]["tree"]
    moves = [{"source": str(source), "destination": str(destination), "tree": expected}]
    require(journal.get("batch") == data["batch"] and journal.get("retirement_version") == 1
            and journal.get("artifact_sha256") == data["artifact_sha256"]
            and journal.get("approved_digest") == data["approved_digest"]
            and journal.get("moves") == moves and journal.get("state") in {"prepared", "applied", "rolled-back"},
            "retirement journal does not match approved exact move")
    safe_parents(source); safe_parents(destination)
    if destination.exists():
        require(not source.exists(), "rollback source collision; refusing overwrite")
        require(source.parent.is_dir(), "original parent missing; manual recovery required")
        require(tree(destination) == expected, "retired bytes changed; refusing rollback")
        rename_exact(destination, source)
    else:
        require(source.exists(), "both original and retired paths missing")
    require(tree(source) == expected and not destination.exists(), "rollback byte verification failed")
    record = journal.get("rollback") or {"at": now(), "restored": [str(source)], "byte_exact": True}
    journal["state"] = "rolled-back"
    journal["rollback"] = record
    save(journal_path, canonical(journal))
    data["rollback"] = record
    save(artifact, canonical(data))
    return {"ok": True, "batch": data["batch"], "rollback": record}


@contextlib.contextmanager
def batch_lock(directory, batch):
    safe_parents(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / f".{batch}.lock"
    safe_parents(lock)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def execute(args, artifact):
    if args.command == "propose":
        require(all((args.store, args.audit, args.evidence, args.profile, args.path)),
                "propose requires --store --audit --evidence --profile --path")
        data = propose(batch=args.batch, store=args.store, audit_path=args.audit,
                       evidence_path=args.evidence, profile=args.profile, source=args.path,
                       kind=args.kind, generated_at=args.generated_at or now())
        if artifact.exists():
            require(load(artifact) == data, "existing proposal differs; choose a new batch")
        save(artifact, canonical(data))
        save(args.artifacts / args.batch / "report.md", report(data))
        return {"ok": True, "artifact": str(artifact), "artifact_sha256": data["artifact_sha256"],
                "blockers": data["candidates"][0]["blockers"]}
    data = checked_artifact(artifact, args.batch)
    if args.command == "review":
        fresh(data)
        return {"ok": True, "artifact_sha256": data["artifact_sha256"], "report": report(data)}
    if args.command == "approve":
        require(not data["approved_by"] and not data["applied"] and not data["rollback"], "batch already approved/executed")
        fresh(data)
        require(args.digest == data["artifact_sha256"], "approval must name the reviewed digest")
        data.update(approved_by=args.approved_by, approved_at=args.approved_at,
                    approval_ref=args.approval_ref, approved_digest=args.digest)
        approval(data)
        save(artifact, canonical(data))
        return {"ok": True, "approved_digest": data["approved_digest"], "batch": args.batch}
    if args.command == "apply":
        return apply(artifact, data)
    return rollback(artifact, data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("propose", "review", "approve", "apply", "rollback"))
    parser.add_argument("--batch", required=True)
    parser.add_argument("--artifacts", type=Path, default=Path(__file__).resolve().parents[1] / "docs/retirement")
    parser.add_argument("--store", type=path)
    parser.add_argument("--audit", type=path)
    parser.add_argument("--evidence", type=path)
    parser.add_argument("--profile")
    parser.add_argument("--path")
    parser.add_argument("--kind", choices=KINDS, default="redundant-copy")
    parser.add_argument("--generated-at")
    parser.add_argument("--digest")
    parser.add_argument("--approved-by")
    parser.add_argument("--approved-at")
    parser.add_argument("--approval-ref")
    args = parser.parse_args(argv)
    try:
        require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}", args.batch), "invalid batch name")
        artifact = path(str(args.artifacts)) / f"{args.batch}.json"
        if args.command == "propose":
            for value in (args.path, args.store):
                if value is not None:
                    protected = path(str(value))
                    require(not args.artifacts.is_relative_to(protected) and
                            not protected.is_relative_to(args.artifacts),
                            "artifact output overlaps source/store")
        with batch_lock(args.artifacts, args.batch):
            result = execute(args, artifact)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, pp.PolicyError,
            sm.ManifestError, ef.FarmError, cutover.CutoverError) as exc:
        print(canonical({"ok": False, "problems": [str(exc)]}), end="")
        return 1
    print(canonical(result), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
