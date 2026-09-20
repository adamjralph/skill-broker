"""Exercise the same baseline/apply/verify/rollback interface as the CLI, on real links."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cutover as co
import exposure_farm as ef
import store_manifest as sm


def package_hash(skill_dir: Path) -> str:
    """Independent worked example of the ADR-0009 package-hash normalization."""
    h = hashlib.sha256()
    for f in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(skill_dir)).encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
        h.update(b"\n")
    return h.hexdigest()


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = self.root / "native"
        self.config = self.root / "config.yaml"
        self.baseline = self.root / "baseline.json"
        self.config.write_text("model: x\nskills:\n  external_dirs:\n    - /already/there\n")
        self.writing = self.skill(self.native, "writing")
        self.store = self.root / "store"
        self.skill(self.store / "one", "novel", body="novel")
        sm.generate(self.store)
        self.manifest = self.root / "exposures.json"
        self.exposures(("novel", "one.novel"))
        self.farm = self.root / "farm"

    def exposures(self, *pairs, consumer="test"):
        self.manifest.write_text(json.dumps({"manifest_version": 1, "consumer": consumer,
                                            "exposures": [{"name": n, "id": i} for n, i in pairs]}))

    def external_dirs(self):
        return (yaml.safe_load(self.config.read_text()).get("skills") or {}).get("external_dirs")

    def apply(self, **kwargs):
        return self.run_cutover("apply", store=self.store, manifest=self.manifest,
                                farm=self.farm, **kwargs)

    def write_policy(self, foundation, brokered=(), profile="test"):
        path = self.root / f"{profile}.json"
        path.write_text(json.dumps({"policy_version": 1, "profile": profile,
                                    "foundation": list(foundation),
                                    "brokered": list(brokered)}))
        return path

    def skill(self, root: Path, name: str, body: str = "x") -> Path:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")
        return d

    def run_cutover(self, command="baseline", roots=None, **kwargs):
        return co.run(command, config=self.config, baseline=self.baseline,
                      consumer="test", roots=(self.native,) if roots is None else roots, **kwargs)

    def test_baseline_records_config_and_resolution_read_only(self):
        before = self.config.read_bytes()
        existing = set(self.root.iterdir())
        result = self.run_cutover()
        self.assertTrue(result["ok"])
        self.assertEqual(result["consumer"], "test")
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(set(self.root.iterdir()) - existing, {self.baseline})
        data = json.loads(self.baseline.read_text())
        self.assertEqual(data["baseline_version"], 1)
        self.assertEqual(data["generator"], co.GENERATOR)
        self.assertEqual(data["consumer"], "test")
        self.assertEqual(data["config"]["path"], str(self.config))
        self.assertEqual(data["config"]["external_dirs"], ["/already/there"])
        self.assertEqual(data["config"]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(data["resolution"]["writing"],
                         {"realpath": str(self.writing), "package_sha256": package_hash(self.writing)})

    def test_resolution_uses_frontmatter_name_and_first_root_wins(self):
        other = self.root / "other"
        self.skill(other, "writing", body="second")
        self.assertEqual(
            self.run_cutover(roots=(self.native, other))["ok"], True)
        data = json.loads(self.baseline.read_text())
        self.assertEqual(data["resolution"]["writing"]["realpath"], str(self.writing))

    def test_missing_config_and_missing_baseline_fail_closed(self):
        with self.assertRaisesRegex(co.CutoverError, "config not found"):
            co.run("baseline", config=self.root / "nope.yaml", baseline=self.baseline,
                   consumer="test", roots=())
        with self.assertRaisesRegex(co.CutoverError, "baseline not found"):
            co.run("apply", store=self.root, config=self.config, baseline=self.baseline)

    def test_apply_generates_farm_and_edits_config_exactly_once(self):
        self.run_cutover()
        self.assertFalse(self.farm.exists())
        result = self.apply()
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["exposures"], 1)
        self.assertEqual(os.readlink(self.farm / "novel"), str(self.store / "one/novel"))
        self.assertEqual(self.external_dirs(), ["/already/there", str(self.farm)])
        once = self.config.read_text()
        again = self.apply()
        self.assertFalse(again["changed"])
        self.assertEqual(once, self.config.read_text())

    def test_apply_failure_before_edit_leaves_config_and_farm_untouched(self):
        self.run_cutover()
        before = self.config.read_bytes()
        self.exposures(("novel", "missing.id"))
        with self.assertRaises(ef.FarmError):
            self.apply()
        self.assertEqual(before, self.config.read_bytes())
        self.assertFalse(self.farm.exists())

    def test_apply_requires_the_baseline_config_path(self):
        self.run_cutover()
        data = json.loads(self.baseline.read_text())
        data["config"]["path"] = str(self.root / "elsewhere.yaml")
        self.baseline.write_text(json.dumps(data))
        with self.assertRaisesRegex(co.CutoverError, "baseline config"):
            self.apply()

    def test_verify_detects_missing_config_entry_and_wrong_link(self):
        self.run_cutover()
        self.apply()
        self.assertTrue(self.run_cutover("verify", store=self.store, manifest=self.manifest,
                                         farm=self.farm)["ok"])
        once = self.config.read_text()
        self.config.write_text(once.replace(f"    - {self.farm}\n", ""))
        with self.assertRaisesRegex(co.CutoverError, "external_dirs"):
            self.run_cutover("verify", store=self.store, manifest=self.manifest, farm=self.farm)
        self.config.write_text(once)
        link = self.farm / "novel"
        link.unlink()
        link.symlink_to(self.store / "one", target_is_directory=True)
        with self.assertRaises(ef.FarmError):
            self.run_cutover("verify", store=self.store, manifest=self.manifest, farm=self.farm)

    def test_rollback_removes_only_the_farm_entry_and_is_idempotent(self):
        self.run_cutover()
        self.apply()
        self.assertTrue((self.farm / "novel").is_symlink())
        result = self.run_cutover("rollback")
        self.assertTrue(result["changed"])
        self.assertEqual(self.external_dirs(), ["/already/there"])
        self.assertTrue((self.farm / "novel").is_symlink())
        again = self.run_cutover("rollback")
        self.assertFalse(again["changed"])
        self.assertEqual(self.external_dirs(), ["/already/there"])

    def test_inline_empty_external_dirs_is_edited_not_duplicated(self):
        self.config.write_text("skills:\n  external_dirs: []\n")
        self.run_cutover()
        self.apply()
        self.assertEqual(self.config.read_text().count("external_dirs"), 1)
        self.assertEqual(self.external_dirs(), [str(self.farm)])
        self.run_cutover("rollback")
        self.assertEqual(self.external_dirs(), [])

    def test_apply_creates_the_skills_block_after_the_last_top_level_key(self):
        self.config.write_text("model: x\ntools:\n  enabled:\n    - a\n\n")
        self.run_cutover()
        self.apply()
        self.assertEqual(self.config.read_text(),
                         "model: x\ntools:\n  enabled:\n    - a\n"
                         f"skills:\n  external_dirs:\n    - {self.farm}\n\n")
        self.assertTrue(json.loads(self.baseline.read_text())["applied"]["created_skills_block"])

    def test_created_skills_block_rolls_back_byte_for_byte(self):
        self.config.write_text("model: x\ntools:\n  enabled:\n    - a\n\n")
        before = self.config.read_bytes()
        self.run_cutover()
        recorded = json.loads(self.baseline.read_text())["resolution"]
        self.apply()
        self.assertTrue(self.run_cutover("rollback")["changed"])
        self.assertEqual(before, self.config.read_bytes())
        data = json.loads(self.baseline.read_text())
        self.assertNotIn("applied", data)
        self.assertEqual(data["resolution"], recorded)

    def test_reapplying_a_created_block_keeps_it_rollback_exact(self):
        self.config.write_text("model: x\n")
        before = self.config.read_bytes()
        self.run_cutover()
        self.apply()
        self.assertFalse(self.apply()["changed"])
        self.assertTrue(json.loads(self.baseline.read_text())["applied"]["created_skills_block"])
        self.run_cutover("rollback")
        self.assertEqual(before, self.config.read_bytes())

    def test_existing_skills_block_is_not_flagged_as_created(self):
        self.run_cutover()
        before = self.config.read_bytes()
        self.apply()
        self.assertFalse(json.loads(self.baseline.read_text())["applied"]["created_skills_block"])
        self.run_cutover("rollback")
        self.assertEqual(before, self.config.read_bytes())

    def test_rollback_of_a_modified_created_block_fails_closed(self):
        self.config.write_text("model: x\n")
        self.run_cutover()
        self.apply()
        edited = self.config.read_text().replace("skills:\n", "skills:\n  disabled:\n    - x\n")
        self.config.write_text(edited)
        with self.assertRaisesRegex(co.CutoverError, "modified"):
            self.run_cutover("rollback")
        self.assertEqual(self.config.read_text(), edited)

    def test_gate_fails_closed_on_lost_resolution(self):
        self.run_cutover()
        shutil.rmtree(self.native / "writing")
        with self.assertRaisesRegex(co.CutoverError, "regression: writing"):
            self.apply()
        self.assertEqual(self.external_dirs(), ["/already/there"])
        self.assertTrue((self.farm / "novel").is_symlink())

    def test_gate_accepts_a_skill_moved_into_the_farm(self):
        self.run_cutover()
        moved = self.store / "one" / "writing"
        moved.mkdir(parents=True)
        (moved / "SKILL.md").write_bytes((self.native / "writing" / "SKILL.md").read_bytes())
        sm.generate(self.store)
        shutil.rmtree(self.native / "writing")
        self.exposures(("writing", "one.writing"))
        self.assertTrue(self.apply()["ok"])
        self.assertEqual(self.external_dirs(), ["/already/there", str(self.farm)])

    def test_cross_skill_reference_must_resolve(self):
        self.skill(self.native, "alpha", body='See skill_view("beta") for details.\n')
        self.run_cutover()
        with self.assertRaisesRegex(co.CutoverError, "unresolved reference: alpha -> beta"):
            self.apply()
        self.assertEqual(self.external_dirs(), ["/already/there"])

    def test_cross_skill_reference_satisfied_by_the_farm_passes(self):
        self.skill(self.native, "alpha", body='See skill_view("beta") for details.\n')
        beta = self.store / "one" / "beta"
        beta.mkdir(parents=True)
        (beta / "SKILL.md").write_text("---\nname: beta\n---\nb\n")
        sm.generate(self.store)
        self.run_cutover()
        self.exposures(("novel", "one.novel"), ("beta", "one.beta"))
        self.assertTrue(self.apply()["ok"])

    def test_policy_gate_accepts_a_resolving_foundation(self):
        self.skill(self.store / "one", "foundation")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"), ("foundation", "one.foundation"))
        policy = self.write_policy([{"id": "one.foundation"}])
        self.run_cutover()
        self.assertTrue(self.apply(policy=policy)["ok"])
        self.assertEqual(self.external_dirs(), ["/already/there", str(self.farm)])

    def test_policy_gate_fails_closed_when_a_foundation_does_not_resolve(self):
        self.skill(self.store / "one", "foundation")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"))  # foundation deliberately not exposed
        policy = self.write_policy([{"id": "one.foundation"}])
        self.run_cutover()
        before = self.config.read_bytes()
        with self.assertRaisesRegex(co.CutoverError, "foundation not resolving: foundation"):
            self.apply(policy=policy)
        self.assertEqual(before, self.config.read_bytes())

    def test_policy_gate_requires_a_project_wired_foundation_by_name(self):
        policy = self.write_policy([{"project": "/p", "name": "method"}])
        self.run_cutover()
        with self.assertRaisesRegex(co.CutoverError, "foundation not resolving: method"):
            self.apply(policy=policy)
        self.assertEqual(self.external_dirs(), ["/already/there"])
        self.skill(self.native, "method")
        self.assertTrue(self.apply(policy=policy)["ok"])

    def test_policy_gate_rejects_a_brokered_skill_in_the_index(self):
        self.skill(self.store / "one", "brokered", body="store copy")
        self.skill(self.native, "brokered", body="native copy")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"))
        policy = self.write_policy([{"id": "one.novel"}], ["one.brokered"])
        self.run_cutover()
        with self.assertRaisesRegex(co.CutoverError, "brokered skill in the index: one.brokered"):
            self.apply(policy=policy)
        self.assertEqual(self.external_dirs(), ["/already/there"])

    def test_policy_gate_rejects_a_brokered_skill_exposed_by_path(self):
        self.skill(self.store / "one", "brokered", body="store copy")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"), ("brokered", "one.brokered"))
        policy = self.write_policy([{"id": "one.novel"}], ["one.brokered"])
        self.run_cutover()
        with self.assertRaisesRegex(co.CutoverError, "brokered skill exposed: one.brokered"):
            self.apply(policy=policy)

    def test_policy_gate_allows_a_brokered_twin_sharing_a_foundation_name(self):
        self.skill(self.store / "one", "tdd", body="foundation copy")
        self.skill(self.store / "two", "tdd", body="brokered copy")
        sm.generate(self.store)
        self.exposures(("tdd", "one.tdd"))
        policy = self.write_policy([{"id": "one.tdd"}], ["two.tdd"])
        self.run_cutover()
        self.assertTrue(self.apply(policy=policy)["ok"])

    def test_policy_gate_records_the_after_resolution_map(self):
        foundation = self.skill(self.store / "one", "foundation")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"), ("foundation", "one.foundation"))
        policy = self.write_policy([{"id": "one.foundation"}])
        self.run_cutover()
        self.apply(policy=policy)
        data = json.loads(self.baseline.read_text())
        self.assertEqual(data["applied"]["resolution"]["foundation"],
                         {"realpath": str(foundation.resolve()),
                          "package_sha256": package_hash(foundation)})

    def test_invalid_policy_fails_closed_before_any_change(self):
        policy = self.write_policy([{"id": "missing.id"}])
        self.run_cutover()
        before = self.config.read_bytes()
        with self.assertRaisesRegex(co.CutoverError, "unknown ID: 'missing.id'"):
            self.apply(policy=policy)
        self.assertEqual(before, self.config.read_bytes())
        self.assertFalse(self.farm.exists())

    def test_verify_with_policy_rechecks_the_policy_conditions(self):
        self.skill(self.store / "one", "foundation")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"), ("foundation", "one.foundation"))
        policy = self.write_policy([{"id": "one.foundation"}])
        self.run_cutover()
        self.apply(policy=policy)
        self.assertTrue(self.run_cutover("verify", store=self.store, manifest=self.manifest,
                                         farm=self.farm, policy=policy)["ok"])
        # The brokered skill reappears in the automatic index after the applied Cutover.
        self.skill(self.store / "one", "brokered", body="store copy")
        self.skill(self.native, "brokered", body="native copy")
        sm.generate(self.store)
        policy = self.write_policy([{"id": "one.foundation"}], ["one.brokered"])
        with self.assertRaisesRegex(co.CutoverError, "brokered skill in the index: one.brokered"):
            self.run_cutover("verify", store=self.store, manifest=self.manifest,
                             farm=self.farm, policy=policy)

    def test_cli_apply_and_verify_accept_a_policy(self):
        self.skill(self.store / "one", "foundation")
        sm.generate(self.store)
        self.exposures(("novel", "one.novel"), ("foundation", "one.foundation"))
        policy = self.write_policy([{"id": "one.foundation"}])
        script = str(Path(co.__file__))
        subprocess.run(
            [sys.executable, script, "baseline", "--consumer", "test", "--config", str(self.config),
             "--baseline", str(self.baseline), "--roots", str(self.native)],
            text=True, capture_output=True, check=True)
        apply = subprocess.run(
            [sys.executable, script, "apply", "--store", str(self.store), "--manifest",
             str(self.manifest), "--farm", str(self.farm), "--config", str(self.config),
             "--baseline", str(self.baseline), "--policy", str(policy)],
            text=True, capture_output=True)
        self.assertEqual(apply.returncode, 0, apply.stderr + apply.stdout)
        verify = subprocess.run(
            [sys.executable, script, "verify", "--store", str(self.store), "--manifest",
             str(self.manifest), "--farm", str(self.farm), "--config", str(self.config),
             "--policy", str(policy), "--roots", str(self.native)],
            text=True, capture_output=True)
        self.assertEqual(verify.returncode, 0, verify.stderr + verify.stdout)

    def test_cli_baseline_and_apply_exit_statuses(self):
        script = str(Path(co.__file__))
        baseline = subprocess.run(
            [sys.executable, script, "baseline", "--consumer", "test", "--config", str(self.config),
             "--baseline", str(self.baseline), "--roots", str(self.native)],
            text=True, capture_output=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr + baseline.stdout)
        self.assertTrue(json.loads(baseline.stdout)["ok"])
        apply = subprocess.run(
            [sys.executable, script, "apply", "--store", str(self.store), "--manifest",
             str(self.manifest), "--farm", str(self.farm), "--config", str(self.config),
             "--baseline", str(self.baseline)], text=True, capture_output=True)
        self.assertEqual(apply.returncode, 0, apply.stderr + apply.stdout)
        self.assertTrue(json.loads(apply.stdout)["changed"])
        self.exposures(("novel", "missing.id"))
        bad = subprocess.run(
            [sys.executable, script, "verify", "--store", str(self.store), "--manifest",
             str(self.manifest), "--farm", str(self.farm), "--config", str(self.config)],
            text=True, capture_output=True)
        self.assertEqual(bad.returncode, 1)
        self.assertFalse(json.loads(bad.stdout)["ok"])
        self.assertNotIn("Traceback", bad.stderr)


if __name__ == "__main__":
    unittest.main()
