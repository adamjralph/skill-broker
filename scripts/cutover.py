#!/usr/bin/env python3
"""Cut one Consumer's skill resolution over to its Exposure Farm, reversibly (ADR-0011/0012).

Cutover is one Consumer at a time, behind a verification gate, and reversible to a recorded
baseline.  This module never touches Skill content: it records what resolved before, generates
and verifies the Consumer's farm (reusing ``scripts/exposure_farm.py``), makes one surgical edit
to the Consumer's loader config, and can undo exactly that edit.

The loader config is Hermes's ``config.yaml``: the farm path is added to
``skills.external_dirs`` byte-preservingly, and rollback removes exactly that entry.  The gate
implements wayfinder #11 section 6.  Without a policy it covers the policy-free conditions:

1. the Exposure Manifest validates against the Store Manifest;
2. every farm link resolves into the store;
3. each exposure resolves to the canonical ``package_sha256``;
4. no name that resolved before Cutover stops resolving (zero regressions);
5. every cross-skill reference in the resolving set resolves.

With ``--policy PATH`` - an authored ``<store>/policies/<profile>.json`` (ADR-0018) - the gate
first validates the policy against the verified Store Manifest via ``scripts/profile_policy.py``
and then adds the two policy-dependent conditions:

4'. every Foundation Skill still resolves: store-backed entries by their indexed Name,
    project-wired entries by their Name;
5'. no Brokered Skill appears in the automatic index, by canonical store realpath or by Name -
    except a Brokered twin that legitimately shares a Foundation's Name (the Name Collision case
    of ADR-0018, whose twin is brokered because Hermes indexes on frontmatter ``name``).

The before/after resolution maps are both recorded: the baseline holds the before map under
``resolution`` and, once Cutover is applied, the after map under ``applied.resolution``.

Usage::

    python3 scripts/cutover.py baseline --consumer NAME --config PATH --baseline PATH \\
        --roots DIR [--roots DIR ...]
    python3 scripts/cutover.py apply    --store DIR --manifest PATH --farm DIR \\
        --config PATH --baseline PATH [--policy PATH]
    python3 scripts/cutover.py verify   --store DIR --manifest PATH --farm DIR --config PATH \\
        [--policy PATH] [--roots DIR ...]
    python3 scripts/cutover.py rollback --config PATH --baseline PATH

Reversible implementation defaults: the baseline is a machine-local JSON file under
``~/.config/skill-broker/baselines/`` and is uncommitted; baseline is read-only; apply requires
an existing baseline so rollback is always possible; a failed apply leaves the previous config
and farm intact.  Rollback restores resolution (the config edit) only and never deletes Skill
content; it leaves a generated farm in place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exposure_farm as ef  # noqa: E402
import profile_policy as pp  # noqa: E402
from skill_audit import EXCLUDED_DIR_NAMES, frontmatter_name, hash_package, references  # noqa: E402

GENERATOR = "scripts/cutover.py"
BASELINE_VERSION = 1


class CutoverError(Exception):
    """Input, baseline, config, or farm state cannot safely be used."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CutoverError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path):
    return json.loads(path.read_text(), object_pairs_hook=_object)


def _skill_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        if "SKILL.md" in filenames:
            found.append(Path(dirpath))
    return found


def resolution(roots) -> dict:
    """First-root-wins ``{name: {realpath, package_sha256}}`` over the given Skill roots."""
    resolved: dict[str, dict] = {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for skill_dir in _skill_dirs(root):
            name = frontmatter_name(skill_dir / "SKILL.md") or skill_dir.name
            if name in resolved:
                continue
            package_sha256, _, _, _ = hash_package(skill_dir)
            resolved[name] = {"realpath": str(skill_dir.resolve()),
                              "package_sha256": package_sha256}
    return resolved


def _external_dirs(text: str) -> list[str]:
    data = yaml.safe_load(text) or {}
    dirs = (data.get("skills") or {}).get("external_dirs") or []
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise CutoverError("skills.external_dirs must be a list of strings")
    return dirs


def _baseline(config: Path, consumer: str, roots) -> dict:
    text = config.read_text()
    return {"baseline_version": BASELINE_VERSION, "generator": GENERATOR, "consumer": consumer,
            "config": {"path": str(config), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                       "external_dirs": _external_dirs(text)},
            "roots": [str(Path(r).expanduser()) for r in roots],
            "resolution": {name: dict(row) for name, row in sorted(resolution(roots).items())}}


def _write_baseline(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _read_baseline(path: Path) -> dict:
    if not path.exists():
        raise CutoverError(f"baseline not found: {path}")
    data = _load(path)
    required = {"baseline_version", "generator", "consumer", "config", "roots", "resolution"}
    if (not isinstance(data, dict) or not required <= set(data)
            or data.get("baseline_version") != BASELINE_VERSION
            or data.get("generator") != GENERATOR):
        raise CutoverError(f"unsupported baseline: {path}")
    return data


# --- config editing: surgical, byte-preserving (Hermes skills.external_dirs) -------------


def _top_block(lines: list[str], key: str) -> tuple[int, int] | None:
    start = next((i for i, ln in enumerate(lines) if ln.rstrip("\n") == f"{key}:"), None)
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j][0].isspace():
            end = j
            break
    return start, end


def _child_key(lines: list[str], start: int, end: int, key: str) -> int | None:
    pattern = "  " + key + ":"
    return next((i for i in range(start + 1, end) if lines[i].startswith(pattern)), None)


def _entry_span(lines: list[str], key_i: int, end: int) -> tuple[int, int]:
    first = last = None
    j = key_i + 1
    while j < end:
        line = lines[j]
        if not line.strip():
            j += 1
            continue
        if line.startswith("    ") and line.lstrip().startswith("- "):
            first = j if first is None else first
            last = j
            j += 1
            continue
        break
    return (first, last) if first is not None else (None, None)


def _set_external_dirs(text: str, dirs: list[str]) -> str:
    """Rewrite skills.external_dirs as a block list, replacing any inline form."""
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        raise CutoverError("config has no top-level `skills:` key")
    start, end = block
    key_i = _child_key(lines, start, end, "external_dirs")
    replacement = (["  external_dirs: []\n"] if not dirs
                   else ["  external_dirs:\n", *(f"    - {d}\n" for d in dirs)])
    if key_i is None:
        lines[start + 1:start + 1] = replacement
    else:
        first, last = _entry_span(lines, key_i, end)
        lines[key_i:(last + 1) if first is not None else key_i + 1] = replacement
    return "".join(lines)


def add_external_dir(text: str, path: str) -> tuple[str, bool]:
    dirs = _external_dirs(text)
    if path in dirs:
        return text, False
    return _set_external_dirs(text, [*dirs, path]), True


def remove_external_dir(text: str, path: str) -> tuple[str, bool]:
    dirs = _external_dirs(text)
    if path not in dirs:
        return text, False
    return _set_external_dirs(text, [d for d in dirs if d != path]), True


def _validate_external(text: str, path: str, present: bool) -> None:
    dirs = _external_dirs(text)
    if (path in dirs) != present:
        state = "present" if present else "absent"
        raise CutoverError(f"config edit failed: expected {path} {state} in skills.external_dirs")


def _policy_inputs(store: Path, policy) -> tuple[dict, dict]:
    """The verified Store Manifest identities and one validated Profile Policy (ADR-0018)."""
    path = Path(policy).expanduser()
    identities = pp.identities(store)
    data = pp.load(path)
    problems = pp.validate(data, identities, filename=path.stem)
    if problems:
        raise CutoverError("policy: " + "; ".join(problems))
    return data, identities


def _policy_problems(policy: dict, identities: dict, store: Path, after: dict) -> list[str]:
    """wayfinder #11 section 6 items 4-5: the Profile Policy conditions (ADR-0018)."""
    problems: list[str] = []
    foundation_names: set[str] = set()
    for entry in policy["foundation"]:
        if set(entry) == {"project", "name"}:
            name = entry["name"]
        else:
            # An exposure alias renames a farm path, never the indexed Name (ADR-0018), and the
            # resolution map is keyed by the indexed frontmatter Name.
            name = identities[entry["id"]]["name"]
        foundation_names.add(name)
        if name not in after:
            problems.append(f"foundation not resolving: {name}")
    after_paths = {row["realpath"] for row in after.values()}
    for ident in policy.get("brokered", []):
        row = identities[ident]
        if str((store / row["path"]).resolve()) in after_paths:
            problems.append(f"brokered skill exposed: {ident}")
        if row["name"] in after and row["name"] not in foundation_names:
            problems.append(f"brokered skill in the index: {ident}")
    return problems


def _gate(before: dict, roots, farm: Path, policy: dict | None = None,
          identities: dict | None = None, store: Path | None = None) -> tuple[list[str], dict]:
    """wayfinder #11 section 6 checks, policy-free plus (when given) the policy conditions."""
    after = resolution([*roots, farm])
    problems = [f"regression: {name} no longer resolves" for name in sorted(before)
                if name not in after]
    for name, row in sorted(after.items()):
        for target in references(Path(row["realpath"])):
            if target not in after:
                problems.append(f"unresolved reference: {name} -> {target}")
    if policy is not None:
        problems.extend(_policy_problems(policy, identities, store, after))
    return problems, after


def _plan_inputs(store, manifest, farm) -> tuple[Path, Path, Path]:
    if store is None or manifest is None or farm is None:
        raise CutoverError("--store, --manifest and --farm are required")
    return (Path(store).expanduser().resolve(), Path(manifest).expanduser().resolve(),
            Path(farm).expanduser())


def _apply(data: dict, baseline: Path, config: Path, store, manifest, farm,
           policy=None) -> dict:
    if str(config) != data["config"]["path"]:
        raise CutoverError(f"baseline config {data['config']['path']} does not match --config {config}")
    store, manifest, farm = _plan_inputs(store, manifest, farm)
    farm = farm.parent.resolve() / farm.name
    policy_data, identities = (None, None)
    if policy is not None:
        policy_data, identities = _policy_inputs(store, policy)
    generated = ef.run("generate", store, manifest, farm)
    ef.run("verify", store, manifest, farm)
    problems, after = _gate(data["resolution"], data["roots"], farm,
                            policy=policy_data, identities=identities, store=store)
    if problems:
        raise CutoverError("; ".join(problems))
    text = config.read_text()
    updated, changed = add_external_dir(text, str(farm))
    if changed:
        _validate_external(updated, str(farm), present=True)
        config.write_text(updated)
    data["applied"] = {"farm": str(farm),
                       "config_sha256": hashlib.sha256(config.read_text().encode()).hexdigest(),
                       "resolution": after}
    _write_baseline(baseline, data)
    return {"ok": True, "consumer": data["consumer"], "config": str(config), "farm": str(farm),
            "changed": changed, "exposures": generated["exposures"]}


def _verify(config: Path, store, manifest, farm, policy=None, roots=()) -> dict:
    store, manifest, farm = _plan_inputs(store, manifest, farm)
    result = ef.run("verify", store, manifest, farm)
    if str(farm) not in _external_dirs(config.read_text()):
        raise CutoverError(f"config does not list the farm in skills.external_dirs: {farm}")
    if policy is not None:
        policy_data, identities = _policy_inputs(store, policy)
        problems, _ = _gate({}, roots, farm, policy=policy_data, identities=identities,
                            store=store)
        if problems:
            raise CutoverError("; ".join(problems))
    return {"ok": True, "consumer": result["consumer"], "config": str(config),
            "farm": str(farm), "exposures": result["exposures"]}


def _rollback(data: dict, baseline: Path, config: Path) -> dict:
    if str(config) != data["config"]["path"]:
        raise CutoverError(f"baseline config {data['config']['path']} does not match --config {config}")
    applied = data.pop("applied", None)
    if not applied:
        return {"ok": True, "consumer": data["consumer"], "config": str(config), "changed": False}
    text = config.read_text()
    updated, changed = remove_external_dir(text, applied["farm"])
    if changed:
        _validate_external(updated, applied["farm"], present=False)
        config.write_text(updated)
    _write_baseline(baseline, data)
    return {"ok": True, "consumer": data["consumer"], "config": str(config), "changed": changed}


def run(command: str, *, config: Path, baseline: Path | None = None, consumer: str = "",
        roots=(), store: Path | None = None, manifest: Path | None = None,
        farm: Path | None = None, policy: Path | None = None) -> dict:
    """Capture a baseline, apply the Cutover, verify it, or roll it back."""
    if command not in {"baseline", "apply", "verify", "rollback"}:
        raise CutoverError(f"unknown command: {command}")
    config = Path(config).expanduser()
    if command == "verify":
        return _verify(config, store, manifest, farm, policy=policy, roots=roots)
    if baseline is None:
        raise CutoverError(f"--baseline is required for {command}")
    baseline = Path(baseline).expanduser()
    if command == "baseline":
        if not config.is_file():
            raise CutoverError(f"config not found: {config}")
        data = _baseline(config, consumer, roots)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        _write_baseline(baseline, data)
        return {"ok": True, "consumer": consumer, "config": str(config),
                "exposures": len(data["resolution"])}
    data = _read_baseline(baseline)
    if command == "apply":
        return _apply(data, baseline, config, store, manifest, farm, policy=policy)
    return _rollback(data, baseline, config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("baseline", "apply", "verify", "rollback"))
    parser.add_argument("--consumer", default="")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path,
                        help="baseline JSON; defaults to ~/.config/skill-broker/baselines/<consumer>.json")
    parser.add_argument("--roots", type=Path, action="append", default=[])
    parser.add_argument("--store", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--farm", type=Path)
    parser.add_argument("--policy", type=Path,
                        help="Profile Policy (<store>/policies/<profile>.json); adds items 4-5")
    args = parser.parse_args(argv)
    baseline = args.baseline
    if baseline is None:
        if args.command == "verify":
            baseline = Path("unused")
        elif args.consumer:
            baseline = (Path.home() / ".config" / "skill-broker" / "baselines"
                        / f"{args.consumer}.json")
    try:
        result = run(args.command, config=args.config, baseline=baseline,
                     consumer=args.consumer, roots=args.roots, store=args.store,
                     manifest=args.manifest, farm=args.farm, policy=args.policy)
    except (CutoverError, ef.FarmError, pp.PolicyError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
