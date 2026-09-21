# Stage 9 retirement

How superseded paths leave the machine after every Consumer resolves through the Skill Store
([ADR-0012](../adr/0012-migration-is-additive-copy-then-cutover-and-reversible.md),
[ADR-0023](../adr/0023-retirement-is-archive-first-and-explicitly-approved.md)). Retirement is
the **reversible** withdrawal of a path; deletion is a separate, later, explicitly-approved
**purge**. Nothing is deleted by a retirement.

This page is the process; a retirement **batch** is the reviewable unit. Until every Consumer is
cut over to its Exposure Farm (`docs/HANDOFF.md` records the live set), retirement **cannot
start** — it stays defined and blocked.

## Two tiers

| Tier | Act | Reversible? | Approval |
| --- | --- | --- | --- |
| **Retire** | move a superseded path into a dated retired set + manifest | yes, byte-for-byte | Adam, recorded in the batch artifact |
| **Purge** | delete retired content after the retention window | no | Adam, in a separate purge batch |

Retirement and purge are separate batches. A retirement approval never authorises a purge, and a
purge batch names only content a prior retirement batch already retired.

## The review artifact and its evidence

A batch is committed as `docs/retirement/<batch>.json` (machine-readable) with a companion
`report.md` (the review surface). One row per candidate:

```json
{
  "retirement_version": 1,
  "batch": "redundant-copies-hermes-engineer",
  "kind": "redundant-copy",
  "generated_at": "…",
  "store_manifest_sha256": "…",
  "audit_index_sha256": "…",
  "approved_by": null,
  "approved_at": null,
  "approval_ref": null,
  "candidates": [
    {
      "path": "/home/hermes/.hermes/profiles/hermes_engineer/skills/…",
      "identity": "mattpocock.writing-shape",
      "kind": "redundant-copy",
      "resolves_to": "…/skill-store/mattpocock/writing-shape",
      "package_sha256": "…",
      "usage": {"record": "…", "ambiguous_same_name": false},
      "references": [],
      "blockers": []
    }
  ],
  "retired_to": "…/retired/redundant-copies-hermes-engineer/",
  "rollback": null
}
```

The **evidence** is generated, never hand-written: the Store Manifest digest and audit-index
digest the batch was cut from; each candidate's exposure→identity resolution with its
`package_sha256`; the curator's usage record (with the name-keyed caveat); the cross-skill /
dependency / cron / pin reference check; and, after apply, the retired location and the rollback
record. A batch with no resolvable evidence fails validation rather than proceeding.

## What proposes candidates

Deterministic tooling (`scripts/retire.py propose`, to be built with the first real batch) reads
the verified Store Manifest and the Stage 1 audit index and proposes four kinds:

- **redundant-copy** — an exposure whose content is canonical elsewhere (the audit's 203);
- **dead-exposure** — a path no Consumer's farm or policy resolves any more;
- **excluded-root** — a superseded old location left after cutover;
- **zero-use-stale** — a store identity flagged by the audit's classification, with usage
  evidence attached.

The proposer never consults a model, and never the curator. The curator's usage record is an
input to a candidate's evidence, not a proposer of the batch.

## The approval gate

Adam approves each batch explicitly. `retire apply` refuses a batch whose `approved_by`,
`approved_at` and `approval_ref` are unset, exactly as injection refuses without a recorded
review. The approval names the batch and binds the artifact digest; a batch is not retired by an
agent acting alone.

## Batching and reversibility

- **One batch, one kind or one root.** Small enough to review and to revert.
- **Move, never copy.** Retirement moves the path into
  `<store>/retired/<batch>/<path>` and writes the batch manifest listing every move.
- **Rollback is exact.** `retire rollback <batch>` restores every path byte-for-byte from the
  manifest and records the rollback in the artifact. A retirement is a revert, not a rebuild.
- **Purge only after the retention window**, in its own approved batch, and only content a prior
  retirement batch retired. Retention follows the curator's windows (14d stale / 30d archive) as
  a floor, extended by the review as needed.

## How the curator absorbs it

Hermes's curator keeps sole ownership of its managed roots: staleness, `.archive/` moves and the
`state: archived` flag, `.curator_backups/` snapshots, and `hermes curator purge` for its own
deletion. Stage 9:

- never reads or mutates `.archive/` or `.curator_backups/`;
- treats a curator-archived skill that is also a store identity as **already-withdrawn evidence**,
  and keeps the store's canonical copy;
- uses the curator's usage record only as evidence;
- leaves curator-managed deletion to the curator, under its own explicit purge.

## Conditions that block a batch

A candidate (or the whole batch) is held when any of these is true:

- a Consumer has not yet cut over to its Exposure Farm;
- the profile has an open Incident (fail closed, as at every other gate);
- the Store Manifest or Profile Policy validation fails, or the batch's cited digests no longer
  match the committed artifacts;
- the candidate is unresolved or its hash is unverified against the manifest;
- the candidate has recorded use inside the retention window;
- the candidate is referenced by a dependency, a cross-skill reference, a cron job, or a pinned
  skill;
- retiring it would leave a licence or `NOTICE` obligation unmet.

## Reproduce

```sh
python3 scripts/store_manifest.py verify --store ~/skill-store
python3 scripts/skill_audit.py --verify          # audit index re-check
python3 scripts/retire.py propose --batch <name> # deterministic candidate proposal (Stage 9)
python3 scripts/retire.py apply   --batch <name> # refused without a recorded approval
python3 scripts/retire.py rollback --batch <name>
```
