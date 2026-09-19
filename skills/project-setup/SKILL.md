---
name: project-setup
description: Wire a project's own Skills into Hermes so it can load them. Use when a project keeps skills in its own repository (e.g. `agent-team/skills/`) and they do not appear for the agent, or when asked to "set up project skills", "enable project-local skills", or "trust this project's skills".
---

# Project skill setup

Projects keep their own Skills in their own repositories; the Skill Store deliberately does
not own them. This skill is the entry point to an idempotent script that exposes a project's
Skills to Hermes.

## Run it

From anywhere inside the project:

```sh
python3 scripts/project_setup.py            # nearest .git ancestor of the cwd
python3 scripts/project_setup.py --project /path/to/project
python3 scripts/project_setup.py --undo     # reverse exactly what a prior run added
```

The script is `scripts/project_setup.py` in the `skill-broker` repository.

## What it does

1. Ensures `<root>/.agents/skills/` exists — Hermes's project-local skills subdir.
2. Symlinks each authored Skill found under the project (any directory holding a `SKILL.md`)
   into `<root>/.agents/skills/<name>`. Additive: it never overwrites an existing entry and
   never deletes.
3. Adds `<root>` to `skills.trusted_project_dirs` in `~/.hermes/config.yaml` — without this
   Hermes loads nothing project-local — editing only that key and preserving the rest of the
   config byte-for-byte.

Every change is printed. `--undo` removes only the symlinks and config entry a prior run
created, using the state file `<root>/.agents/project-setup.json`.

## Notes

- Project Skills override same-named profile or builtin Skills. That is Hermes's rule and is
  intended for project-owned method sets, but it is worth knowing.
- Trust is per-root, so the script runs once per project.
- It does nothing else: no content copying, no Skill Store involvement, no `external_dirs`
  edits, no deletion. These symlinks are the project's own, not an Exposure Farm.
