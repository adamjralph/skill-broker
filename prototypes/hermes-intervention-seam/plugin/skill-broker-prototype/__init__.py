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


def register(ctx):
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_api_request", on_pre_api_request)
