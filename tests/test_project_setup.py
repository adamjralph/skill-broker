#!/usr/bin/env python3
"""Unit tests for the project-scoped setup script (#27).

Run from the repo root: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import project_setup as ps  # noqa: E402

BASE_CONFIG = """\
model:
  default: gpt-5.6-terra
skills:
  external_dirs:
    - /home/hermes/.agents/skills
    - /home/hermes/Documents/stillroom-wiki/skills
  creation_nudge_interval: 30
  disabled:
    - ai-content-kit-system
approvals:
  destructive_slash_confirm: false
"""


def make_skill(project: Path, rel: str, name: str) -> Path:
    d = project / rel / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n\n# {name}\n")
    return d


class ProjectSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = self.tmp / "proj"
        (self.project / ".git").mkdir(parents=True)
        self.config = self.tmp / "config.yaml"
        self.config.write_text(BASE_CONFIG)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_find_project_root_walks_up(self) -> None:
        nested = self.project / "a" / "b"
        nested.mkdir(parents=True)
        self.assertEqual(ps.find_project_root(nested), self.project.resolve())

    def test_setup_links_skills_and_trusts_root(self) -> None:
        make_skill(self.project, "agent-team/skills", "foo")
        make_skill(self.project, "", "bar")  # a skill at the project root
        ps.setup(self.project, self.config)

        target = self.project / ".agents" / "skills"
        self.assertTrue((target / "foo").is_symlink())
        self.assertTrue((target / "bar").is_symlink())
        self.assertEqual((target / "foo").resolve(), (self.project / "agent-team/skills/foo").resolve())
        # relative links, so the project stays movable
        self.assertFalse((target / "foo").readlink().is_absolute())

        data = yaml.safe_load(self.config.read_text())
        self.assertIn(str(self.project), data["skills"]["trusted_project_dirs"])
        self.assertTrue((self.project / ".agents" / ps.STATE_NAME).exists())

    def test_idempotent_rerun(self) -> None:
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        after_first = self.config.read_text()
        second = ps.setup(self.project, self.config)
        self.assertEqual(self.config.read_text(), after_first)
        self.assertTrue(any("already in skills.trusted_project_dirs" in m for m in second))
        self.assertTrue(any("skipped" in m for m in second))
        data = yaml.safe_load(self.config.read_text())
        self.assertEqual(data["skills"]["trusted_project_dirs"].count(str(self.project)), 1)

    def test_never_overwrites(self) -> None:
        make_skill(self.project, "agent-team/skills", "foo")
        target = self.project / ".agents" / "skills"
        (target / "foo").mkdir(parents=True)
        (target / "foo" / "KEEP").write_text("mine")
        ps.setup(self.project, self.config)
        self.assertFalse((target / "foo").is_symlink())
        self.assertEqual((target / "foo" / "KEEP").read_text(), "mine")

    def test_undo_reverses_exactly(self) -> None:
        before = self.config.read_text()
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        target = self.project / ".agents" / "skills"
        self.assertTrue((target / "foo").is_symlink())

        messages = ps.setup(self.project, self.config, undo=True)
        self.assertFalse((target / "foo").exists())
        self.assertEqual(self.config.read_text(), before)  # byte-for-byte reversal
        self.assertFalse((self.project / ".agents" / ps.STATE_NAME).exists())
        self.assertTrue(any("removed symlink" in m for m in messages))

    def test_undo_without_state(self) -> None:
        messages = ps.setup(self.project, self.config, undo=True)
        self.assertTrue(any("nothing to undo" in m for m in messages))
        self.assertEqual(self.config.read_text(), BASE_CONFIG)

    def test_config_preservation(self) -> None:
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        data = yaml.safe_load(self.config.read_text())
        original = yaml.safe_load(BASE_CONFIG)
        self.assertEqual(data["model"], original["model"])
        self.assertEqual(data["approvals"], original["approvals"])
        self.assertEqual(data["skills"]["external_dirs"], original["skills"]["external_dirs"])
        self.assertEqual(data["skills"]["disabled"], original["skills"]["disabled"])
        self.assertEqual(data["skills"]["creation_nudge_interval"], 30)

    def test_existing_trusted_dirs_appended_and_preserved(self) -> None:
        text = BASE_CONFIG.replace(
            "  creation_nudge_interval: 30",
            "  trusted_project_dirs:\n    - /existing/project\n  creation_nudge_interval: 30",
        )
        self.config.write_text(text)
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        dirs = yaml.safe_load(self.config.read_text())["skills"]["trusted_project_dirs"]
        self.assertEqual(dirs, ["/existing/project", str(self.project)])

        ps.setup(self.project, self.config, undo=True)
        dirs = yaml.safe_load(self.config.read_text())["skills"]["trusted_project_dirs"]
        self.assertEqual(dirs, ["/existing/project"])

    def test_preexisting_trusted_root_not_removed_on_undo(self) -> None:
        text = BASE_CONFIG.replace(
            "  creation_nudge_interval: 30",
            f"  trusted_project_dirs:\n    - {self.project}\n  creation_nudge_interval: 30",
        )
        self.config.write_text(text)
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        ps.setup(self.project, self.config, undo=True)
        dirs = yaml.safe_load(self.config.read_text())["skills"]["trusted_project_dirs"]
        self.assertEqual(dirs, [str(self.project)])

    def test_setup_creates_a_missing_skills_block(self) -> None:
        text = "model:\n  default: gpt-5.6-terra\napprovals:\n  destructive_slash_confirm: false\n"
        self.config.write_text(text)
        before = self.config.read_bytes()
        make_skill(self.project, "agent-team/skills", "foo")

        ps.setup(self.project, self.config)

        self.assertEqual(
            self.config.read_text(),
            text + "skills:\n  trusted_project_dirs:\n" f"    - {self.project}\n",
        )
        self.assertIn(
            str(self.project),
            yaml.safe_load(self.config.read_text())["skills"]["trusted_project_dirs"],
        )
        state = json.loads((self.project / ".agents" / ps.STATE_NAME).read_text())
        self.assertTrue(state["created_skills_block"])

        ps.setup(self.project, self.config, undo=True)
        self.assertEqual(self.config.read_bytes(), before)  # byte-for-byte reversal

    def test_reapplying_a_created_block_keeps_undo_exact(self) -> None:
        self.config.write_text("model:\n  default: gpt-5.6-terra\n")
        before = self.config.read_bytes()
        make_skill(self.project, "agent-team/skills", "foo")

        ps.setup(self.project, self.config)
        after_first = self.config.read_text()
        ps.setup(self.project, self.config)

        self.assertEqual(self.config.read_text(), after_first)
        state = json.loads((self.project / ".agents" / ps.STATE_NAME).read_text())
        self.assertTrue(state["created_skills_block"])
        ps.setup(self.project, self.config, undo=True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_existing_skills_block_is_not_flagged_as_created(self) -> None:
        make_skill(self.project, "agent-team/skills", "foo")
        before = self.config.read_bytes()

        ps.setup(self.project, self.config)

        state = json.loads((self.project / ".agents" / ps.STATE_NAME).read_text())
        self.assertFalse(state["created_skills_block"])
        ps.setup(self.project, self.config, undo=True)
        self.assertEqual(self.config.read_bytes(), before)

    def test_undo_of_a_modified_created_block_fails_closed(self) -> None:
        self.config.write_text("model:\n  default: gpt-5.6-terra\n")
        make_skill(self.project, "agent-team/skills", "foo")
        ps.setup(self.project, self.config)
        edited = self.config.read_text().replace(
            "skills:\n", "skills:\n  external_dirs:\n    - /x\n"
        )
        self.config.write_text(edited)
        with self.assertRaisesRegex(ps.SetupError, "modified"):
            ps.setup(self.project, self.config, undo=True)
        self.assertEqual(self.config.read_text(), edited)

    def test_inline_skills_key_fails_closed_rather_than_duplicating(self) -> None:
        text = "model:\n  default: gpt-5.6-terra\nskills: {}\n"
        self.config.write_text(text)
        make_skill(self.project, "agent-team/skills", "foo")
        with self.assertRaisesRegex(ps.SetupError, "inline"):
            ps.setup(self.project, self.config)
        self.assertEqual(self.config.read_text(), text)


if __name__ == "__main__":
    unittest.main()
