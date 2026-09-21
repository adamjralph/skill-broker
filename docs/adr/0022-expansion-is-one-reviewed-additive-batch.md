# Expansion is one reviewed, additive batch at a time, and fails closed

The bounded pilot enables injection for one profile and one batch. Expansion grows that exposure
as a **routine**, not a bespoke effort, and the routine is deliberately narrow:

- **One batch at a time.** Each step enables exactly one named batch on **fresh** replayed or
  shadow evidence with **its own** review. A batch is never enabled on the evidence that admitted
  an earlier one, and a profile cannot enable two batches from a single review.
- **Additive.** The new batch must cover every Skill the running batch covers. Enabling it cannot
  withdraw guidance the previous batch was already delivering, so the first batch's behaviour is
  preserved by construction and re-checked by replaying its requests after the expansion.
- **Thresholds held, or re-derived larger.** The pre-registered Soft Thresholds are carried
  forward unchanged. Moving them requires an explicit re-derivation against a **strictly larger**
  reviewed corpus, and the re-registration records the new corpus digest and the measured
  baseline alongside the thresholds (ADR-0019, ADR-0020). Thresholds never move to admit a batch.
- **Rollback rehearsed before enabling.** The running batch is disabled, the profile is observed
  foundation-only, and the batch is restored, all while the good state is still known. A later
  rollback of the new batch is therefore a revert to the previous exposure, not a rebuild.
- **Checked after enabling.** The post-batch window is read through the same data-independent Hard
  Gates as a turn: a foundation-resolution regression or a broker/native hash disagreement fails
  the profile closed and is recorded.
- **Fails closed.** A profile with an **open Incident** cannot expand at all. Recovery from a
  breach is a review recorded after it, not growth; expansion resumes only once no Incident is
  open, the profile is not failed closed, and no Soft-Threshold regression is holding expansion.

The procedure is enforced in code (`scripts/broker/expansion.py`) and recorded per step in an
append-only expansion ledger carrying metadata and hashes only (ADR-0015). The operator drives it
with `scripts/expand.py` for the read-only status and rollback rehearsal, and the reviewed Shadow
Report (`scripts/shadow_report.py review`) remains the only thing that enables a batch.

_Considered options_: replacing the running batch with a larger one without rehearsal — rejected,
it makes the first batch's preservation an assertion rather than a checked invariant; allowing a
second batch from the first batch's review — rejected, it is exactly the unreviewed growth
ADR-0019 forbids; expanding a failed-closed profile as its recovery path — rejected, a breach is
a correctness failure and recovery must be reviewed against the breach; re-deriving thresholds to
reach a batch — rejected, it moves the goalposts (ADR-0019).
