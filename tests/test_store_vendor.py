"""Vendoring tests use real packages, git patch bases, and manifest verification."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import store_vendor as sv
import store_manifest as sm
from skill_audit import hash_package


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: alpha\n---\n\nOriginal\n")
        (self.source / "binary.dat").write_bytes(b"\x00\x01original")
        self.row = {"id": "local.alpha", "owner": "local", "name": "alpha", "aliases": [],
                    "canonical_source": str(self.source), "local_patch": False,
                    "provenance": {"repo": None, "commit": None},
                    "license": "LicenseRef-Proprietary",
                    "package_sha256": hash_package(self.source)[0]}
        self.plan = {"expected_identities": 1, "notices": {}, "patches": {}, "overrides": {}}
        self.output = self.root / "store"

    def run_vendor(self, rows=None, checkouts=None):
        index = self.root / "audit.json"
        index.write_text(json.dumps({"identities": rows or [self.row]}))
        self.plan["audit_sha256"] = hashlib.sha256(index.read_bytes()).hexdigest()
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(self.plan))
        sv.vendor(index, plan, self.output, checkouts or {})

    def test_complete_copy_aliases_dependencies_and_no_source_mutation(self):
        self.row.update(aliases=["other"], dependencies=["local.beta"])
        before = hash_package(self.source)[0]
        self.run_vendor()
        rows = sm.collect(self.output)
        self.assertEqual(1, len(rows))
        self.assertEqual(before, rows[0]["package_sha256"])
        self.assertEqual(["other"], rows[0]["aliases"])
        self.assertEqual(["local.beta"], rows[0]["dependencies"])
        self.assertEqual([], sm.verify(self.output))
        self.assertEqual(before, hash_package(self.source)[0])

    def test_source_drift_leaves_no_destination(self):
        (self.source / "SKILL.md").write_text("changed")
        with self.assertRaisesRegex(sv.VendorError, "source drift"):
            self.run_vendor()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_dangling_symlink_refused(self):
        self.output.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(sv.VendorError, "already exist"):
            self.run_vendor()
        self.assertTrue(self.output.is_symlink())

    def test_missing_license_fails_closed(self):
        self.row["license"] = None
        with self.assertRaisesRegex(sv.VendorError, "unresolved licence"):
            self.run_vendor()
        self.assertFalse(self.output.exists())

    def test_missing_notice_fails_closed(self):
        self.row["license"] = "MIT"
        with self.assertRaisesRegex(sv.VendorError, "missing upstream notice"):
            self.run_vendor()

    def test_version_selection(self):
        base = self.row
        patch = {**base, "local_patch": True}
        stale = {**base, "version_status": "superseded-stale-profile-copy"}
        self.assertEqual([patch], sv.select_versions([base, patch]))
        self.assertEqual([base], sv.select_versions([stale, base]))
        with self.assertRaisesRegex(sv.VendorError, "ambiguous"):
            sv.select_versions([base, base])

    def test_missing_patch_base_fails_closed(self):
        self.row["local_patch"] = True
        with self.assertRaisesRegex(sv.VendorError, "missing patch base"):
            self.run_vendor()

    def test_slug_collision_fails_closed(self):
        other = {**self.row, "name": "ALPHA", "id": "local.ALPHA"}
        self.plan["expected_identities"] = 2
        with self.assertRaisesRegex(sv.VendorError, "directory collision"):
            self.run_vendor([self.row, other])
        self.assertFalse(self.output.exists())

    def test_audit_pin_refused(self):
        index, plan = self.root / "audit.json", self.root / "plan.json"
        index.write_text("{}")
        plan.write_text(json.dumps({**self.plan, "audit_sha256": "wrong"}))
        with self.assertRaisesRegex(sv.VendorError, "audit differs"):
            sv.vendor(index, plan, self.output, {})

    def test_patch_ignores_global_crlf_and_package_filters(self):
        base, current = self.root / "base", self.root / "current"
        base.mkdir()
        current.mkdir()
        for root, text in ((base, b"before\r\n"), (current, b"after\r\n")):
            (root / "SKILL.md").write_bytes(text)
            (root / ".gitattributes").write_text("* text eol=lf\n")
        global_config = self.root / "gitconfig"
        global_config.write_text("[core]\n autocrlf = true\n")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(global_config)}):
            diff = sv.make_patch(base, current)
        subprocess.run(["git", "apply", "--binary", "-"], input=diff, cwd=base,
                       env=sv.git_environment(), check=True)
        self.assertEqual(hash_package(current)[0], hash_package(base)[0])

    def test_patch_reconstructs_binary_add_delete_and_mode_from_pinned_objects(self):
        repo = self.root / "upstream"
        package = repo / "skills" / "alpha"
        sv.copy_package(self.source, package)
        (package / "deleted.md").write_text("deleted upstream file")
        sv.git(repo, "init", "--quiet")
        sv.git(repo, "add", ".")
        sv.git(repo, "-c", "user.name=Test", "-c", "user.email=test@localhost",
               "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "base")
        commit = sv.git(repo, "rev-parse", "HEAD").decode().strip()
        # Even local archive attributes must not alter pinned raw objects.
        (repo / ".git/info/attributes").write_text("skills/alpha/deleted.md export-ignore\n")
        extracted = self.root / "extracted"
        sv.extract_base(repo, commit, "skills/alpha", extracted)
        self.assertEqual("deleted upstream file", (extracted / "deleted.md").read_text())
        (package / "SKILL.md").write_text("dirty checkout must not enter patch base")
        (self.source / "SKILL.md").write_text("---\nname: alpha\n---\n\nPatched\n")
        (self.source / "binary.dat").write_bytes(b"\x00\x02patched")
        (self.source / "run.sh").write_text("#!/bin/sh\necho ok\n")
        (self.source / "run.sh").chmod(0o755)
        self.row.update(local_patch=True, package_sha256=hash_package(self.source)[0])
        self.plan["patches"]["local.alpha"] = {"repo": "test/upstream", "commit": commit,
                                                "path": "skills/alpha"}
        self.run_vendor(checkouts={"test/upstream": repo})
        patch = self.output / "local/alpha/.skill-broker/local.patch"
        self.assertIn(b"GIT binary patch", patch.read_bytes())
        reconstruction = self.root / "reconstruction"
        sv.extract_base(repo, commit, "skills/alpha", reconstruction)
        subprocess.run(["git", "apply", str(patch)], cwd=reconstruction, check=True)
        self.assertEqual(hash_package(self.source)[0], hash_package(reconstruction)[0])
        self.assertTrue((reconstruction / "run.sh").stat().st_mode & 0o111)
        self.assertEqual([], sm.verify(self.output))
        self.assertEqual(commit, sm.collect(self.output)[0]["local_patch"]["upstream_commit"])


if __name__ == "__main__":
    unittest.main()
