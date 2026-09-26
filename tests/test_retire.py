"""Stage 9 exercises the real CLI and filesystem, never live skill paths."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import exposure_farm
import profile_policy
import skill_audit
import store_manifest


class RetirementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.canonical = self.store / "local" / "example"
        self.canonical.mkdir(parents=True)
        (self.canonical / "SKILL.md").write_text("---\nname: example\ndescription: Test\n---\nExample\n")
        self.skills = self.root / ".hermes/profiles/test/skills"
        self.source = self.skills / "category/example"
        shutil.copytree(self.canonical, self.source)
        (self.source / ".cache").mkdir()
        (self.source / ".cache/raw.bin").write_bytes(b"\x00\xff\r\n")
        (self.source / "empty").mkdir()
        (self.source / "SKILL.md").chmod(0o640)
        store_manifest.generate(self.store)
        policies = self.store / "policies"
        policies.mkdir()
        self.policy = policies / "test.json"
        self.write(self.policy, {"policy_version": 1, "profile": "test",
                                 "foundation": [{"id": "local.example"}]})
        manifest = profile_policy.derive(profile_policy.load(self.policy), profile_policy.identities(self.store))
        self.exposures = self.root / "exposures.json"
        self.write(self.exposures, manifest)
        self.farm = self.root / "farm"
        exposure_farm.run("generate", self.store, self.exposures, self.farm)
        self.config = self.root / "config.yaml"
        self.config.write_text(f"skills:\n  external_dirs:\n    - {self.farm}\n")
        self.gate = self.root / "gate.json"
        self.write(self.gate, {"state_version": 1, "profiles": {"test": {}}})
        self.incidents = self.root / "gate_incidents.jsonl"
        self.incidents.write_text("")
        self.usage = self.root / "usage.json"
        self.write(self.usage, {"example": {"use_count": 0, "view_count": 1, "patch_count": 0,
                                           "last_viewed_at": "2025-01-01T00:00:00+00:00",
                                           "state": "active", "pinned": False}})
        self.cron = self.root / "jobs.json"
        self.write(self.cron, {"jobs": []})
        self.inventory = self.root / "inputs.json"
        self.write(self.inventory, {"evidence_version": 1, "consumers": [{
            "profile": "test", "skills_root": str(self.skills), "config": str(self.config),
            "manifest": str(self.exposures), "farm": str(self.farm),
            "gate": str(self.gate), "incidents": str(self.incidents),
            "usage": str(self.usage), "cron": str(self.cron)}]})
        pkg, md, n, _ = skill_audit.hash_package(self.canonical)
        self.index = self.repo / "index.json"
        self.write(self.index, {"artifact": "stage-1-verified-audit", "identities": [{
            "id": "local.example", "name": "example", "aliases": [],
            "canonical_source": str(self.canonical), "package_sha256": pkg,
            "skill_md_sha256": md, "file_count": n, "license": "LicenseRef-Proprietary",
            "references": [], "exposures": [{"path": str(self.source)}],
            "redundant_realpaths": [str(self.source)], "proposed_classification": "brokered"}],
            "redundant_copies": [{"realpath": str(self.source), "of": "local.example", "package_sha256": pkg}],
            "conflicts": []})
        for directory in (self.store, self.repo):
            self.git(directory, "init", "-q")
            self.git(directory, "config", "user.name", "Fixture")
            self.git(directory, "config", "user.email", "fixture@example.invalid")
            self.commit(directory)
        self.out = self.repo / "retirement"
        self.artifact = self.out / "fixture.json"

    def write(self, path, obj):
        path.write_text(json.dumps(obj, indent=2) + "\n")

    def git(self, path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)

    def commit(self, directory):
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-qm", "fixture evidence")

    def cli(self, command, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / "retire.py"), command,
                                 "--batch", "fixture", "--artifacts", str(self.out), *map(str, args)],
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def propose(self, **kwargs):
        return self.cli("propose", "--store", self.store, "--audit", self.index,
                        "--evidence", self.inventory, "--profile", "test", "--path", self.source,
                        "--generated-at", "2026-01-01T00:00:00+00:00", **kwargs)

    def approve(self):
        digest = json.loads(self.artifact.read_text())["artifact_sha256"]
        self.cli("approve", "--digest", digest, "--approved-by", "Adam",
                 "--approved-at", "2026-01-02T00:00:00+00:00", "--approval-ref", "user:fixture")
        self.commit(self.repo)

    def test_proposal_is_deterministic_and_does_not_move(self):
        self.propose()
        data = json.loads(self.artifact.read_text())
        self.assertEqual(data["candidates"][0]["path"], str(self.source))
        self.assertEqual(data["candidates"][0]["identity"], "local.example")
        self.assertEqual(data["candidates"][0]["blockers"], [])
        self.assertIsNone(data["approved_by"])
        self.assertTrue((self.out / "fixture/report.md").is_file())
        first = self.artifact.read_bytes()
        self.propose()
        self.assertEqual(first, self.artifact.read_bytes())
        self.assertTrue(self.source.is_dir())
        self.assertFalse((self.store / "retired").exists())

    def test_approved_apply_and_rollback_restore_every_byte_and_mode(self):
        before = self.snapshot(self.source)
        parent = self.source.parent
        self.propose()
        self.cli("apply", ok=False)
        self.assertEqual(before, self.snapshot(self.source))
        self.approve()
        self.cli("review")
        self.cli("apply")
        self.assertFalse(self.source.exists())
        self.assertTrue(parent.is_dir())
        data = json.loads(self.artifact.read_text())
        destination = Path(data["applied"]["moves"][0]["destination"])
        self.assertEqual(before, self.snapshot(destination))
        self.assertTrue((self.store / "retired/fixture/manifest.json").is_file())
        self.cli("apply", ok=False)
        self.cli("rollback")
        self.assertEqual(before, self.snapshot(self.source))
        self.assertFalse(destination.exists())
        self.assertIsNotNone(json.loads(self.artifact.read_text())["rollback"])
        self.cli("rollback")

    def change_json(self, path, edit):
        data = json.loads(path.read_text())
        edit(data)
        self.write(path, data)

    def assert_blocked(self, message):
        result = self.propose()
        data = json.loads(self.artifact.read_text())
        self.assertIn(message, " ".join(data["candidates"][0]["blockers"]), result.stdout)
        self.cli("approve", "--digest", data["artifact_sha256"], "--approved-by", "Adam",
                 "--approved-at", "2026-01-02T00:00:00+00:00", "--approval-ref", "test", ok=False)
        self.assertTrue(self.source.exists())

    def test_manifest_drift_fails_closed(self):
        (self.canonical / "SKILL.md").write_text("changed")
        self.assertIn("Manifest drift", self.propose(ok=False).stdout)

    def test_audit_verification_covers_noncandidate_exposures(self):
        other = self.root / "other"
        shutil.copytree(self.canonical, other)
        self.change_json(self.index, lambda d: d["identities"][0]["exposures"].append({"path": str(other)}))
        self.commit(self.repo)
        (other / "SKILL.md").write_text("drift")
        self.assertIn("audit exposure hash drift", self.propose(ok=False).stdout)

    def test_uncommitted_audit_refused(self):
        self.index.write_text(self.index.read_text() + " ")
        self.assertIn("Git HEAD", self.propose(ok=False).stdout)

    def test_unresolved_audit_decision_refused(self):
        self.change_json(self.index, lambda d: d["conflicts"].append({"requires_decision": True}))
        self.commit(self.repo)
        self.assertIn("unresolved", self.propose(ok=False).stdout)

    def test_owner_drift_is_not_remapped_by_name_or_hash(self):
        self.change_json(self.index, lambda d: d["identities"][0].update(id="other.example"))
        self.commit(self.repo)
        self.assertIn("identity or hash mismatch", self.propose(ok=False).stdout)

    def test_invalid_policy_refused(self):
        self.change_json(self.policy, lambda d: d.update(brokered=["absent.id"]))
        self.assertIn("policy validation", self.propose(ok=False).stdout)

    def test_all_consumers_must_be_cut_over(self):
        self.config.write_text("skills: {}\n")
        self.assertIn("does not list the farm", self.propose(ok=False).stdout)

    def test_missing_consumer_not_silently_ignored(self):
        second = self.store / "policies/second.json"
        self.write(second, {"policy_version": 1, "profile": "second", "foundation": []})
        self.assertIn("every policy", self.propose(ok=False).stdout)

    def test_missing_incident_log_is_not_empty_log(self):
        self.incidents.unlink()
        self.propose(ok=False)
        self.assertFalse(self.artifact.exists())

    def test_open_incident_blocks(self):
        self.change_json(self.gate, lambda d: d["profiles"]["test"].update(failed_closed=True))
        self.assert_blocked("failed-closed")

    def test_corrupt_incident_state_fails_closed(self):
        self.gate.write_text("{bad")
        self.propose(ok=False)
        self.assertFalse(self.artifact.exists())

    def test_recent_usage_blocks(self):
        self.change_json(self.usage, lambda d: d["example"].update(last_used_at="2025-12-25T00:00:00+00:00"))
        self.assert_blocked("retention")

    def test_missing_usage_does_not_mean_zero(self):
        self.write(self.usage, {})
        self.assert_blocked("missing usage")

    def test_pinned_skill_blocks(self):
        self.change_json(self.usage, lambda d: d["example"].update(pinned=True))
        self.assert_blocked("pinned")

    def test_cron_reference_blocks(self):
        self.write(self.cron, {"jobs": [{"prompt": "Use example to complete this job"}]})
        self.assert_blocked("referenced")

    def test_dependency_reference_blocks(self):
        self.write(self.store / "store-meta.json", {"local/example": {"dependencies": ["local.example"]}})
        store_manifest.generate(self.store)
        self.commit(self.store)
        self.assert_blocked("referenced")

    def test_notice_obligation_is_not_dropped(self):
        (self.source.parent / "NOTICE").write_text("Upstream notice")
        self.assert_blocked("NOTICE")

    def test_parent_and_nested_skill_refused(self):
        original = self.source
        self.source = self.skills
        self.assertIn("exact profile skill", self.propose(ok=False).stdout)
        self.source = original
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "SKILL.md").write_text("child")
        # Rebaseline only fixture hashes so refusal reaches the parent-move check.
        shutil.copytree(nested, self.canonical / "nested")
        self.refresh_hashes()
        self.assertIn("nested skill", self.propose(ok=False).stdout)

    def refresh_hashes(self):
        store_manifest.generate(self.store)
        pkg, md, n, _ = skill_audit.hash_package(self.canonical)
        self.change_json(self.index, lambda d: d["identities"][0].update(
            package_sha256=pkg, skill_md_sha256=md, file_count=n))
        self.change_json(self.index, lambda d: d["redundant_copies"][0].update(package_sha256=pkg))
        self.commit(self.repo)
        self.commit(self.store)
        exposure_farm.run("generate", self.store, self.exposures, self.farm)

    def test_curator_subtree_never_traversed(self):
        (self.source / ".archive").mkdir()
        (self.source / ".archive/secret").write_text("untouched")
        self.assertIn("curator-owned", self.propose(ok=False).stdout)
        self.assertEqual((self.source / ".archive/secret").read_text(), "untouched")

    def test_symlink_candidate_refused(self):
        moved = self.source.with_name("real")
        self.source.rename(moved)
        self.source.symlink_to(moved, target_is_directory=True)
        self.assertIn("symlink", self.propose(ok=False).stdout)

    def test_approval_is_bound_to_digest_reviewer_and_reference(self):
        self.propose()
        for reviewer, digest_value, reference in [("Agent", json.loads(self.artifact.read_text())["artifact_sha256"], "x"),
                                                  ("Adam", "0" * 64, "x"), ("Adam", "", "")]:
            with self.subTest(reviewer=reviewer, digest=digest_value):
                self.cli("approve", "--digest", digest_value, "--approved-by", reviewer,
                         "--approved-at", "2026-01-02T00:00:00+00:00", "--approval-ref", reference, ok=False)
        self.assertIsNone(json.loads(self.artifact.read_text())["approved_by"])

    def test_apply_refuses_uncommitted_approval(self):
        self.propose()
        data = json.loads(self.artifact.read_text())
        self.cli("approve", "--digest", data["artifact_sha256"], "--approved-by", "Adam",
                 "--approved-at", "2026-01-02T00:00:00+00:00", "--approval-ref", "user")
        self.assertIn("Git HEAD", self.cli("apply", ok=False).stdout)
        self.assertTrue(self.source.exists())

    def test_apply_rechecks_usage_and_exact_nonpackage_bytes(self):
        self.propose()
        self.approve()
        (self.source / ".cache/raw.bin").write_bytes(b"changed excluded file")
        self.assertIn("drift", self.cli("apply", ok=False).stdout)
        self.assertTrue(self.source.exists())
        (self.source / ".cache/raw.bin").write_bytes(b"\x00\xff\r\n")
        self.change_json(self.usage, lambda d: d["example"].update(pinned=True))
        self.assertIn("drift", self.cli("apply", ok=False).stdout)

    def test_tampered_artifact_refused(self):
        self.propose()
        self.approve()
        self.change_json(self.artifact, lambda d: d["candidates"][0].update(path=str(self.skills)))
        self.assertIn("digest mismatch", self.cli("apply", ok=False).stdout)

    def test_existing_archive_is_not_overwritten(self):
        self.propose()
        self.approve()
        retired = self.store / "retired/fixture"
        retired.mkdir(parents=True)
        (retired / "sentinel").write_text("keep")
        self.assertIn("already exists", self.cli("apply", ok=False).stdout)
        self.assertEqual((retired / "sentinel").read_text(), "keep")
        self.assertTrue(self.source.exists())

    def test_rollback_collision_and_archive_tampering_fail_closed(self):
        self.propose()
        self.approve()
        self.cli("apply")
        data = json.loads(self.artifact.read_text())
        destination = Path(data["applied"]["moves"][0]["destination"])
        self.source.mkdir()
        self.assertIn("collision", self.cli("rollback", ok=False).stdout)
        self.source.rmdir()
        (destination / ".cache/raw.bin").write_bytes(b"corrupt")
        self.assertIn("bytes changed", self.cli("rollback", ok=False).stdout)
        self.assertFalse(self.source.exists())

    def test_rollback_recovers_prepared_journal_without_apply_record(self):
        self.propose()
        self.approve()
        before = self.artifact.read_bytes()
        self.cli("apply")
        journal = self.store / "retired/fixture/manifest.json"
        self.change_json(journal, lambda d: d.update(state="prepared"))
        self.artifact.write_bytes(before)  # simulate crash after rename, before artifact write
        self.index.write_text("corrupt live audit")  # recovery must not need today's audit
        self.cli("rollback")
        self.assertTrue(self.source.exists())

    def test_same_identity_symlink_reference_blocks(self):
        link = self.root / "dependent-link"
        link.symlink_to(self.source, target_is_directory=True)
        self.change_json(self.index, lambda d: d["identities"][0]["exposures"].append({"path": str(link)}))
        self.commit(self.repo)
        self.assert_blocked("referenced")

    def test_depth_three_requires_separate_placement_review(self):
        original = self.source
        self.source = self.skills / "mlops/evaluation/example"
        self.source.parent.mkdir(parents=True)
        original.rename(self.source)
        self.change_json(self.index, lambda d: d["identities"][0].update(
            exposures=[{"path": str(self.source)}], redundant_realpaths=[str(self.source)]))
        self.change_json(self.index, lambda d: d["redundant_copies"][0].update(realpath=str(self.source)))
        self.commit(self.repo)
        self.assert_blocked("depth-3")

    def test_fixture_bare_name_resolution_preserves_all_names_and_hashes(self):
        import cutover
        shared = self.root / "shared"
        unrelated = shared / "unrelated"
        unrelated.mkdir(parents=True)
        (unrelated / "SKILL.md").write_text("---\nname: unrelated\n---\nKeep")
        self.config.write_text(self.config.read_text() + f"    - {shared}\n")
        roots = [self.skills, self.farm, shared]
        before = cutover.resolution(roots)
        self.propose()
        self.approve()
        self.cli("apply")
        after = cutover.resolution(roots)
        self.assertEqual({k: v["package_sha256"] for k, v in before.items()},
                         {k: v["package_sha256"] for k, v in after.items()})
        self.assertEqual(after["example"]["realpath"], str(self.canonical))
        data = json.loads(self.artifact.read_text())
        self.assertEqual(data["resolution_before"], data["applied"]["resolution_after"])
        self.assertEqual(set(data["resolution_before"]["test"]), {"example", "unrelated"})
        self.cli("rollback")
        self.assertEqual(cutover.resolution(roots), before)

    def test_reference_from_store_identity_absent_from_audit_blocks(self):
        other = self.store / "local/other"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\n---\nCall skill_view(name='example')")
        store_manifest.generate(self.store)
        self.commit(self.store)
        exposure_farm.run("generate", self.store, self.exposures, self.farm)
        self.assert_blocked("referenced")

    def test_path_traversal_spelling_is_not_normalised(self):
        result = self.cli("propose", "--store", self.store, "--audit", self.index,
                          "--evidence", self.inventory, "--profile", "test",
                          "--path", str(self.skills) + "/./category/example", ok=False)
        self.assertIn("noncanonical", result.stdout)

    def test_artifact_directory_cannot_write_into_candidate(self):
        before = self.snapshot(self.source)
        self.out = self.source
        result = self.propose(ok=False)
        self.assertIn("artifact output overlaps", result.stdout)
        self.assertEqual(self.snapshot(self.source), before)

    def test_nonredundant_kinds_are_conservative(self):
        result = self.cli("propose", "--store", self.store, "--audit", self.index,
                          "--evidence", self.inventory, "--profile", "test", "--path", self.source,
                          "--kind", "excluded-root", "--generated-at", "2026-01-01T00:00:00+00:00")
        self.assertIn("excluded-root withdrawal", result.stdout)
        self.cli("apply", ok=False)
        self.assertTrue(self.source.exists())

    def test_vendor_licence_requires_preserved_notice(self):
        self.write(self.store / "store-meta.json", {"local/example": {"license": "MIT"}})
        store_manifest.generate(self.store)
        self.change_json(self.index, lambda d: d["identities"][0].update(license="MIT"))
        self.commit(self.store)
        self.commit(self.repo)
        self.assert_blocked("preservation unproven")

    def test_resolution_regression_automatically_rolls_back(self):
        self.change_json(self.policy, lambda d: d.update(foundation=[]))
        self.write(self.exposures, profile_policy.derive(profile_policy.load(self.policy),
                                                        profile_policy.identities(self.store)))
        exposure_farm.run("generate", self.store, self.exposures, self.farm, prune=True)
        self.cli("propose", "--store", self.store, "--audit", self.index,
                 "--evidence", self.inventory, "--profile", "test", "--path", self.source,
                 "--kind", "dead-exposure", "--generated-at", "2026-01-01T00:00:00+00:00")
        before = self.snapshot(self.source)
        self.approve()
        result = self.cli("apply", ok=False)
        self.assertIn("rollback restored exact bytes", result.stdout)
        self.assertEqual(before, self.snapshot(self.source))
        self.assertTrue(json.loads(self.artifact.read_text())["rollback"]["byte_exact"])

    def snapshot(self, root):
        return {str(p.relative_to(root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
                for p in [root, *root.rglob("*")]}


if __name__ == "__main__":
    unittest.main()
