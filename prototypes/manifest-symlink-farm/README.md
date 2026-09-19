# Prototype — manifest-driven symlink-farm generator

**THROWAWAY.** This answers wayfinder ticket
[#7](https://github.com/adamjralph/skill-broker/issues/7): *can a
manifest-driven generator emit per-consumer skill exposures (symlink farms)
from a content-addressed store, so Codex, Pi, BB, and Hermes each resolve their
intended variants?*

**Verdict: yes.** Run `./run.sh` to reproduce (41 invariants hold).

> **Scope note (binding):** **manor-ai is excluded.** It is a project-scoped
> tree and project-scoped skills stay project-owned (ADR-0008); they are wired
> by the per-project setup path (#16), never by the store. manor-ai appears
> nowhere in this prototype and must not reappear in future versions.

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
  Two-level identity: a namespaced **stable id** plus a **content hash** version.
  The **namespace is owner/provenance** (`openai`, `nousresearch`, `bb`,
  `mattpocock`, `cursor`) — never the consuming tool (#9).
- **Policy** — `manifest.json`, hand-authored: per consumer, which *name* maps to
  which id. The generator does **no discovery**; this file is the desired state.
- **Spokes** — `farms/<consumer>/skills/<name>` symlinks into the hub.
- **`generate.py`** — reads the manifest + catalog, emits farms; idempotent;
  `--prune`, `--dry-run`.
- **`verify.py`** — resolves every link and re-hashes the package.

## Recorded result

```
== build fixture store ==
store: store/objects (6 objects, 6 ids)
pdf variants: 2; skill-creator: 2; tdd: 2 distinct objects
note: manor-ai excluded (project-scoped, ADR-0008)

== generate farms (manifest-driven) ==
  create codex/skills/pdf -> .../d06e44461265...
  create codex/skills/skill-creator -> .../22063bebbdf5...
  create codex/skills/test-driven-development -> .../05003d7cd99f...
  create hermes/skills/pdf -> .../a9b715d64c7b...
  create bb/skills/skill-creator -> .../f596cfc1e0e2...
  create bb/skills/tdd -> .../05003d7cd99f...
  create pi/skills/pdf -> .../a9b715d64c7b...
  create pi/skills/tdd -> .../5ae90eb32907...
links: 8 created, 0 replaced, 0 unchanged, 0 pruned

== idempotency: second run must change nothing ==
idempotent: identical link set on re-run

== prune: a stale link is removed ==
prune: stale link removed

=== resolved exposures ===
  codex   pdf                     openai.pdf             d06e44461265
  codex   skill-creator           openai.skill-creator   22063bebbdf5
  codex   test-driven-development  mattpocock.tdd         05003d7cd99f
  hermes  pdf                     nousresearch.pdf       a9b715d64c7b
  bb      skill-creator           bb.skill-creator       f596cfc1e0e2
  bb      tdd                     mattpocock.tdd         05003d7cd99f
  pi      pdf                     nousresearch.pdf       a9b715d64c7b
  pi      tdd                     cursor.tdd             5ae90eb32907

=== same-name divergence ===
  pdf: 2 distinct content(s) across 3 consumers
  skill-creator: 2 distinct content(s) across 2 consumers
  tdd: 2 distinct content(s) across 2 consumers

=== aliases (one identity, many names) ===
  mattpocock.tdd: names tdd, test-driven-development

VERDICT: PASS (41 invariants hold)
```

Three things are demonstrated:

1. **Divergence is preserved.** `pdf` resolves to two *different* objects
   (openai vs nousresearch) across three consumers; `skill-creator` and `tdd`
   to two each. Nothing is overwritten.
2. **Names are not identity.** `mattpocock.tdd` is exposed to codex as
   `test-driven-development` and to bb as `tdd` — two names, one identity, one
   object. No two ids share content: identical content is one identity.
3. **The farms are generated, idempotent, and prunable** — a re-run changes
   nothing; a stale link is removed.

## Real audit provenance

The fixture is synthetic on purpose (it proves the mechanism without vendoring
third-party skill content). These are the real machine variants it is shaped
from — `SKILL.md` and canonical package hashes, from the audit, **manor-ai
excluded**:

| name | id (owner namespace) | real path | SKILL.md | package |
|---|---|---|---|---|
| pdf | `openai.pdf` | `~/.codex/plugins/cache/openai-primary-runtime/pdf/*/skills/pdf` | `afc4472ec4d6` | `39ea3721e79e` |
| pdf | `nousresearch.pdf` | `~/.hermes/hermes-agent/skills/productivity/pdf` (+ profile, shelf) | `c16b9c159a3a` | `e042958d7fbb` |
| skill-creator | `openai.skill-creator` | `~/.codex/skills/.system/skill-creator` | `6656e5475563` | `637c8ac89eb8` |
| skill-creator | `bb.skill-creator` | `~/.bb/runtime/global-skills/<sha>/skills/skill-creator` | `d33bedeb1f3a` | `6cf6fcb5a66d` |
| tdd | `mattpocock.tdd` | `~/Documents/skills-archive/skills/skills/engineering/tdd` (+ `~/Work/.agents/skills/tdd`, identical) | `cb01f66bebfa` | `19c1f265f65c` |
| tdd | `cursor.tdd` | `~/Documents/skills-archive/pstack/skills/tdd` | `adad031f9e79` | `f8a1b18995b1` |

`docx` and `xlsx` were only same-name collisions because of manor-ai; with
manor-ai excluded they have a single identity each (Hermes), so they are not
name collisions in store scope.

Consumer roots the spokes represent: Codex `~/.codex/{skills,plugins}`; Pi
`~/.pi/agent/skills`; BB `~/.bb/runtime/global-skills`; Hermes
`~/.hermes/**/skills` + configured `external_dirs`.

## What this informs

- **#9 (stable-ID + divergence policy):** a two-level id (`owner.name` + package
  hash) lets same-name divergences coexist and lets identical contents collapse
  to one identity with multiple names; name-addressing overwrites variants,
  pure content-addressing loses logical identity.
- **#10 (exposure/manifest):** the manifest can be a small per-consumer policy
  file separate from the store inventory; the generator needs no discovery and
  is idempotent, diffable, and prunable.

## Caveats / not proven

- Synthetic content; no real skill packages were moved or vendored.
- No Hermes/Codex/Pi/BB loader was actually pointed at a generated farm — the
  proof is that the *paths resolve to the intended bytes*, not that each tool's
  discovery then behaves.
- Project-scoped exclusion is represented only by the binding scope note above;
  the prototype does not exercise a project setup path (that is #16).
- No conflict handling for a *shared* farm root (a consumer wanting two variants
  of one name); each consumer is a separate farm here.
- Manifest has no schema yet (see #10).

## Files

| file | purpose |
|---|---|
| `manifest.json` | authored per-consumer exposure policy (the desired state) |
| `make_fixture.py` | builds the synthetic content-addressed store + catalog |
| `generate.py` | manifest-driven farm generator (`--prune`, `--dry-run`) |
| `verify.py` | resolves links, re-hashes packages, asserts divergence/aliases |
| `run.sh` | end-to-end: fixture → generate → idempotency → prune → verify |
