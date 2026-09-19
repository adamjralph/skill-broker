# Candidate retrieval is lexical, and Jev's v1 judgment is one Choice with a no-skill outcome

Candidates for one turn come from a deterministic, model-free funnel over the profile's
Authorised Closure: an exact ID/Name/Alias match ranks first, then Okapi BM25 over each
candidate's Name, Aliases, description, and Hermes tags — never its body. Jev (TypeSafe
System One) then answers exactly one batched `Choice` over the ranked candidates plus a
reserved `no_skill`, validated into a nullable Primary Skill, confidence, the full
probability distribution, and the echoed candidate set. An explicit invocation makes a Skill
a Candidate, never a Grant.

_Considered options_: embeddings or an LLM pre-rank — deferred until evaluation justifies
them, since they add a model dependency and non-determinism to the narrowing step whose
whole job is to keep Jev's input small; a second Jev primitive (`Noul` or a per-candidate
`Score`) — not adopted for v1; treating an explicit user invocation as a deterministic
auto-grant — rejected, it makes request text a grant path outside the Profile Policy
(ADR-0003, ADR-0004).

## Consequences

No skill-level dependency declarations exist on this machine, so the Dependency Resolver is
implemented declared-only with an empty graph: v1 is strictly single-primary, and ADR-0006's
"multi-skill enters via dependency closure" path stays dormant until a validated skill-level
`dependencies` field exists in the Store Manifest. Cross-skill "see also" references are not
treated as dependencies.
