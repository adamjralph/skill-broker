# One external seam, two internal seams

`prepare_turn(request, profile, session_context) -> InterventionResult` is the only
external interface: callers need not understand catalog layout, retrieval, Jev prompts,
dependencies, thresholds, or content assembly. Inside it, only the **Judgment Source**
(live Jev / recorded replay / stub) and the **Hermes Adapter** are replaceable seams,
because those are the two places with demonstrated alternative implementations; every
other module stays private.

_Considered options_: exposing Catalog, Policy, Retrieval, and Dependency Resolver as
seams. Rejected as speculative adapters that would freeze internal shape before a second
implementation exists.
