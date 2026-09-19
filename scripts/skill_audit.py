#!/usr/bin/env python3
"""Stage 1 skill audit — enumerate, hash, and classify skills with no filesystem change.

Scope, identity, and hashing rules come from ADR-0008 (authored skills across every
consumer; derived caches, snapshot archives, and project-scoped skills excluded) and
ADR-0009 (``<owner>.<name>`` identity, package-hash version, ``SKILL.md`` hash as a
separate index field).

Read-only against the skill trees. Writes ``docs/audit/index.json`` in this repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parent.parent

EXCLUDED_DIR_NAMES = {
    ".git", ".github", ".hub", ".archive", ".curator_backups", "node_modules",
    "__pycache__", ".cache", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".hg", ".svn", ".DS_Store",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

ESSENTIAL_NAMES = {"hermes-agent", "find-skills", "agent-skill-library-management", "life-os",
                   "hermes-agent-skill-authoring"}

# Same-name divergences already reconciled and decided as intentional profile customisations
# (docs/research/divergence-reconciliation.md).  These are not open conflicts.
RESOLVED_PROFILE_DIVERGENCES = {
    "agent-skill-library-management", "life-os", "blocked-page-recovery", "blogwatcher",
    "codex", "himalaya", "hermes-agent-skill-authoring", "research-paper-writing",
    "youtube-content", "multi-agent-profile-workflows", "model-identity-audit",
    "pydantic-graph-workflows", "typesafe-ai",
}


def git(args: list[str], cwd: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def root(path: str, category: str, scope: str, owner: str | None,
         repo: str | None = None, git_root: str | None = None,
         license_: str | None = None, priority: int = 50, kind: str = "real",
         note: str = "") -> dict:
    exclusion = None
    if scope not in ("in", "project"):
        exclusion = scope
        scope = "excluded"
    p = Path(path) if path.startswith("/") else (HOME / path)
    gr = (HOME / git_root) if git_root else None
    return {
        "path": p,
        "category": category,
        "scope": scope,               # "in" | "project" | "excluded"
        "exclusion": exclusion,
        "owner": owner,
        "repo": repo,
        "commit": git(["rev-parse", "HEAD"], gr) if gr else None,
        "origin_commit": git(["rev-parse", "origin/main"], gr) if gr else None,
        "license": license_,
        "priority": priority,
        "kind": kind,                 # real | symlink-target | project-scoped
        "note": note,
    }


ROOTS = [
    # --- authored, in scope -------------------------------------------------
    root(".hermes/hermes-agent/skills", "hermes_builtin", "in", "nousresearch",
         repo="NousResearch/hermes-agent", git_root=".hermes/hermes-agent",
         license_="MIT", priority=10, note="Hermes builtin skills; git-managed canonical source"),
    root(".hermes/hermes-agent/optional-skills", "hermes_optional", "in", "nousresearch",
         repo="NousResearch/hermes-agent", git_root=".hermes/hermes-agent",
         license_="MIT", priority=10, note="Hermes optional skills; git-managed canonical source"),
    root(".hermes/skills", "hermes_shared_shelf", "in", "local",
         priority=30, note="Default-profile writable tier: bundled copies plus agent-authored skills"),
    root(".hermes/profiles/hermes_engineer/skills", "hermes_profile", "in", "hermes_engineer",
         priority=20, note="Profile writable tier; intentional divergences preserved"),
    root(".hermes/profiles/life-agent/skills", "hermes_profile", "in", "life-agent", priority=20),
    root(".hermes/profiles/astra-pinned/skills", "hermes_profile", "in", "astra-pinned", priority=20),
    root(".hermes/profiles/stillroom-chief-of-staff/skills", "hermes_profile", "in", "stillroom-chief-of-staff", priority=20),
    root(".hermes/profiles/stillroom-content-scout/skills", "hermes_profile", "in", "stillroom-content-scout", priority=20),
    root(".hermes/profiles/stillroom-media-analyst/skills", "hermes_profile", "in", "stillroom-media-analyst", priority=20),
    root(".hermes/profiles/stillroom-research-assistant/skills", "hermes_profile", "in", "stillroom-research-assistant", priority=20),
    root(".hermes/profiles/stillroom-signal-generator/skills", "hermes_profile", "in", "stillroom-signal-generator", priority=20),
    root(".hermes/profiles/stillroom-signal-guardian/skills", "hermes_profile", "in", "stillroom-signal-guardian", priority=20),
    root(".hermes/profiles/stillroom-studio-producer/skills", "hermes_profile", "in", "stillroom-studio-producer", priority=20),
    root("Documents/skills-archive/skills", "upstream_checkout", "in", "mattpocock",
         repo="mattpocock/skills", git_root="Documents/skills-archive/skills",
         license_="MIT", priority=5, note="mattpocock/skills checkout"),
    root("Documents/skills-archive/marketingskills", "upstream_checkout", "in", "coreyhaines31",
         repo="coreyhaines31/marketingskills", git_root="Documents/skills-archive/marketingskills",
         license_="MIT", priority=5, note="coreyhaines31/marketingskills checkout; one local commit ahead of origin"),
    root("Documents/skills-archive/pstack", "upstream_plugin", "in", "cursor",
         repo="cursor/plugins (pstack)", license_="MIT", priority=5,
         note="Cursor plugin archive; no .git; manifest author Lauren Tan vs README 'poteto'"),
    root(".codex/skills", "agent_tooling", "in", "openai",
         repo="openai/skills", license_="MIT", priority=15,
         note="Codex .system skills installed from openai/skills"),
    root("Work/.agents/skills", "agent_tooling", "in", "work", priority=25,
         note="Standalone authored engineering tree; no discoverable upstream"),
    root(".agents/skills", "agent_tooling", "in", "agents", priority=40,
         note="Agent-tooling symlink farm"),
    root("Documents/stillroom-wiki/skills", "content", "in", "stillroom", priority=25,
         note="Hermes configured external_dirs"),
    root("Documents/life-os", "content", "in", "life-os", priority=25,
         note="Life OS vault skill material"),
    root("honcho", "service_repo", "in", "plastic-labs",
         repo="plastic-labs/honcho", git_root="honcho", priority=15, note="Honcho service repo skills"),
    root("services/honcho", "service_repo", "in", "plastic-labs",
         repo="plastic-labs/honcho", git_root="services/honcho", priority=15, note="Honcho service repo skills"),
    root("honcho-assessment", "service_repo", "in", "plastic-labs", priority=15,
         note="Honcho assessment tree"),
    root("/usr/share/omarchy/default/agents/skills", "os_provided", "in", "omarchy", priority=20),
    root(".claude/skills", "agent_tooling", "in", "claude", priority=35),
    root(".pi/agent/skills", "agent_tooling", "in", "pi", priority=35),
    # --- project-scoped (excluded from the store, ADR-0008) ------------------
    root("Projects/manor-ai/packages/core/ai/skills", "project_scoped", "project",
         "manor-ai", priority=70, kind="project-scoped"),
    root("Projects/manor-ai/.agents/skills", "project_scoped", "project",
         "manor-ai", priority=70, kind="project-scoped"),
    root("Projects/stillroom-client-acquisition/profile-staging", "project_scoped", "project",
         "stillroom-client-acquisition", priority=70, kind="project-scoped"),
    # --- excluded -----------------------------------------------------------
    root("research", "archive", "excluded: snapshot archive (Hermes PR evidence)", None, priority=90),
    root("backups", "archive", "excluded: snapshot archive", None, priority=90),
    root(".hermes/backups", "archive", "excluded: snapshot archive", None, priority=90),
    root(".hermes/reports", "archive", "excluded: snapshot archive", None, priority=90),
    root("Documents/skills-archive", "archive",
         "excluded: snapshot archive except the embedded upstream checkouts above", None, priority=90),
    root(".bb/runtime/global-skills", "derived_cache", "excluded: BB derived runtime cache", None, priority=95),
    root(".bb/runtime/skill-store", "derived_cache", "excluded: BB content-addressed cache", None, priority=95),
    root(".codex/plugins", "derived_cache", "excluded: plugin cache", None, priority=95),
    root("Downloads", "misc", "excluded: ad-hoc download, not an authored root", None, priority=90),
]


def match_root(realpath: Path) -> dict | None:
    best = None
    for r in ROOTS:
        try:
            realpath.relative_to(r["path"])
        except ValueError:
            continue
        if best is None or len(r["path"].parts) > len(best["path"].parts):
            best = r
    return best


_NAME_RE = re.compile(r"^name:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


def frontmatter_name(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    m = _NAME_RE.search(text[3:end])
    return m.group(1).strip() if m else ""


def package_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        for name in sorted(filenames):
            if name in EXCLUDED_DIR_NAMES or Path(name).suffix in EXCLUDED_SUFFIXES:
                continue
            files.append(Path(dirpath) / name)
    return sorted(files, key=lambda p: str(p.relative_to(skill_dir)))


def hash_package(skill_dir: Path) -> tuple[str, str, int, list[str]]:
    h = hashlib.sha256()
    count = 0
    skill_md_sha = ""
    rels: list[str] = []
    for f in package_files(skill_dir):
        rel = str(f.relative_to(skill_dir))
        try:
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
        h.update(rel.encode()); h.update(b"\0"); h.update(digest.encode()); h.update(b"\n")
        count += 1
        rels.append(rel)
        if rel == "SKILL.md":
            skill_md_sha = digest
    return h.hexdigest(), skill_md_sha, count, rels


_REF_RE = re.compile(r'skill_view\(\s*["\']([^"\']+)["\']|\]\(\.\./([^/)]+)/')
_REF_STOPWORDS = {"..", "assets", "references", "scripts", "templates", "examples",
                   "docs", "plugins", "images", "fixtures", "data"}


def references(skill_dir: Path) -> list[str]:
    refs: set[str] = set()
    for f in package_files(skill_dir):
        if f.suffix != ".md":
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for a, b in _REF_RE.findall(text):
            tok = (a or b).strip()
            if tok and tok not in _REF_STOPWORDS and "/" not in tok and " " not in tok:
                refs.add(tok)
    return sorted(refs)


def walk_root(r: dict) -> list[dict]:
    exposures: list[dict] = []
    if not r["path"].exists():
        return exposures
    for dirpath, dirnames, filenames in os.walk(r["path"], followlinks=True):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        if "SKILL.md" not in filenames:
            continue
        skill_dir = Path(dirpath)
        exposures.append({"exposure": skill_dir, "root": r})
    return exposures


def load_usage() -> dict:
    path = HOME / ".hermes/skills/.usage.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_bundled() -> set[str]:
    path = HOME / ".hermes/skills/.bundled_manifest"
    if not path.exists():
        return set()
    return {line.split(":", 1)[0] for line in path.read_text().splitlines() if ":" in line}


def changed_paths(git_root: str) -> list[str]:
    gr = HOME / git_root
    if not (gr / ".git").exists():
        return []
    out = git(["diff", "--name-only", "origin/main...HEAD"], gr)
    return out.splitlines() if out else []


def owner_for(rec: dict, bundled: set[str]) -> str:
    r = rec["root"]
    if r["path"] == HOME / ".hermes/skills":
        return "nousresearch" if rec["name"] in bundled else "local"
    return r["owner"] or "unknown"


def main() -> int:
    if "--verify" in sys.argv:
        return verify()
    usage = load_usage()
    bundled = load_bundled()
    market_local = changed_paths("Documents/skills-archive/marketingskills")

    in_scope = [r for r in ROOTS if r["scope"] == "in"]
    project = [r for r in ROOTS if r["scope"] == "project"]

    real_recs: dict[str, dict] = {}
    for r in in_scope:
        for e in walk_root(r):
            real = e["exposure"].resolve()
            rec = real_recs.setdefault(str(real), {"realpath": str(real), "exposures": []})
            rec["exposures"].append({
                "path": str(e["exposure"]),
                "root": str(e["root"]["path"]),
                "link": os.path.realpath(e["exposure"]) != str(e["exposure"]),
            })

    rows = []
    for real_str, rec in real_recs.items():
        real = Path(real_str)
        if not real.is_dir():
            continue
        pr = match_root(real)
        pkg, skill_sha, nfiles, rels = hash_package(real)
        name = frontmatter_name(real / "SKILL.md") or real.name
        rows.append({
            "realpath": real_str, "root": pr, "name": name,
            "package_sha256": pkg, "skill_md_sha256": skill_sha,
            "file_count": nfiles, "_rels": rels,
            "references": references(real), "exposures": rec["exposures"],
        })

    # ---- group by package hash -> one identity-version per group -----------
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["package_sha256"]].append(row)

    identities = []
    redundant = []
    for pkg, members in groups.items():
        canonical = min(members, key=lambda m: (m["root"]["priority"], m["realpath"]))
        owner = owner_for(canonical, bundled)
        names = sorted({m["name"] for m in members})
        name = canonical["name"]
        aliases = sorted(n for n in names if n != name)
        r = canonical["root"]
        local_patch = False
        # marketingskills carries one local commit ahead of origin/main touching
        # skills/prospecting/references/compliance.md (ACMA section).
        if r["repo"] == "coreyhaines31/marketingskills" and any(
                "prospecting" in p for p in market_local):
            local_patch = "prospecting" in canonical["realpath"]
        exp_map: dict[str, dict] = {}
        for m in members:
            for e in m["exposures"]:
                cur = exp_map.setdefault(e["path"], {"path": e["path"], "root": e["root"], "link": False})
                cur["link"] = cur["link"] or e["link"]
        exposures = [exp_map[p] for p in sorted(exp_map)]
        refs = sorted({x for m in members for x in m["references"]})
        u = usage.get(name) or usage.get(aliases[0] if aliases else "")
        usage_row = None
        if u:
            last = max([t for t in (u.get("last_used_at"), u.get("last_viewed_at"),
                                    u.get("last_patched_at")) if t] or [""])
            usage_row = {
                "use_count": u.get("use_count", 0), "view_count": u.get("view_count", 0),
                "patch_count": u.get("patch_count", 0), "last_activity": last or None,
                "state": u.get("state"), "pinned": u.get("pinned", False),
                "created_by": u.get("created_by"),
            }
        identities.append({
            "id": f"{owner}.{name}",
            "owner": owner,
            "name": name,
            "aliases": aliases,
            "package_sha256": pkg,
            "skill_md_sha256": canonical["skill_md_sha256"],
            "file_count": canonical["file_count"],
            "canonical_source": canonical["realpath"],
            "canonical_kind": "project-scoped" if r["scope"] == "project" else r["kind"],
            "category": r["category"],
            "provenance": {
                "repo": r["repo"], "commit": r["commit"], "origin_commit": r.get("origin_commit"),
                "pseudo_owner": r["repo"] is None,
            },
            "local_patch": local_patch,
            "license": r["license"],
            "references": refs,
            "usage": usage_row,
            "divergence_status": None,
            "exposures": exposures,
            "redundant_realpaths": sorted(m["realpath"] for m in members if m["realpath"] != canonical["realpath"]),
        })
        for m in members:
            if m["realpath"] != canonical["realpath"]:
                redundant.append({
                    "realpath": m["realpath"], "of": f"{owner}.{name}",
                    "package_sha256": pkg,
                })

    all_names = {i["name"] for i in identities} | {a for i in identities for a in i["aliases"]}
    name_counts: dict[str, int] = defaultdict(int)
    for i in identities:
        name_counts[i["name"]] += 1
    for i in identities:
        i["dangling_references"] = sorted(r for r in i["references"] if r not in all_names)
        u = i.get("usage")
        if u is not None:
            u["keyed_by_name_only"] = True
            u["ambiguous_same_name"] = name_counts[i["name"]] > 1

    # ---- conflicts ---------------------------------------------------------
    conflicts = []
    by_owner_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for i in identities:
        by_owner_name[(i["owner"], i["name"])].append(i)
    for (owner, name), items in sorted(by_owner_name.items()):
        if len(items) > 1:
            conflicts.append({
                "kind": "same-identity-divergent-versions",
                "id": f"{owner}.{name}",
                "requires_decision": True,
                "detail": sorted(i["package_sha256"][:16] for i in items),
                "sources": sorted(i["canonical_source"] for i in items),
            })
    # possible local patches: pseudo-owner skill whose name matches an upstream owner
    upstream_names: dict[str, list[str]] = defaultdict(list)
    for i in identities:
        if i["provenance"]["repo"]:
            upstream_names[i["name"]].append(i["id"])
    for i in identities:
        if not i["provenance"]["repo"]:
            for up in upstream_names.get(i["name"], []):
                resolved = i["owner"] == "hermes_engineer" and i["name"] in RESOLVED_PROFILE_DIVERGENCES
                i["divergence_status"] = (
                    "intentional profile divergence (preserve; divergence-reconciliation)"
                    if resolved else "unreconciled same-name divergence"
                )
                conflicts.append({
                    "kind": "possible-local-patch",
                    "id": i["id"],
                    "requires_decision": not resolved,
                    "detail": (f"same name as upstream {up}; could be a patched version of that "
                               f"identity or a distinct identity"),
                    "sources": [i["canonical_source"]],
                })
    # explicit, non-mechanical conflicts
    conflicts.append({
        "kind": "owner-attribution",
        "id": "cursor.*",
        "requires_decision": True,
        "detail": ("pstack has no .git; its manifest names Lauren Tan while the README names "
                   "'poteto'. Proposed owner 'cursor' (repo path) is provisional"),
        "sources": [str(HOME / "Documents/skills-archive/pstack")],
    })
    dangling = sorted({d for i in identities for d in i["dangling_references"]})
    for d in dangling:
        conflicts.append({
            "kind": "dangling-reference",
            "id": d,
            "requires_decision": True,
            "detail": "referenced by skill bodies but present nowhere on the machine (intended skill or stale reference?)",
            "sources": [i["id"] for i in identities if d in i["dangling_references"]],
        })
    conflicts.append({
        "kind": "scope-question",
        "id": "service-repo skills (plastic-labs/honcho)",
        "requires_decision": True,
        "detail": ("8 honcho service-repo skills are authored but not obviously consumed by an "
                   "agent skill loader; confirm they belong in the store scope (ADR-0008)"),
        "sources": sorted(i["canonical_source"] for i in identities if i["owner"] == "plastic-labs"),
    })
    # same name, different content, both in Hermes roots: shelf (pseudo-owner local) vs a profile
    local_names = {i["name"] for i in identities if i["owner"] == "local"}
    for i in identities:
        if i["owner"] != "local" and i["name"] in local_names and i["category"] == "hermes_profile":
            conflicts.append({
                "kind": "hermes-shelf-vs-profile-divergence",
                "id": i["name"],
                "requires_decision": True,
                "detail": (f"agent-authored shelf identity local.{i['name']} and profile identity "
                           f"{i['id']} both exist with different content; decide which is canonical "
                           f"or whether the profile copy is a patched version"),
                "sources": [i["canonical_source"]],
            })

    # ---- classification proposal ------------------------------------------
    for i in identities:
        u = i["usage"]
        name = i["name"]
        if name in ESSENTIAL_NAMES:
            i["proposed_classification"] = "foundation"
        elif u and not u["ambiguous_same_name"] and u["state"] == "stale" and u["use_count"] == 0:
            i["proposed_classification"] = "retirement-candidate"
        elif u and not u["ambiguous_same_name"] and u["use_count"] >= 5:
            i["proposed_classification"] = "foundation"
        else:
            i["proposed_classification"] = "brokered"

    # ---- project-scoped boundary ------------------------------------------
    project_rows = []
    for r in project:
        for e in walk_root(r):
            real = e["exposure"].resolve()
            pkg, skill_sha, nfiles, rels = hash_package(real)
            name = frontmatter_name(real / "SKILL.md") or real.name
            project_rows.append({
                "name": name, "owner": r["owner"], "id": f"{r['owner']}.{name}",
                "canonical_source": str(real), "canonical_kind": "project-scoped",
                "package_sha256": pkg, "scope": "excluded: project-owned (ADR-0008)",
            })

    scope_report = {
        "in_scope_roots": [
            {"path": str(r["path"]), "category": r["category"], "owner": r["owner"],
             "repo": r["repo"], "commit": r["commit"], "origin_commit": r.get("origin_commit"),
             "license": r["license"], "exists": r["path"].exists()}
            for r in in_scope
        ],
        "project_roots": [{"path": str(r["path"]), "owner": r["owner"]} for r in project],
        "excluded_roots": [
            {"path": str(r["path"]), "category": r["category"], "reason": r["exclusion"]}
            for r in ROOTS if r["scope"] == "excluded"
        ],
    }

    index = {
        "artifact": "stage-1-verified-audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/skill_audit.py",
        "scope": scope_report,
        "counts": {
            "distinct_realpaths": len(rows),
            "identity_versions": len(identities),
            "distinct_identities": len({i["id"] for i in identities}),
            "redundant_copies": len(redundant),
            "project_scoped_skills": len(project_rows),
            "conflicts": len(conflicts),
            "dangling_references": sorted({d for i in identities for d in i["dangling_references"]}),
        },
        "classification_counts": {
            k: sum(1 for i in identities if i["proposed_classification"] == k)
            for k in ("foundation", "brokered", "retirement-candidate")
        },
        "identities": sorted(identities, key=lambda i: i["id"]),
        "redundant_copies": sorted(redundant, key=lambda c: c["realpath"]),
        "project_scoped": sorted(project_rows, key=lambda i: i["id"]),
        "conflicts": conflicts,
    }
    out = REPO / "docs" / "audit" / "index.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=2) + "\n")

    digest = {
        "counts": index["counts"],
        "classification_counts": index["classification_counts"],
        "conflicts": len(conflicts),
        "conflict_kinds": sorted({c["kind"] for c in conflicts}),
        "conflicts_requiring_decision": sum(1 for c in conflicts if c.get("requires_decision")),
        "conflicts_resolved": sum(1 for c in conflicts if not c.get("requires_decision")),
        "same_identity_divergent": [c["id"] for c in conflicts if c["kind"] == "same-identity-divergent-versions"],
        "possible_local_patch_count": sum(1 for c in conflicts if c["kind"] == "possible-local-patch"),
    }
    print(json.dumps(digest, indent=2))
    return 0


def verify() -> int:
    """Re-hash every canonical source and every exposure realpath against the index."""
    path = REPO / "docs" / "audit" / "index.json"
    index = json.loads(path.read_text())
    bad = []
    exp_bad = []
    exp_total = 0
    seen: dict[str, str] = {}
    for i in index["identities"]:
        pkg, sm, n, _ = hash_package(Path(i["canonical_source"]))
        if (pkg, sm, n) != (i["package_sha256"], i["skill_md_sha256"], i["file_count"]):
            bad.append(i["id"])
        for e in i["exposures"]:
            exp_total += 1
            rp = os.path.realpath(e["path"])
            if rp not in seen:
                seen[rp] = hash_package(Path(rp))[0]
            if seen[rp] != i["package_sha256"]:
                exp_bad.append(e["path"])
    print(json.dumps({"identities_verified": len(index["identities"]) - len(bad),
                      "identities_total": len(index["identities"]),
                      "hash_mismatches": bad,
                      "exposures_verified": exp_total - len(exp_bad),
                      "exposures_total": exp_total,
                      "exposure_hash_mismatches": exp_bad}, indent=2))
    return 1 if (bad or exp_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
