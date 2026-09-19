#!/usr/bin/env python3
"""Unit tests for the Store Manifest generator/verifier (ADR-0011).

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import store_manifest as sm  # noqa: E402


def make_skill(root: Path, owner: str, slug: str, name: str, body: str = "# body") -> Path:
    d = root / owner / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n\n{body}\n")
    return d


class StoreManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "store"
        self.store.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_store_is_valid(self) -> None:
        self.assertEqual(sm.build(self.store)["identities"], [])
        sm.generate(self.store)
        self.assertEqual(sm.verify(self.store), [])

    def test_placeholder_dirs_ignored(self) -> None:
        placeholder = self.store / "local" / "placeholder"
        placeholder.mkdir(parents=True)
        (placeholder / ".gitkeep").write_text("")
        self.assertEqual(sm.collect(self.store), [])

    def test_hash_normalization_excludes_placeholders_and_caches(self) -> None:
        a = make_skill(self.store, "local", "alpha", "alpha")
        (a / ".gitkeep").write_text("")
        (a / "__pycache__").mkdir()
        (a / "__pycache__" / "x.pyc").write_text("junk")
        (a / ".git").mkdir()
        (a / ".git" / "config").write_text("junk")
        (a / "mod.pyc").write_text("junk")

        clean = Path(self._tmp.name) / "clean"
        clean.mkdir()
        make_skill(clean, "local", "alpha", "alpha")

        row, row_clean = sm.collect(self.store)[0], sm.collect(clean)[0]
        self.assertEqual(row["package_sha256"], row_clean["package_sha256"])
        self.assertEqual(row["file_count"], row_clean["file_count"])

    def test_skill_md_hash_is_separate(self) -> None:
        a = make_skill(self.store, "local", "alpha", "alpha", body="one")
        first = sm.collect(self.store)[0]
        (a / "SKILL.md").write_text((a / "SKILL.md").read_text().replace("one", "two"))
        second = sm.collect(self.store)[0]
        self.assertNotEqual(first["package_sha256"], second["package_sha256"])
        self.assertNotEqual(first["skill_md_sha256"], second["skill_md_sha256"])
        (a / "extra.md").write_text("support file")
        third = sm.collect(self.store)[0]
        self.assertNotEqual(second["package_sha256"], third["package_sha256"])
        self.assertEqual(second["skill_md_sha256"], third["skill_md_sha256"])

    def test_meta_overrides_and_default_licence(self) -> None:
        make_skill(self.store, "local", "alpha", "alpha")
        make_skill(self.store, "nousresearch", "beta", "beta")
        (self.store / sm.META_NAME).write_text(json.dumps({
            "local/alpha": {
                "aliases": ["a2", "a1"],
                "provenance": {"repo": "x/y", "commit": "abc"},
                "local_patch": {"bool": True, "upstream_commit": "def"},
                "dependencies": ["beta"],
                "license": "MIT",
            }
        }))
        rows = {r["id"]: r for r in sm.collect(self.store)}
        self.assertEqual(rows["local.alpha"]["aliases"], ["a1", "a2"])
        self.assertEqual(rows["local.alpha"]["provenance"], {"repo": "x/y", "commit": "abc"})
        self.assertEqual(rows["local.alpha"]["local_patch"],
                         {"bool": True, "upstream_commit": "def"})
        self.assertEqual(rows["local.alpha"]["dependencies"], ["beta"])
        self.assertEqual(rows["local.alpha"]["license"], "MIT")
        self.assertIsNone(rows["nousresearch.beta"]["license"])

        make_skill(self.store, "local", "gamma", "gamma")
        rows = {r["id"]: r for r in sm.collect(self.store)}
        self.assertEqual(rows["local.gamma"]["license"], sm.DEFAULT_LICENSE)
        self.assertEqual(rows["local.gamma"]["provenance"], {"repo": None, "commit": None})

    def test_generate_verify_roundtrip_and_drift(self) -> None:
        a = make_skill(self.store, "local", "alpha", "alpha")
        sm.generate(self.store)
        self.assertEqual(sm.verify(self.store), [])
        (a / "extra.md").write_text("drift")
        problems = sm.verify(self.store)
        self.assertTrue(any("drift" in p for p in problems), problems)

    def test_added_and_removed_dirs_detected(self) -> None:
        make_skill(self.store, "local", "alpha", "alpha")
        sm.generate(self.store)
        make_skill(self.store, "local", "beta", "beta")
        self.assertTrue(any("missing from manifest" in p for p in sm.verify(self.store)))
        sm.generate(self.store)
        shutil.rmtree(self.store / "local" / "beta")
        self.assertTrue(any("no identity directory" in p for p in sm.verify(self.store)))

    def test_duplicate_content_rejected(self) -> None:
        make_skill(self.store, "local", "alpha", "alpha")
        make_skill(self.store, "local", "beta", "alpha")  # identical bytes
        (self.store / sm.META_NAME).write_text(json.dumps({"local/beta": {"name": "beta"}}))
        with self.assertRaises(sm.ManifestError):
            sm.collect(self.store)

    def test_duplicate_id_rejected(self) -> None:
        make_skill(self.store, "local", "alpha", "dup")
        make_skill(self.store, "local", "beta", "dup")
        with self.assertRaises(sm.ManifestError):
            sm.collect(self.store)

    def test_serialization_is_canonical(self) -> None:
        make_skill(self.store, "local", "alpha", "alpha")
        self.assertEqual(sm.serialize(sm.build(self.store)), sm.serialize(sm.build(self.store)))


if __name__ == "__main__":
    unittest.main()
