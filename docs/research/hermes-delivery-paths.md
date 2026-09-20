# Hermes delivery paths and fallback behaviour

**Scope.** This is source inspection of the read-only Hermes installation at
`/home/hermes/.hermes/hermes-agent/`. No Hermes configuration or runtime code was
changed, and no live Hermes turn was run. The tests cited below are primary-source
executable evidence, but were not treated as a live observation.

## Supported-path matrix

Legend: **supported** means the source establishes the behaviour for the normal
case; **unsupported** means the delivery channel drops the broker context; and
**unknown** means the source does not establish the required end-to-end property.
The three columns are deliberately independent.

| Request path | (a) `pre_llm_call` fires / Intervention can be injected | (b) injected context reaches API request payload | (c) a by-reference Pack can be recovered downstream |
|---|---|---|---|
| Ordinary text user message, normal chat-completions/compatible route | **supported** (source) | **supported** (source + executable test) | **supported** (live observation, [#21](https://github.com/adamjralph/skill-broker/issues/21): on an ordinary text turn the agent's first action was reading the spilled path, and it returned a mid-Pack canary absent from the preview; one model, one turn) |
| Multimodal user content (`content` is a list of text/image/attachment parts) | **supported** (source) | **unsupported** for the `pre_llm_call` injection channel (source) | **unsupported** for a Pack delivered through that channel: no injected pointer reaches the request (source consequence; no live observation) |
| MoA (ordinary text) | **supported** (source) | **supported** for the assembled MoA request (source; no live observation) | **unknown**: the pointer can be present in the MoA request, but recovery by advisors/aggregator is not established by Hermes source (policy/runtime integration question) |
| `codex_app_server` (text) | **supported** (source: prologue still collects the hook) | **unsupported** for this seam (source + executable regression test) | **unsupported** through this seam: the pointer is not passed to `turn/start` (source) |
| Context-engine `select_context()` returns `None` / does not override | **supported** (source) | **supported** (source: request is unchanged) | **supported** for ordinary text by the #21 live observation; a no-op engine preserves the request that carries the pointer |
| Context-engine `select_context()` returns a replacement request | **supported** (source) | **unknown**: an engine may replace the request and can omit the injected user message (source) | **unknown**: depends on the replacement and on downstream pointer recovery |
| Context-engine transformation/compaction that rewrites history before the request | **supported** for hook invocation (source) | **unknown** for preservation of the current Pack: the cited seam does not prove every engine preserves it (source) | **unknown** |

“Supported” in this table is not a claim that a configured third-party context
engine, model, provider, or filesystem policy will accept every payload. It means
only that the Hermes host path preserves the relevant injection channel.

## Evidence

### Hook invocation and normal text injection

* `agent/turn_context.py:663-716` defines `_collect_pre_llm_call_context()`. It
  calls `invoke_hook("pre_llm_call", ...)`, accepts either a result dictionary's
  `context` or a non-empty string, and joins the results. The docstring says the
  result is injected into the user message. Hook failures are caught and return
  an empty string.
* `hermes_cli/plugins_dispatch.py:172-179` documents the callback contract:
  “`pre_llm_call` may return `{"context": "..."}` (or a str) to inject.”
* `hermes_cli/plugins.py:1681-1692` shows the public `invoke_hook()` delegates to
  the plugin delivery manager (lazy discovery included).
* `agent/turn_context.py:1041-1090` builds API messages and, for the current user
  message, either reuses the stamped `api_content` or calls
  `compose_user_api_content(...)` to add `plugin_user_context` at API-build time.
  `agent/turn_context.py:81-94` is decisive for multimodal input: `compose_user_api_content`
  immediately returns `None` unless `content` is a `str`.
* `agent/turn_context.py:980-1004` shows the order: the hook is collected for the
  turn, but the sidecar is deliberately not stamped when `moa_active` is true or
  `api_mode == "codex_app_server"`.

The ordinary-text wire invariant is executable primary evidence in
`tests/agent/test_api_content_sidecar.py:492-523`: the test expects both requests
in a tool loop to contain `"hello please\\n\\nPLUGIN-CTX"`, and asserts
`api_content` never reaches the provider. The same file's
`tests/agent/test_api_content_sidecar.py:272-284` explicitly tests that
`codex_app_server` does not stamp the sidecar because it bypasses the API-message
build; `:572-584` makes the analogous MoA observation.

### MoA

`agent/turn_request_assembly.py:204-227` first builds the API messages (including
normal text hook context) and then, when `moa_config` exists, calls
`_append_moa_context(...)`; `:221-227` prepares the resulting request through the
MoA client. Thus the source supports reachability of the hook context in the
assembled MoA request, while also explaining why the durable sidecar is not used:
MoA adds per-call context after composition. The test at
`tests/agent/test_api_content_sidecar.py:572-584` says a stamped sidecar would
persist bytes that do not match the MoA wire.

This is source evidence, not a live advisor/aggregator observation. Hermes does
not establish that a local spill path is readable by every MoA participant or
that any participant will invoke a tool to read it.

### `codex_app_server`

`agent/codex_runtime.py:470-500` hands the turn to the app-server with
`agent._codex_session.run_turn(user_input=user_message)`; it does not pass
`plugin_user_context` or the API-message list. The session adapter's
`agent/transports/codex_app_server_session.py:101-119` says the protocol input is
text-only and converts rich parts to text/image markers. At `:326-351`,
`run_turn()` sends exactly:

```python
{"threadId": self._thread_id,
 "input": [{"type": "text", "text": result.submitted_user_text}]}
```

The `codex_app_server` path therefore has a real Hermes hook invocation upstream,
but no source-supported route from that result to the app-server payload. The
regression test's wording at `tests/agent/test_api_content_sidecar.py:272-276`
confirms this is intentional, not an accidental missing assertion.

### Context engines

`agent/context_engine.py:120-140` defines `select_context()` as an optional
per-request replacement. Its contract says the returned list is request-only and
that the host runs the hook before sanitization; `None` leaves the request
unchanged. `agent/conversation_loop.py:1180-1221` implements this fail-open:
exceptions, invalid returns, and `None` retain the original `api_messages`, but a
non-empty list of dictionaries replaces them. Therefore a no-op engine preserves
an already injected Pack, while a transforming engine may remove or rewrite it;
Hermes source alone cannot promote the latter to supported.

### Reference/spill behaviour

`tools/hook_output_spill.py:1-10` states that oversized hook text is written under
`<HERMES_HOME>/hook_outputs/<session>` and replaced by a preview plus path.
`tools/hook_output_spill.py:55-101` implements that behaviour and explicitly
returns a pointer only after attempting the write; on write failure it returns a
preview saying the spill is unavailable. This proves pointer production and the
failure mode, not downstream content recovery — which #21 later supplied (below).

The broker design makes the same distinction: `docs/adr/0010-pack-delivery-under-the-hook-cap.md:1-15`
defines spill as a presentation threshold and requires a Pack to be delivered
complete, while `:17-20` defers broker-owned tiering until a prototype shows the
native spill pointer fails.

#21 supplied the missing end-to-end observation for the ordinary-text path. In a live turn
(`prototype/hermes-intervention-seam` branch, `run_spill_proof.py --mode live`) an 11,978-char
Pack spilled above the default 10,000-char cap; the wire carried only the 1,261-char preview
plus path; the agent's **first action** was `read_file` on that path, and it returned a canary
buried mid-Pack and absent from the preview. Column (c) for ordinary text therefore moves from
unknown to supported, and native spill stands (broker-owned tiering is not adopted). Recovery
of content is not instructional authority: the agent read the buried instruction and refused it
as hook-side text with no authority to redirect the reply. The MoA, context-engine-replacement,
and `codex_app_server` rows are unaffected.

## Recommended fallback options

Ranked from strongest delivery guarantee to least disruptive:

1. **Refuse brokered intervention on `codex_app_server` and multimodal turns, and
   report the reason; retain native Hermes skill exposure.** Technical basis:
   the chosen seam does not reach either payload. **Policy choice — requires
   Adam's decision:** refusal is safer than silently losing a Pack, but sacrifices
   the intervention for those turns.
2. **Degrade to native-index exposure on unsupported/unknown paths.** Do not emit
   a Pack or pointer that Hermes cannot prove reaches the model; expose only the
   profile's Foundation Skills through Hermes's native discovery. **Policy choice
   — requires Adam's decision:** this preserves discoverability but gives up
   per-request selection and may expose more baseline material than the broker.
3. **Allow inline-only delivery on paths where the text injection channel is
   supported; refuse oversize/by-reference delivery until #21 proves recovery.**
   This is appropriate for ordinary text, MoA, and a no-op context engine. Cost:
   oversized Packs cannot be delivered complete under the native cap. **Policy
   choice — requires Adam's decision:** it conflicts with ADR-0010's complete-Pack
   objective, but avoids an unverified pointer contract.
4. **Add a broker-owned, path-specific reference/retrieval mechanism** (for
   example a tool-visible artifact or a Codex-compatible input path), then enable
   by-reference delivery only after an end-to-end canary. Cost: implementation,
   cleanup/security work, and separate integration for MoA and Codex; it is not
   available from the current `pre_llm_call` seam. **Policy choice — requires
   Adam's decision** whether that complexity is justified.
5. **Treat context-engine replacements as opt-in compatible only.** Require an
   engine to declare that it preserves the current user content and Pack marker;
   otherwise use option 2 (or refuse). Cost: engine-specific capability metadata
   and tests. **Policy choice — requires Adam's decision.** The fail-open host
   behaviour alone is not a delivery guarantee.

Recommended default pending Adam's decisions: options 1 and 2 for multimodal and
`codex_app_server`; **by-reference delivery (native spill) on ordinary text and
no-op-engine paths, now that #21 evidences recovery**; option 2 for a context
engine that replaces the request; and no claim of by-reference support for MoA
participants.

## Open questions and uncertainty

* ~~Has the spill pointer been successfully read by the actual downstream agent in
  the ordinary-text path?~~ Answered by #21: yes (live observation, one model).
* Should a brokered intervention be refused, or should Hermes silently/native-index
  degrade, when the seam is unsupported? **Adam's decision required.**
* Is the local spill directory readable to every MoA advisor and aggregator, and
  does the selected MoA route provide a tool/retrieval path? **Unknown from source.**
* Which configured context engines are expected to preserve broker content after
  `select_context()` or compaction? **Unknown; requires per-engine contract/tests.**
* Is a future Codex app-server input extension acceptable, or must the broker
  permanently exclude that route? **Adam's decision required.**
