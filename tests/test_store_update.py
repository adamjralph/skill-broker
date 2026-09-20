import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import store_manifest as manifest  # noqa: E402
import store_update as su  # noqa: E402
import store_vendor as vendor  # noqa: E402

SKILL = "---\nname: {name}\ndescription: test skill\n---\n\n{body}\n"


class UpdateFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.upstream = self.root / "upstream"
        self.store = self.root / "store"
        self._init_repo(self.upstream)

    def tearDown(self):
        self._tmp.cleanup()

    # -- fixture helpers ---------------------------------------------------

    def _init_repo(self, path):
        path.mkdir(parents=True, exist_ok=True)
        vendor.git(path, "init", "--quiet", "--template=")

    def _commit(self, repo, message):
        vendor.git(repo, "add", "--all", "--force")
        vendor.git(repo, "-c", "user.name=Test", "-c", "user.email=t@localhost",
                   "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
                   "commit", "--quiet", "-m", message)
        return vendor.git(repo, "rev-parse", "HEAD").decode().strip()

    def _head(self, repo):
        return vendor.git(repo, "rev-parse", "HEAD").decode().strip()

    def write_skill(self, repo, subdir, name, body, files=None):
        d = repo / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(SKILL.format(name=name, body=body))
        for rel, text in (files or {}).items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def materialize(self, commit, subdir, dest):
        su.materialize(self.upstream, commit, subdir, dest)
        return dest

    def build_store(self, subdir, name, content_dir, *, repo="acme/skills",
                    upstream_commit=None, path="skills/foo", patch=None,
                    license_id="MIT"):
        rel = f"acme/{name}"
        dest = self.store / rel
        su.copy_content(content_dir, dest)
        if patch is not None:
            artifact = dest / su.PATCH_PATH
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(patch)
        local_patch = ({"bool": True, "upstream_commit": upstream_commit, "path": su.PATCH_PATH}
                       if patch is not None
                       else {"bool": False, "upstream_commit": None})
        provenance = {"repo": repo, "commit": upstream_commit}
        if path is not None:
            provenance["path"] = path
        meta = {rel: {"name": name, "aliases": [], "license": license_id,
                      "provenance": provenance, "local_patch": local_patch,
                      "dependencies": []}}
        (self.store / manifest.META_NAME).write_text(manifest.serialize(meta))
        manifest.generate(self.store)
        self._init_repo(self.store)
        self._commit(self.store, "initial")
        return rel

    def checkouts(self):
        return {"acme/skills": self.upstream}

    # -- tests -------------------------------------------------------------

    def test_fast_forward_update(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         upstream_commit=c1)
        head0 = self._head(self.store)
        self.write_skill(self.upstream, "skills/foo", "foo", "version two")
        c2 = self._commit(self.upstream, "c2")

        result = su.update(self.store, "acme.foo", self.checkouts())

        self.assertEqual("updated", result.status)
        self.assertEqual(c2, result.new_commit)
        self.assertNotEqual(result.old_version, result.new_version)
        self.assertIn("version two", (self.store / "acme/foo/SKILL.md").read_text())
        self.assertEqual([], manifest.verify(self.store))
        self.assertEqual(head0, vendor.git(self.store, "rev-parse", "HEAD~1").decode().strip())
        row = next(r for r in manifest.collect(self.store) if r["id"] == "acme.foo")
        self.assertEqual(result.new_version, row["package_sha256"])
        self.assertEqual(c2, manifest.load_meta(self.store)["acme/foo"]["provenance"]["commit"])

    def test_no_op_when_target_is_current(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         upstream_commit=c1)
        head0 = self._head(self.store)

        result = su.update(self.store, "acme.foo", self.checkouts())

        self.assertEqual("no-op", result.status)
        self.assertEqual(c1, result.new_commit)
        self.assertIsNone(result.commit)
        self.assertEqual(head0, self._head(self.store))

    def test_path_discovery_by_frontmatter_name(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         upstream_commit=c1, path=None)
        self.write_skill(self.upstream, "skills/foo", "foo", "version two")
        c2 = self._commit(self.upstream, "c2")

        result = su.update(self.store, "acme.foo", self.checkouts())

        self.assertEqual("updated", result.status)
        self.assertEqual("skills/foo", result.upstream_path)
        self.assertIn("version two", (self.store / "acme/foo/SKILL.md").read_text())

    def test_clean_merge_preserves_local_patch(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one",
                         files={"notes.md": "upstream notes\n"})
        c1 = self._commit(self.upstream, "c1")
        base = self.materialize(c1, "skills/foo", self.root / "c1")
        local = self.root / "local"
        su.copy_content(base, local)
        (local / "SKILL.md").write_text(SKILL.format(name="foo", body="version one\nlocal edit"))
        patch = vendor.make_patch(base, local)
        self.build_store("skills/foo", "foo", local, upstream_commit=c1, patch=patch)

        self.write_skill(self.upstream, "skills/foo", "foo", "version one",
                         files={"notes.md": "upstream notes\n", "extra.md": "new upstream file\n"})
        c2 = self._commit(self.upstream, "c2")

        result = su.update(self.store, "acme.foo", self.checkouts())

        self.assertEqual("updated", result.status)
        self.assertIn("local edit", (self.store / "acme/foo/SKILL.md").read_text())
        self.assertEqual("new upstream file\n", (self.store / "acme/foo/extra.md").read_text())
        self.assertTrue((self.store / "acme/foo" / su.PATCH_PATH).is_file())
        meta = manifest.load_meta(self.store)["acme/foo"]
        self.assertEqual(c2, meta["local_patch"]["upstream_commit"])
        self.assertEqual(c2, meta["provenance"]["commit"])
        self.assertEqual([], manifest.verify(self.store))

    def test_conflict_fails_closed(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "alpha")
        c1 = self._commit(self.upstream, "c1")
        base = self.materialize(c1, "skills/foo", self.root / "c1")
        local = self.root / "local"
        su.copy_content(base, local)
        (local / "SKILL.md").write_text(SKILL.format(name="foo", body="alpha local"))
        patch = vendor.make_patch(base, local)
        self.build_store("skills/foo", "foo", local, upstream_commit=c1, patch=patch)
        meta_bytes = (self.store / manifest.META_NAME).read_bytes()
        manifest_bytes = (self.store / manifest.MANIFEST_NAME).read_bytes()
        head0 = self._head(self.store)

        self.write_skill(self.upstream, "skills/foo", "foo", "alpha upstream")
        self._commit(self.upstream, "c2")
        report = self.root / "report.patch"

        result = su.update(self.store, "acme.foo", self.checkouts(), report=report)

        self.assertEqual("conflict", result.status)
        self.assertTrue(report.is_file())
        self.assertIn("conflicted files", report.read_text())
        self.assertEqual(meta_bytes, (self.store / manifest.META_NAME).read_bytes())
        self.assertEqual(manifest_bytes, (self.store / manifest.MANIFEST_NAME).read_bytes())
        self.assertEqual(head0, self._head(self.store))
        self.assertIn("alpha local", (self.store / "acme/foo/SKILL.md").read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            code = su.main(["update", "--store", str(self.store), "--id", "acme.foo",
                            "--checkout", f"acme/skills={self.upstream}",
                            "--report", str(report)])
        self.assertEqual(2, code)

    def test_repo_head_provenance_with_unchanged_skill_is_no_op(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        base = self.materialize(c1, "skills/foo", self.root / "c1")
        (self.upstream / "readme.md").write_text("unrelated\n")
        c2 = self._commit(self.upstream, "c2")
        self.build_store("skills/foo", "foo", base, upstream_commit=c2)
        head0 = self._head(self.store)

        result = su.update(self.store, "acme.foo", self.checkouts())

        self.assertEqual("no-op", result.status)
        self.assertEqual(c1, result.new_commit)
        self.assertIsNone(result.commit)
        self.assertEqual(head0, self._head(self.store))

    def test_unrecorded_divergence_fails_closed(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        base = self.materialize(c1, "skills/foo", self.root / "c1")
        divergent = self.root / "divergent"
        su.copy_content(base, divergent)
        (divergent / "SKILL.md").write_text(SKILL.format(name="foo", body="version one local"))
        self.build_store("skills/foo", "foo", divergent, upstream_commit=c1)
        self.write_skill(self.upstream, "skills/foo", "foo", "version two")
        self._commit(self.upstream, "c2")

        with self.assertRaisesRegex(su.UpdateError, "diverges"):
            su.update(self.store, "acme.foo", self.checkouts())

    def test_pseudo_owner_is_unsupported(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         repo=None, upstream_commit=None, path=None)
        with self.assertRaisesRegex(su.UpdateError, "pseudo-owner"):
            su.update(self.store, "acme.foo", self.checkouts())

    def test_dirty_store_is_refused(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         upstream_commit=c1)
        (self.store / "acme/foo/extra.md").write_text("dirty\n")
        with self.assertRaisesRegex(su.UpdateError, "uncommitted"):
            su.update(self.store, "acme.foo", self.checkouts())

    def test_cli_update(self):
        self.write_skill(self.upstream, "skills/foo", "foo", "version one")
        c1 = self._commit(self.upstream, "c1")
        self.build_store("skills/foo", "foo", self.materialize(c1, "skills/foo", self.root / "c1"),
                         upstream_commit=c1)
        self.write_skill(self.upstream, "skills/foo", "foo", "version two")
        self._commit(self.upstream, "c2")

        with contextlib.redirect_stdout(io.StringIO()):
            code = su.main(["update", "--store", str(self.store), "--id", "acme.foo",
                            "--checkout", f"acme/skills={self.upstream}"])

        self.assertEqual(0, code)
        self.assertIn("version two", (self.store / "acme/foo/SKILL.md").read_text())


if __name__ == "__main__":
    unittest.main()
