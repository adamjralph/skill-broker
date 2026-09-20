#!/usr/bin/env python3
"""The Hermes Adapter at its pure seam (ticket #51, ADR-0001/B9).

The Adapter is the second internal replaceable seam. These tests drive its hook callbacks with
injected collaborators — a real broker over the shared store fixture, an in-memory request
evidence log and a fake plugin context — so every acceptance criterion that is not Hermes-runtime
specific is observable here: Shadow Mode emits nothing while a Pack is built, an unsupported
delivery path records why, one evidence record per request carries the system-prompt hash, tool
count and correlation ids and locates its Route Decision, an Adapter failure is swallowed, and
the Adapter registers exactly two hooks and nothing else.

The real Hermes seam under an isolated ``HERMES_HOME`` is driven separately by
``tests/hermes_adapter/`` (it needs the Hermes virtualenv, so it is not part of the default
stdlib discovery).

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import broker  # noqa: E402
from broker import Broker, FirstCandidateJudgmentSource, TurnOutcome  # noqa: E402
from broker.adapter import (  # noqa: E402
    Adapter,
    ApiRequestEvidence,
    DeliveryPath,
    JsonlRequestEvidenceLog,
    api_mode_from,
    classify_delivery_path,
    hook_config_from,
    request_text,
)
from broker.pack import DEFAULT_ADAPTER_RESERVE, DEFAULT_HOOK_CAP  # noqa: E402
from broker.gate import InjectionBatch, GateReview, InjectionGate  # noqa: E402
from broker_fixture import RecordingEvidenceLog  # noqa: E402
from store_fixture import ALIASED, DISTINCT, StoreFixtureTestCase  # noqa: E402

REQUEST = "Please use writing-method for this piece."
PACK_MARKER = "SKILL-BROKER PACK"


class FakePluginContext:
    """A stand-in for Hermes's ``PluginContext`` recording exactly what a plugin registers."""

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.hooks: dict[str, object] = {}
        self.tools: list = []
        self.capabilities: list = []
        self.system_prompt_sections: list = []

    def get_config(self, key: str, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback

    def register_tool(self, *args, **kwargs) -> None:
        self.tools.append((args, kwargs))

    def register_capability(self, *args, **kwargs) -> None:
        self.capabilities.append((args, kwargs))

    def register_system_prompt_section(self, *args, **kwargs) -> None:
        self.system_prompt_sections.append((args, kwargs))


class RaisingBroker:
    """A broker whose one external seam fails, to prove the Adapter survives it."""

    def prepare_turn(self, request, profile, session_context=None):
        raise RuntimeError("broker exploded")


class FailingEvidenceLog:
    def append(self, record) -> None:
        raise OSError("disk full")


class AdapterTestCase(StoreFixtureTestCase):
    """Helpers: one broker over the store fixture and an in-memory request evidence log."""

    def make_broker(self, fixture=None, *, source=None):
        fixture = fixture or self.store
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence,
                        judgment_source=source or FirstCandidateJudgmentSource())
        return broker, evidence

    def make_adapter(self, *, fixture=None, source=None, inject=False, request_evidence=None,
                     runtime_api_mode=None):
        broker, evidence = self.make_broker(fixture, source=source)
        adapter = Adapter(broker=broker, profile=(fixture or self.store).profile,
                          request_evidence=request_evidence, inject=inject,
                          runtime_api_mode=runtime_api_mode)
        return adapter, evidence

    def pre_llm(self, adapter, *, user_message=REQUEST, session_id="s-1", task_id="t-1",
                turn_id="turn-1"):
        return adapter.on_pre_llm_call(session_id=session_id, task_id=task_id,
                                       turn_id=turn_id, user_message=user_message)

    def pre_api(self, adapter, **overrides):
        payload = {
            "session_id": "s-1", "task_id": "t-1", "turn_id": "turn-1",
            "api_request_id": "turn-1:api:1", "api_mode": "chat_completions",
            "api_call_count": 1, "tool_count": 7, "system_prompt": "SYSTEM PROMPT",
            "request_char_count": 4321,
        }
        payload.update(overrides)
        return adapter.on_pre_api_request(**payload)


class DeliveryPathTest(unittest.TestCase):
    def test_a_text_user_message_is_the_supported_path(self) -> None:
        self.assertIs(classify_delivery_path("hello", api_mode="chat_completions"),
                      DeliveryPath.TEXT)
        self.assertTrue(DeliveryPath.TEXT.supported)

    def test_multimodal_user_content_is_unsupported(self) -> None:
        path = classify_delivery_path([{"type": "text", "text": "hello"},
                                       {"type": "image_url", "image_url": {"url": "x"}}])
        self.assertIs(path, DeliveryPath.MULTIMODAL)
        self.assertFalse(path.supported)

    def test_the_app_server_route_is_unsupported(self) -> None:
        path = classify_delivery_path("hello", api_mode="codex_app_server")
        self.assertIs(path, DeliveryPath.CODEX_APP_SERVER)
        self.assertFalse(path.supported)

    def test_request_text_flattens_multimodal_parts_to_their_text(self) -> None:
        self.assertEqual(request_text("plain"), "plain")
        self.assertEqual(request_text([{"type": "text", "text": "one"}, "two"]), "one\ntwo")
        self.assertEqual(request_text(None), "")


class HookConfigTest(unittest.TestCase):
    def test_the_host_cap_and_spill_setting_are_read_not_defaulted(self) -> None:
        config = hook_config_from({"hooks": {"output_spill": {"enabled": True, "max_chars": 4321}}})
        self.assertEqual(config.hook_cap, 4321)
        self.assertEqual(config.reserve, DEFAULT_ADAPTER_RESERVE)
        self.assertTrue(config.spill)
        self.assertEqual(config.inline_budget, 4321 - DEFAULT_ADAPTER_RESERVE)

    def test_a_disabled_spill_is_unbounded(self) -> None:
        config = hook_config_from({"hooks": {"output_spill": {"enabled": False}}})
        self.assertFalse(config.spill)
        self.assertIsNone(config.inline_budget)

    def test_a_missing_or_malformed_section_falls_back_to_the_host_defaults(self) -> None:
        self.assertEqual(hook_config_from({}).hook_cap, DEFAULT_HOOK_CAP)
        self.assertEqual(hook_config_from({"hooks": "nonsense"}).hook_cap, DEFAULT_HOOK_CAP)
        self.assertEqual(
            hook_config_from({"hooks": {"output_spill": {"max_chars": "nope"}}}).hook_cap,
            DEFAULT_HOOK_CAP)

    def test_the_app_server_route_is_read_only_for_the_providers_it_applies_to(self) -> None:
        self.assertEqual(api_mode_from({"model": {"provider": "openai",
                                                   "openai_runtime": "codex_app_server"}}),
                         "codex_app_server")
        self.assertEqual(api_mode_from({"model": {"provider": "openai-codex",
                                                   "openai_runtime": "codex_app_server"}}),
                         "codex_app_server")

    def test_a_stray_app_server_setting_on_another_provider_is_ordinary_text(self) -> None:
        self.assertEqual(api_mode_from({"model": {"provider": "openai-compat",
                                                   "openai_runtime": "codex_app_server"}}), "")
        self.assertEqual(api_mode_from({"model": {"provider": "openai"}}), "")
        self.assertEqual(api_mode_from({}), "")


class ShadowModeTest(AdapterTestCase):
    def test_shadow_mode_returns_no_context_even_when_a_pack_is_available(self) -> None:
        adapter, evidence = self.make_adapter(inject=False)

        self.assertIsNone(self.pre_llm(adapter))

        decision = evidence.decisions[-1]
        self.assertEqual([grant.id for grant in decision.grants], [decision.grants[0].id])
        self.assertTrue(decision.grants)
        self.assertIsNotNone(decision.pack_sha256)

    def test_shadow_mode_puts_no_pack_content_anywhere_in_its_return(self) -> None:
        adapter, _ = self.make_adapter(inject=False)

        result = self.pre_llm(adapter)

        self.assertIsNone(result)
        self.assertNotIn(PACK_MARKER, json.dumps(result))

    def test_injection_enabled_returns_the_pack_as_context(self) -> None:
        adapter, evidence = self.make_adapter(inject=True)

        result = self.pre_llm(adapter)

        self.assertIsInstance(result, dict)
        self.assertIn(PACK_MARKER, result["context"])
        self.assertTrue(evidence.decisions[-1].grants)


class UnsupportedPathTest(AdapterTestCase):
    def test_multimodal_content_emits_nothing_and_records_why(self) -> None:
        adapter, evidence = self.make_adapter(inject=True)

        result = self.pre_llm(adapter, user_message=[{"type": "text", "text": REQUEST}])

        self.assertIsNone(result)
        decision = evidence.decisions[-1]
        self.assertIs(decision.outcome, TurnOutcome.NO_SKILL)
        self.assertIn("delivery_path_unsupported", decision.reasons)
        self.assertEqual(decision.delivery_path, DeliveryPath.MULTIMODAL.value)
        self.assertEqual(decision.grants, ())
        self.assertIsNone(decision.pack_sha256)

    def test_the_app_server_route_emits_nothing_and_records_why(self) -> None:
        adapter, evidence = self.make_adapter(inject=True,
                                             runtime_api_mode=lambda: "codex_app_server")

        result = self.pre_llm(adapter)

        self.assertIsNone(result)
        decision = evidence.decisions[-1]
        self.assertIn("delivery_path_unsupported", decision.reasons)
        self.assertEqual(decision.delivery_path, DeliveryPath.CODEX_APP_SERVER.value)

    def test_an_unsupported_path_short_circuits_before_the_store_is_read(self) -> None:
        broker, evidence = self.make_broker()
        # Break the store so the only way a NO_SKILL outcome appears is the path short-circuit.
        (self.store.root / "store-meta.json").unlink()
        adapter = Adapter(broker=broker, profile=self.store.profile, inject=True)

        result = self.pre_llm(adapter, user_message=[{"type": "text", "text": REQUEST}])

        self.assertIsNone(result)
        self.assertNotIn("store_manifest_invalid", evidence.decisions[-1].reasons)
        self.assertIn("delivery_path_unsupported", evidence.decisions[-1].reasons)


class RequestEvidenceTest(AdapterTestCase):
    def test_one_evidence_record_per_request_carries_the_hash_tool_count_and_ids(self) -> None:
        log = RecordingRequestEvidence()
        adapter, _ = self.make_adapter(inject=False, request_evidence=log)
        self.pre_llm(adapter)

        self.pre_api(adapter, api_request_id="turn-1:api:1", tool_count=7,
                     system_prompt="SYSTEM PROMPT")
        self.pre_api(adapter, api_request_id="turn-1:api:2", tool_count=9,
                     system_prompt="SYSTEM PROMPT", api_call_count=2)

        self.assertEqual(len(log.records), 2)
        first = log.records[0]
        self.assertEqual(first.system_prompt_sha256,
                         hashlib.sha256(b"SYSTEM PROMPT").hexdigest())
        self.assertEqual(first.system_prompt_chars, len("SYSTEM PROMPT"))
        self.assertEqual(first.tool_count, 7)
        self.assertEqual((first.session_id, first.task_id, first.turn_id, first.api_request_id),
                         ("s-1", "t-1", "turn-1", "turn-1:api:1"))

    def test_the_evidence_record_locates_the_route_decision_it_belongs_to(self) -> None:
        log = RecordingRequestEvidence()
        adapter, evidence = self.make_adapter(inject=False, request_evidence=log)
        self.pre_llm(adapter)
        self.pre_api(adapter)

        decision = evidence.decisions[-1]
        record = log.records[-1]
        self.assertEqual(record.route_decision_id, decision.route_decision_id)
        self.assertEqual((record.session_id, record.turn_id),
                         (decision.session_id, decision.turn_id))

    def test_the_system_prompt_falls_back_to_the_request_messages(self) -> None:
        log = RecordingRequestEvidence()
        adapter, _ = self.make_adapter(inject=False, request_evidence=log)

        self.pre_api(adapter, system_prompt=None, request_messages=[
            {"role": "system", "content": "FROM MESSAGES"}, {"role": "user", "content": "hi"}])

        self.assertEqual(log.records[-1].system_prompt_sha256,
                         hashlib.sha256(b"FROM MESSAGES").hexdigest())

    def test_a_record_written_without_a_prior_decision_has_no_locator(self) -> None:
        log = RecordingRequestEvidence()
        adapter, _ = self.make_adapter(inject=False, request_evidence=log)

        self.pre_api(adapter, turn_id="never-seen")

        self.assertIsNone(log.records[-1].route_decision_id)


class FailureToleranceTest(AdapterTestCase):
    def test_a_broker_failure_returns_no_context_and_does_not_raise(self) -> None:
        adapter = Adapter(broker=RaisingBroker(), profile=self.store.profile, inject=True)

        with self.assertLogs("broker.adapter", level="WARNING"):
            self.assertIsNone(self.pre_llm(adapter))

    def test_a_request_evidence_failure_does_not_raise(self) -> None:
        adapter = Adapter(broker=RaisingBroker(), profile=self.store.profile,
                          request_evidence=FailingEvidenceLog())

        with self.assertLogs("broker.adapter", level="WARNING"):
            self.assertIsNone(self.pre_api(adapter))


class RegistrationTest(AdapterTestCase):
    def test_the_adapter_registers_exactly_two_hooks_and_nothing_else(self) -> None:
        adapter, _ = self.make_adapter()
        ctx = FakePluginContext()

        adapter.register(ctx)

        self.assertEqual(set(ctx.hooks), {"pre_llm_call", "pre_api_request"})
        self.assertEqual(ctx.tools, [])
        self.assertEqual(ctx.capabilities, [])
        self.assertEqual(ctx.system_prompt_sections, [])

    def test_from_context_composes_a_broker_over_the_configured_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The switch is operator-controlled: a batch must be enabled on a recorded review
            # before injection can happen, even with ``inject`` set in the plugin config.
            gate = InjectionGate(state_path=Path(tmp) / "gate.json")
            gate.enable(
                InjectionBatch(name="pilot", profile=self.store.profile),
                GateReview(profile=self.store.profile, batch="pilot", reviewer="test",
                           outcome="approve", reviewed_at="2026-09-21T00:00:00+00:00"))
            ctx = FakePluginContext({
                "store": str(self.store.root),
                "profile": self.store.profile,
                "evidence_dir": tmp,
                "judgment": "first_candidate",
            })

            adapter = Adapter.from_context(ctx)
            result = adapter.on_pre_llm_call(session_id="s", task_id="t", turn_id="turn",
                                             user_message=REQUEST)
            adapter.on_pre_api_request(session_id="s", task_id="t", turn_id="turn",
                                       api_request_id="turn:api:1", system_prompt="P",
                                       tool_count=3)

            self.assertIsInstance(result, dict)
            self.assertIn(PACK_MARKER, result["context"])
            self.assertTrue((Path(tmp) / "route_decisions.jsonl").exists())
            self.assertTrue((Path(tmp) / "api_requests.jsonl").exists())

    def test_from_context_registers_only_the_two_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = FakePluginContext({"store": str(self.store.root),
                                     "profile": self.store.profile,
                                     "evidence_dir": tmp, "judgment": "no_skill"})

            Adapter.from_context(ctx).register(ctx)

            self.assertEqual(set(ctx.hooks), {"pre_llm_call", "pre_api_request"})
            self.assertEqual(ctx.tools, [])


class JsonlRequestEvidenceLogTest(unittest.TestCase):
    def test_appends_one_line_per_record_and_rewrites_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = JsonlRequestEvidenceLog(Path(tmp) / "api_requests.jsonl")
            log.append(ApiRequestEvidence(profile="p", session_id="s", task_id="t", turn_id="1",
                                          api_request_id="1:api:1", api_mode="chat_completions",
                                          api_call_count=1, tool_count=2, system_prompt_sha256="a",
                                          system_prompt_chars=1, request_char_count=3))
            log.append(ApiRequestEvidence(profile="p", session_id="s", task_id="t", turn_id="2",
                                          api_request_id="2:api:1", api_mode="chat_completions",
                                          api_call_count=1, tool_count=2, system_prompt_sha256="b",
                                          system_prompt_chars=1, request_char_count=4))

            records = [json.loads(line) for line in log.path.read_text().splitlines()]

        self.assertEqual([record["turn_id"] for record in records], ["1", "2"])
        self.assertEqual(records[0]["tool_count"], 2)


class RecordingRequestEvidence:
    """An in-memory request evidence log: keeps every record it is handed, in order."""

    def __init__(self) -> None:
        self.records: list[ApiRequestEvidence] = []

    def append(self, record: ApiRequestEvidence) -> None:
        self.records.append(record)


if __name__ == "__main__":
    unittest.main()
