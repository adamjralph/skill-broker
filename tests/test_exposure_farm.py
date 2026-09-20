"""Exercise the same generate/verify/validate interface as the CLI, using real links."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import exposure_farm as ef
import store_manifest as sm


class ExposureFarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        for owner in ("one", "two"):
            skill = self.store / owner / "writing"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"---\nname: writing\n---\n{owner}\n")
            (skill / "reference.txt").write_text(owner)
        sm.generate(self.store)
        self.manifest = self.root / "exposures.json"
        self.farm = self.root / "farm"
        self.exposures(("writing", "one.writing"))

    def exposures(self, *pairs, consumer="test"):
        self.manifest.write_text(json.dumps({"manifest_version": 1, "consumer": consumer,
                                           "exposures": [{"name": n, "id": i} for n, i in pairs]}))

    def run_farm(self, command="generate", **kwargs):
        return ef.run(command, self.store, self.manifest, self.farm, **kwargs)

    def snapshot(self):
        return (self.farm.stat().st_ino, (self.farm / ef.RECEIPT).read_bytes(),
                {p.name: os.readlink(p) for p in self.farm.iterdir() if p.is_symlink()})

    def test_generate_verify_and_idempotence(self):
        self.assertTrue(self.run_farm()["changed"])
        self.assertEqual(os.readlink(self.farm / "writing"), str(self.store / "one/writing"))
        before = self.snapshot()
        self.assertTrue(self.run_farm("verify")["ok"])
        self.assertFalse(self.run_farm()["changed"])
        self.assertEqual(before, self.snapshot())

    def test_aliases_and_distinct_same_name_identities(self):
        self.exposures(("first", "one.writing"), ("alias", "one.writing"), ("second", "two.writing"))
        self.run_farm()
        self.assertEqual((self.farm / "first").resolve(), (self.farm / "alias").resolve())
        self.assertNotEqual((self.farm / "first").resolve(), (self.farm / "second").resolve())
        self.run_farm("verify")

    def test_collision_reports_both_ids_and_preserves_farm(self):
        self.run_farm()
        before = self.snapshot()
        self.exposures(("same", "one.writing"), ("same", "two.writing"))
        with self.assertRaisesRegex(ef.FarmError, "one.writing and two.writing"):
            self.run_farm()
        self.assertEqual(before, self.snapshot())

    def test_unknown_id_and_unsafe_names_fail_without_output(self):
        for name, ident in [("writing", "missing.id"), ("../escape", "one.writing"),
                            ("/absolute", "one.writing"), (".", "one.writing"),
                            (ef.RECEIPT, "one.writing"), ("nested/name", "one.writing")]:
            with self.subTest(name=name):
                self.exposures((name, ident))
                with self.assertRaises(ef.FarmError):
                    self.run_farm()
                self.assertFalse(self.farm.exists())

    def test_bad_schema_and_duplicate_json_keys(self):
        for text in ['[]', '{}', '{"consumer":"a","consumer":"b"}',
                     '{"manifest_version":true,"consumer":"a","exposures":[]}']:
            self.manifest.write_text(text)
            with self.assertRaises(ef.FarmError):
                self.run_farm("validate")

    def test_validate_needs_no_farm_and_writes_nothing(self):
        before = set(self.root.iterdir())
        self.assertTrue(ef.run("validate", self.store, self.manifest)["ok"])
        self.assertEqual(before, set(self.root.iterdir()))

    def test_store_support_file_drift_preserves_farm(self):
        self.run_farm()
        before = self.snapshot()
        (self.store / "one/writing/reference.txt").write_text("changed")
        for command in ("validate", "generate", "verify"):
            with self.assertRaisesRegex(ef.FarmError, "Store Manifest drift"):
                self.run_farm(command)
        self.assertEqual(before, self.snapshot())

    def test_verified_new_store_version_requires_regeneration(self):
        self.run_farm()
        before = self.snapshot()
        (self.store / "one/writing/reference.txt").write_text("new Version")
        sm.generate(self.store)
        with self.assertRaisesRegex(ef.FarmError, "Versions"):
            self.run_farm("verify")
        self.run_farm()
        self.run_farm("verify")
        self.assertNotEqual(before[1], self.snapshot()[1])

    def test_wrong_identity_link_is_rejected(self):
        self.run_farm()
        link = self.farm / "writing"
        link.unlink()
        link.symlink_to(self.store / "two/writing")
        for command in ("generate", "verify"):
            with self.assertRaisesRegex(ef.FarmError, "modified or missing"):
                self.run_farm(command)

    def test_same_content_at_noncanonical_path_is_not_accepted(self):
        self.run_farm()
        duplicate = self.root / "duplicate"
        duplicate.mkdir()
        for source in (self.store / "one/writing").iterdir():
            (duplicate / source.name).write_bytes(source.read_bytes())
        link = self.farm / "writing"
        link.unlink()
        link.symlink_to(duplicate)
        with self.assertRaisesRegex(ef.FarmError, "modified or missing"):
            self.run_farm("verify")

    def test_identity_spelling_is_independent_of_exposure_name(self):
        (self.store / "one/writing/SKILL.md").write_text("---\nname: Make Bot UI\n---\ntext\n")
        sm.generate(self.store)
        self.exposures(("make-bot-ui", "one.Make Bot UI"))
        self.run_farm()
        self.run_farm("verify")

    def test_prune_requires_flag_and_only_unlinks_generated_exposures(self):
        self.exposures(("first", "one.writing"), ("second", "two.writing"))
        self.run_farm()
        before = self.snapshot()
        self.exposures(("first", "one.writing"))
        with self.assertRaisesRegex(ef.FarmError, "requires --prune"):
            self.run_farm()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_farm(prune=True)["pruned"], ["second"])
        self.assertFalse((self.farm / "second").exists())
        self.assertTrue((self.store / "two/writing/SKILL.md").exists())
        self.run_farm("verify")

    def test_unmanaged_entries_are_never_pruned(self):
        self.run_farm()
        native = self.farm / "native"
        native.mkdir()
        (native / "SKILL.md").write_text("leave alone")
        self.exposures()
        with self.assertRaisesRegex(ef.FarmError, "unmanaged"):
            self.run_farm(prune=True)
        self.assertEqual((native / "SKILL.md").read_text(), "leave alone")
        self.assertTrue((self.farm / "writing").is_symlink())

    def test_unmanaged_directory_and_symlink_root_are_not_adopted(self):
        self.farm.mkdir()
        with self.assertRaisesRegex(ef.FarmError, "ownership"):
            self.run_farm()
        self.farm.rmdir()
        self.farm.symlink_to(self.store, target_is_directory=True)
        with self.assertRaisesRegex(ef.FarmError, "unmanaged"):
            self.run_farm(prune=True)
        self.assertTrue(self.farm.is_symlink())

    def test_consumer_mismatch_refused(self):
        self.run_farm()
        self.exposures(("writing", "one.writing"), consumer="another")
        with self.assertRaisesRegex(ef.FarmError, "mismatch"):
            self.run_farm()

    def test_failed_publication_preserves_previous_farm(self):
        self.run_farm()
        before = self.snapshot()
        self.exposures(("writing", "two.writing"))
        with patch.object(ef, "_publish", side_effect=OSError("publication failed")):
            with self.assertRaisesRegex(OSError, "publication failed"):
                self.run_farm()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.root.glob(".farm.stage-*")), [])

    def test_failed_staging_preserves_previous_farm(self):
        self.run_farm()
        before = self.snapshot()
        self.exposures(("writing", "two.writing"))
        with patch.object(Path, "symlink_to", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.run_farm()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(list(self.root.glob(".farm.stage-*")), [])

    def test_publication_exchanges_complete_directories(self):
        self.run_farm()
        old_inode = self.farm.stat().st_ino
        self.exposures(("writing", "two.writing"), ("alias", "one.writing"))
        self.run_farm()
        self.assertNotEqual(self.farm.stat().st_ino, old_inode)
        self.assertEqual({p.name for p in self.farm.iterdir()}, {"writing", "alias", ef.RECEIPT})
        self.assertEqual(list(self.root.glob(".farm.stage-*")), [])
        self.run_farm("verify")

    def test_empty_farm_and_prune_all(self):
        self.run_farm()
        self.exposures()
        self.run_farm(prune=True)
        self.run_farm("verify")
        self.assertEqual({p.name for p in self.farm.iterdir()}, {ef.RECEIPT})

    def test_duplicate_store_rows_rejected(self):
        path = self.store / sm.MANIFEST_NAME
        data = json.loads(path.read_text())
        data["identities"].append(data["identities"][0])
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ef.FarmError, "Store Manifest drift"):
            self.run_farm()

    def test_farm_may_not_overlap_inputs(self):
        for farm in (self.store, self.store / "farm", self.root):
            with self.subTest(farm=farm), self.assertRaisesRegex(ef.FarmError, "separate"):
                ef.run("generate", self.store, self.manifest, farm)

    def test_cli_and_error_exit_status(self):
        args = [sys.executable, str(Path(ef.__file__)), "generate", "--store", str(self.store),
                "--manifest", str(self.manifest), "--farm", str(self.farm)]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.exposures(("unknown", "missing.id"))
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertNotIn("Traceback", result.stderr)

    def test_verify_missing_farm_and_invalid_prune_flag(self):
        with self.assertRaisesRegex(ef.FarmError, "generate it first"):
            self.run_farm("verify")
        with self.assertRaisesRegex(ef.FarmError, "only valid"):
            self.run_farm("verify", prune=True)

    def test_unmanaged_symlinks_and_receipt_symlinks_are_preserved(self):
        self.run_farm()
        dangling = self.farm / "unmanaged"
        dangling.symlink_to(self.root / "missing")
        before = self.snapshot()
        self.exposures()
        with self.assertRaisesRegex(ef.FarmError, "unmanaged"):
            self.run_farm(prune=True)
        self.assertEqual(before, self.snapshot())
        dangling.unlink()
        receipt = self.farm / ef.RECEIPT
        external = self.root / "external-receipt.json"
        external.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(external)
        with self.assertRaisesRegex(ef.FarmError, "ownership"):
            self.run_farm(prune=True)
        self.assertTrue(receipt.is_symlink())
        self.assertEqual(external.read_bytes(), before[1])

    def test_initial_publication_refuses_destination_created_during_staging(self):
        publish = ef._publish

        def intervening_writer(stage, farm, existing):
            farm.mkdir()
            (farm / "native").write_text("not ours")
            publish(stage, farm, existing)

        with patch.object(ef, "_publish", side_effect=intervening_writer):
            with self.assertRaises(OSError):
                self.run_farm()
        self.assertEqual((self.farm / "native").read_text(), "not ours")
        self.assertEqual(list(self.root.glob(".farm.stage-*")), [])

    def test_cli_verifier_waits_for_generator_lock(self):
        self.run_farm()
        args = [sys.executable, str(Path(ef.__file__)), "verify", "--store", str(self.store),
                "--manifest", str(self.manifest), "--farm", str(self.farm)]
        with ef._lock(self.farm, True):
            process = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.2)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr + stdout)
        self.assertTrue(json.loads(stdout)["ok"])

    def test_symlinked_store_identity_is_not_a_canonical_target(self):
        source = self.store / "one/writing"
        moved = self.root / "outside"
        source.rename(moved)
        source.symlink_to(moved, target_is_directory=True)
        sm.generate(self.store)
        with self.assertRaisesRegex(ef.FarmError, "must not be a symlink"):
            self.run_farm()

    def test_malformed_receipt_fails_closed(self):
        self.run_farm()
        receipt = self.farm / ef.RECEIPT
        for text in ('[]', '{}', '{"entries":{},"entries":{}}'):
            receipt.write_text(text)
            with self.assertRaises(ef.FarmError):
                self.run_farm(prune=True)
            self.assertEqual(receipt.read_text(), text)
            self.assertTrue((self.farm / "writing").is_symlink())

    def test_cleanup_failure_is_warning_after_successful_publication(self):
        self.run_farm()
        self.exposures(("writing", "two.writing"))
        stderr = io.StringIO()
        with patch.object(ef, "_discard", side_effect=OSError("cleanup failed")), contextlib.redirect_stderr(stderr):
            self.assertTrue(self.run_farm()["ok"])
        self.assertIn("warning", stderr.getvalue())
        self.run_farm("verify")


if __name__ == "__main__":
    unittest.main()
