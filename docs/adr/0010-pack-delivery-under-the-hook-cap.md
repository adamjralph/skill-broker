# The hook cap is a presentation threshold; a Skill Pack is delivered complete

Hermes caps each plugin's `pre_llm_call` context at `hooks.output_spill.max_chars`
(10,000 by default) and, above that, writes the full text to disk and substitutes a
head/tail preview plus the path. The broker treats that as a **presentation threshold, not
a delivery limit**: a Skill Pack is always delivered whole — inline at or below the
Adapter's budget, **by reference** above it. Size never changes which Skills a Pack
contains; the Primary Skill's Dependency Closure arrives entire, and no-intervention stays
reserved for missing or cyclic dependencies. A hard cap was rejected because 37% of 418
distinct `SKILL.md` bodies exceed 10,000 chars, and reducing the closure supplies
instructions the Primary's own dependencies contradict.

The Pack carries `SKILL.md` only; `references/`, `scripts/` and `templates/` remain the
Skill's progressive-disclosure layer, read on demand. The Adapter **reads** the effective
cap at runtime rather than setting it, and derives its inline budget as cap − 500 (default
9,500 chars) over the whole returned context; Profile Policy may lower that budget, never
raise it. A Pack is ordered so the spilled preview is the useful part: minimal framing and
the Primary's opening at the head; identifiers, versions, hashes, exposure paths and the
closure at the tail. With spill disabled the effective cap is unbounded: the Pack is
delivered inline in full and the configuration is recorded in the Route Decision.

_Considered options_: a hard budget that refuses or reduces oversize Packs — rejected (a
third of the library, and incomplete closures); broker-owned tiering, the broker writing
its own deterministic artifact and returning a bounded frame — deferred, not rejected: it
is adopted only if a prototype shows the native spill pointer fails to let the agent
recover the content; the Adapter raising `hooks.output_spill.max_chars` — rejected, it
inflates every plugin's every subsequent turn.
