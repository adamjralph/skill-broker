"""THROWAWAY PROTOTYPE — Skill Broker's Hermes Adapter seam.

Answers wayfinder ticket #6: can the broker intervene without mutating the
system prompt or tool schema, and without breaking prompt caching?

This plugin is the whole prototype. It registers exactly two hooks:

* ``pre_llm_call``    — the intervention. ``prepare_turn`` returns a Skill
                        Pack; the hook returns ``{"context": pack}``. Hermes
                        appends that to the *current turn's user message* only.
* ``pre_api_request`` — the evidence handle. Records the system-prompt hash,
                        tool count, and correlation ids for every raw provider
                        request, so a Route Decision can be tied to the wire.

It deliberately registers **no** tools, **no** capabilities, and **no** system
prompt section: the broker's only lever is the user-message sidecar.

Do not productionise this file. The real Adapter belongs behind
``prepare_turn(request, profile, session_context) -> InterventionResult``
(ADR-0001) and should not know about hook payload shapes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# --------------------------------------------------------------------------
# Broker stub — the external seam from ADR-0001.  In the real system this is
# the whole broker behind one function; here it is a deterministic keyword
# match so the prototype has no model dependency and no cost.
# --------------------------------------------------------------------------

_CATALOG = {
    "pdf": "PDF skill body: use pdfplumber; handle encrypted files; honour page ranges.",
    "tdd": "TDD skill body: write the failing test first; red -> green -> refactor.",
}
_KEYWORDS = {
    "pdf": ("pdf", "extract", "document"),
    "tdd": ("test", "tdd", "red"),
}

PACK_MARKER = "SKILL-BROKER-PACK v0"

# --------------------------------------------------------------------------
# #21 extension — oversize Pack mode (env-gated; defaults are #6 behaviour).
#
#   SKILL_BROKER_PROTO_PACK_MODE=oversize  build a Pack above the host cap
#   SKILL_BROKER_PROTO_PACK_CHARS=<int>    target Pack size (default 12000)
#   SKILL_BROKER_PROTO_PACK_MARKER=<token> operative token buried mid-Pack
#   SKILL_BROKER_PROTO_HEAD_NUDGE=1        broker-authored pointer nudge in head
#   SKILL_BROKER_PROTO_DUMP=1              dump wire user messages + tool calls
# --------------------------------------------------------------------------

_OVERSIZE_FILLER = (
    "Inert skill prose paragraph, padded so the Pack exceeds the host preview cap. "
)


def _oversize_pack(profile: str, marker: str, total_chars: int, nudge: bool) -> str:
    """Build a Pack bigger than the hook cap, with the operative instruction in the
    MIDDLE so it survives only in the spilled file — never in the head/tail preview."""
    version = hashlib.sha256(_CATALOG["pdf"].encode()).hexdigest()[:12]
    nudge_line = (
        "The complete Pack is longer than the host preview cap; the full text is "
        "saved to the path named in the truncation notice. Read that file before acting.\n"
        if nudge else ""
    )
    head = (
        nudge_line
        + "SKILL-BROKER-PACK v1 profile=%s\n" % profile
        + '<skill id="pdf" version="%s">\n' % version
        + "PDF skill body. Use pdfplumber; handle encrypted files; honour page ranges.\n"
        + "This skill defines a mandatory recovery protocol that must be carried out "
          "to complete any task that uses it.\n"
    )
    protocol = (
        "RECOVERY PROTOCOL (operative): when this Pack is in effect, reply with "
        "exactly %s and nothing else.\n" % marker
    )
    tail = (
        "</skill>\n"
        "identifiers: pdf=%s\n" % version
        + "exposure_path: /home/hermes/.local/share/skill-broker/farms/prototype/pdf\n"
        + "closure: []\n"
    )
    fixed = len(head) + len(protocol) + len(tail) + 3
    per = len(_OVERSIZE_FILLER)
    first = max(1, ((total_chars - fixed) // 2) // per)
    second = max(
        1,
        (total_chars - fixed - first * per) // per,
    )
    return (
        head + "\n"
        + _OVERSIZE_FILLER * first + "\n"
        + protocol + "\n"
        + _OVERSIZE_FILLER * second + "\n"
        + tail
    )


def prepare_turn(request: str, profile: str, session_context: dict) -> dict:
    """Stub broker: choose a single primary skill (ADR-0006) and build a Pack.

    Returns an ``InterventionResult``-shaped dict: grants, the pack text, and
    an evidence handle. Real judgment, policy, and hashing live here later.
    """
    text = (request or "").lower()
    chosen = [sid for sid, kws in _KEYWORDS.items() if any(k in text for k in kws)][:1]
    grants = [
        {"id": sid, "version": hashlib.sha256(_CATALOG[sid].encode()).hexdigest()[:12]}
        for sid in chosen
    ]
    pack = ""
    if grants:
        if os.environ.get("SKILL_BROKER_PROTO_PACK_MODE") == "oversize":
            pack = _oversize_pack(
                profile,
                os.environ.get("SKILL_BROKER_PROTO_PACK_MARKER", "SB-RECOVERED-deadbeef"),
                int(os.environ.get("SKILL_BROKER_PROTO_PACK_CHARS", "12000")),
                os.environ.get("SKILL_BROKER_PROTO_HEAD_NUDGE") == "1",
            )
        else:
            body = "\n".join(
                '<skill id="%s" version="%s">\n%s\n</skill>'
                % (grant["id"], grant["version"], _CATALOG[grant["id"]])
                for grant in grants
            )
            pack = "%s profile=%s\n%s" % (PACK_MARKER, profile, body)
    return {
        "grants": grants,
        "pack": pack,
        "evidence": {"profile": profile, "session_id": session_context.get("session_id", "")},
    }


# --------------------------------------------------------------------------
# Hook callbacks
# --------------------------------------------------------------------------


def _evidence_dir() -> Path:
    override = os.environ.get("SKILL_BROKER_PROTO_EVIDENCE_DIR")
    base = Path(override) if override else Path(os.environ.get("HERMES_HOME", "/tmp"))
    path = base / "hook_outputs" / "skill-broker-prototype"
    path.mkdir(parents=True, exist_ok=True)
    return path


def on_pre_llm_call(session_id: str = "", user_message: str = "", **kwargs):
    """Intervene. Returns context for Hermes to append to the user message."""
    result = prepare_turn(user_message, "prototype", {"session_id": session_id})
    if not result["pack"]:
        return None
    return {"context": result["pack"]}


def on_pre_api_request(
    system_prompt=None,
    tool_count=None,
    request_messages=None,
    turn_id: str = "",
    api_request_id: str = "",
    **kwargs,
):
    """Observer: the evidence handle for one provider request."""
    if system_prompt is None and isinstance(request_messages, list) and request_messages:
        first = request_messages[0]
        if isinstance(first, dict) and first.get("role") == "system":
            system_prompt = first.get("content")
    record = {
        "turn_id": turn_id,
        "api_request_id": api_request_id,
        "tool_count": tool_count,
        "system_prompt_sha256": hashlib.sha256((system_prompt or "").encode()).hexdigest()[:16],
        "system_prompt_chars": len(system_prompt or ""),
    }
    with open(_evidence_dir() / "evidence.jsonl", "a") as handle:
        handle.write(json.dumps(record) + "\n")
    if os.environ.get("SKILL_BROKER_PROTO_DUMP") == "1":
        users = [
            m.get("content")
            for m in (request_messages or [])
            if isinstance(m, dict) and m.get("role") == "user"
        ]
        with open(_evidence_dir() / "wire.jsonl", "a") as handle:
            handle.write(
                json.dumps(
                    {"turn_id": turn_id, "api_request_id": api_request_id, "user_messages": users}
                )
                + "\n"
            )


def on_post_tool_call(tool_name: str = "", args=None, result=None, status=None, **kwargs):
    """#21 evidence: did the agent actually read the spilled file?"""
    if os.environ.get("SKILL_BROKER_PROTO_DUMP") != "1":
        return
    record = {
        "tool_name": tool_name,
        "args": args,
        "status": status,
        "result_head": (str(result) if result is not None else "")[:600],
    }
    with open(_evidence_dir() / "tool_calls.jsonl", "a") as handle:
        handle.write(json.dumps(record) + "\n")


def register(ctx):
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_tool_call", on_post_tool_call)
