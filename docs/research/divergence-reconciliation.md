# Same-name divergence reconciliation (wayfinder #4)

**Date:** 2026-09-19  
**Scope:** the `hermes_engineer` profile, its shared shelf, Hermes builtin/optional
skills, and the profile variants called out by `catalogue-topology.md`.

## Finding

The outline's “five intentional profile-versus-builtin divergences” is not a
complete description of the current filesystem. Direct SHA-256 comparison of
`SKILL.md` files finds **five clear profile-versus-builtin customisations**:
`blocked-page-recovery`, `codex`, `hermes-agent-skill-authoring`, `himalaya`, and
`youtube-content`. It also finds two profile-versus-optional divergences:
`blogwatcher` and `research-paper-writing`. `google-workspace` is listed in the
reconnaissance as a profile variant, but the profile and shelf copy are the same
content and no separate builtin copy was found in the measured roots; it must not
be manufactured as a second identity.

The six “unique to `hermes_engineer`” entries are **unique contents**, not all
unique names: three have a different same-name shelf variant, while three are
profile-only in the measured roots. They are still profile-owned material and
should be retained. “Preserve as distinct” means retain separate stable IDs / hash
versions where content differs; identical exposures should remain aliases of one
identity.

## Provenance table

Variant counts are counts of distinct `SKILL.md` hashes observed in the relevant
roots, not counts of filesystem exposures. Paths below are canonical source
locations; repeated paths/symlinks are omitted. Hashes are shortened prefixes of
SHA-256 for audit convenience.

| Name | Variant count | Owner/source of each variant | Why it diverges (if determinable) | Preserve as distinct? |
|---|---:|---|---|---|
| `agent-skill-library-management` | 2 | `hermes_engineer` profile `8a05fff8fe69`; shared shelf `565d95334f6f` | Profile adds audit/dependency and source-management warnings; clearly profile curation. | **Yes** |
| `life-os` | 2 | `hermes_engineer` profile `51eb0e892919`; shared shelf `c2e76c75b2a7` | Profile removes a shelf-specific tracker/reminder rule. Profile customisation is evidenced; rationale beyond that is not recorded. | **Yes** |
| `model-identity-audit` | 1 | `hermes_engineer` profile `mlops/model-identity-audit/SKILL.md` (`hermes_engineer`-owned) | Profile-only skill; no competing same-name content found. | **Yes** (retain as a profile-owned skill, though not a collision) |
| `multi-agent-profile-workflows` | 2 | `hermes_engineer` profile `5f7ee429cba5`; shared shelf `d990ad836bf3` | Profile adds batch-verification/parallel-worker workflow guidance. | **Yes** |
| `pydantic-graph-workflows` | 1 | `hermes_engineer` profile `software-development/pydantic-graph-workflows/SKILL.md` (`hermes_engineer`-owned) | Profile-only skill; no competing same-name content found. | **Yes** (retain as a profile-owned skill, though not a collision) |
| `typesafe-ai` | 1 | `hermes_engineer` profile `typesafe-ai/SKILL.md` (`hermes_engineer`-owned) | Profile-only skill; no competing same-name content found. | **Yes** (retain as a profile-owned skill, though not a collision) |
| `blocked-page-recovery` | 2 | `hermes_engineer` profile `5d6bf1a7f7a7`; Hermes builtin `hermes-agent/skills/web/blocked-page-recovery/SKILL.md` `7f7d321f1773` | Profile adds job-listing/recruitment verification guidance. This is one of the five outline divergences. | **Yes** |
| `blogwatcher` | 2 | `hermes_engineer` profile/shared shelf `b85be5a9b86b`; Hermes optional `hermes-agent/optional-skills/research/blogwatcher/SKILL.md` `5f77f5d2e040` | Profile/shelf omits the optional copy's Hermes-tool operating section; likely profile curation. Optional, not one of the five builtin baseline entries. | **Yes** |
| `codex` | 2 | `hermes_engineer` profile/shared shelf `6f5568a5015c`; Hermes builtin `hermes-agent/skills/autonomous-ai-agents/codex/SKILL.md` `3ed457c2b8d5` | Profile adds Codex instruction-chain and rollout-verification guidance. This is one of the five outline divergences. | **Yes** |
| `google-workspace` | 1 | `hermes_engineer` profile/shared shelf and Hermes builtin `hermes-agent/skills/productivity/google-workspace/SKILL.md`, all `c22194d932d9` | Listed by the reconnaissance as a profile-shelf variant, but all three measured exposures have identical bytes. No divergence to preserve. | **No** (one identity; keep aliases) |
| `himalaya` | 2 | `hermes_engineer` profile/shared shelf `834326eba2d4`; Hermes builtin `hermes-agent/skills/email/himalaya/SKILL.md` `0f6c9bb4c0a5` | Profile changes credential wording and adds Arch/Omarchy installation instructions. This is one of the five outline divergences. | **Yes** |
| `hermes-agent-skill-authoring` | 2 | `hermes_engineer` profile/shared shelf `622f26326eda`; Hermes builtin `hermes-agent/skills/software-development/hermes-agent-skill-authoring/SKILL.md` `3a274d5b42fa` | Profile adds separation-of-deliverables guidance. This is one of the five outline divergences. | **Yes** |
| `research-paper-writing` | 2 | `hermes_engineer` profile/shared shelf `52aec0edc55d`; Hermes optional `hermes-agent/optional-skills/research/research-paper-writing/SKILL.md` `ad6b059c628f` | Profile adds `plan` as a related skill. Optional, not a builtin in the narrow five-count. | **Yes** |
| `youtube-content` | 2 | `hermes_engineer` profile/shared shelf `5a418d8ee866`; Hermes builtin `hermes-agent/skills/media/youtube-content/SKILL.md` `52349e30ac74` | Profile expands from YouTube transcripts to repost/original-source resolution and claim checking. This is one of the five outline divergences. | **Yes** |

## Decisions and limits

1. Preserve the six profile-owned contents and every profile variant marked **Yes**;
   do not overwrite them with builtin/optional content during consolidation.
2. Do not create a distinct `google-workspace` identity from path ownership alone:
   the measured bytes are one content identity.
3. The table intentionally does **not** claim that every one of the catalogue's
   35 divergent names is intentional. The reconnaissance explicitly says the
   35-name set was not individually reconciled, and names such as `pdf`, `docx`,
   `xlsx`, `skill-creator`, and engineering-tool copies have other owners and
   require separate provenance decisions.
4. “Why” is based on byte diffs and path ownership, not author intent. Where no
   explicit rationale exists, this is stated as “profile curation” rather than
   asserted as fact.

## Sources and reproducibility

- `PROJECT-OUTLINE.md:299-310` records the five intentional profile-versus-builtin
  divergences and the rule not to overwrite profile customisations.
- `docs/research/catalogue-topology.md:105-136` defines same-name divergence,
  names the 35-name total, and identifies the six `hermes_engineer` unique contents
  plus profile variants as preservation candidates.
- `docs/research/catalogue-topology.md:208-210` explicitly says the five were not
  individually reconciled in the earlier pass; this report supplies that missing
  reconciliation.
- `docs/research/catalogue-topology.md:18-25` defines the inventory method
  (realpath, content hash, frontmatter name) and warns that only `SKILL.md` was
  hashed.
- Filesystem audit performed in this workspace: enumerate `SKILL.md` below
  `~/.hermes/profiles/hermes_engineer/skills`, `~/.hermes/skills`,
  `~/.hermes/hermes-agent/{skills,optional-skills}`; parse frontmatter `name`; group
  by name and full SHA-256; then inspect unified diffs for the listed profile/source
  pairs. The shortened hashes in the table are the resulting provenance keys.

Supporting files (`references/`, `scripts/`, etc.) were not hashed, so a future
full-package audit could discover additional divergence even where the table's
`SKILL.md` hashes match.
