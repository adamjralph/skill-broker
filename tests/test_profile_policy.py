"""Exercise profile-policy validation and Exposure Manifest derivation (ADR-0018)."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import profile_policy as pp
import store_manifest as sm

# Independent fixture: minimal store rows, only the fields the policy logic reads.
IDENTITIES = {
    "one.writing": {"name": "writing"},
    "two.writing": {"name": "writing"},
    "one.other": {"name": "other"},
    "three.unique": {"name": "unique"},
}


def policy(**overrides):
    base = {"policy_version": 1, "profile": "p", "foundation": [{"id": "one.writing"}]}
    base.update(overrides)
    return base


class ValidateTests(unittest.TestCase):
    def test_valid_policy_has_no_problems(self):
        self.assertEqual(pp.validate(policy(), IDENTITIES, filename="p"), [])

    def test_unknown_ids_in_every_set_are_reported(self):
        p = policy(foundation=[{"id": "missing.one"}], brokered=["missing.two"],
                   preferred=["missing.three"], denied=["missing.four"])
        problems = pp.validate(p, IDENTITIES, filename="p")
        for ident in ("missing.one", "missing.two", "missing.three", "missing.four"):
            self.assertTrue(any(ident in q for q in problems), (ident, problems))

    def test_sets_must_be_pairwise_disjoint(self):
        p = policy(foundation=[{"id": "one.writing"}], brokered=["one.writing"],
                   preferred=["one.other"], denied=["one.other"])
        problems = pp.validate(p, IDENTITIES, filename="p")
        self.assertTrue(any("both foundation and brokered" in q for q in problems), problems)
        self.assertTrue(any("both preferred and denied" in q for q in problems), problems)

    def test_duplicate_id_within_one_set_is_reported(self):
        p = policy(foundation=[{"id": "one.writing"}, {"id": "one.writing"}])
        problems = pp.validate(p, IDENTITIES, filename="p")
        self.assertTrue(any("appears twice in foundation" in q for q in problems), problems)

    def test_profile_must_match_filename(self):
        problems = pp.validate(policy(profile="other"), IDENTITIES, filename="p")
        self.assertTrue(any("does not match filename" in q for q in problems), problems)

    def test_bad_schema_fails_closed(self):
        cases = {
            "version": policy(policy_version=2),
            "unknown-key": policy(nonsense=1),
            "foundation-not-list": policy(foundation={"id": "one.writing"}),
            "bad-entry": policy(foundation=[{"id": "one.writing", "extra": 1}]),
            "empty-id": policy(foundation=[{"id": ""}]),
            "brokered-not-list": policy(brokered="one.other"),
            "bad-alias": policy(foundation=[{"id": "one.writing", "exposure": "../escape"}]),
        }
        for label, p in cases.items():
            with self.subTest(label=label):
                self.assertTrue(pp.validate(p, IDENTITIES, filename="p"), label)

    def test_project_wired_entry_is_valid_and_must_not_shadow(self):
        ok = policy(foundation=[{"id": "three.unique"}, {"project": "/p", "name": "method"}])
        self.assertEqual(pp.validate(ok, IDENTITIES, filename="p"), [])
        clash = policy(foundation=[{"id": "one.writing"}, {"project": "/p", "name": "writing"}])
        problems = pp.validate(clash, IDENTITIES, filename="p")
        self.assertTrue(any("shadows foundation" in q for q in problems), problems)

    def test_validate_is_deterministic(self):
        p = policy(foundation=[{"id": "missing.one"}], brokered=["missing.two"])
        self.assertEqual(pp.validate(p, IDENTITIES, filename="p"),
                         pp.validate(p, IDENTITIES, filename="p"))


class DeriveTests(unittest.TestCase):
    def test_derive_emits_foundation_only_sorted_by_name(self):
        p = policy(foundation=[{"id": "three.unique"}, {"id": "one.writing"}],
                   brokered=["one.other"])
        self.assertEqual(pp.derive(p, IDENTITIES, filename="p"), {
            "manifest_version": 1, "consumer": "p",
            "exposures": [{"name": "unique", "id": "three.unique"},
                          {"name": "writing", "id": "one.writing"}]})

    def test_derive_uses_explicit_exposure_alias(self):
        p = policy(foundation=[{"id": "one.writing", "exposure": "renamed"}])
        self.assertEqual(pp.derive(p, IDENTITIES, filename="p")["exposures"],
                         [{"name": "renamed", "id": "one.writing"}])

    def test_derive_excludes_project_wired_entries(self):
        p = policy(foundation=[{"project": "/p", "name": "method"}])
        self.assertEqual(pp.derive(p, IDENTITIES, filename="p")["exposures"], [])

    def test_brokered_preferred_denied_never_enter_the_manifest(self):
        p = policy(foundation=[{"id": "one.writing"}], brokered=["one.other"],
                   preferred=["three.unique"], denied=["two.writing"])
        emitted = {row["id"] for row in pp.derive(p, IDENTITIES, filename="p")["exposures"]}
        self.assertEqual(emitted, {"one.writing"})

    def test_name_collision_fails_closed_naming_both_ids(self):
        p = policy(foundation=[{"id": "one.writing"}, {"id": "two.writing"}])
        with self.assertRaises(pp.PolicyError) as ctx:
            pp.derive(p, IDENTITIES, filename="p")
        self.assertIn("one.writing", str(ctx.exception))
        self.assertIn("two.writing", str(ctx.exception))

    def test_alias_collision_fails_closed(self):
        p = policy(foundation=[{"id": "one.writing"}, {"id": "three.unique", "exposure": "writing"}])
        with self.assertRaisesRegex(pp.PolicyError, "one.writing and three.unique"):
            pp.derive(p, IDENTITIES, filename="p")

    def test_invalid_policy_raises_before_emitting(self):
        with self.assertRaises(pp.PolicyError):
            pp.derive(policy(foundation=[{"id": "missing.one"}]), IDENTITIES, filename="p")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        for owner, body in (("one", "one body"), ("three", "three body")):
            for slug, name in (("writing", "writing"), ("unique", "unique")):
                skill = self.store / owner / slug
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n{owner} {body}\n")
        self.policies = self.store / pp.POLICIES_DIR
        self.policies.mkdir()
        sm.generate(self.store)

    def write_policy(self, stem, **overrides):
        path = self.policies / f"{stem}.json"
        path.write_text(json.dumps(policy(profile=stem, **overrides)))
        return path

    def test_run_validate_scans_the_store_policies(self):
        self.write_policy("p")
        result = pp.run("validate", self.store)
        self.assertTrue(result["ok"])
        self.assertEqual(result["policies"], 1)

    def test_duplicate_profile_is_rejected(self):
        self.write_policy("p")
        # A second file claiming profile "p" (its filename stem differs, so both checks fire).
        (self.policies / "q.json").write_text(json.dumps(policy(profile="p")))
        with self.assertRaises(pp.PolicyError) as ctx:
            pp.run("validate", self.store)
        self.assertIn("profile 'p' claimed by", str(ctx.exception))

    def test_single_policy_validate_reports_a_sibling_claim(self):
        path = self.write_policy("p")
        (self.policies / "q.json").write_text(json.dumps(policy(profile="p")))
        with self.assertRaisesRegex(pp.PolicyError, "also claimed by q.json"):
            pp.run("validate", self.store, policy=path)

    def test_derive_out_writes_the_manifest(self):
        path = self.write_policy("p", foundation=[{"id": "one.writing"}])
        out = self.root / "manifest.json"
        result = pp.run("derive", self.store, policy=path, out=out)
        self.assertEqual(result["out"], str(out))
        self.assertEqual(json.loads(out.read_text()), {
            "manifest_version": 1, "consumer": "p",
            "exposures": [{"name": "writing", "id": "one.writing"}]})

    def test_store_manifest_drift_fails_closed(self):
        self.write_policy("p")
        (self.store / "one" / "writing" / "SKILL.md").write_text(
            "---\nname: writing\n---\nchanged\n")
        with self.assertRaisesRegex(pp.PolicyError, "drift"):
            pp.run("validate", self.store)

    def test_cli_exit_codes_and_json(self):
        self.write_policy("p")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(pp.main(["validate", "--store", str(self.store)]), 0)
        self.assertTrue(json.loads(buf.getvalue())["ok"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(pp.main(["derive", "--store", str(self.store),
                                      "--policy", str(self.policies / "missing.json")]), 1)
        self.assertFalse(json.loads(buf.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
