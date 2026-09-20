#!/usr/bin/env python3
"""Behaviour of the broker's one external seam, ``prepare_turn`` (ticket #46).

Drives only ``Broker.prepare_turn(request, profile, session_context)`` and asserts on the
Intervention Result and the recorded Route Decision — never on broker internals.

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from broker import Broker, JsonlEvidenceLog, SessionLedger, TurnOutcome  # noqa: E402
from broker_fixture import (  # noqa: E402
    RecordingEvidenceLog,
    corrupt_policy,
    remove_policy,
    stale_manifest,
)
from store_fixture import (  # noqa: E402
    ALIASED,
    DISTINCT,
    Identity,
    StoreFixtureTestCase,
    make_store,
)


class NoSkillOutcomeTest(StoreFixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.evidence = RecordingEvidenceLog()
        self.broker = Broker(store=self.store.root, evidence_log=self.evidence)
        self.request = "Please write the release note."

    def test_a_valid_turn_is_an_explicit_no_skill_outcome(self) -> None:
        result = self.broker.prepare_turn(self.request, self.store.profile, {"session_id": "s-1"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(result.grants, ())
        self.assertIsNone(result.pack)
        self.assertEqual(result.reasons, ())

    def test_a_turn_appends_exactly_one_route_decision(self) -> None:
        self.broker.prepare_turn(self.request, self.store.profile, {"session_id": "s-1"})

        self.assertEqual(len(self.evidence.decisions), 1)
        decision = self.evidence.decisions[0]
        self.assertEqual(decision.profile, self.store.profile)
        self.assertEqual(decision.session_id, "s-1")
        self.assertIs(decision.outcome, TurnOutcome.NO_SKILL)

    def test_the_decision_records_the_authorised_closure_with_versions(self) -> None:
        self.broker.prepare_turn(self.request, self.store.profile, {"session_id": "s-1"})

        closure = self.evidence.decisions[0].authorised_closure
        rows = self.store.identities()
        self.assertEqual([entry.id for entry in closure], sorted([DISTINCT.id, ALIASED.id]))
        for entry in closure:
            self.assertEqual(entry.version, rows[entry.id]["package_sha256"])

    def test_the_decision_records_request_identity_as_a_hash_and_a_length(self) -> None:
        self.broker.prepare_turn(self.request, self.store.profile, {"session_id": "s-1"})

        decision = self.evidence.decisions[0]
        self.assertEqual(decision.request_sha256,
                         hashlib.sha256(self.request.encode("utf-8")).hexdigest())
        self.assertEqual(decision.request_chars, len(self.request))

    def test_the_record_carries_no_request_text_and_no_skill_body(self) -> None:
        self.broker.prepare_turn(self.request, self.store.profile, {"session_id": "s-1"})

        record = self.evidence.records[0]
        self.assertNotIn("release note", str(record))
        self.assertNotIn(ALIASED.body.strip(), str(record))
        self.assertNotIn(DISTINCT.body.strip(), str(record))


class StoreManifestFailureTest(StoreFixtureTestCase):
    def test_a_stale_manifest_is_a_recorded_failure_never_a_delivery(self) -> None:
        stale_manifest(self.store)
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence)

        result = broker.prepare_turn("Write the release note.", self.store.profile,
                                     {"session_id": "s-2"})

        self.assertIs(result.outcome, TurnOutcome.FAILURE)
        self.assertTrue(result.failed)
        self.assertIsNone(result.pack)
        self.assertEqual(result.grants, ())
        self.assertIn("store_manifest_invalid", result.reasons)
        self.assertEqual(len(evidence.decisions), 1)
        self.assertEqual(evidence.decisions[0].authorised_closure, ())


class PolicyFallbackTest(StoreFixtureTestCase):
    def test_a_missing_policy_is_foundation_only_with_a_recorded_reason(self) -> None:
        remove_policy(self.store)
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence)

        result = broker.prepare_turn("Write the release note.", self.store.profile,
                                     {"session_id": "s-3"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertFalse(result.failed)
        self.assertIsNone(result.pack)
        self.assertIn("policy_missing", result.reasons)
        self.assertEqual(evidence.decisions[0].authorised_closure, ())

    def test_a_malformed_policy_is_foundation_only_with_a_recorded_reason(self) -> None:
        corrupt_policy(self.store)
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence)

        result = broker.prepare_turn("Write the release note.", self.store.profile,
                                     {"session_id": "s-4"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertFalse(result.failed)
        self.assertIsNone(result.pack)
        self.assertIn("policy_invalid", result.reasons)
        self.assertEqual(evidence.decisions[0].authorised_closure, ())


DEPENDENCY = Identity(owner="local", slug="house-style", name="house-style",
                      body="# House style\n\nPreferred spelling and casing.\n")
PREFERRED = Identity(owner="local", slug="tone-guide", name="tone-guide",
                     body="# Tone guide\n\nVoice for this profile.\n")


class AuthorisedClosureTest(StoreFixtureTestCase):
    def closure_ids(self, *, identities, **policy_overrides) -> list[str]:
        fixture = self.make_store(identities=identities, **policy_overrides)
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence)
        broker.prepare_turn("Write the release note.", fixture.profile, {"session_id": "s-5"})
        return [entry.id for entry in evidence.decisions[0].authorised_closure]

    def test_closure_extends_by_a_declared_dependency_the_policy_does_not_list(self) -> None:
        ids = self.closure_ids(identities=(DISTINCT, replace(ALIASED, dependencies=(DEPENDENCY.id,)),
                                          DEPENDENCY))

        self.assertEqual(ids, sorted([ALIASED.id, DEPENDENCY.id, DISTINCT.id]))

    def test_a_denied_dependency_is_intersected_out_of_the_closure(self) -> None:
        ids = self.closure_ids(identities=(DISTINCT, replace(ALIASED, dependencies=(DEPENDENCY.id,)),
                                          DEPENDENCY),
                               denied=(DEPENDENCY.id,))

        self.assertEqual(ids, sorted([ALIASED.id, DISTINCT.id]))

    def test_a_preferred_skill_is_authorised_and_so_in_the_closure(self) -> None:
        ids = self.closure_ids(identities=(DISTINCT, ALIASED, PREFERRED),
                               brokered=[], preferred=(PREFERRED.id,))

        self.assertEqual(ids, sorted([DISTINCT.id, PREFERRED.id]))

    def test_an_unknown_dependency_is_recorded_as_a_problem(self) -> None:
        fixture = self.make_store(identities=(DISTINCT, replace(ALIASED, dependencies=("local.ghost",))))
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence)

        result = broker.prepare_turn("Write the release note.", fixture.profile,
                                     {"session_id": "s-6"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertTrue(any("local.ghost" in reason for reason in result.reasons), result.reasons)

    def test_a_dependency_cycle_is_recorded_as_a_problem(self) -> None:
        first = Identity(owner="local", slug="one", name="one", body="# One\n",
                         dependencies=("local.two",))
        second = Identity(owner="local", slug="two", name="two", body="# Two\n",
                          dependencies=("local.one",))
        fixture = self.make_store(identities=(first, second), foundation=(first.id,), brokered=[])
        evidence = RecordingEvidenceLog()
        broker = Broker(store=fixture.root, evidence_log=evidence)

        result = broker.prepare_turn("Write the release note.", fixture.profile,
                                     {"session_id": "s-7"})

        self.assertTrue(any("cycle" in reason for reason in result.reasons), result.reasons)


class SeamContractTest(StoreFixtureTestCase):
    def test_prepare_turn_takes_exactly_the_agreed_arguments(self) -> None:
        self.assertEqual(list(inspect.signature(Broker.prepare_turn).parameters),
                         ["self", "request", "profile", "session_context"])

    def test_every_collaborator_is_injectable_at_construction(self) -> None:
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root,
                        evidence_log=evidence,
                        policies_dir=self.store.root / "policies",
                        judgment_source=object(),
                        session_ledger=SessionLedger())

        result = broker.prepare_turn("Write the release note.", self.store.profile,
                                     {"session_id": "s-8"})

        self.assertIs(result.outcome, TurnOutcome.NO_SKILL)
        self.assertEqual(len(evidence.decisions), 1)

    def test_each_call_appends_exactly_one_decision(self) -> None:
        evidence = RecordingEvidenceLog()
        broker = Broker(store=self.store.root, evidence_log=evidence)

        broker.prepare_turn("one", self.store.profile, {"session_id": "s"})
        broker.prepare_turn("two", self.store.profile, {"session_id": "s"})

        self.assertEqual(len(evidence.decisions), 2)


class JsonlEvidenceLogTest(unittest.TestCase):
    def test_appends_one_line_per_decision_and_rewrites_nothing(self) -> None:
        fixture = make_store()
        self.addCleanup(fixture.close)
        with tempfile.TemporaryDirectory() as tmp:
            log = JsonlEvidenceLog(Path(tmp) / "evidence.jsonl")
            broker = Broker(store=fixture.root, evidence_log=log)

            broker.prepare_turn("the first request", fixture.profile, {"session_id": "s"})
            first_line = log.path.read_text()
            broker.prepare_turn("the second request", fixture.profile, {"session_id": "s"})

            lines = log.path.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(log.path.read_text().startswith(first_line))
            self.assertNotIn("the first request", log.path.read_text())
