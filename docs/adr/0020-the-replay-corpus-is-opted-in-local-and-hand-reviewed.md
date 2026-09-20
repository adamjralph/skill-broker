# The replay corpus is per-source opt-in, machine-local, and hand-reviewed

The Stage 5 replay corpus is assembled by **per-source opt-in**, not by sweeping the machine: a
gated profile's own sessions and the global agent-authored channels (`cli`, `cron`, `subagent`,
`tool`) are in; third-party chat (`telegram`, `desktop`) is held back unless Adam consents per
source. Raw request text is the one thing replay needs in full (ADR-0015), so it lives
**machine-local and uncommitted**, redacted of secrets and identifiers at extraction, deletion
on request, and a retention review at the Stage 5/6 boundary. The repository keeps only the
harness, reviewed labels, and SHA-bound recordings — never request text.

A **Case** is one request turn with its provenance and the profile's Authorised Closure at
extraction. A **Reviewed Case** — its ground-truth outcome (a Primary Skill or a No-Skill
Outcome) corrected by hand — is the only thing that counts for precision/recall and threshold
setting. The agent pre-labels; Adam reviews. This is **annotation for evaluation, not
training**: nothing learns from the corpus in v1 and the labels carry no authority (ADR-0004).
Engineering Jev's prompt from these labels is a separate decision that must leave the held-out
split untouched.

Each gated profile needs its own Reviewed Cases; a shared profile-agnostic set serves only
retrieval development, and a profile too thin for the Stage 5 minimum stays in shadow rather
than borrowing one. Reviewed Cases split into a threshold-setting set and a disjoint **held-out**
set, so the gate is reported on data it was not tuned on.

_Considered options_: sweeping every session — rejected, it retains correspondents' text without
consent; committing the corpus to the broker repo — rejected, main stays docs/spec/tooling and
the text is the sensitive artifact; weak labels from observed skill use — rejected, they bake the
status quo into the gate; one pooled corpus for every profile — rejected, it reports a profile's
gate on other profiles' traffic; one set for both tuning and reporting — rejected, it tunes the
gate to its own test.
