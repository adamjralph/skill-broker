# Authority is per-turn; the Session Ledger is a lease in no sense

A Grant authorises delivery of one Resolved Skill Version to exactly one agent turn. Every
turn re-derives the profile's Authorised Closure from the Profile Policy, which remains the
only source of authority; the Session Ledger records the content hashes already supplied and
exists only to suppress duplicate intervention. A prior Grant, a Preferred flag, and an
explicit invocation confer no authority on any later turn.

_Considered options_: a session-scoped lease minted by each Grant, so a granted Skill stays
available for the rest of the conversation without a fresh Judgment. Rejected — it makes the
Session Ledger a second authority source beside the Profile Policy, and the one thing it would
buy is already delivered: Hermes replays turn 1's injected user message byte-for-byte on every
later turn (#6), so a granted Skill's content is present for the rest of the conversation by
construction. Per-turn authority is also what makes the zero-unauthorised-grant rate provable
rather than argued: every Grant is a fresh intersection of policy and the current turn.
