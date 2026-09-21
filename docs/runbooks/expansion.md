# Measured expansion runbook

How to grow a brokered profile by one batch (ticket #58, ADR-0019/0022). This is the operator
path; the deterministic rehearsal that proves the same routine against the real Skill Store
without touching live state is `tests/hermes_adapter/run_expansion_proof.py` (`VERDICT: PASS`).

Read the whole page before starting. Expansion is **additive and reviewed**: it never withdraws a
running batch, never moves the Soft Thresholds to admit a batch, and **fails closed** while the
profile has an open Incident. The reviewed Shadow Report remains the only thing that enables a
batch; this procedure wraps that review with the checks that make growth safe.

## What a batch needs (the evidence)

A second batch is enabled only on evidence the profile has not already reviewed:

- **A fresh window** of replay or Shadow Mode traffic for the profile, recorded as Route Decisions
  in the Evidence Log. Fresh means a new observation window, not the one that admitted the
  running batch.
- **A reviewed Shadow Report** assembled from that window
  (`python3 scripts/shadow_report.py build --profile <profile> --since <epoch> --until <epoch>`),
  with every checked Hard Gate passing and no quality floor regressed.
- **Its own review** by the operator, recorded by `scripts/expand.py apply --reviewer <name>`.
  The review binds the report's digest; the procedure refuses a digest the profile has already
  reviewed and a review not recorded after the running batch's review. Do **not** run
  `scripts/shadow_report.py review` for an expansion — that enables the batch on its own, and the
  procedure would then correctly refuse the already-reviewed digest.
- **An additive batch**: every Skill the running batch covers must still be covered. Growth adds
  Skills; it never drops one.

## Who reviews it

Adam reviews and approves each batch, exactly as for the pilot (ADR-0019). An agent prepares the
report and runs the rehearsal; the approval is human. A batch is not enabled by an agent acting
alone, and the review is recorded with `reviewer` and `reviewed_at` in the gate state.

## What re-derives the thresholds

Nothing, by default: the pre-registered Soft Thresholds are carried forward unchanged. They move
only by **re-derivation against a strictly larger reviewed corpus**, through
`python3 scripts/evaluate.py register --profile <profile> --re-derive --minimum <n>`, which
refuses the same corpus, refuses a reviewed set that is not strictly larger, and records the new
`corpus_sha256` and the measured `baseline` alongside the thresholds. Thresholds are never re-set
to admit a batch; a re-derivation is a deliberate corpus milestone (ADR-0019, ADR-0020).

## The procedure

1. **Status.** Confirm the profile may expand:

   ```sh
   python3 scripts/expand.py status --profile <profile>
   ```

   It reports `expandable`, any open Incidents, the running batch, and whether the thresholds are
   pre-registered. A non-empty `open_incidents` is a hard stop.

2. **Rehearse the rollback** of the running batch (disable → foundation-only → restore):

   ```sh
   python3 scripts/expand.py rehearse --profile <profile>
   ```

   Expect `"foundation_only": true, "restored": true`. Do not continue if the running exposure
   cannot be restored.

3. **Build a fresh Shadow Report** for a new window (see Phase 6 of
   [`live-pilot.md`](live-pilot.md)), grow the profile's `brokered` list in the store (a reviewed
   store change, ADR-0018), and validate the report:

   ```sh
   python3 scripts/shadow_report.py build --profile <profile> --since <epoch> --until <epoch>
   python3 scripts/expand.py status --profile <profile>
   ```

4. **Apply the procedure** with the report. `apply` records the review through the same
   `apply_review` guards that enable the pilot batch (thresholds pre-registered, every checked
   Hard Gate passed, no quality floor regressed), rehearses the running batch's rollback, enables
   the additive batch, then replays the probe requests and reports the post-batch checks:

   ```sh
   python3 scripts/expand.py apply --report <shadow-report.json> --reviewer adam \
       --probe-request "run the stillroom-research-workflow for this topic"
   ```

   Expect `"post": {"ok": true, ...}` with zero foundation regressions and zero hash
   disagreements, and `"rollback": {"restored": true, ...}`. A breach fails the profile closed.

5. **Roll back if needed.** The rehearsal restores the running batch; to actually withdraw a
   batch, disable it (`python3 scripts/gate.py disable --profile <profile> --reason <why>`) and
   re-enable the previous batch with its own recorded review. A bad expansion is a revert to the
   previous exposure, not a rebuild.

## Fail closed

The procedure refuses to expand when the profile has an **open Incident** (an Incident no later
review closed), is failed closed, has expansion blocked by a Soft-Threshold regression, or has no
running batch to expand from. Recovery from a breach is a review recorded after the breach, not
another batch.

## What is recorded

Each expansion appends one step to the expansion ledger, carrying the batch, its review digest,
the previous batch, the rollback rehearsal result, the before/after thresholds, and the post-batch
foundation/hash report — metadata and hashes only, never request text and never a Skill body
(ADR-0015). The `tests/hermes_adapter/run_expansion_proof.py` rehearsal exercises every step
against the real store and prints `VERDICT: PASS`.
