# Catalogue Topology Reconnaissance

**Status:** reconnaissance output, feeding the grilling. Findings only; the decisions they
inform are Adam's.
**Date:** 2026-09-19
**Informs:** open decisions #4 (stable-ID/alias policy), #5 (manifest generated/checked in),
#6 (first retrieval method), and ADR-0007 (single Skill Store precedes brokering).

## Question

What is the real topology of skills on this machine — every source, every exposure, every
duplicate and alias — such that a single authoritative Skill Store can be defined and a
broker built over it?

## Method

- Enumerated every `SKILL.md` under `/home/hermes`, `/usr/share`, `/usr/local/share`, `/opt`,
  `/srv`, pruning VCS/dependency/cache directories (`node_modules`, `.git`, `.cache`,
  `site-packages`, `.npm`, `.cargo`, `.rustup`, …). **Raw count: 2296.**
- Because roots are symlink farms, a plain `find` silently misses most of them (it does not
  descend symlinked directories). Re-walked a curated list of 36 roots with
  `os.walk(followlinks=True)`, recording for every `SKILL.md`: the **exposure path**, the
  root that exposes it, its **realpath**, the frontmatter `name`, and the **sha256** of the
  file. **Curated result: 1783 exposures over 1659 realpaths, collapse to 585 distinct
  contents.**
- Canonical identity = `realpath` (same file reached many ways). Content identity = sha256 of
  `SKILL.md`. `name` is used only for collision analysis, never as identity.
- Hermes's own discovery rules were read from primary source, not inferred (citations below).
- Per-skill supporting files (`references/`, `scripts/`…) were **not** hashed this pass; the
  sha covers `SKILL.md` only. A skill whose body is unchanged but whose scripts differ would
  appear identical here. Flagged as an open item.

## Headline numbers

| Measure | Value |
| --- | --- |
| Raw `SKILL.md` files found | 2296 |
| Exposures of a skill to some consumer | 1783 |
| Distinct realpaths | 1659 |
| **Distinct skill contents (sha256)** | **585** |
| Distinct frontmatter names | 548 |
| Names living at >1 path | 347 |
| **Contents copied to >1 path (redundant)** | **364, producing 1074 redundant paths** |
| **Names with genuinely divergent content** | **35** |
| Cross-skill references that dangle | 2 of 26 targets |

The library is ~585 real skills wearing 1659 paths. Two thirds of every path is a copy.

## Source inventory

Ordered by distinct content. "Uniq" = contents that exist *only* in this root.

| Root | Category | Exp | Sha | Uniq | Link% | Git |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `~/research` | archive (Hermes PR evidence) | 713 | 199 | 2 | 0% | no |
| `~/.hermes/hermes-agent/optional-skills` | Hermes runtime | 147 | 147 | 9 | 0% | **yes** (hermes-agent) |
| `~/Documents/skills-archive` | content archive | 136 | 136 | 43 | 0% | no |
| `~/.hermes/profiles/hermes_engineer/skills` | Hermes profile | 118 | 118 | **6** | 1% | no |
| `~/.hermes/skills` (shared shelf / default profile) | Hermes runtime | 117 | 117 | 1 | 3% | no |
| `~/backups` | archive | 122 | 95 | 0 | 0% | no |
| `~/Projects/manor-ai` | project-scoped | 60 | 60 | **60** | 0% | yes (manor-ai) |
| `~/.hermes/hermes-agent/skills` | Hermes builtin | 58 | 58 | 0 | 0% | **yes** |
| `~/.codex/plugins` (cache) | agent plugin bundles | 53 | 53 | 53 | 0% | no |
| `~/.codex/skills` | agent tooling (symlink farm) | 45 | 45 | 6 | 86% | no |
| `~/Work/.agents/skills` | agent tooling (real copy) | 37 | 37 | 0 | 0% | no |
| `~/.agents/skills` | agent tooling (symlink farm) | 23 | 23 | 0 | 91% | no |
| `~/.hermes/backups` | archive | 16 | 16 | 13 | 12% | no |
| `~/.bb/runtime/{global-skills,skill-store}` | existing hash store | 30 | 30 | 1 | 0% | no |
| `~/Documents/life-os` | content archive | 8 | 8 | 1 | 0% | no |
| `~/.hermes/reports` | archive | 8 | 8 | 5 | 0% | no |
| `~/honcho`, `~/services/honcho`, `~/honcho-assessment` | service repo | 18 | 6 | 0 | 83% | yes (honcho) |
| 9 × `~/.hermes/profiles/*/skills` (stillroom, life-agent, astra) | Hermes profiles | 30 | 30 | 0 | 60% | no |
| `/usr/share/omarchy/default/agents/skills` | OS-provided | 2 | 2 | 0 | 0% | no |
| `~/.claude/skills`, `~/.pi/agent/skills`, `~/Downloads`, `~/Documents/stillroom-wiki` | misc | 5 | 5 | 1 | 40% | no |

Not all 585 are *live*: `~/research` (199), `~/backups` (95), `~/.hermes/{backups,reports}`
(24) and large parts of `skills-archive` are historical copies. The live discoverable set is
materially smaller and is concentrated in: Hermes runtime + profiles, the agent-tooling
farms, `manor-ai`, `codex-plugins`, and `honcho`.

## How Hermes actually resolves skills (primary source)

- `HERMES_HOME/skills` is the profile's own root — `get_skills_dir()` at
  `~/.hermes/hermes-agent/hermes_constants.py:1138`. For the default profile that is
  `~/.hermes/skills` (the "shared shelf"); each named profile gets its own.
- Discovery order — `get_all_skills_dirs()` at `agent/skill_utils.py:398`: local profile
  dir → configured `create_dir` → configured `external_dirs` (`:337`).
- This machine's configured `external_dirs` (`~/.hermes/config.yaml:267-270`):
  `/home/hermes/.agents/skills` and `/home/hermes/Documents/stillroom-wiki/skills`.
- Project-local skills — `PROJECT_SKILLS_SUBDIRS` (`agent/skill_utils.py`): `<root>/.hermes/skills`
  and `<root>/.agents/skills`, where `<root>` is the nearest `.git` ancestor. They load **only**
  for trusted roots (`skills.trusted_project_dirs`) and then **override** same-named profile and
  builtin skills.
- Excluded from scanning — `EXCLUDED_SKILL_DIRS` (`agent/skill_utils.py:24`): `.git`,
  `.github`, `.hub`, `.archive`, `.curator_backups`, `node_modules`, caches. Support dirs
  (`references/`, `templates/`, `assets/`, `scripts/`) are not standalone skills.
- Org mirrors — `_org/<org_id>/` are token-gated by a persisted `.active_org` marker
  (`agent/skill_utils.py:44`); no marker, no load.

**Consequence:** the same skill is reachable by different consumers through different roots,
and Hermes's own view is a small slice of the 1783 exposures. `~/Work/.agents/skills` — a real,
independent copy of the engineering skills — is **not configured for Hermes at all**; it serves
a different harness. That is the scatter ADR-0007 exists to end.

## Identity: aliases, copies, divergences

Three distinct phenomena, currently conflated by "duplicate":

1. **Same file, many exposures** (an *alias/exposure* problem, harmless): e.g. `tdd` has 8
   exposures across `skills-archive`, `.codex`, `.agents`, `Work`, `backups`. All the same
   content. 364 contents are in this class.
2. **Same name, different content** (*divergence* — the dangerous one): **35 names**. Examples:
   - `pdf` — 3 variants: `codex-plugins`, Hermes runtime, `manor-ai`.
   - `skill-creator` — 3 variants: `codex`, `bb` store, `manor-ai`.
   - `docx`, `xlsx` — Hermes runtime vs `manor-ai`.
   - `graft`, `handoff`, `tdd`, `teach`, `triage`, `wayfinder`, `wizard`, `retro`,
     `implement`, `prototype`, `resolving-merge-conflicts`, `scaffold-exercises`,
     `setup-pre-commit`, `grill-me` — engineering skills where `Work/.agents` and
     `skills-archive` have drifted apart.
   - `youtube-content`, `google-workspace`, `himalaya`, `codex`, `blocked-page-recovery`,
     `hermes-agent-skill-authoring`, `blogwatcher`, `research-paper-writing` — Hermes builtin
     vs profile-shelf variants.
   - `stillroom-*-workflow` — profile sym link variants vs a project copy.
3. **Genuinely distinct skills sharing one name** — the hard case, indistinguishable from (2)
   without provenance. `pdf`, `skill-creator`, `docx`, `xlsx` look like this: different owners
   shipped different skills under a common name.

**Intentional divergences to preserve** (candidate list, needs Adam's confirmation): the six
contents unique to `hermes_engineer` — `agent-skill-library-management`, `life-os`,
`model-identity-audit`, `multi-agent-profile-workflows`, `pydantic-graph-workflows`,
`typesafe-ai` — plus the `hermes_engineer` variants of shared-shelf names. These are the
"profile customisation must not be overwritten" cases.

## Redundancy evidence (which roots are near-pure copies)

| Root | Contents | Also elsewhere |
| --- | ---: | ---: |
| `~/.hermes/skills` | 117 | 116 |
| `~/.hermes/hermes-agent/skills` (builtin) | 58 | 58 |
| `~/.hermes/profiles/hermes_engineer/skills` | 118 | 112 |
| `~/Work/.agents/skills` | 37 | 37 |
| `~/backups` | 95 | 95 |
| `~/.agents/skills` | 23 | 23 |
| `~/research` | 199 | 197 |
| `~/Documents/skills-archive` | 136 | 93 |

332 of the 585 contents are absent from **both** `~/.hermes/skills` and `skills-archive`. The
unique material is scattered across `hermes-optional` (136), `manor-ai` (60), `codex-plugins`
(53), `.bb` (16), `hermes_engineer` (6), `hermes-backups`/`reports` (18), `life-os` (8),
`honcho` (6). **No existing root is a superset; the store must be assembled from several.**

## Cross-skill references and "dependencies"

- 26 distinct cross-skill reference targets exist in skill bodies (via `skill_view("<id>")`
  or `](../<name>/)` links). **2 dangle**: `diagramming` and `data-science`, 6 references
  each — referenced by other skills but present nowhere on the machine. This confirms the
  outline's "references can fail even when the parent resolves", but at small scale (~8%).
- Frontmatter "dependencies" are **Python/system packages** (torch, vllm, wandb, …), not
  skills. No observed skill declares a skill-level dependency. The proposed Dependency
  Resolver / Dependency Closure concept (ADR-0006, outline) currently has **no data to
  resolve against** — it would be built for a dependency graph that does not exist yet.

## An existing hash store

`~/.bb/runtime/skill-store/` is **content-addressed**: directory names are 64-hex sha256
values, one per skill (`global-skills/` holds a single one). It is a live precedent on this
machine for the manifest scheme (#5) and stable identity (#4), and is a candidate component or
prior art rather than something to ignore. It currently holds 15 of the 585 contents.

## Implications for the open decisions (recommendations only, for the grill)

- **#4 stable ID / alias policy.** Identity must be two-level: a **stable ID** (human-authored,
  namespaced) that denotes the *skill*, and a **content hash** that denotes a *version*. Name
  is an alias, never identity — 35 divergent names prove names collide. Same-name-different-
  content must resolve to distinct IDs via provenance, not silently to one winner.
- **#5 manifest.** With zero git coverage outside `hermes-agent` and 1783 exposures, the only
  auditable option is a **generated manifest checked into the Skill Store**, generated from the
  store and verified in CI, not hand-maintained. The `.bb` store shows content addressing is
  workable here.
- **#6 first retrieval method.** Frontmatter carries only `name` + `description` (plus ad-hoc
  fields). There are no tags, no skill-dependencies, no structured capability taxonomy.
  Deterministic lexical/description matching over the authorised candidate set is therefore the
  only defensible first method; embeddings/reranking must wait for evaluation evidence. Jev's
  shortlist stays small because retrieval, not Jev, does the narrowing.

## Open questions for Adam

1. **Seed for the store:** `skills-archive` (136 contents, 43 unique) is the richest single
   root; `hermes-optional` (147) is git-managed but *upstream-owned by hermes-agent*. Which is
   the seed, and is `hermes-optional` content in scope to move at all?
2. **Project-scoped skills:** `manor-ai` (60 unique) and `stillroom-client-acquisition` rely on
   Hermes's project-trust model. Do they live in the global store, or stay project-local?
3. **Agent-tooling skills:** the engineering/marketing skills (`.codex`, `.agents`, `Work`,
   `skills-archive`, `skills-archive/marketingskills`) are a different consumer. Q10 says
   single source of truth; does that include them, or only Hermes-runtime skills?
4. **The six `hermes_engineer` uniques** and the divergent profile variants — confirm these are
   intentional and must be preserved as distinct skills.
5. **332 orphan contents:** which of these are live skills that must move, and which are
   historical copies safe to archive?
6. **Dangling `diagramming` / `data-science`:** intended skills, or stale references to fix?
7. **`.bb` `skill-store`:** reuse as the store's implementation, or ignore as a sibling system?
8. **Hashing depth:** should identity include supporting files (`references/`, `scripts/`), or
   is `SKILL.md` sufficient? This pass hashed `SKILL.md` only.

## Reconciliation with the outline's prior baseline

- "Sixty names present in more than one source" — **confirmed and exceeded**: 347 names at >1 path.
- "Five intentional profile-vs-builtin divergences" — **directionally confirmed**; 6 uniques in
  `hermes_engineer` found, the specific five not individually reconciled in this pass.
- "Ten shelf entries from a non-git location without an update path" — **far worse**: essentially
  *all* skill content outside `hermes-agent` has no git update path.
- "Twenty-seven unmanaged skills" — **superseded**: the unmanaged set is the whole library minus
  the two upstream repos.
- "Twenty-eight curator-stale skills" — **not measured** this pass (usage evidence not gathered).
- "Cross-skill references that can fail" — **confirmed**, 2 dangling targets.
