# Catalog hiding is context control, not a filesystem security boundary

Withholding brokered skills from a profile's automatic index is context and attention
control, not isolation: a profile with terminal or file access can still read a known
path. The first release therefore enforces authority only at the broker interface —
unauthorised skills cannot be granted or injected.

Genuine filesystem isolation is **out of scope** for this effort. It returns only as a
separate design when a profile's threat model requires it. This is a scope boundary, not
a deferred phase.
