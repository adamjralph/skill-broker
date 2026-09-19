# Stage 1 verified audit

**Status:** audit artifact for the Skill Store build (wayfinder [#24 — Produce the Stage 1
verified audit](https://github.com/adamjralph/skill-broker/issues/24)). Findings and proposals
only; every conflict below is Adam's to decide. The audit itself made no filesystem change;
the two stale references it found were removed afterwards on Adam's instruction (see
[Cross-skill references](#cross-skill-references)).

**Companion:** [`index.json`](./index.json) — the machine-readable index, one row per
Skill identity-version. **Reproduce:** `python3 scripts/skill_audit.py` (writes the index);
**verify:** `python3 scripts/skill_audit.py --verify` (re-hashes every source and exposure
against the committed index).

| Re-verification | Result |
| --- | --- |
| Identity-version hashes re-checked | **436 / 436** |
| Exposure paths re-checked (realpath → package hash) | **773 / 773** |

## Scope

In scope, per [ADR-0008](../../docs/adr/0008-skill-store-hosting-and-scope.md): **authored
skills across every consumer** — Hermes runtime and agent tooling alike. Excluded: derived
runtime caches, snapshot archives, and project-scoped skills.

### In-scope roots (27)

| Root | Category | Owner | Provenance |
| --- | --- | --- | --- |
| `.hermes/hermes-agent/skills` | hermes_builtin | `nousresearch` | NousResearch/hermes-agent @ `5eb99eb` |
| `.hermes/hermes-agent/optional-skills` | hermes_optional | `nousresearch` | NousResearch/hermes-agent @ `5eb99eb` |
| `.hermes/skills` | hermes_shared_shelf | `local` / `nousresearch` | mixed (see below) |
| `.hermes/profiles/*/skills` (10 profiles) | hermes_profile | per-profile pseudo-owner | profile writable tier |
| `Documents/skills-archive/skills` | upstream_checkout | `mattpocock` | mattpocock/skills @ `c55ee46` (MIT) |
| `Documents/skills-archive/marketingskills` | upstream_checkout | `coreyhaines31` | coreyhaines31/marketingskills @ `884027c` (MIT; origin `5b2c000`, one local commit) |
| `Documents/skills-archive/pstack` | upstream_plugin | `cursor` | Cursor `pstack`; no `.git` (MIT) |
| `.codex/skills` | agent_tooling | `openai` | openai/skills `.system` (MIT) |
| `Work/.agents/skills` | agent_tooling | `work` | no discoverable upstream |
| `.agents/skills` | agent_tooling | `agents` | symlink farm into the above |
| `Documents/stillroom-wiki/skills` | content | `stillroom` | Hermes `external_dirs` |
| `Documents/life-os` | content | `life-os` | Life OS vault |
| `honcho`, `services/honcho`, `honcho-assessment` | service_repo | `plastic-labs` | plastic-labs/honcho @ `699c993` |
| `/usr/share/omarchy/default/agents/skills` | os_provided | `omarchy` | OS-provided |
| `.claude/skills`, `.pi/agent/skills` | agent_tooling | `claude`, `pi` | symlink farms (all targets resolve in-scope) |

`.hermes/skills` is mixed: Hermes-bundled copies (owner `nousresearch`) plus agent-authored
skills (pseudo-owner `local`). Membership in `.hermes/skills/.bundled_manifest` is the signal.

### Project-scoped — excluded from the store (ADR-0008), recorded at the boundary (62 skills)

| Root | Pseudo-owner | Skills |
| --- | --- | ---: |
| `Projects/manor-ai/packages/core/ai/skills` | `manor-ai` | 50 |
| `Projects/manor-ai/.agents/skills` | `manor-ai` | 10 |
| `Projects/stillroom-client-acquisition/profile-staging` | `stillroom-client-acquisition` | 2 |

These stay project-owned and are wired by the project-setup path
([Define the project-scoped skill setup path](https://github.com/adamjralph/skill-broker/issues/16)).
They must not enter the store or the broker catalog.

### Excluded roots (9), with reason

`~/research` and `~/backups`, `~/.hermes/backups`, `~/.hermes/reports` (snapshot archives);
`~/Documents/skills-archive` outside the three checkouts above (snapshot archive);
`~/.bb/runtime/global-skills`, `~/.bb/runtime/skill-store` (BB derived caches);
`~/.codex/plugins` (plugin cache); `~/Downloads` (ad-hoc, not an authored root).

## Method

- **Walk.** Every in-scope root is walked following symlinks; `.git`, `.github`, `.hub`,
  `.archive`, `.curator_backups`, dependency and cache directories are pruned. Each
  `SKILL.md` yields an **exposure** (a path a consumer can reach) and a **canonical source**
  (its resolved realpath).
- **Hashes** (ADR-0009, research `identity-hash-depth.md`):
  - `package_sha256` — SHA-256 over a canonical manifest of `relative-path \0 file-sha256`
    for the normalized package file set (support files included; bytecode and VCS/dependency
    caches excluded);
  - `skill_md_sha256` — the `SKILL.md` bytes, kept separately as an index field.
- **Identity.** `<owner>.<name>` where `owner` is the upstream publisher or a pseudo-owner;
  `name` is the frontmatter name (falling back to the directory name). Content is grouped by
  `package_sha256`; within a group the canonical source is the highest-priority root (git
  upstream > profile writable > shared shelf > tooling tree > farm). Identical content under
  another root is a **redundant copy** (alias/exposure), not a second identity.
- **Verification.** `--verify` re-hashes all canonical sources and resolves every exposure to
  content matching its identity's package hash.

## Headline numbers

| Measure | Value |
| --- | ---: |
| In-scope exposures | 773 (122 via symlink) |
| Distinct canonical realpaths | 651 |
| **Identity-versions (`package_sha256`)** | **436** |
| Distinct IDs (`<owner>.<name>`) | 436 |
| Redundant copies | 215 |
| Project-scoped skills (excluded) | 62 |
| Names carried by more than one owner (legitimate Name Collision) | 30 |
| Cross-skill references found | 14 across 8 identities |
| Dangling reference targets | 0 (two stale references removed) |
| Local patches detected | 1 |

The library is ~436 real skills wearing 773 paths; 215 paths are copies.

### Inventory by owner

| Owner | Skills | License |
| --- | ---: | --- |
| `nousresearch` (Hermes builtin + optional) | 205 | MIT |
| `hermes_engineer` | 52 | — |
| `coreyhaines31` | 50 | MIT |
| `cursor` | 48 | MIT (attribution unresolved) |
| `mattpocock` | 38 | MIT |
| `work` | 13 | — |
| `life-os` | 8 | — |
| `plastic-labs` | 6 | — (not recorded) |
| `local` (agent-authored, default profile) | 5 | — |
| `openai` | 6 | MIT |
| `agents` | 2 | — |
| `omarchy` | 2 | — |
| `stillroom` | 1 | — |

Upstream-derived content is MIT (347 identities). 89 identities have **no recorded license** —
all pseudo-owner or service-repo content; this is a conflict below.

### Redundancy

Near-pure copy roots (redundant copies of content that is canonical elsewhere):

| Root | Exposures | Redundant copies |
| --- | ---: | ---: |
| `.hermes/skills` | 116 | 108 |
| `.hermes/profiles/hermes_engineer/skills` | 117 | 63 |
| `Work/.agents/skills` | 37 | 24 |
| `honcho` / `services/honcho` | 32 | 12 |

The symlink farms (`.agents/skills`, `.codex/skills`, `.claude/skills`, `.pi/agent/skills`)
resolve into the canonical roots above and add no distinct content.

## Cross-skill references

14 references across 8 identities; none dangle. The two targets that previously resolved nowhere
— **`diagramming`** and **`data-science`**, referenced by `research-paper-writing` — were
confirmed stale and removed from all three `research-paper-writing` variants
(`nousresearch`, `hermes_engineer`, `local`) on Adam's instruction.

## Classification proposal

A **proposal only**, and deliberately profile-independent: ADR-0002 makes Foundation/Brokered
profile-relative, so the Profile Policy remains the source of truth. The heuristic:

- **foundation** — an operational essential (`hermes-agent`, `find-skills`,
  `agent-skill-library-management`, `life-os`, `hermes-agent-skill-authoring`), or a skill
  with an unambiguous Hermes usage count ≥ 5;
- **retirement-candidate** — an unambiguous Hermes-managed record marked `stale` with zero
  recorded use (Stage 9 review; nothing deleted);
- **brokered** — everything else, including skills whose usage is ambiguous because the
  telemetry is name-keyed.

| Proposed | Count |
| --- | ---: |
| foundation | 39 |
| brokered | 371 |
| retirement-candidate | 26 |

The 26 retirement candidates are exactly the zero-use `stale` records in
`docs/research/curatorial-staleness.md` (all bundled Hermes skills), e.g. `airtable`,
`apple-notes`, `notion`, `xurl`. They are not proposed for deletion; they are flagged for the
Stage 9 review.

**Usage caveat.** Hermes telemetry is keyed by skill *name*, not content hash or ID, so it
cannot distinguish divergent same-name copies. The index carries `ambiguous_same_name` on every
usage record; the heuristic abstains when it is set.

## Conflicts surfaced for Adam's decision

### 1. `cursor` owner attribution (`cursor.*`, 48 skills)

`Documents/skills-archive/pstack` has no `.git`. Its `.cursor-plugin/plugin.json` names
**Lauren Tan**; its README names **"poteto"**. Proposed owner `cursor` (repo path) is
provisional. *Decide:* `cursor`, `poteto`, or a `pstack` pseudo-owner.

### 2. Same-name divergences in `Work/.agents/skills` (13 identities)

`Work/.agents/skills` holds engineering skills with **no discoverable upstream** whose names
collide with mattpocock/skills content that differs: `implement`,
`improve-codebase-architecture`, `prototype`, `resolving-merge-conflicts`,
`setup-matt-pocock-skills`, `triage`, `wayfinder`, `wizard`, `implement-spec`, `retro`,
`git-guardrails-claude-code`, `scaffold-exercises`, `setup-pre-commit`. *Decide per skill:*
distinct `work.*` identity, or a **patched version of the mattpocock identity**
(ADR-0009 `local_patch`). The audit currently proposes distinct `work.*` identities.

### 3. Hermes shelf-vs-profile divergences (5 names)

Agent-authored names that exist with **different content** in both the default shelf
(pseudo-owner `local`) and the `hermes_engineer` profile:

`agent-skill-library-management`, `multi-agent-profile-workflows`, `life-os`,
`adam-content-writing`, `linkedin-post-writing`.

`adam-content-writing` is in the broker pilot's brokered set
([Choose the broker pilot's profile, foundation set, and brokered set](https://github.com/adamjralph/skill-broker/issues/12)),
so this one blocks the pilot's identity choice. *Decide:* which is canonical, or whether the
profile copy is a patched version.

### 4. Unreconciled `hermes_engineer` divergences (2)

The other profile divergences are already reconciled as intentional
(`docs/research/divergence-reconciliation.md`). Two are not covered there:
`hermes_engineer.hermes-agent` and `hermes_engineer.handoff`. *Decide:* intentional profile
divergence (preserve) or distinct identity.

### 5. `plastic-labs.honcho-memory` — one identity, two divergent versions

`honcho-assessment/examples/zo` and `honcho-assessment/skills/honcho-memory` share a name and
differ in content. *Decide:* which is canonical; the other is a patched version or a distinct
identity.

### 6. Dangling references — resolved

`diagramming` and `data-science` were referenced by `research-paper-writing` but present
nowhere. Adam ruled them stale; the reference rows were removed from all three
`research-paper-writing` variants (`nousresearch`, `hermes_engineer`, `local`). No references
dangle.

### 7. Service-repo skills in scope?

`plastic-labs/honcho` contributes 6 skills that are authored but not obviously consumed by any
agent skill loader. ADR-0008 says "authored skills across every consumer", which admits them.
*Confirm:* in scope, or excluded like project-scoped.

### 8. Licensing for pseudo-owner content

89 identities have no recorded license (all `work`, `hermes_engineer`, `local`, `life-os`,
`agents`, `omarchy`, `stillroom`, `plastic-labs`). Upstream-derived content is MIT and carries
its notice. *Decide:* the license/notice convention for locally-authored and service-repo
content before vendoring.

### 9. marketingskills local patch

`coreyhaines31.prospecting` carries a local commit ahead of `origin/main`
(`skills/prospecting/references/compliance.md`, ACMA section). The audit records
`local_patch: true` on this identity. *Confirm:* admit as a patched version of the upstream
identity, preserving the edit (ADR-0009;
[Decide how upstream updates and local patches reconcile inside the store](https://github.com/adamjralph/skill-broker/issues/19)).

## Limitations

- **Usage is name-keyed** and cannot attribute use to a specific divergent copy; the index
  flags this per row. Non-Hermes identities have no usage telemetry at all.
- **Hashes are of current disk state.** A skill with an uncommitted edit is hashed as edited
  (this is why `coreyhaines31.prospecting` is captured as a local patch).
- **Excluded archives were not walked.** Content known only as a derived copy or archive
  snapshot is not admitted (ADR-0009) and does not appear in the index; the excluded roots are
  listed above.
- **Symlinked roots are resolved.** `.codex/skills` (37 of 45), `.agents/skills`,
  `.claude/skills`, `.pi/agent/skills`, and the honcho `.claude`/`.agents` sub-farms resolve
  into other in-scope roots; they add exposures, not identities.
- **Package-hash normalization** excludes VCS metadata, dependency and cache directories, and
  `__pycache__`/bytecode. Two packages differing only in those are one version.

## Reproduce

```sh
python3 scripts/skill_audit.py            # regenerate docs/audit/index.json
python3 scripts/skill_audit.py --verify   # re-hash everything against the committed index
```
