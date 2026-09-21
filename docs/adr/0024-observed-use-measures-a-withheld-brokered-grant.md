# A withheld Brokered Grant is measured by observed use, and a live expansion window must show it

A Shadow Report's precision and recall are measured against **observed `skill_view` use** read from
the profile's Hermes `state.db`, not against the Shadow Report's human review. The review is a
separate signal: it supplies the ground truth for a *disagreement*'s attribution and for the
offline Reviewed Corpus, but it does not move the metrics (`build_shadow_report` → `_summarize`
reads `turn.observed` only). This decision keeps that split, and states its consequence.

Once the Cutover withholds a Brokered Name (ADR-0021), the agent normally follows the **inline
Pack** and does not call `skill_view` on the withheld Skill. A correct Grant is then scored a
false intervention, the Report's precision regresses against the pre-registered Soft Thresholds,
and expansion is blocked (ADR-0022). **That is the intended reading for now:** the seam is judged
by whether the agent actually loaded what it was given, and a Grant nobody loaded is not evidence
that the intervention helped.

The operator consequence is part of the routine, not a workaround:

- A **live** expansion window must contain turns in which the granted Skill is actually loaded
  (the request names it and the agent loads it), together with at least one genuine no-skill turn
  so `correct_no_skill_rate` is measurable. A window of plain inline-Pack turns will correctly
  read as a precision regression and does not admit a batch.
- Otherwise the batch is reviewed on **replay evidence** (ADR-0022 already allows "replayed or
  shadow evidence"), where observed use is staged.
- The Soft Thresholds are **never** re-derived to admit a batch (ADR-0019). A regression is the
  honest signal that the window does not demonstrate use.

A future ticket may record the intervention's own consumption signal — the Adapter observing that
an injected Pack was used — so the inline case becomes observable and the two measurements can be
reconciled without a hand review. Until then, observed use is the one authoritative metric.

_Considered options_: treating the human review as ground truth for the metrics — rejected for now,
it would credit a Grant the agent never loaded and make the Report disagree with its own
"what actually happened" premise; scoping the metric to Skills still visible in the index and
using the review for withheld ones — rejected, it reintroduces a second measurement; recording Pack
consumption now — deferred, it needs Adapter/protocol work and is the ticket that can retire this
ADR's limitation.
