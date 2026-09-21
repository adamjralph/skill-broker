#!/usr/bin/env python3
"""Cut one Consumer's skill resolution over to its Exposure Farm, reversibly (ADR-0011/0012).

Cutover is one Consumer at a time, behind a verification gate, and reversible to a recorded
baseline.  This module never touches Skill content: it records what resolved before, generates
and verifies the Consumer's farm (reusing ``scripts/exposure_farm.py``), makes one surgical edit
to the Consumer's loader config, and can undo exactly that edit.

The loader config is Hermes's ``config.yaml``: the farm path is added to
``skills.external_dirs`` byte-preservingly — creating the top-level ``skills:`` block first
when the config has none — and each Brokered Skill's Name is added to ``skills.disabled`` so it
is withheld from the automatic index (ADR-0021).  Rollback removes exactly those edits,
restoring the config byte-for-byte (removing a block this tool created rather than leaving an
empty one, and never touching a ``disabled`` entry another writer placed there).  The gate
implements wayfinder #11 section 6.  Without a policy it covers the policy-free conditions:

1. the Exposure Manifest validates against the Store Manifest;
2. every farm link resolves into the store;
3. each exposure resolves to the canonical ``package_sha256``;
4. no name that resolved before Cutover stops resolving (zero regressions);
5. no cross-skill reference is left unresolved by the Cutover. A reference that was already
   dangling in the recorded baseline is a pre-existing native defect, not a Cutover regression
   (ADR-0025), so it is exempt; a reference a newly-exposed Skill leaves dangling still fails
   closed.

The baseline records the Consumer's **effective exposure**, not a single filesystem walk: the
Cutover roots given on the command line plus the config's pre-existing ``skills.external_dirs``
(the Consumer's other native skill roots), minus every Name in ``skills.disabled``. Modelling the
extra roots and the disabled list makes the baseline agree with the automatic index the gate
checks, so a policy authored from the baseline resolves and references into an external directory
(for example ``ask-matt`` in ``~/.agents/skills``) are seen. The extra directories stay native —
nothing is wired or brokered from them — and a Foundation Set may list or omit them as the
operator decides (ADR-0025).

With ``--policy PATH`` - an authored ``<store>/policies/<profile>.json`` (ADR-0018) - the gate
first validates the policy against the verified Store Manifest via ``scripts/profile_policy.py``
and then adds the two policy-dependent conditions:

4'. every Foundation Skill still resolves: store-backed entries by their indexed Name,
    project-wired entries by their Name;
5'. no Brokered Skill appears in the automatic index, by canonical store realpath or by Name -
    except a Brokered twin that legitimately shares a Foundation's Name (the Name Collision case
    of ADR-0018, whose twin is brokered because Hermes indexes on frontmatter ``name``).

Cutover withholds each Brokered Skill from that index by adding its Name to the Consumer's
``skills.disabled`` (ADR-0021); item 5' is checked against the withheld index, and rollback
removes exactly the Names this Cutover added.

The before/after resolution maps are both recorded: the baseline holds the before map under
``resolution`` and, once Cutover is applied, the after map under ``applied.resolution``.

Usage::

    python3 scripts/cutover.py baseline --consumer NAME --config PATH --baseline PATH \\
        --roots DIR [--roots DIR ...]
    python3 scripts/cutover.py apply    --store DIR --manifest PATH --farm DIR \\
        --config PATH --baseline PATH [--policy PATH]
    python3 scripts/cutover.py verify   --store DIR --manifest PATH --farm DIR --config PATH \\
        [--policy PATH] [--roots DIR ...] [--baseline PATH]
    python3 scripts/cutover.py rollback --config PATH --baseline PATH

Reversible implementation defaults: the baseline is a machine-local JSON file under
``~/.config/skill-broker/baselines/`` and is uncommitted; baseline is read-only; apply requires
an existing baseline so rollback is always possible; a failed apply leaves the previous config
and farm intact.  Rollback restores resolution (the config edit) only and never deletes Skill
content; it leaves a generated farm in place.  ``verify`` re-runs the policy gate and, when a
baseline is available (an explicit ``--baseline`` or the ``--consumer`` default), reloads the
recorded before map and effective roots so its checks match ``apply``.
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


def resolution(roots, disabled=()) -> dict:
    """First-root-wins ``{name: {realpath, package_sha256}}`` over the given Skill roots.

    ``disabled`` Names are withheld from the result: this is the effective exposure Hermes
    builds its automatic index from (``skills.disabled``), not a raw directory walk (ADR-0025).
    """
    withheld = set(disabled)
    resolved: dict[str, dict] = {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for skill_dir in _skill_dirs(root):
            name = frontmatter_name(skill_dir / "SKILL.md") or skill_dir.name
            if name in withheld or name in resolved:
                continue
            package_sha256, _, _, _ = hash_package(skill_dir)
            resolved[name] = {"realpath": str(skill_dir.resolve()),
                              "package_sha256": package_sha256}
    return resolved


def _skills_list(text: str, key: str) -> list[str]:
    data = yaml.safe_load(text) or {}
    values = (data.get("skills") or {}).get(key) or []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise CutoverError(f"skills.{key} must be a list of strings")
    return values


def _external_dirs(text: str) -> list[str]:
    return _skills_list(text, "external_dirs")


def _disabled_names(text: str) -> list[str]:
    return _skills_list(text, "disabled")


def _effective_roots(roots, text: str) -> list[str]:
    """The Cutover roots plus the config's pre-existing ``external_dirs`` (its native roots)."""
    out: list[str] = []
    for root in roots:
        path = str(Path(root).expanduser())
        if path not in out:
            out.append(path)
    for directory in _external_dirs(text):
        if directory not in out:
            out.append(directory)
    return out


def _recorded_disabled(data: dict) -> list[str] | None:
    """The pre-Cutover ``skills.disabled`` a baseline recorded, or ``None`` for an older one."""
    return (data.get("config") or {}).get("disabled")


def _baseline(config: Path, consumer: str, roots) -> dict:
    text = config.read_text()
    roots = _effective_roots(roots, text)
    disabled = _disabled_names(text)
    resolved = resolution(roots, disabled)
    return {"baseline_version": BASELINE_VERSION, "generator": GENERATOR, "consumer": consumer,
            "config": {"path": str(config), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                       "external_dirs": _external_dirs(text), "disabled": disabled},
            "roots": roots,
            "resolution": {name: dict(row) for name, row in sorted(resolved.items())}}


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


# --- config editing: surgical, byte-preserving (Hermes skills.external_dirs/disabled) ------


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


def _set_child_list(text: str, key: str, values: list[str]) -> str:
    """Rewrite one ``skills.<key>`` list as a block list, replacing any inline form.

    A config with no top-level ``skills:`` key gets the block created after its last top-level
    key, every other byte untouched: ``remove_created_skills_block`` relies on that to restore
    the config exactly. A missing key in an existing block is appended after its siblings.
    """
    lines = text.splitlines(keepends=True)
    replacement = ([f"  {key}: []\n"] if not values
                   else [f"  {key}:\n", *(f"    - {v}\n" for v in values)])
    block = _top_block(lines, "skills")
    if block is None:
        at = _create_block_index(lines)
        if at and not lines[at - 1].endswith("\n"):
            raise CutoverError("cannot create the `skills:` block: no line break to insert after")
        lines[at:at] = ["skills:\n", *replacement]
        return "".join(lines)
    start, end = block
    key_i = _child_key(lines, start, end, key)
    if key_i is None:
        lines[start + 1:start + 1] = replacement
    else:
        first, last = _entry_span(lines, key_i, end)
        lines[key_i:(last + 1) if first is not None else key_i + 1] = replacement
    return "".join(lines)


def _set_external_dirs(text: str, dirs: list[str]) -> str:
    return _set_child_list(text, "external_dirs", dirs)


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


def add_disabled_names(text: str, names: list[str]) -> tuple[str, list[str]]:
    """Add Brokered Names to ``skills.disabled``, preserving existing entries.

    Returns the edited text and exactly the Names this call added, so rollback removes only
    those and never an entry another writer placed there.
    """
    current = _disabled_names(text)
    added = [name for name in names if name not in current]
    if not added:
        return text, []
    return _set_child_list(text, "disabled", [*current, *added]), added


def remove_added_disabled(text: str, names: list[str], key_existed: bool) -> tuple[str, bool]:
    """Remove only the Names this Cutover added, restoring a pre-existing list."""
    if not names:
        return text, False
    added = set(names)
    remaining = [n for n in _disabled_names(text) if n not in added]
    if not remaining and not key_existed:
        return _remove_child_key(text, "disabled"), True
    return _set_child_list(text, "disabled", remaining), True


def _remove_child_key(text: str, key: str) -> str:
    """Delete one ``skills.<key>`` entry (inline or block) from the ``skills:`` block."""
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        return text
    start, end = block
    key_i = _child_key(lines, start, end, key)
    if key_i is None:
        return text
    first, last = _entry_span(lines, key_i, end)
    del lines[key_i:(last + 1) if first is not None else key_i + 1]
    return "".join(lines)


def _has_skills_block(text: str) -> bool:
    return _top_block(text.splitlines(keepends=True), "skills") is not None


def _has_child_key(text: str, key: str) -> bool:
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    return block is not None and _child_key(lines, block[0], block[1], key) is not None


def _create_block_index(lines: list[str]) -> int:
    """Index just past the last top-level block, before any trailing blank lines."""
    last = next((i for i, line in enumerate(lines) if line.strip() and not line[0].isspace()),
                None)
    if last is None:
        return 0
    index = len(lines)
    while index > last + 1 and not lines[index - 1].strip():
        index -= 1
    return index


def remove_created_skills_block(text: str, path: str,
                                added_disabled=()) -> tuple[str, bool]:
    """Undo a ``skills:`` block this tool created, restoring the config exactly.

    Fails closed unless the block holds nothing but the Cutover's own ``external_dirs`` entry
    and the ``disabled`` entries it added, so rollback never discards another writer's keys.
    """
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        return text, False
    start, end = block
    spans = {}
    for key in ("external_dirs", "disabled"):
        key_i = _child_key(lines, start, end, key)
        first, last = _entry_span(lines, key_i, end) if key_i is not None else (None, None)
        spans[key] = (key_i, first, last)
    key_i, first, last = spans["external_dirs"]
    if key_i is None:
        raise CutoverError("rollback: the created `skills:` block has no external_dirs")
    entries = [] if first is None else [lines[i] for i in range(first, last + 1)]
    if entries != [f"    - {path}\n"]:
        raise CutoverError("rollback: the created `skills:` block was modified")
    d_key_i, d_first, d_last = spans["disabled"]
    if (d_key_i is not None) != bool(added_disabled):
        raise CutoverError("rollback: the created `skills:` block was modified")
    disabled_entries = ([] if d_first is None
                        else [lines[i] for i in range(d_first, d_last + 1)])
    if disabled_entries != [f"    - {n}\n" for n in added_disabled]:
        raise CutoverError("rollback: the created `skills:` block was modified")
    content = {key_i}
    if first is not None:
        content.update(range(first, last + 1))
    if d_key_i is not None:
        content.add(d_key_i)
    if d_first is not None:
        content.update(range(d_first, d_last + 1))
    if {i for i in range(start + 1, end) if lines[i].strip()} != content:
        raise CutoverError("rollback: the created `skills:` block was modified")
    del lines[start:max(content) + 1]
    return "".join(lines), True


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


def _entry_name(entry: dict, identities: dict) -> str:
    """The indexed Name of a Foundation Set entry (a project-wired entry carries it directly)."""
    return entry["name"] if set(entry) == {"project", "name"} else identities[entry["id"]]["name"]


def _foundation_names(policy: dict, identities: dict) -> set[str]:
    return {_entry_name(entry, identities) for entry in policy["foundation"]}


def _identity_names(row: dict) -> list[str]:
    """Names a Brokered identity may be indexed under: its Name plus its aliases (ADR-0009)."""
    return list(dict.fromkeys([row["name"], *(row.get("aliases") or [])]))


def _withheld_names(policy: dict, identities: dict) -> list[str]:
    """The Brokered Names Cutover withholds, excluding Foundation Name Collisions (ADR-0021)."""
    foundation_names = _foundation_names(policy, identities)
    names: list[str] = []
    for ident in policy.get("brokered", []):
        for name in _identity_names(identities[ident]):
            if name not in foundation_names and name not in names:
                names.append(name)
    return names


def _policy_problems(policy: dict, identities: dict, store: Path, after: dict,
                     withheld=()) -> list[str]:
    """wayfinder #11 section 6 items 4-5: the Profile Policy conditions (ADR-0018/0021)."""
    problems: list[str] = []
    index = {name: row for name, row in after.items() if name not in set(withheld)}
    foundation_names: set[str] = set()
    for entry in policy["foundation"]:
        # An exposure alias renames a farm path, never the indexed Name (ADR-0018), and the
        # resolution map is keyed by the indexed frontmatter Name.
        name = _entry_name(entry, identities)
        foundation_names.add(name)
        if name not in index:
            problems.append(f"foundation not resolving: {name}")
    after_paths = {row["realpath"] for row in after.values()}
    for ident in policy.get("brokered", []):
        row = identities[ident]
        if str((store / row["path"]).resolve()) in after_paths:
            problems.append(f"brokered skill exposed: {ident}")
        if any(name in index and name not in foundation_names
               for name in _identity_names(row)):
            problems.append(f"brokered skill in the index: {ident}")
    return problems


def _unresolved_references(before: dict, after: dict) -> list[str]:
    """References the Cutover leaves unresolved, exempting ones already dangling in the baseline.

    A Cutover is additive: it can break a reference only where one resolved before, so a
    reference that was already dangling is a pre-existing native defect the baseline records,
    not a Cutover regression (ADR-0025). A reference a newly-exposed Skill leaves dangling is
    still a problem.
    """
    baseline_dangling = {(name, target)
                         for name, row in before.items()
                         for target in references(Path(row["realpath"]))
                         if target not in before}
    problems: list[str] = []
    for name, row in sorted(after.items()):
        for target in references(Path(row["realpath"])):
            if target not in after and (name, target) not in baseline_dangling:
                problems.append(f"unresolved reference: {name} -> {target}")
    return problems


def _gate(before: dict, roots, farm: Path, policy: dict | None = None,
          identities: dict | None = None, store: Path | None = None,
          withheld=(), disabled=()) -> tuple[list[str], dict]:
    """wayfinder #11 section 6 checks, policy-free plus (when given) the policy conditions."""
    after = resolution([*roots, farm], disabled)
    problems = [f"regression: {name} no longer resolves" for name in sorted(before)
                if name not in after]
    problems.extend(_unresolved_references(before, after))
    if policy is not None:
        problems.extend(_policy_problems(policy, identities, store, after, withheld=withheld))
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
    withheld: list[str] = []
    if policy is not None:
        policy_data, identities = _policy_inputs(store, policy)
        withheld = _withheld_names(policy_data, identities)
    generated = ef.run("generate", store, manifest, farm)
    ef.run("verify", store, manifest, farm)
    text = config.read_text()
    # The gate is checked against the index that will result: existing disabled Names plus
    # the Brokered Names this Cutover is about to withhold (ADR-0021).  ``disabled`` restricts
    # the resolution map (the effective exposure); ``withheld`` restricts the policy's index.
    # The baseline's disabled list is the pre-Cutover one; the live config may already carry
    # this Cutover's withheld Names on an idempotent re-apply.
    recorded_disabled = _recorded_disabled(data)
    pre_existing_disabled = (list(recorded_disabled) if recorded_disabled is not None
                             else _disabled_names(text))
    effective_disabled = list(dict.fromkeys([*pre_existing_disabled, *withheld]))
    problems, after = _gate(data["resolution"], data["roots"], farm,
                            policy=policy_data, identities=identities, store=store,
                            withheld=effective_disabled, disabled=pre_existing_disabled)
    if problems:
        raise CutoverError("; ".join(problems))
    created = not _has_skills_block(text)
    disabled_existed = _has_child_key(text, "disabled")
    updated, farm_added = add_external_dir(text, str(farm))
    updated, added_disabled = add_disabled_names(updated, withheld)
    changed = farm_added or bool(added_disabled)
    if changed:
        _validate_external(updated, str(farm), present=True)
        config.write_text(updated)
    else:
        # An idempotent re-apply must not forget what this Cutover created or added.
        prior = data.get("applied") or {}
        created = bool(prior.get("created_skills_block"))
        disabled_existed = bool(prior.get("disabled_key_existed", disabled_existed))
        added_disabled = list(prior.get("added_disabled", []))
        farm_added = bool(prior.get("farm_added", False))
    data["applied"] = {"farm": str(farm),
                       "config_sha256": hashlib.sha256(config.read_text().encode()).hexdigest(),
                       "created_skills_block": created,
                       "disabled_key_existed": disabled_existed,
                       "farm_added": farm_added,
                       "added_disabled": added_disabled,
                       "resolution": after}
    _write_baseline(baseline, data)
    return {"ok": True, "consumer": data["consumer"], "config": str(config), "farm": str(farm),
            "changed": changed, "exposures": generated["exposures"],
            "withheld": list(withheld)}


def _verify(config: Path, store, manifest, farm, policy=None, roots=(), baseline=None) -> dict:
    store, manifest, farm = _plan_inputs(store, manifest, farm)
    result = ef.run("verify", store, manifest, farm)
    text = config.read_text()
    if str(farm) not in _external_dirs(text):
        raise CutoverError(f"config does not list the farm in skills.external_dirs: {farm}")
    if policy is not None:
        policy_data, identities = _policy_inputs(store, policy)
        before: dict = {}
        disabled: list[str] = []
        verify_roots = _effective_roots(roots, text)
        if baseline is not None and Path(baseline).expanduser().is_file():
            recorded = _read_baseline(Path(baseline).expanduser())
            before = recorded["resolution"]
            disabled = list(_recorded_disabled(recorded) or [])
            verify_roots = _effective_roots(recorded["roots"], text)
        problems, _ = _gate(before, verify_roots, farm,
                            policy=policy_data, identities=identities, store=store,
                            withheld=_disabled_names(text), disabled=disabled)
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
    added_disabled = list(applied.get("added_disabled", []))
    if applied.get("created_skills_block"):
        updated, changed = remove_created_skills_block(text, applied["farm"], added_disabled)
    else:
        if applied.get("farm_added"):
            updated, farm_changed = remove_external_dir(text, applied["farm"])
        else:
            # A farm that predates this Cutover (an idempotent re-apply) is not ours to remove.
            updated, farm_changed = text, False
        updated, disabled_changed = remove_added_disabled(
            updated, added_disabled, bool(applied.get("disabled_key_existed")))
        changed = farm_changed or disabled_changed
    if changed:
        if applied.get("created_skills_block") or applied.get("farm_added"):
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
        return _verify(config, store, manifest, farm, policy=policy, roots=roots,
                       baseline=baseline)
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
    if baseline is None and args.consumer:
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
