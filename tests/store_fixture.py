"""Shared temporary Skill Store fixture for the broker suites (ticket #45).

Stands up a real store shape on a temporary directory — identity directories, an authored
``store-meta.json``, a generated and verified Store Manifest, and one authored Profile Policy —
so broker tests exercise the artifacts the store tooling actually produces instead of
hand-rolled mocks. Built only from the existing store tooling (ADR-0011, ADR-0018); this module
is a fixture, not a test, so the ``test*.py`` discovery pattern leaves it alone.

The default store founds the distinct-named identity and Brokers the aliased one, so a caller
gets both a Foundation Set and a Withheld Skill — the two sides of the policy line the broker
sits on.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import profile_policy as pp  # noqa: E402
import store_manifest as sm  # noqa: E402

DEFAULT_PROFILE = "broker-test"


@dataclass(frozen=True)
class Identity:
    """One authored store identity: a ``<owner>/<slug>/SKILL.md`` plus its manifest metadata.

    ``name`` is the Skill's Name (the profile-relative handle Hermes keys on); ``aliases`` are
    the extra handles that identify the same content without creating a second identity.
    """

    owner: str
    slug: str
    name: str
    body: str
    aliases: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.owner}.{self.name}"

    @property
    def path(self) -> str:
        return f"{self.owner}/{self.slug}"


# The aliased identity: one content-addressed identity reachable by extra handles.
ALIASED = Identity(
    owner="local",
    slug="writing-method",
    name="writing-method",
    aliases=("local-writing", "writing"),
    body="# Writing method\n\nA deterministic writing procedure.\n",
)

# The distinct-named identity: no aliases, so its Name is the only handle it answers to.
DISTINCT = Identity(
    owner="nousresearch",
    slug="hermes-agent",
    name="hermes-agent",
    body="# Hermes agent\n\nOperating the Hermes agent loop.\n",
)


@dataclass
class StoreFixture:
    """A temporary store plus the artifacts the store tooling generates for it."""

    root: Path
    profile: str
    policy_path: Path
    _tmp: tempfile.TemporaryDirectory

    def manifest_path(self) -> Path:
        return self.root / sm.MANIFEST_NAME

    def manifest(self) -> dict:
        return json.loads(self.manifest_path().read_text())

    def identities(self) -> dict[str, dict]:
        """The manifest's ``{id: row}``, exactly as the verifier reads it."""
        return {row["id"]: row for row in self.manifest()["identities"]}

    def policy(self) -> dict:
        return pp.load(self.policy_path)

    def close(self) -> None:
        self._tmp.cleanup()

    def __enter__(self) -> "StoreFixture":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def make_store(
    *,
    identities: Sequence[Identity] = (DISTINCT, ALIASED),
    profile: str = DEFAULT_PROFILE,
    foundation: Sequence[str] | None = None,
    brokered: Sequence[str] | None = None,
    denied: Sequence[str] = (),
    preferred: Sequence[str] = (),
) -> StoreFixture:
    """Build a temporary store that already passes the existing manifest and policy validators.

    Callers own the result's lifetime: ``close()`` it, or use it as a context manager.
    """
    foundation = [DISTINCT.id] if foundation is None else list(foundation)
    brokered = [ALIASED.id] if brokered is None else list(brokered)

    tmp = tempfile.TemporaryDirectory(prefix="skill-broker-store-")
    root = Path(tmp.name) / "store"
    root.mkdir()

    meta: dict[str, dict] = {}
    for identity in identities:
        write_identity(root, identity)
        meta[identity.path] = {"name": identity.name, "aliases": list(identity.aliases),
                               "dependencies": list(identity.dependencies)}
    (root / sm.META_NAME).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    sm.generate(root)

    policy_path = write_policy(root, profile, foundation=foundation, brokered=brokered,
                               denied=list(denied), preferred=list(preferred))
    return StoreFixture(root=root, profile=profile, policy_path=policy_path, _tmp=tmp)


def write_identity(store: Path, identity: Identity) -> Path:
    """Write one ``<owner>/<slug>/SKILL.md`` under ``store``; returns its directory."""
    skill_dir = store / identity.path
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {identity.name}\ndescription: fixture identity\n---\n\n{identity.body}"
    )
    return skill_dir


def write_policy(
    store: Path,
    profile: str,
    *,
    foundation: Sequence[str],
    brokered: Sequence[str],
    preferred: Sequence[str] = (),
    denied: Sequence[str] = (),
    **extra: object,
) -> Path:
    """Write ``<store>/policies/<profile>.json`` naming the Foundation Set and Brokered Allowlist."""
    policy: dict = {
        "policy_version": pp.POLICY_VERSION,
        "profile": profile,
        "foundation": [{"id": ident} for ident in foundation],
        "brokered": list(brokered),
        "preferred": list(preferred),
        "denied": list(denied),
    }
    policy.update(extra)
    policies = store / pp.POLICIES_DIR
    policies.mkdir(exist_ok=True)
    path = policies / f"{profile}.json"
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n")
    return path


class StoreFixtureTestCase(unittest.TestCase):
    """``setUp`` builds a default fixture at ``self.store`` and tears it down afterwards."""

    store: StoreFixture

    def setUp(self) -> None:
        self.store = make_store()
        self.addCleanup(self.store.close)

    def make_store(self, **overrides) -> StoreFixture:
        """Build a further fixture with these overrides; cleaned up with the test."""
        fixture = make_store(**overrides)
        self.addCleanup(fixture.close)
        return fixture
