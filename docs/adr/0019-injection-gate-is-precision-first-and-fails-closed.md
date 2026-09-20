# Injection is gated precision-first behind zero-tolerance hard gates, and fails closed

The gate that ends shadow mode is a **procedure**, not a number: the Stage 5 replay corpus and
the measured baseline set the soft thresholds at the Stage 5/6 boundary, **pre-registered
before** the Shadow Report is reviewed so the goalposts cannot move. It optimises for
**precision** — a wrong Intervention spends the turn's context budget and can steer the agent
wrongly, while the no-intervention baseline is already functional. Recall is measured and gates
expansion, never injection.

A small set of **hard gates** never moves with data and is checked on every turn: unauthorised
Grants (rate 0), foundation-resolution regressions (0), broker/native exact-content-hash
disagreement (100% agreement), unknown/unauthorised Jev output or a validation failure, an
incomplete or cyclic Dependency Closure, and any mutation of the system prompt or tool schema.
A breach fails the profile **closed**: automatic revert to foundation-only with injection off,
recorded as an incident, re-enabled only after review. Soft-threshold regression holds
expansion but leaves running injection in place.

Injection is best-effort and never blocks the turn: the Adapter imposes a budget well under
`pre_llm_call`'s 30 s timeout and falls back to a recorded Judgment, else No-Skill, on expiry;
latency and Jev cost are soft metrics, not injection gates. Injection is off by default;
enabling it for a profile requires a reviewed Shadow Report and explicit approval **per batch**.
Expansion (Stage 8) is one batch at a time, always on fresh replayed/shadow evidence with a
fresh review; thresholds move only by re-derivation against a larger corpus, never to admit a
batch.

_Considered options_: recall-first or balanced gating — rejected, it trades an invisible
degradation for a visible one; a one-time global clearance — rejected, it leaves every later
profile or skill expansion un-reviewed; blocking the turn on Jev — rejected, it makes a routing
optimisation a latency tax and contradicts the hook's fail-open contract; hold-and-alert on a
hard-gate breach — rejected, an unauthorised Grant is a correctness failure with no acceptable
window; moving thresholds to admit a batch — rejected, it makes the Stage 5 baseline a moving
goalpost.
