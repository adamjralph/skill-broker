# Jev judges relevance; only deterministic code grants

The judgment model returns relevance and confidence over a bounded, authorised candidate
set, and grants nothing. Deterministic code builds candidates, enforces the profile
policy, rejects unknown or unauthorised identifiers, applies thresholds and budgets,
resolves dependencies, and records the final grant.

_Considered options_: letting the model select and inject skills directly. Rejected
because model output cannot be a permission.
