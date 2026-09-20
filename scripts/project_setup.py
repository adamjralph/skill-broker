#!/usr/bin/env python3
"""Idempotent project-scoped skill setup (wayfinder #16; build ticket #27).

Given a project root (default: the nearest `.git` ancestor of the cwd):

1. ensures `<root>/.agents/skills/` exists (Hermes's project-local skills subdir);
2. discovers the project's authored Skills (any directory holding a `SKILL.md`) and symlinks
   each one into `<root>/.agents/skills/<name>` — additive, never overwriting, never deleting;
3. adds the resolved `<root>` to `skills.trusted_project_dirs` in `~/.hermes/config.yaml`,
   creating the `skills:` block when the config has none, idempotently and preserving every
   other byte of the config.

Every change is printed, and `--undo` reverses exactly what a previous run added, using the
state file `<root>/.agents/project-setup.json`.

Boundaries (#16): no content copying; no Skill Store involvement; no `external_dirs` edits;
no deletion. These symlinks are the project's own, not an Exposure Farm (ADR-0011).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

PRUNE = {
    ".git", ".github", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache", ".DS_Store",
}
SKILLS_SUBDIR = Path(".agents") / "skills"
STATE_NAME = "project-setup.json"


class SetupError(Exception):
    """The project or config is not in a state the script can set up."""


def find_project_root(start: Path) -> Path:
    """The nearest ancestor of ``start`` (inclusive) containing a `.git` entry."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise SetupError(f"no .git ancestor found from {start}; pass --project")


def discover_skill_dirs(root: Path, target: Path) -> list[Path]:
    """Authored Skill directories under ``root``, excluding the target and caches."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        try:
            here.relative_to(target)
            inside_target = here != root
        except ValueError:
            inside_target = False
        if inside_target:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE)
        if "SKILL.md" in filenames and here != root:
            found.append(here)
    return sorted(found)


# --- config editing: surgical, byte-preserving -----------------------------------------


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
    return next((i for i in range(start + 1, end) if lines[i].rstrip("\n") == pattern), None)


def _entry_span(lines: list[str], key_i: int, end: int) -> tuple[int, int]:
    """First and last indices of the ``    - `` entries following a child key."""
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


def add_trusted_root(text: str, root: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        if _has_inline_skills_key(lines):
            raise SetupError("config's top-level `skills:` key is inline, not a block")
        at = _create_block_index(lines)
        if at and not lines[at - 1].endswith("\n"):
            raise SetupError("cannot create the `skills:` block: no line break to insert after")
        lines[at:at] = ["skills:\n", "  trusted_project_dirs:\n", f"    - {root}\n"]
        return "".join(lines), True
    start, end = block
    key_i = _child_key(lines, start, end, "trusted_project_dirs")
    if key_i is None:
        lines[start + 1:start + 1] = ["  trusted_project_dirs:\n", f"    - {root}\n"]
        return "".join(lines), True
    first, last = _entry_span(lines, key_i, end)
    if first is not None:
        for i in range(first, last + 1):
            if lines[i].strip() == f"- {root}":
                return text, False
    insert_at = (last + 1) if last is not None else key_i + 1
    lines[insert_at:insert_at] = [f"    - {root}\n"]
    return "".join(lines), True


def remove_trusted_root(text: str, root: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        return text, False
    start, end = block
    key_i = _child_key(lines, start, end, "trusted_project_dirs")
    if key_i is None:
        return text, False
    first, last = _entry_span(lines, key_i, end)
    if first is None:
        return text, False
    entry = next((i for i in range(first, last + 1) if lines[i].strip() == f"- {root}"), None)
    if entry is None:
        return text, False
    del lines[entry]
    block = _top_block(lines, "skills")
    start, end = block
    first, last = _entry_span(lines, key_i, end)
    if first is None:
        del lines[key_i]
    return "".join(lines), True


def _has_skills_block(text: str) -> bool:
    return _top_block(text.splitlines(keepends=True), "skills") is not None


def _has_inline_skills_key(lines: list[str]) -> bool:
    """True when a top-level ``skills:`` key exists in inline form (``skills: {...}``)."""
    return any(not line[0].isspace() and line.rstrip("\n").startswith("skills:")
               for line in lines if line.strip())


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


def remove_created_skills_block(text: str, root: str) -> tuple[str, bool]:
    """Undo a ``skills:`` block this tool created, restoring the config exactly.

    Fails closed unless the block holds nothing but our own ``trusted_project_dirs`` entry,
    so undo never discards another writer's keys.
    """
    lines = text.splitlines(keepends=True)
    block = _top_block(lines, "skills")
    if block is None:
        return text, False
    start, end = block
    key_i = _child_key(lines, start, end, "trusted_project_dirs")
    if key_i is None:
        raise SetupError("undo: the created `skills:` block has no trusted_project_dirs")
    first, last = _entry_span(lines, key_i, end)
    entries = [] if first is None else [lines[i] for i in range(first, last + 1)]
    if entries != [f"    - {root}\n"]:
        raise SetupError("undo: the created `skills:` block was modified")
    content = {key_i}
    if first is not None:
        content.update(range(first, last + 1))
    if {i for i in range(start + 1, end) if lines[i].strip()} != content:
        raise SetupError("undo: the created `skills:` block was modified")
    del lines[start:max(content) + 1]
    return "".join(lines), True


def _validate_config(text: str, root: str, present: bool) -> None:
    data = yaml.safe_load(text)
    dirs = (data.get("skills") or {}).get("trusted_project_dirs") or []
    if (root in dirs) != present:
        state = "present" if present else "absent"
        raise SetupError(f"config edit failed: expected {root} {state} in trusted_project_dirs")


# --- state and the setup run -----------------------------------------------------------


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"created_links": [], "trusted_root_added": False,
                "created_skills_block": False}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def setup(project: Path, config: Path, undo: bool = False) -> list[str]:
    target = project / SKILLS_SUBDIR
    state_path = project / ".agents" / STATE_NAME
    messages: list[str] = []

    if undo:
        if not state_path.exists():
            return [f"nothing to undo: no state file at {state_path}"]
        state = load_state(state_path)
        for link in state.get("created_links", []):
            link_path = Path(link)
            if link_path.is_symlink():
                link_path.unlink()
                messages.append(f"removed symlink {link_path}")
            elif link_path.exists():
                messages.append(f"left {link_path} in place (not a symlink we created)")
        if state.get("trusted_root_added"):
            text = config.read_text()
            if state.get("created_skills_block"):
                updated, changed = remove_created_skills_block(text, str(project))
            else:
                updated, changed = remove_trusted_root(text, str(project))
            if changed:
                _validate_config(updated, str(project), present=False)
                config.write_text(updated)
                messages.append(f"removed {project} from skills.trusted_project_dirs")
        state_path.unlink()
        messages.append(f"removed state file {state_path}")
        return messages

    target.mkdir(parents=True, exist_ok=True)
    messages.append(f"ensured {target}")

    state = load_state(state_path)
    created = set(state.get("created_links", []))
    for skill_dir in discover_skill_dirs(project, target):
        link = target / skill_dir.name
        if os.path.lexists(link):
            messages.append(f"skipped {link} (already exists)")
            continue
        link.symlink_to(os.path.relpath(skill_dir, target))
        created.add(str(link))
        messages.append(f"linked {link} -> {skill_dir}")

    text = config.read_text()
    created_block = not _has_skills_block(text)
    updated, added = add_trusted_root(text, str(project))
    if added:
        _validate_config(updated, str(project), present=True)
        config.write_text(updated)
        messages.append(f"added {project} to skills.trusted_project_dirs")
    else:
        messages.append(f"{project} already in skills.trusted_project_dirs")

    state["created_links"] = sorted(created)
    state["trusted_root_added"] = bool(state.get("trusted_root_added")) or added
    state["created_skills_block"] = bool(state.get("created_skills_block")) or created_block
    save_state(state_path, state)
    messages.append(f"wrote state file {state_path}")
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=None, help="project root (default: nearest .git ancestor)")
    parser.add_argument("--config", default=str(Path.home() / ".hermes" / "config.yaml"),
                        help="Hermes config path (default: ~/.hermes/config.yaml)")
    parser.add_argument("--undo", action="store_true", help="reverse exactly what a prior run added")
    args = parser.parse_args(argv)

    try:
        project = (Path(args.project).expanduser() if args.project
                   else find_project_root(Path.cwd()))
        project = project.resolve()
        config = Path(args.config).expanduser()
        if not config.exists():
            raise SetupError(f"config not found: {config}")
        messages = setup(project, config, undo=args.undo)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
