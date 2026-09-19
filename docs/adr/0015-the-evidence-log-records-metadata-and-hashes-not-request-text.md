# The durable Evidence Log records metadata and hashes, never request text

The durable, append-only Evidence Log stores one Route Decision per turn as structured
metadata plus content hashes: no raw request text, and no Skill Pack or Skill body. Request
identity is recorded as a content hash and length. Full request text for replay lives only in
a separate, reviewed fixture corpus carrying its own privacy and permission constraints.

_Considered options_: a full-fidelity durable log retaining request text and delivered packs
for maximal audit and replay. Rejected as the standing default — retaining potentially
sensitive prompts is the hard-to-reverse direction and a privacy commitment, while the
outline's measurements and per-turn replayability are satisfied by hashes plus the recorded
Judgment. Choosing to retain text later remains possible, but as a deliberate act that should
carry a redaction and expiry rule.
