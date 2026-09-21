#!/usr/bin/env python3
"""The live Jev Judgment Source and the fallback chain, observed at ``prepare_turn`` (#50).

The Source is driven through its injectable client factory, so no network and no SDK are needed:
a fake answers exactly what a live TypeSafe Choice returns. What matters is that the shared
validator accepts a live answer, that only the bounded authorised Candidate set is ever sent,
that expiry or an unavailable model never blocks a turn or broadens access, and that the source
that answered, its latency and its token usage land in the Route Decision.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import (  # noqa: E402
    JEV_DEFAULT_TIMEOUT,
    Broker,
    Recording,
    TurnOutcome,
    live_judgment_source,
)
from broker.judgment import NO_SKILL, JudgmentError  # noqa: E402
from broker_fixture import RecordingEvidenceLog, judgment_claim  # noqa: E402
from store_fixture import ALIASED, DISTINCT, StoreFixtureTestCase  # noqa: E402

REQUEST = "Please use writing-method for this piece."
CANDIDATES = (DISTINCT.id, ALIASED.id)
HOOK_CALLBACK_TIMEOUT = 30.0  # Hermes's plugins.hook_callback_timeout default


class FakeAnswer:
    def __init__(self, choice, confidence, probabilities):
        self.choice = choice
        self.confidence = confidence
        self.probabilities = probabilities


class FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, *, chosen, confidence, probabilities, input_tokens=120,
                 output_tokens=15):
        self.answers = {"primary": FakeAnswer(chosen, confidence, probabilities)}
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeClient:
    """A TypeSafe client stand-in: returns a response, raises an error, or records the call."""

    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    def system_one(self, *, state, questions, model):
        self.calls.append({"state": state, "questions": questions, "model": model})
        if self.error is not None:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


class FakeClientFactory:
    def __init__(self, client):
        self.client = client
        self.timeouts: list[float] = []

    def __call__(self, timeout):
        self.timeouts.append(timeout)
        return self.client


def jev_response(ids, *, chosen, confidence=0.9, input_tokens=120, output_tokens=15):
    """A well-formed TypeSafe ChoiceAnswer: a probability for every offered label."""
    labels = [*ids, NO_SKILL]
    others = [label for label in labels if label != chosen]
    share = (1.0 - confidence) / len(others) if others else 0.0
    probabilities = {label: share for label in others}
    probabilities[chosen] = round(1.0 - share * len(others), 6)
    return FakeResponse(chosen=chosen, confidence=probabilities[chosen],
                        probabilities=probabilities, input_tokens=input_tokens,
                        output_tokens=output_tokens)


class JevSourceTestCase(StoreFixtureTestCase):
    def run_turn(self, client, *, recording=None, request=REQUEST, session_id="s-jev"):
        evidence = RecordingEvidenceLog()
        factory = FakeClientFactory(client)
        source = live_judgment_source(recording=recording, client_factory=factory)
        broker = Broker(store=self.store.root, evidence_log=evidence, judgment_source=source)
        result = broker.prepare_turn(request, self.store.profile, {"session_id": session_id})
        return result, evidence.decisions[-1], factory, client

    def recording_for_alias(self):
        return Recording.of(REQUEST, judgment_claim(CANDIDATES, primary=ALIASED.id))


class LiveCallTest(JevSourceTestCase):
    def test_a_live_call_returns_a_judgment_the_validator_accepts(self) -> None:
        result, decision, _, client = self.run_turn(
            FakeClient(response=jev_response(CANDIDATES, chosen=ALIASED.id)))

        self.assertIs(result.outcome, TurnOutcome.GRANTED)
        self.assertEqual([grant.id for grant in result.grants], [ALIASED.id])
        self.assertIsNotNone(result.pack)
        self.assertIs(decision.judgment_source, "jev")
        self.assertTrue(client.closed)

    def test_only_the_bounded_authorised_candidate_set_is_sent(self) -> None:
        result, decision, _, client = self.run_turn(
            FakeClient(response=jev_response(CANDIDATES, chosen=ALIASED.id)))

        call = client.calls[0]
        labels = set(call["questions"]["primary"]["criteria"])
        self.assertEqual(labels, {DISTINCT.id, ALIASED.id, NO_SKILL})
        self.assertEqual(call["state"], REQUEST)
        closure = {entry.id for entry in decision.authorised_closure}
        self.assertTrue({DISTINCT.id, ALIASED.id} <= closure)

    def test_the_model_may_choose_the_reserved_no_skill_option(self) -> None:
        result, decision, _, _ = self.run_turn(
            FakeClient(response=jev_response(CANDIDATES, chosen=NO_SKILL)))

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertIs(decision.judgment_source, "jev")
        self.assertIsNone(decision.judgment.primary)

    def test_usage_latency_and_source_are_recorded(self) -> None:
        _, decision, _, _ = self.run_turn(
            FakeClient(response=jev_response(CANDIDATES, chosen=ALIASED.id,
                                             input_tokens=321, output_tokens=42)))

        self.assertEqual(decision.judgment_source, "jev")
        self.assertEqual(decision.judgment_usage, {"input_tokens": 321, "output_tokens": 42})
        self.assertIsInstance(decision.judgment_latency_ms, float)
        self.assertGreaterEqual(decision.judgment_latency_ms, 0.0)

    def test_the_timeout_budget_is_well_under_the_hook_callback_timeout(self) -> None:
        _, _, factory, _ = self.run_turn(
            FakeClient(response=jev_response(CANDIDATES, chosen=ALIASED.id)))

        self.assertEqual(factory.timeouts, [JEV_DEFAULT_TIMEOUT])
        self.assertGreater(JEV_DEFAULT_TIMEOUT, 0.0)
        self.assertLess(JEV_DEFAULT_TIMEOUT, HOOK_CALLBACK_TIMEOUT)

    def test_the_default_client_needs_an_api_key(self) -> None:
        from broker.jev import _http_client

        previous = os.environ.pop("TYPESAFE_API_KEY", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "TYPESAFE_API_KEY", previous)

        with self.assertRaises(JudgmentError):
            _http_client(JEV_DEFAULT_TIMEOUT)

    def test_the_http_client_posts_one_bounded_request(self) -> None:
        from broker.jev import _HttpJevClient

        seen: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> bytes:
                return json.dumps({
                    "model": "jev-latest",
                    "usage": {"input_tokens": 5, "output_tokens": 6},
                    "answers": {"primary": {"type": "choice", "choice": ALIASED.id,
                                             "confidence": 0.9,
                                             "probabilities": {ALIASED.id: 0.9}}},
                }).encode("utf-8")

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = _HttpJevClient(api_key="secret", timeout=JEV_DEFAULT_TIMEOUT, opener=opener)
        response = client.system_one(state=REQUEST, model="jev-latest",
                                     questions={"primary": {"type": "choice", "criteria": {}}})

        self.assertEqual(seen["url"], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(seen["auth"], "Bearer secret")
        self.assertEqual(seen["timeout"], JEV_DEFAULT_TIMEOUT)
        self.assertGreater(JEV_DEFAULT_TIMEOUT, 0.0)
        self.assertLess(JEV_DEFAULT_TIMEOUT, HOOK_CALLBACK_TIMEOUT)
        self.assertEqual(seen["body"]["state"], REQUEST)
        self.assertEqual(seen["body"]["questions"]["primary"]["type"], "choice")
        self.assertEqual(response.answers["primary"].choice, ALIASED.id)
        self.assertEqual(response.usage.input_tokens, 5)


class FallbackTest(JevSourceTestCase):
    def test_expiry_falls_back_to_a_recording(self) -> None:
        result, decision, _, _ = self.run_turn(
            FakeClient(error=TimeoutError("the Jev call exceeded its budget")),
            recording=self.recording_for_alias())

        self.assertIs(result.outcome, TurnOutcome.GRANTED)
        self.assertEqual([grant.id for grant in result.grants], [ALIASED.id])
        self.assertIs(decision.judgment_source, "recorded")
        self.assertTrue(any("TimeoutError" in reason for reason in decision.reasons),
                        decision.reasons)

    def test_an_unavailable_model_never_broadens_access_and_never_blocks(self) -> None:
        result, decision, _, _ = self.run_turn(
            FakeClient(error=RuntimeError("model unavailable")))

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertIs(decision.judgment_source, NO_SKILL)
        self.assertIsNone(decision.judgment.primary)
        self.assertTrue(any("RuntimeError" in reason for reason in decision.reasons),
                        decision.reasons)

    def test_a_fallback_reason_never_records_the_exception_text(self) -> None:
        canary = "REQUEST-SECRET-CANARY"
        _, decision, _, _ = self.run_turn(FakeClient(error=RuntimeError(canary)))

        self.assertTrue(any("RuntimeError" in reason for reason in decision.reasons),
                        decision.reasons)
        self.assertNotIn(canary, str(decision.to_record()))

    def test_an_invalid_response_falls_back_rather_than_granting(self) -> None:
        result, decision, _, _ = self.run_turn(
            FakeClient(response=FakeResponse(
                chosen="local.ghost", confidence=0.9,
                probabilities={"local.ghost": 0.9, NO_SKILL: 0.1})),
            recording=self.recording_for_alias())

        self.assertIs(result.outcome, TurnOutcome.GRANTED)
        self.assertIs(decision.judgment_source, "recorded")
        self.assertNotIn("local.ghost", {grant.id for grant in result.grants})

    def test_a_live_answer_that_fails_validation_never_grants(self) -> None:
        broken = FakeResponse(chosen=ALIASED.id, confidence=0.9,
                              probabilities={DISTINCT.id: 0.2, ALIASED.id: 0.2, NO_SKILL: 0.2})

        result, decision, _, _ = self.run_turn(FakeClient(response=broken))

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertIn("judgment_invalid", result.reasons)
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertIs(decision.judgment_source, "jev")

    def test_a_recording_bound_to_another_request_is_skipped_for_no_skill(self) -> None:
        other = Recording.of("a different request entirely",
                             judgment_claim(CANDIDATES, primary=ALIASED.id))

        result, decision, _, _ = self.run_turn(
            FakeClient(error=TimeoutError("expired")), recording=other)

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertIs(decision.judgment_source, NO_SKILL)


if __name__ == "__main__":
    unittest.main()
