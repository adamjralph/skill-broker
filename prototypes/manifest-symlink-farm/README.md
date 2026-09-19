# Prototype — manifest-driven symlink-farm generator

**THROWAWAY.** This answers wayfinder ticket
[#7](https://github.com/adamjralph/skill-broker/issues/7): *can a
manifest-driven generator emit per-consumer skill exposures (symlink farms)
from a content-addressed store, so Codex, Pi, BB, and Hermes each resolve their
intended variants?*

**Verdict: yes.** Run `./run.sh` to reproduce (35 invariants hold).

## Run it

```sh
./run.sh
```

Pure Python 3, no dependencies, no network, scratch output under this directory
(gitignored). Override the interpreter with `PYTHON=`.

## The model (hub and spoke)

- **Hub** — `store/objects/<package_sha256>/`: content-addressed. One object per
  package content, named by its canonical package hash (`docs/research/identity-hash-depth.md`).
- **Inventory** — `store/catalog.json`: `id → {namespace, name, package_sha, skill_md_sha}`.
  Two-level identity: a namespaced **stable id** (`codex.pdf`) plus a **content
  hash** version.
- **Policy** — `manifest.json`, hand-authored: per consumer, which name maps to
  which id. The generator does **no discovery**; this file is the desired state.
- **Spokes** — `farms/<consumer>/skills/<name>` symlinks into the hub.
- **`generate.py`** — reads the manifest + catalog, emits farms; idempotent;
  `--prune`, `--dry-run`.
- **`verify.py`** — resolves every link and re-hashes the package.

## Recorded result

```
== build fixture store ==
store: store/objects (7 objects, 8 ids)
pdf variants: 3 distinct objects
tdd variants: 2 distinct objects; work.tdd == archive.tdd (alias)

== generate farms (manifest-driven) ==
  create codex/skills/pdf -> ../../../store/objects/fb730c36a3f0...
  create codex/skills/skill-creator -> ../../../store/objects/eb5d5e77206d...
  create codex/skills/tdd -> ../../../store/objects/e41aca684cd2...
  create hermes/skills/pdf -> ../../../store/objects/bbae3c8026b3...
  create bb/skills/skill-creator -> ../../../store/objects/f596cfc1e0e2...
  create bb/skills/tdd -> ../../../store/objects/e41aca684cd2...
  create pi/skills/pdf -> ../../../store/objects/461df525f136...
  create pi/skills/tdd -> ../../../store/objects/519fea83a8fa...
links: 8 created, 0 replaced, 0 unchanged, 0 pruned

== idempotency: second run must change nothing ==
idempotent: identical link set on re-run

== prune: a stale link is removed ==
prune: stale link removed

=== resolved exposures ===
  codex   pdf            codex.pdf              fb730c36a3f0
  codex   skill-creator  codex.skill-creator    eb5d5e77206d
  codex   tdd            work.tdd               e41aca684cd2
  hermes  pdf            hermes.pdf             bbae3c8026b3
  bb      skill-creator  bb.skill-creator       f596cfc1e0e2
  bb      tdd            archive.tdd            e41aca684cd2
  pi      pdf            manor-ai.pdf           461df525f136
  pi      tdd            pstack.tdd             519fea83a8fa

=== same-name divergence ===
  pdf: 3 distinct content(s) across 3 consumers
  skill-creator: 2 distinct content(s) across 2 consumers
  tdd: 2 distinct content(s) across 3 consumers

=== aliases ===
  e41aca684cd2: archive.tdd, work.tdd

VERDICT: PASS (35 invariants hold)
```

Three things are demonstrated:

1. **Divergence is preserved.** `pdf` resolves to three *different* objects for
   codex/hermes/pi; `skill-creator` to two; `tdd` to two. Nothing is overwritten.
2. **Aliases collapse.** `work.tdd` and `archive.tdd` share one object
   (`e41aca684cd2`) — one content, two ids, two farm links.
3. **The farms are generated, idempotent, and prunable** — a re-run changes
   nothing; a stale link is removed.

## Real audit provenance

The fixture is synthetic on purpose (it proves the mechanism without vendoring
third-party skill content). These are the real machine variants it is shaped
from — `SKILL.md` and canonical package hashes, from the audit:

| name | id (namespace) | real path | SKILL.md | package |
|---|---|---|---|---|
| pdf | `codex.pdf` | `~/.codex/plugins/cache/openai-primary-runtime/pdf/*/skills/pdf` | `afc4472ec4d6` | `39ea3721e79e` |
| pdf | `hermes.pdf` | `~/.hermes/hermes-agent/skills/productivity/pdf` (+ profile, shelf) | `c16b9c159a3a` | `e042958d7fbb` |
| pdf | `manor-ai.pdf` | `~/Projects/manor-ai/packages/core/ai/skills/pdf` | `0b3beff9a270` | `006551bd5933` |
| skill-creator | `codex.skill-creator` | `~/.codex/skills/.system/skill-creator` | `6656e5475563` | `637c8ac89eb8` |
| skill-creator | `manor-ai.skill-creator` | `~/Projects/manor-ai/packages/core/ai/skills/skill-creator` | `6e0f52d8885c` | `3c1eddd7b8dc` |
| skill-creator | `bb.skill-creator` | `~/.bb/runtime/global-skills/<sha>/skills/skill-creator` | `d33bedeb1f3a` | `6cf6fcb5a66d` |
| tdd | `pstack.tdd` | `~/Documents/skills-archive/pstack/skills/tdd` | `adad031f9e79` | `f8a1b18995b1` |
| tdd | `work.tdd` | `~/Work/.agents/skills/tdd` (+ `skills-archive/.../engineering/tdd`) | `cb01f66bebfa` | `19c1f265f65c` |

Consumer roots the spokes represent: Codex `~/.codex/{skills,plugins}`; Pi
`~/.pi/agent/skills`; BB `~/.bb/runtime/global-skills`; Hermes
`~/.hermes/**/skills` + configured `external_dirs`.

## What this informs

- **#9 (stable-ID + divergence policy):** a two-level id (`namespace.name` +
  package hash) lets same-name divergences coexist and lets identical contents
  collapse to one object; pure content-addressing alone loses the *logical*
  identity, and pure name-addressing overwrites variants.
- **#10 (exposure/manifest):** the manifest can be a small per-consumer policy
  file separate from the store inventory; the generator needs no discovery and
  is idempotent, diffable, and prunable.

## Caveats / not proven

- Synthetic content; no real skill packages were moved or vendored.
- No Hermes/Codex/Pi/BB loader was actually pointed at a generated farm — the
  proof is that the *paths resolve to the intended bytes*, not that each tool's
  discovery then behaves.
- Project-scoped exclusion (ADR-0008) is not exercised here; `manor-ai` appears
  only as a variant owner.
- No conflict handling (two consumers wanting different variants under one
  shared farm) — each consumer is a separate farm, so it does not arise, but a
  shared-farm consumer would need policy.
- Manifest is not yet validated against a schema, and has no generated/checked-in
  policy decision (see #5 in the reconnaissance).

## Files

| file | purpose |
|---|---|
| `manifest.json` | authored per-consumer exposure policy (the desired state) |
| `make_fixture.py` | builds the synthetic content-addressed store + catalog |
| `generate.py` | manifest-driven farm generator (`--prune`, `--dry-run`) |
| `verify.py` | resolves links, re-hashes packages, asserts divergence/aliases |
| `run.sh` | end-to-end: fixture → generate → idempotency → prune → verify |
