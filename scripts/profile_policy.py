#!/usr/bin/env python3
"""Validate Hermes Profile Policies and derive Exposure Manifests (ADR-0018).

A **Profile Policy** is authored JSON at ``<store>/policies/<profile>.json``. Its ``profile``
field must equal the filename stem, and its Foundation Set, Brokered Allowlist, Preferred
Skills, and Denied Skills must be pairwise disjoint over store IDs. The **Exposure Manifest**
is *derived* from the policy, never authored or committed::

    {"manifest_version": 1, "consumer": "<profile>",
     "exposures": [{"name": "<exposure>", "id": "<id>"}, ...]}

Policy v1 (unknown top-level keys fail closed)::

    {
      "policy_version": 1,
      "profile": "stillroom-signal-generator",
      "foundation": [
        {"id": "nousresearch.hermes-agent"},
        {"id": "local.some-skill", "exposure": "renamed-skill"},
        {"project": "/path/to/project", "name": "project-method"}
      ],
      "brokered": ["local.adam-content-writing"],
      "preferred": [],
      "denied": [],
      "limits": {"max_skills": null, "max_injected_chars": null},
      "thresholds": {"candidate_floor": null, "confidence": null},
      "dependency_policy": "declared-only",
      "fallback": "foundation-only",
      "constraints": {"platforms": [], "tools": [], "projects": []}
    }

Derivation emits one exposure per store-backed foundation entry, named by its ``exposure``
alias if present and the Skill's Name otherwise; project-wired foundation entries, and every
Brokered, Preferred, and Denied Skill, emit nothing. Exposure names must be injective; a
collision fails closed naming both IDs, because Hermes keys its skill index on frontmatter
``name`` first-wins and cannot present two same-named Skills. The derived manifest is
deterministic (sorted by name) and consumed unchanged by ``scripts/exposure_farm.py``.

Usage::

    python3 scripts/profile_policy.py validate --store ~/skill-store [--policy PATH]
    python3 scripts/profile_policy.py derive   --store ~/skill-store --policy PATH [--out PATH]

``validate`` without ``--policy`` checks every ``<store>/policies/*.json`` and rejects a
``profile`` claimed by two files. Both commands re-verify the whole Store Manifest first.
Read-only against the store; ``derive --out`` writes only the requested manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exposure_farm as ef  # noqa: E402  (NAME is the farm's exposure-name rule)
import store_manifest as sm  # noqa: E402

POLICY_VERSION = 1
POLICIES_DIR = "policies"
GENERATOR = "scripts/profile_policy.py"

TOP_KEYS = {"policy_version", "profile", "foundation", "brokered", "preferred", "denied",
            "limits", "thresholds", "dependency_policy", "fallback", "constraints"}
SET_KEYS = ("brokered", "preferred", "denied")
DEFAULTS = {"brokered": [], "preferred": [], "denied": [], "limits": {}, "thresholds": {},
            "dependency_policy": "declared-only", "fallback": "foundation-only",
            "constraints": {}}


class PolicyError(Exception):
    """A policy or store manifest cannot safely be used."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path):
    """Load policy/JSON with duplicate keys rejected."""
    return json.loads(path.read_text(), object_pairs_hook=_object)


def identities(store: Path) -> dict:
    """The verified Store Manifest's ``{id: row}``; fails closed on drift."""
    if not store.is_dir():
        raise PolicyError(f"store not found: {store}")
    committed = load(store / sm.MANIFEST_NAME)
    expected = sm.build(store)
    if committed != expected:
        raise PolicyError("Store Manifest drift; regenerate and review it first")
    return {row["id"]: row for row in expected["identities"]}


def _foundation_ids(policy: dict) -> list[str]:
    return [entry["id"] for entry in policy.get("foundation", [])
            if isinstance(entry, dict) and "id" in entry]


def validate(policy: dict, identities: dict, *, filename: str | None = None) -> list[str]:
    """Return the problems with one policy (``[]`` when valid). Deterministic order."""
    problems: list[str] = []
    if not isinstance(policy, dict):
        return ["policy must be a JSON object"]

    unknown = set(policy) - TOP_KEYS
    if unknown:
        problems.append(f"unknown policy keys: {', '.join(sorted(unknown))}")
    if policy.get("policy_version") != POLICY_VERSION:
        problems.append(f"policy_version must be {POLICY_VERSION}")
    profile = policy.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        problems.append("profile must be a non-empty string")
    elif filename is not None and profile != filename:
        problems.append(f"profile {profile!r} does not match filename {filename!r}")

    foundation = policy.get("foundation")
    if not isinstance(foundation, list):
        problems.append("foundation must be a list")
        foundation = []
    for i, entry in enumerate(foundation):
        if not isinstance(entry, dict):
            problems.append(f"foundation[{i}] must be an object")
            continue
        keys = set(entry)
        if keys == {"id"} or keys == {"id", "exposure"}:
            if not isinstance(entry["id"], str) or not entry["id"]:
                problems.append(f"foundation[{i}].id must be a non-empty string")
            elif entry["id"] not in identities:
                problems.append(f"unknown ID: {entry['id']!r}")
            if "exposure" in entry:
                name = entry["exposure"]
                if not isinstance(name, str) or not ef.NAME.fullmatch(name) \
                        or len(name.encode()) > 255:
                    problems.append(f"invalid exposure alias: {name!r}")
        elif keys == {"project", "name"}:
            if not isinstance(entry["project"], str) or not entry["project"]:
                problems.append(f"foundation[{i}].project must be a non-empty string")
            if not isinstance(entry["name"], str) or not entry["name"]:
                problems.append(f"foundation[{i}].name must be a non-empty string")
        else:
            problems.append(f"foundation[{i}] must be {{id}} or {{project, name}}")

    sets: dict[str, list[str]] = {}
    for key in SET_KEYS:
        values = policy.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            problems.append(f"{key} must be a list of non-empty IDs")
            sets[key] = []
            continue
        sets[key] = values
        for ident in values:
            if ident not in identities:
                problems.append(f"unknown ID in {key}: {ident!r}")
    sets["foundation"] = _foundation_ids(policy)

    seen: dict[str, str] = {}
    for key in ("foundation", *SET_KEYS):
        for ident in sets.get(key, []):
            if ident in seen:
                where = seen[ident]
                problems.append(f"ID {ident!r} appears twice in {key}" if where == key
                                else f"ID {ident!r} appears in both {where} and {key}")
            else:
                seen[ident] = key

    # A project-wired Skill shadows a same-named foundation Skill (Hermes project precedence).
    project_names = {e["name"] for e in foundation
                     if isinstance(e, dict) and set(e) == {"project", "name"}}
    for ident in sets["foundation"]:
        row = identities.get(ident)
        if row and row["name"] in project_names:
            problems.append(f"project-wired name {row['name']!r} shadows foundation {ident!r}")
    return problems


def _policy_files(policies_dir: Path) -> list[Path]:
    return sorted(policies_dir.glob("*.json")) if policies_dir.is_dir() else []


def validate_dir(store: Path, policies_dir: Path | None = None) -> list[str]:
    """Validate every policy in the store's ``policies/`` directory, including uniqueness."""
    identities_by_id = identities(store)
    policies_dir = policies_dir or (store / POLICIES_DIR)
    problems: list[str] = []
    by_profile: dict[str, str] = {}
    for path in _policy_files(policies_dir):
        try:
            policy = load(path)
        except (PolicyError, OSError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        problems.extend(f"{path.name}: {p}" for p in validate(policy, identities_by_id,
                                                               filename=path.stem))
        profile = policy.get("profile") if isinstance(policy, dict) else None
        if isinstance(profile, str) and profile:
            if profile in by_profile:
                problems.append(f"profile {profile!r} claimed by {by_profile[profile]} and {path.name}")
            else:
                by_profile[profile] = path.name
    return problems


def derive(policy: dict, identities: dict, *, filename: str | None = None) -> dict:
    """Derive the Exposure Manifest v1, or raise ``PolicyError`` naming every problem."""
    problems = validate(policy, identities, filename=filename)
    if problems:
        raise PolicyError("; ".join(problems))
    exposures: dict[str, str] = {}
    for entry in policy["foundation"]:
        if set(entry) == {"project", "name"}:
            continue  # wired by the project setup path, never the farm
        name = entry.get("exposure", identities[entry["id"]]["name"])
        if name in exposures:
            raise PolicyError(
                f"exposure collision {name!r}: {exposures[name]} and {entry['id']}")
        exposures[name] = entry["id"]
    return {"manifest_version": 1, "consumer": policy["profile"],
            "exposures": [{"name": n, "id": exposures[n]} for n in sorted(exposures)]}


def run(command: str, store: Path, *, policy: Path | None = None,
        policies_dir: Path | None = None, out: Path | None = None) -> dict:
    """Validate the store's policies, or derive one policy's Exposure Manifest."""
    if command not in {"validate", "derive"}:
        raise PolicyError(f"unknown command: {command}")
    store = Path(store).expanduser().resolve()
    if command == "validate":
        if policy is not None:
            identities_by_id = identities(store)
            path = Path(policy).expanduser()
            problems = [f"{path.name}: {p}"
                        for p in validate(load(path), identities_by_id, filename=path.stem)]
            for other in _policy_files(path.parent):
                if other == path:
                    continue
                sibling = load(other)
                if isinstance(sibling, dict) and sibling.get("profile") == path.stem:
                    problems.append(f"{path.name}: profile {path.stem!r} also claimed by {other.name}")
            if problems:
                raise PolicyError("; ".join(problems))
            return {"ok": True, "store": str(store), "policy": str(path)}
        problems = validate_dir(store, policies_dir)
        if problems:
            raise PolicyError("; ".join(problems))
        return {"ok": True, "store": str(store),
                "policies": len(_policy_files(policies_dir or store / POLICIES_DIR))}

    if policy is None:
        raise PolicyError("--policy is required for derive")
    path = Path(policy).expanduser()
    manifest = derive(load(path), identities(store), filename=path.stem)
    if out is not None:
        dest = Path(out).expanduser()
        dest.write_text(sm.serialize(manifest))
        return {"ok": True, "consumer": manifest["consumer"],
                "exposures": len(manifest["exposures"]), "out": str(dest)}
    return {"ok": True, "consumer": manifest["consumer"],
            "exposures": len(manifest["exposures"]), "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("validate", "derive"))
    parser.add_argument("--store", type=Path, default=Path.home() / "skill-store")
    parser.add_argument("--policy", type=Path, help="one policy file (<store>/policies/<profile>.json)")
    parser.add_argument("--policies", type=Path, help="policies directory (default <store>/policies)")
    parser.add_argument("--out", type=Path, help="write the derived manifest here")
    args = parser.parse_args(argv)
    try:
        result = run(args.command, args.store, policy=args.policy,
                     policies_dir=args.policies, out=args.out)
    except (PolicyError, sm.ManifestError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
