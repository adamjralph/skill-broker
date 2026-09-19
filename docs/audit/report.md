# Stage 1 verified audit

**Status:** audit artifact for the Skill Store build (wayfinder [#24 — Produce the Stage 1
verified audit](https://github.com/adamjralph/skill-broker/issues/24)), regenerated under the
identity and admission decisions of [Resolve the Stage 1 audit's identity and admission
conflicts](https://github.com/adamjralph/skill-broker/issues/28) (ADR-0016/0017; re-run in
[Re-run the Stage 1 audit under the decided identity and admission policy](https://github.com/adamjralph/skill-broker/issues/29)).
The audit made no filesystem change; the two stale references it found were removed
afterwards on Adam's instruction (see [Cross-skill references](#cross-skill-references)).
No conflict remains open.

**Companion:** [`index.json`](./index.json) — the machine-readable index, one row per
Skill identity-version. **Reproduce:** `python3 scripts/skill_audit.py` (writes the index);
**verify:** `python3 scripts/skill_audit.py --verify` (re-hashes every source and exposure
against the committed index).

| Re-verification | Result |
| --- | --- |
| Identity-version hashes re-checked | **430 / 430** |
| Exposure paths re-checked (realpath → package hash) | **725 / 725** |

## Scope

In scope, per [ADR-0008](../../docs/adr/0008-skill-store-hosting-and-scope.md) as amended by
[ADR-0017](../../docs/adr/0017-service-repo-skills-are-excluded-and-licence-is-recorded-per-identity.md):
**authored skills across every consumer** — Hermes runtime and agent tooling alike.
Excluded: derived runtime caches, snapshot archives, project-scoped skills, and
service-repo-owned skills.

### In-scope roots (24)

| Root | Category | Owner | Provenance |
| --- | --- | --- | --- |
| `.hermes/hermes-agent/skills` | hermes_builtin | `nousresearch` | NousResearch/hermes-agent @ `5eb99eb` |
| `.hermes/hermes-agent/optional-skills` | hermes_optional | `nousresearch` | NousResearch/hermes-agent @ `5eb99eb` |
| `.hermes/skills` | hermes_shared_shelf | `local` / `nousresearch` | mixed (see below) |
| `.hermes/profiles/*/skills` (10 profiles) | hermes_profile | per-profile pseudo-owner | profile writable tier |
| `Documents/skills-archive/skills` | upstream_checkout | `mattpocock` | mattpocock/skills @ `c55ee46` (MIT) |
| `Documents/skills-archive/marketingskills` | upstream_checkout | `coreyhaines31` | coreyhaines31/marketingskills @ `884027c` (MIT; origin `5b2c000`, one local commit) |
| `Documents/skills-archive/pstack` | upstream_plugin | `poteto` | pstack author Lauren Tan (`poteto`); hosted in the `cursor/plugins` monorepo (MIT) |
| `.codex/skills` | agent_tooling | `openai` | openai/skills `.system` (MIT) |
| `Work/.agents/skills` | agent_tooling | `mattpocock` (declared Fork) | Fork of mattpocock/skills @ `c55ee46`; same-name content is a Local Patch (ADR-0016) |
| `.agents/skills` | agent_tooling | `agents` | symlink farm into the above |
| `Documents/stillroom-wiki/skills` | content | `stillroom` | Hermes `external_dirs` |
| `Documents/life-os` | content | `life-os` | Life OS vault |
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

### Excluded roots (12), with reason

`~/research` and `~/backups`, `~/.hermes/backups`, `~/.hermes/reports` (snapshot archives);
`~/Documents/skills-archive` outside the three checkouts above (snapshot archive);
`~/.bb/runtime/global-skills`, `~/.bb/runtime/skill-store` (BB derived caches);
`~/.codex/plugins` (plugin cache); `~/Downloads` (ad-hoc, not an authored root); and the
three honcho service-repo roots (`~/honcho`, `~/services/honcho`, `~/honcho-assessment`) —
service-repo-owned and AGPL-3.0, not Adam-authored (ADR-0017).

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
| In-scope exposures | 725 (92 via symlink) |
| Distinct canonical realpaths | 633 |
| **Identity-versions (`package_sha256`)** | **430** |
| Distinct IDs (`<owner>.<name>`) | 415 |
| Redundant copies | 203 |
| Project-scoped skills (excluded) | 62 |
| Names carried by more than one owner (legitimate Name Collision) | 15 |
| Cross-skill references found | 14 across 8 identities |
| Dangling reference targets | 0 (two stale references removed) |
| Local patches detected | 14 (13 mattpocock Fork patches + marketingskills `prospecting`) |
| Stale profile copies (superseded versions) | 2 |

The library is ~415 distinct identities wearing 725 exposures; 203 paths are copies, and
13 of those are the declared mattpocock Fork's Local Patches.

### Inventory by owner

| Owner | Skills | License |
| --- | ---: | --- |
| `nousresearch` (Hermes builtin + optional) | 205 | MIT |
| `coreyhaines31` | 50 | MIT |
| `hermes_engineer` | 50 | `LicenseRef-Proprietary` |
| `poteto` (pstack) | 48 | MIT |
| `mattpocock` (+13 Fork patches) | 38 | MIT |
| `life-os` | 8 | `LicenseRef-Proprietary` |
| `openai` | 6 | MIT |
| `local` (agent-authored, shared shelf) | 5 | `LicenseRef-Proprietary` |
| `agents` | 2 | unresolved (resolve at vendoring) |
| `omarchy` | 2 | unresolved (resolve at vendoring) |
| `stillroom` | 1 | `LicenseRef-Proprietary` |

Upstream-derived content is MIT (360 identity-versions, including the 13 Fork patches, which
keep their upstream licence). Adam-authored content records `LicenseRef-Proprietary` (66);
four identities (`agents`, `omarchy`) have no recorded licence and are resolved per source
when vendored (ADR-0017). The AGPL-3.0 honcho skills are excluded.

### Redundancy

Near-pure copy roots (redundant copies of content that is canonical elsewhere):

| Root | Exposures | Redundant copies |
| --- | ---: | ---: |
| `.hermes/skills` | 116 | 107 |
| `.hermes/profiles/hermes_engineer/skills` | 117 | 63 |
| `Work/.agents/skills` | 37 | 24 |

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
| brokered | 365 |
| retirement-candidate | 26 |

The 26 retirement candidates are exactly the zero-use `stale` records in
`docs/research/curatorial-staleness.md` (all bundled Hermes skills), e.g. `airtable`,
`apple-notes`, `notion`, `xurl`. They are not proposed for deletion; they are flagged for the
Stage 9 review.

**Usage caveat.** Hermes telemetry is keyed by skill *name*, not content hash or ID, so it
cannot distinguish divergent same-name copies. The index carries `ambiguous_same_name` on every
usage record; the heuristic abstains when it is set.

## Decisions applied (wayfinder #28)

The conflicts this audit surfaced were resolved by Adam on 2026-09-20 and recorded in
[ADR-0016](../adr/0016-divergence-resolves-by-declared-forks-and-consumers-never-patch-shared-identities.md)
and
[ADR-0017](../adr/0017-service-repo-skills-are-excluded-and-licence-is-recorded-per-identity.md).
None remains open: `index.json` carries a `policy` block restating them, and every conflict row
carries `requires_decision: false` with its `decision`.

1. **pstack owner — `poteto`.** `cursor/plugins` is a multi-author distribution monorepo;
   pstack's author is Lauren Tan (`poteto`). The 48 identities carry `poteto.*`, not `cursor.*`.
2. **`Work/.agents/skills` — declared Fork of `mattpocock/skills`.** The 13 flagged names are
   mattpocock content with small local edits; each is a Local Patch Version of its
   `mattpocock.*` identity, with the Work path as `patch_source`, not a distinct `work.*`
   identity. No `work.*` IDs are minted.
3. **Profile deviations — distinct identities.** A Hermes profile is a Consumer, and a shared
   patch would leak a profile's customisation into every other profile resolving that identity.
   `hermes_engineer.hermes-agent` and `hermes_engineer.handoff`, together with the seven
   already-reconciled variants, stay distinct.
4. **Five shelf-vs-profile names.** The shared shelf (`local`) is canonical. The
   deliberately-maintained profile variants — `agent-skill-library-management`,
   `multi-agent-profile-workflows`, `life-os` — are distinct `hermes_engineer.*` identities.
   The stale `hermes_engineer` copies of `adam-content-writing` and `linkedin-post-writing` are
   recorded as superseded Versions of `local.*`; the pilot's brokered ID is
   `local.adam-content-writing`.
5. **Service-repo skills — excluded.** The six `plastic-labs/honcho` skills are upstream
   content consumed only inside honcho checkouts, and the repository is AGPL-3.0. They stay
   repo-owned; `honcho-memory`'s two Versions are therefore moot.
6. **Licence convention.** Authored content records `LicenseRef-Proprietary`; vendored upstream
   records its SPDX id plus notice; `agents` and `omarchy` are resolved per source at vendoring;
   the store carries a top-level `LICENSE` and a `NOTICE`.
7. **Already settled, no decision.** `coreyhaines31.prospecting` is admitted as a patched
   Version preserving its ACMA edit; the two dangling references (`diagramming`, `data-science`)
   were removed as stale.

Effect: distinct identities 436 → **415**; identity-versions 436 → **430**; owner
`cursor` → `poteto`. The corrected inventory is consumed by
[Stand up the Skill Store repository](https://github.com/adamjralph/skill-broker/issues/25).

## Limitations

- **Usage is name-keyed** and cannot attribute use to a specific divergent copy; the index
  flags this per row. Non-Hermes identities have no usage telemetry at all.
- **Hashes are of current disk state.** A skill with an uncommitted edit is hashed as edited
  (this is why `coreyhaines31.prospecting` is captured as a local patch).
- **Excluded archives were not walked.** Content known only as a derived copy or archive
  snapshot is not admitted (ADR-0009) and does not appear in the index; the excluded roots are
  listed above.
- **Symlinked roots are resolved.** `.codex/skills` (45 exposures), `.agents/skills`,
  `.claude/skills`, `.pi/agent/skills` resolve into other in-scope roots; they add exposures,
  not identities.
- **Declared Fork patches.** The 13 `mattpocock.*` Local Patches have their `canonical_source`
  in `Work/.agents/skills` and their provenance pinned to the mattpocock upstream commit
  (ADR-0016); two stale `hermes_engineer` copies are recorded as superseded versions of the
  `local.*` identity.
- **Package-hash normalization** excludes VCS metadata, dependency and cache directories, and
  `__pycache__`/bytecode. Two packages differing only in those are one version.

## Reproduce

```sh
python3 scripts/skill_audit.py            # regenerate docs/audit/index.json
python3 scripts/skill_audit.py --verify   # re-hash everything against the committed index
```
