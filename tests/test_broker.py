#!/usr/bin/env python3
"""Smoke tests for the broker module home and the shared store fixture (ticket #45).

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import broker  # noqa: E402
import profile_policy as pp  # noqa: E402
import store_manifest as sm  # noqa: E402
from store_fixture import ALIASED, DISTINCT, StoreFixtureTestCase  # noqa: E402


class BrokerModuleHomeTest(unittest.TestCase):
    def test_broker_package_is_importable_by_the_existing_runner(self) -> None:
        self.assertEqual(broker.__name__, "broker")


class SharedStoreFixtureTest(StoreFixtureTestCase):
    def test_manifest_passes_the_existing_verifier(self) -> None:
        self.assertEqual(sm.verify(self.store.root), [])

    def test_policy_passes_the_existing_validator(self) -> None:
        self.assertEqual(pp.validate_dir(self.store.root), [])

    def test_fixture_carries_an_aliased_and_a_distinct_named_identity(self) -> None:
        rows = self.store.identities()
        aliased, distinct = rows[ALIASED.id], rows[DISTINCT.id]
        self.assertEqual(aliased["aliases"], sorted(ALIASED.aliases))
        self.assertEqual(distinct["aliases"], [])
        self.assertNotEqual(distinct["name"], aliased["name"])
        self.assertNotIn(distinct["name"], aliased["aliases"])

    def test_foundation_is_exposed_and_brokered_is_withheld(self) -> None:
        derived = pp.derive(self.store.policy(), self.store.identities())
        self.assertEqual(derived["exposures"],
                         [{"name": DISTINCT.name, "id": DISTINCT.id}])
