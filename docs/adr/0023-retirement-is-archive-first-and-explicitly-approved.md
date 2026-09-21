# Retirement is archive-first, batched, and explicitly approved; deletion is a separate purgatory act

Stage 9 removes superseded paths only after every Consumer resolves through the Skill Store.
The irreversible migration act is split in two, so "nothing is deleted without explicit
approval" stays literally true:

- **Retirement is reversible.** A retired path is **moved** into a dated, manifest-backed
  retired set, from which `rollback` restores every path byte-for-byte. Retirement is batched,
  proposed by deterministic tooling, and gated on Adam's explicit approval. It is not a deletion.
- **Purge is irreversible and separate.** Only a later, separately-approved act deletes retired
  content, after a retention window, in its own batch. Purge is the *only* irreversible migration
  act, and it is never reached by a retirement approval.

The process is recorded in [`docs/retirement/README.md`](../retirement/README.md): the review
artifact and its evidence, the deterministic proposer, the approval gate, batch and rollback
rules, curator absorption, and the conditions that block a batch.

Three consequences fix the design:

- **The curator already archives, never deletes.** Hermes's curator moves a stale Skill to
  `.archive/` and sets `state: archived`, with whole-tree rollback snapshots, and deletes only
  through an explicit `hermes curator purge`. Stage 9 **absorbs** that model rather than
  duplicating it: the curator keeps sole ownership of its managed Hermes roots, Stage 9 never
  reaches into `.archive/` or `.curator_backups/`, and a retired store path uses the same
  archive-not-delete shape.
- **The store owns retirement; the curator does not.** Retirement spans redundant exposures,
  dead exposures and excluded roots across many Consumers, not the curator's name-keyed managed
  set. The curator's usage evidence is an input, not the proposer.
- **Zero use is evidence, never authority.** Usage telemetry is name-keyed and thin; a candidate
  is proposed by deterministic resolution over the Store Manifest and audit index, and the review
  reads the evidence — the model never proposes a path for removal.

_Considered options_: **deletion-first retirement** — rejected, it is irreversible, destroys the
rollback baseline, and contradicts ADR-0012; **flag-only retirement** (leave every path, mark it
in the manifest) — rejected, it reclaims nothing and leaves resolution ambiguity in place;
**letting the curator own retirement** — rejected, the curator manages only its Hermes roots,
keys usage by Name not ID, and cannot see the store's consumers; **auto-retiring on zero recorded
use** — rejected, telemetry is name-keyed and cannot attribute use to a divergent copy, so
zero-use is a flag for review, not a licence to act; **one irreversible act with approval as its
only brake** — rejected, it makes the first mistake unbounded and unrecoverable, whereas the
archive/purge split makes a bad retirement a revert and confines the irreversible step to a
terminal, separately-reviewed window.
