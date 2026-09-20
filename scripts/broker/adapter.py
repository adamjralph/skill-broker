"""The Hermes Adapter: the broker at the cache-safe Hermes seam (ADR-0001, B9, ticket #51).

The Adapter is the second internal replaceable seam. It composes a :class:`Broker` and registers
exactly two hooks on the host and nothing else:

* ``pre_llm_call``    — the Intervention. In injection mode it returns ``{"context": pack}`` for
                        the current turn's user message only; in Shadow Mode it returns no context
                        while the Route Decision is still recorded from real traffic.
* ``pre_api_request`` — the evidence handle. Appends one record per provider request carrying the
                        system-prompt hash, the tool count and the Hermes correlation ids, so each
                        request can be located back to the Route Decision it belongs to.

Cache safety is by construction, not by discipline: Hermes appends hook context to the *current
turn's user copy at API-call time only*, stamps the exact sent bytes into the message's
``api_content`` sidecar and replays them verbatim on later turns. The Adapter therefore registers
**no** tools, **no** capabilities and **no** system-prompt section — the broker's only lever is
the user-message sidecar — and it never writes the host's configuration. It *reads* the effective
hook cap at runtime and budgets against the host's real configuration.

Delivery paths that cannot carry the seam — multimodal user content and the ``codex_app_server``
route — are classified as unsupported: the Adapter emits no Intervention, the Route Decision
records why, and native foundation exposure is untouched (B10). Any Adapter failure is swallowed,
so a broker problem can never make the agent unusable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .broker import Broker
from .evidence import JsonlEvidenceLog, append_jsonl
from .judgment import (
    FallbackJudgmentSource,
    FirstCandidateJudgmentSource,
    JudgmentSource,
    RecordedJudgmentSource,
    Recording,
)
from .jev import live_judgment_source
from .pack import DEFAULT_ADAPTER_RESERVE, DEFAULT_HOOK_CAP, HookConfig

logger = logging.getLogger(__name__)

DEFAULT_STORE = "~/skill-store"
DEFAULT_PROFILE = "default"
DECISION_MEMORY = 256

_HOOK_CAP_DEFAULT = DEFAULT_HOOK_CAP


class DeliveryPath(str, Enum):
    """How a turn's content can reach the model through the Hermes seam (B10).

    Only ``TEXT`` supports the user-message sidecar. Multimodal content and the app-server route
    drop it, so a Pack delivered there would be silently lost; the Adapter refuses them instead.
    """

    TEXT = "text"
    MULTIMODAL = "multimodal"
    CODEX_APP_SERVER = "codex_app_server"

    @property
    def supported(self) -> bool:
        return self is DeliveryPath.TEXT


def classify_delivery_path(user_message: object, *, api_mode: object = None) -> DeliveryPath:
    """Classify the turn's delivery path from the hook payload.

    A non-string user message is multimodal content (``compose_user_api_content`` returns ``None``
    unless the content is a ``str``, so the sidecar never lands). ``codex_app_server`` hands the
    turn to the app-server subprocess without the sidecar, so it is refused too.
    """
    if not isinstance(user_message, str):
        return DeliveryPath.MULTIMODAL
    if str(api_mode or "").strip().lower() == DeliveryPath.CODEX_APP_SERVER.value:
        return DeliveryPath.CODEX_APP_SERVER
    return DeliveryPath.TEXT


def request_text(user_message: object) -> str:
    """The turn's request as text: a plain message verbatim, multimodal parts joined by newline."""
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, (list, tuple)):
        parts = [part for part in (_part_text(item) for item in user_message) if part]
        return "\n".join(parts)
    return "" if user_message is None else str(user_message)


def _part_text(part: object) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, Mapping):
        text = part.get("text")
        if isinstance(text, str):
            return text
    return ""


@dataclass(frozen=True)
class ApiRequestEvidence:
    """One provider request's evidence handle (B9).

    Carries metadata and hashes only — never request text and never a Skill body (ADR-0015) — and
    names the Route Decision it belongs to, directly by ``route_decision_id`` when the turn was
    observed in this process and durably by the ``session_id``/``turn_id`` pair either way.
    """

    profile: str
    session_id: str
    task_id: str
    turn_id: str
    api_request_id: str
    api_mode: str = ""
    api_call_count: int = 0
    tool_count: int = 0
    system_prompt_sha256: str = ""
    system_prompt_chars: int = 0
    request_char_count: int = 0
    route_decision_id: str | None = None

    def to_record(self) -> dict:
        return {
            "profile": self.profile,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "api_request_id": self.api_request_id,
            "api_mode": self.api_mode,
            "api_call_count": self.api_call_count,
            "tool_count": self.tool_count,
            "system_prompt_sha256": self.system_prompt_sha256,
            "system_prompt_chars": self.system_prompt_chars,
            "request_char_count": self.request_char_count,
            "route_decision_id": self.route_decision_id,
        }


@runtime_checkable
class RequestEvidenceLog(Protocol):
    """Where one provider request's evidence handle is durably appended."""

    def append(self, record: ApiRequestEvidence) -> None: ...


class JsonlRequestEvidenceLog:
    """The durable API-request evidence handle: one record per line, appended, never rewritten."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, record: ApiRequestEvidence) -> None:
        append_jsonl(self.path, record.to_record())


def hook_config_from(config: Mapping | None) -> HookConfig:
    """The host's output-spill configuration as a :class:`HookConfig`, never a write.

    ``hooks.output_spill.max_chars`` is the effective hook cap and ``hooks.output_spill.enabled``
    is whether the host's native spill will carry an oversize Pack. A missing or malformed
    section falls back to the host defaults rather than failing the Adapter.
    """
    if not isinstance(config, Mapping):
        return HookConfig()
    hooks = config.get("hooks")
    if not isinstance(hooks, Mapping):
        return HookConfig()
    spill = hooks.get("output_spill")
    if not isinstance(spill, Mapping):
        return HookConfig()
    enabled = spill.get("enabled", True)
    return HookConfig(
        hook_cap=_positive_int(spill.get("max_chars"), _HOOK_CAP_DEFAULT),
        reserve=DEFAULT_ADAPTER_RESERVE,
        spill=bool(enabled) if enabled is not None else True,
    )


def hermes_hook_config() -> HookConfig:
    """Read the effective hook cap from the live Hermes configuration. Never raises."""
    return hook_config_from(_host_config())


def configured_api_mode() -> str:
    """The effective provider route from the live Hermes configuration. Never raises."""
    return api_mode_from(_host_config())


def api_mode_from(config: Mapping | None) -> str:
    """The provider route Hermes would resolve for this configuration.

    Hermes rewrites the route to ``codex_app_server`` only for the ``openai``/``openai-codex``
    providers (``hermes_cli.runtime_provider``), and the rewrite takes effect on the next session,
    so reproducing that gate here keeps the classification honest: a stray ``openai_runtime`` on
    another provider is ordinary text, not a refusable path.
    """
    if not isinstance(config, Mapping):
        return ""
    model = config.get("model")
    if not isinstance(model, Mapping):
        return ""
    provider = str(model.get("provider") or "").strip().lower()
    runtime = str(model.get("openai_runtime") or "").strip().lower()
    if provider in {"openai", "openai-codex"} and runtime == "codex_app_server":
        return "codex_app_server"
    return ""


def default_profile() -> str:
    """The active Hermes profile id, or ``default`` when the home is not a named profile."""
    try:
        from hermes_constants import get_hermes_home, profile_name_for_home

        return profile_name_for_home(get_hermes_home()) or DEFAULT_PROFILE
    except Exception:  # noqa: BLE001
        return DEFAULT_PROFILE


def judgment_source_from(**settings: Any) -> JudgmentSource:
    """The configured Judgment Source: live Jev, a Recording, the first Candidate, or No-Skill."""
    choice = str(settings.get("judgment") or "live").strip().lower()
    if choice == "recorded":
        recording_path = settings.get("recording")
        if not recording_path:
            raise ValueError("judgment 'recorded' needs a 'recording' path")
        record = json.loads(Path(str(recording_path)).expanduser().read_text(encoding="utf-8"))
        return RecordedJudgmentSource(Recording.from_record(record))
    if choice == "first_candidate":
        return FirstCandidateJudgmentSource()
    if choice == "no_skill":
        return FallbackJudgmentSource()
    return live_judgment_source()


class Adapter:
    """The broker behind the two Hermes hooks. Construct it with its collaborators injected.

    ``inject`` is the per-batch injection switch and is **off by default**: Shadow Mode runs the
    whole pipeline at the real seam and returns no context. ``runtime_api_mode`` is the host's
    route probe, injectable so a test drives the app-server path without changing global config.
    """

    def __init__(
        self,
        *,
        broker: Broker,
        profile: str,
        request_evidence: RequestEvidenceLog | None = None,
        inject: bool = False,
        runtime_api_mode: Callable[[], str] | None = None,
    ) -> None:
        self._broker = broker
        self._profile = profile
        self._request_evidence = request_evidence
        self._inject = bool(inject)
        self._runtime_api_mode = runtime_api_mode or configured_api_mode
        self._decisions: dict[str, str] = {}

    @property
    def injecting(self) -> bool:
        """Whether this Adapter would emit an Intervention. Shadow Mode is ``False``."""
        return self._inject

    def register(self, ctx: Any) -> None:
        """Register exactly the two hooks and nothing else (B9, user story 46)."""
        ctx.register_hook("pre_llm_call", self.on_pre_llm_call)
        ctx.register_hook("pre_api_request", self.on_pre_api_request)

    def on_pre_llm_call(
        self,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        user_message: object = "",
        **kwargs: Any,
    ) -> dict | None:
        """The Intervention. Returns context for the current turn's user message, or ``None``.

        Shadow Mode returns ``None`` even when a Pack was built. An unsupported delivery path
        returns ``None`` with the reason recorded on the Route Decision. Any failure returns
        ``None`` so ordinary Hermes operation is untouched.
        """
        del kwargs
        try:
            return self._intervene(session_id=str(session_id or ""), task_id=str(task_id or ""),
                                   turn_id=str(turn_id or ""), user_message=user_message)
        except Exception:  # noqa: BLE001 — the Adapter must never break the turn
            logger.warning("Skill Broker intervention failed; the turn continues", exc_info=True)
            return None

    def on_pre_api_request(
        self,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        api_request_id: str = "",
        api_mode: str = "",
        api_call_count: int = 0,
        tool_count: int = 0,
        system_prompt: object = None,
        request_messages: object = None,
        request_char_count: int = 0,
        **kwargs: Any,
    ) -> None:
        """The evidence handle: append one record for this provider request."""
        del kwargs
        try:
            prompt = system_prompt if system_prompt is not None \
                else _system_prompt_from_messages(request_messages)
            text = prompt if isinstance(prompt, str) else ("" if prompt is None else str(prompt))
            turn = str(turn_id or "")
            record = ApiRequestEvidence(
                profile=self._profile,
                session_id=str(session_id or ""),
                task_id=str(task_id or ""),
                turn_id=turn,
                api_request_id=str(api_request_id or ""),
                api_mode=str(api_mode or ""),
                api_call_count=_int(api_call_count),
                tool_count=_int(tool_count),
                system_prompt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                system_prompt_chars=len(text),
                request_char_count=_int(request_char_count),
                route_decision_id=self._decisions.get(turn),
            )
            if self._request_evidence is not None:
                self._request_evidence.append(record)
        except Exception:  # noqa: BLE001 — evidence must never break the turn
            logger.warning("Skill Broker evidence handle failed", exc_info=True)
        return None

    def _intervene(self, *, session_id: str, task_id: str, turn_id: str,
                   user_message: object) -> dict | None:
        path = classify_delivery_path(user_message, api_mode=self._runtime_api_mode())
        result = self._broker.prepare_turn(
            request_text(user_message),
            self._profile,
            {
                "session_id": session_id or None,
                "task_id": task_id or None,
                "turn_id": turn_id or None,
                "delivery_path": path.value,
                "delivery_supported": path.supported,
            },
        )
        if result.decision is not None and turn_id:
            self._remember(turn_id, result.decision.route_decision_id)
        if not path.supported or not self._inject or not result.pack:
            return None
        return {"context": result.pack}

    def _remember(self, turn_id: str, decision_id: str) -> None:
        self._decisions[turn_id] = decision_id
        if len(self._decisions) > DECISION_MEMORY:
            self._decisions.pop(next(iter(self._decisions)))

    @classmethod
    def from_context(cls, ctx: Any, *, broker: Broker | None = None,
                     hook_config: HookConfig | None = None) -> "Adapter":
        """Compose the Adapter from the host plugin context. Reads settings; writes nothing."""
        settings = {
            "store": ctx.get_config("store", DEFAULT_STORE),
            "profile": ctx.get_config("profile", None),
            "evidence_dir": ctx.get_config("evidence_dir", None),
            "judgment": ctx.get_config("judgment", "live"),
            "recording": ctx.get_config("recording", None),
            "inject": ctx.get_config("inject", False),
        }
        profile = settings["profile"] or default_profile()
        evidence_dir = Path(str(settings["evidence_dir"] or _default_evidence_dir())).expanduser()
        if broker is None:
            broker = Broker(
                store=Path(str(settings["store"])).expanduser(),
                evidence_log=JsonlEvidenceLog(evidence_dir / "route_decisions.jsonl"),
                judgment_source=judgment_source_from(**settings),
                hook=hook_config if hook_config is not None else hermes_hook_config(),
            )
        return cls(
            broker=broker,
            profile=str(profile),
            request_evidence=JsonlRequestEvidenceLog(evidence_dir / "api_requests.jsonl"),
            inject=bool(settings["inject"]),
        )


def _default_evidence_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()) / "skill-broker"
    except Exception:  # noqa: BLE001
        return Path("~/.hermes/skill-broker")


def _host_config() -> Mapping:
    """The live Hermes configuration, or ``{}`` when it cannot be read. Never raises."""
    try:
        from hermes_cli.config import load_config_readonly

        return load_config_readonly() or {}
    except Exception:  # noqa: BLE001 — the host is outside our control; defaults are safe
        logger.debug("could not read the Hermes configuration", exc_info=True)
        return {}


def _system_prompt_from_messages(request_messages: object) -> str | None:
    if not isinstance(request_messages, list):
        return None
    for message in request_messages:
        if isinstance(message, Mapping) and message.get("role") == "system":
            content = message.get("content")
            return content if isinstance(content, str) else None
    return None


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if not isinstance(value, int):
        try:
            value = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
    return value if value > 0 else default


def _int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int):
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
    return value
