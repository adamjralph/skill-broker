# Prototype — the cache-safe Hermes intervention seam

**THROWAWAY.** This answers wayfinder ticket
[#6](https://github.com/adamjralph/skill-broker/issues/6): *can Skill Broker
intervene without mutating the system prompt or tool schema, and without
breaking prompt caching?*

**Verdict: yes, via the documented `pre_llm_call` plugin hook.** Run `./run.sh`
to reproduce.

## Run it

```sh
./run.sh
```

Uses `~/.hermes/hermes-agent/.venv/bin/python` by default. Override with
`HERMES_PYTHON` / `HERMES_AGENT_SRC`. No model, no network, no cost: an
in-process mock provider captures the raw request bodies Hermes sends.

## What it does

1. Spins up an OpenAI-compatible mock provider that records every raw request.
2. Creates an isolated `HERMES_HOME` and drops in
   [`plugin/skill-broker-prototype/`](plugin/skill-broker-prototype) — a real
   Hermes plugin.
3. Loads it through Hermes's real plugin manager.
4. Runs two turns of a real `AIAgent`; turn 2 uses a **fresh agent with history
   reloaded from the session DB**, modelling a resumed process.
5. Runs the whole thing twice — control (plugin disabled) and treatment (plugin
   enabled) — and compares the wire payloads.

The plugin registers exactly two hooks:

- **`pre_llm_call`** — the intervention. `prepare_turn(request, profile,
  session_context)` (the ADR-0001 external seam, stubbed here as a keyword
  match) returns a Skill Pack; the hook returns `{"context": pack}`. Hermes
  appends that to the *current turn's user message only*.
- **`pre_api_request`** — the evidence handle. Records the system-prompt hash,
  tool count, and correlation ids for each provider request.

It registers **no** tools, **no** capabilities, and **no** system-prompt
section. The broker's only lever is the user-message sidecar.

## Recorded result — 12/12 invariants

```
=== summary ===
  control req0: system_prompt=e30bc275493394d4 chars=4860 tools=4 tools_sha=2a6d257ef5163962 messages=2 users=1 injected=0
  control req1: system_prompt=e30bc275493394d4 chars=4860 tools=4 tools_sha=2a6d257ef5163962 messages=4 users=2 injected=0
  treatment req0: system_prompt=e30bc275493394d4 chars=4860 tools=4 tools_sha=2a6d257ef5163962 messages=2 users=1 injected=1
  treatment req1: system_prompt=e30bc275493394d4 chars=4860 tools=4 tools_sha=2a6d257ef5163962 messages=4 users=2 injected=2
  treatment evidence: [{"turn_id": "sess-proof:t1:8565509f", "api_request_id": "sess-proof:t1:8565509f:api:1", "tool_count": 4, "system_prompt_sha256": "e30bc275493394d4", "system_prompt_chars": 4860}, {"turn_id": "sess-proof:t2:25286bc9", "api_request_id": "sess-proof:t2:25286bc9:api:1", "tool_count": 4, "system_prompt_sha256": "e30bc275493394d4", "system_prompt_chars": 4860}]

  PASS  control captured 2 chat requests (got 2)
  PASS  treatment captured 2 chat requests (got 2)
  PASS  control request carried a non-empty tool schema
  PASS  control: no Skill Pack on the wire
  PASS  treatment: every turn's wire carried a Skill Pack
  PASS  system prompt bytes identical control vs treatment, per turn
  PASS  tool schema identical control vs treatment, per turn
  PASS  treatment: system prompt byte-identical across turns (cache prefix stable)
  PASS  treatment: turn 2 replays turn 1's injected user message byte-for-byte
  PASS  treatment: turn 2's own user message is a distinct, freshly-injected turn
  PASS  treatment: one evidence record per request
  PASS  evidence handle records the same system-prompt hash as the wire

VERDICT: PASS (12 invariants hold)
```

Note the same system-prompt hash (`e30bc275493394d4`) across **all four**
requests — two modes × two turns — and the same tool-schema hash
(`2a6d257ef5163962`). The only difference the enabled plugin makes is the
injected Skill Pack in the user messages.

## Why it is cache-safe by construction

Hermes injects hook context into the **user message**, not the system prompt:

- `agent/turn_context.py::_collect_pre_llm_call_context` collects
  `pre_llm_call` returns and `compose_user_api_content` appends them to the
  current turn's user copy at API-call time only.
- The exact sent bytes are stamped into the message's `api_content` sidecar
  (`agent/turn_context.py::build_turn_context`) and replayed verbatim on later
  turns (`agent/conversation_loop.py`), so turn N+1's cache prefix is
  byte-identical to what turn N sent. `agent/turn_api_request.py` fires
  `pre_api_request` with the built payload before it goes out.
- The system prompt itself is frozen per session, so the cached prefix never
  moves. The tool schema is built once and, with no `register_tool`, unchanged.

Hermes also offers `ctx.register_system_prompt_section()` for *durable*,
cache-safe prompt guidance (frozen once per session, capped at 4,000 chars per
section). That is the natural home for **foundation** guidance; **brokered**
content is per-turn and must ride `pre_llm_call`.

## Caveats this raises (not decided here)

- Per-hook context is capped at **10,000 chars** by default; oversize is spilled
  to disk as a head/tail preview
  (`hooks.output_spill` in `config.yaml`). The broker must budget the Skill Pack
  and define overflow behaviour.
- `pre_llm_call` returns from all plugins are joined with double newlines and
  appended; the broker must define its ordering/priority alongside memory and
  guardrail injectors.
- `pre_llm_call` is **timeout-bounded** (30s default) and fails **open**. A live
  Jev call must fit the budget or fall back to a recorded/stub judgment.
- This is a harness around one seam, not the Adapter. It proves the seam; it does
  not implement retrieval, policy, Jev, or evidence persistence.

## Files

| file | purpose |
|---|---|
| `plugin/skill-broker-prototype/` | the real Hermes plugin (drop-in) |
| `run_proof.py` | one pass: mock provider, isolated home, two turns, JSON report |
| `compare_proof.py` | asserts the invariants across control and treatment |
| `run.sh` | runs both passes and the comparison |
