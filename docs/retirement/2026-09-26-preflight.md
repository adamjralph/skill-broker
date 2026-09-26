# Stage 9 preflight — 2026-09-26

## Current result — approved one-time exception

Adam explicitly approved a one-time, reversible, backup-first profile-overlap cleanup without ADR-0023's 30-day wait, ten-consumer evidence inventory or individual digest approvals. This is **not** an amendment to ADR-0023 or a purge. The full backup at `/home/hermes/Documents/skill-backups/2026-09-26-profile-skills-before-stage9/profile-skills.tar.gz` was checked against **1,295 files and 25 symlinks** from all 11 profile skill trees; `manifest.json` records its SHA-256. The per-profile byte-inventory journals and original overlapping paths now live under the sibling `retired-profile-paths/` tree; 123 paths were moved across ten cut-over profiles. `default-2` and non-overlapping profile skills were left live. Each moved path was verified against its original byte inventory and can be moved back; no purge, store removal, shared-shelf change or curator-archive change occurred.

The actual Hermes name resolver passed **127 post-move checks, with zero missing/ambiguous profile names**. Direct `skill_view` loads of `github`, `life-os` and nested `typesafe-ai-patterns` resolve from the `hermes_engineer` farm. Store manifest (431 identities), all ten policies/farms and the full **486-test** suite pass. The regenerated post-move Stage 1 audit verifies **437/437 identity versions and 613/613 exposures**, but its conflict classifier now requires 19 decisions because source attribution changed as profile copies left; that does **not** invalidate the live skill-resolution result. Do not claim the strict `retire.py` gate passed or use this one-time exception for future batches. Scoped local commits were authorized, not pushes; check Git state before asserting they landed.

## Earlier preflight (historical)

Status: **promotion completed for three unique skills; live retirement blocked**. This is a review artifact, not an approved retirement batch. No profile path was moved, removed, or purged. Adam approved proceeding with a bounded, reversible cleanup; the Stage 9 procedure's own validation gate still applies.

## Verified actions

- Copied `retained-python-runtime`, `status-reconciliation`, and `typesafe-ai-patterns` from `~/.hermes/profiles/hermes_engineer/skills/` into distinct `~/skill-store/hermes_engineer/<name>/` identities. The sources were not touched; copy package hashes matched: `d0aa144e4df91dc207d0d0c0c2b4e66fb797025da563b5a1fcce43e42fdb49b2`, `d317d1799c05ee4a0d63708b3492455b7cbf5736563984263e40240ca5b1c5e1`, `3275dfa4b34ec6a0b1cf42d48074f7dd3a9fdab774092a669f2ff7aa0daad62f` respectively.
- Updated `~/skill-store/store-meta.json` and `store-manifest.json`. `python3 scripts/store_manifest.py verify --store ~/skill-store` returned `ok: true` (418 identities); `python3 scripts/profile_policy.py validate --store ~/skill-store` returned `ok: true` (10 policies). **Committed locally** as store commit `2b35fef774ffa03d190d620c71cba802dddc2411` after Adam's specific approval; not pushed. The commit contains exactly the three `SKILL.md` files and two store metadata/manifest files. No profile policy/farm was changed to expose them through the store.
- Compared each `hermes_engineer` profile skill against the actual target of its farm symlink, not against an arbitrary same-name store identity. Nineteen diverge. Full text diff is in the disposable scratch file `~/.hermes/profiles/hermes_engineer/cache/scratch/skill-divergence-review.diff` (not the authority for continuation).

## Divergent names — choice before retirement

- **Prefer profile changes, but record them as reviewed local patches rather than silently overwriting vendor provenance:** `claude-code` (removes blanket permission-bypass default), `computer-use` (new wrapper action semantics), `email-inbox-triage` (sent-mail voice calibration), `handoff` (new sequencing pitfalls), `github-auth` (account-scoped GitHub config), `notion` (corrected multiline example), `test-driven-development` (intermediate-state tests), `requesting-code-review` (one-shot flow), `findmy`, `opencode`, `github`, `pdf`, `inspecting-hermes-desktop-dom`, `node-inspect-debugger`, `python-debugpy` (scratch-path corrections). This is a recommendation from the inspected diffs, **not an applied promotion**.
- **Requires a deliberate merge:** `agent-skill-library-management`: the profile adds accurate collision/depth warnings but contradicts the store's warning that `skills.disabled` also prevents on-demand loading. Keep the collision guidance without adopting the incorrect disable recommendation.
- **Keep the newer store material while merging any unique profile facts:** `adam-content-writing` (store has the copy/hook-bank workflow; profile has one newer website-heading preference in `references/voice-profile.md`); `linkedin-post-writing` (store has version 0.3.0 and `references/attention-to-value.md`, both absent from profile 0.2.0).
- **Nested child, not a parent replacement:** `typesafe-ai` differs only because `typesafe-ai-patterns` lives beneath it in the profile. The child has now been copied as its own store identity; do not retire the parent directory until the child is independently exposed and verified.

## Retirement gate fails today

`python3 scripts/skill_audit.py --verify` returned exit 1: **57 of 430 canonical identity hashes** and **95 of 725 exposure hashes** disagree with the committed Stage 1 audit index. The original `docs/audit/index.json` was restored unchanged after an accidental unqualified audit invocation regenerated it; do not run `python3 scripts/skill_audit.py --help` (it does not parse help and overwrites the index). Much of the drift reflects source updates since September 19. The retirement procedure requires a verified audit index, so its digest cannot safely authorise a batch now. `scripts/retire.py` does not yet exist. No proposed batch has a review artifact/digest or recorded approval.

## Smallest next step

1. Reconcile/rebaseline the Stage 1 audit against current roots, ownership and provenance **without treating its regenerated output as automatically authoritative**. Re-run `scripts/skill_audit.py --verify` and require success; reconcile the 57/95 drift and any changed classifications first.
2. Resolve the 19 profile/store differences above; update store metadata/patch provenance, manifest, affected policies and farms in a checked sequence. Keep the three promoted originals live until their store exposures are verified by bare-name load.
3. Build and test the deterministic retirement proposer/apply/rollback described in `docs/retirement/README.md`. Select one exact, unchanged, unused profile skill, check cross-references, incidents, hashes and licence, rehearse on a copy, then create its digest-bound approval artifact. Only then retire the approved exact path and verify bare-name resolution plus rollback. Do not purge.

Do not reset, clean, stage, commit or include the other pre-existing edits in either repository. The three-skill promotion alone was committed locally as `2b35fef`; a future retirement artifact requires its own exact review and approval. No push was authorised.
