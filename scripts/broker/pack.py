"""Skill Pack assembly and Pack Delivery modes (ADR-0010, ticket #49).

A **Skill Pack** is the granted Resolved Skill Versions plus their Dependency Closure,
assembled so the whole Pack is delivered: inline at or below the effective inline budget, by
reference above it. The budget is the runtime hook cap minus the Adapter's reserve (default
10,000 - 500 = 9,500 characters over the whole returned context); the Profile Policy may lower
it and never raise it. With spill disabled the effective cap is unbounded and the Pack is
inline in full.

A Pack carries complete ``SKILL.md`` bodies only. ``references/``, ``scripts/`` and
``templates/`` stay the Skill's progressive-disclosure layer and are read on demand. The
delivered bytes are verified against the Store-resolved canonical hashes before anything is
assembled: a mismatch rejects the Pack and names the source. Ordering puts the framing and the
Primary's opening at the head and the identifiers, versions, hashes, paths and the closure at
the tail, so Hermes's native spill preview is the useful part.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import skill_audit

from .closure import dependency_closure
from .types import PackDelivery, ResolvedSkillVersion

DEFAULT_HOOK_CAP = 10_000
DEFAULT_ADAPTER_RESERVE = 500
PACK_MARKER = "SKILL-BROKER PACK"

_FRAMING = (
    "{marker} profile={profile}\n"
    "Granted Skill guidance for this turn. The complete SKILL.md bodies follow; each Skill's "
    "references/, scripts/ and templates/ stay in its directory and are read only when needed.\n"
)


@dataclass(frozen=True)
class HookConfig:
    """The Adapter's view of the host's hook output-spill configuration (ADR-0010).

    The Adapter resolves these at runtime rather than setting them, so the broker budgets
    against the host's real configuration. ``reserve`` is the Adapter's own overhead allowance;
    ``spill`` is whether Hermes's native spill is enabled at all.
    """

    hook_cap: int = DEFAULT_HOOK_CAP
    reserve: int = DEFAULT_ADAPTER_RESERVE
    spill: bool = True

    @property
    def inline_budget(self) -> int | None:
        """The runtime-derived inline budget, or ``None`` when spill is disabled (unbounded)."""
        if not self.spill:
            return None
        return max(0, self.hook_cap - self.reserve)


@dataclass(frozen=True)
class Budget:
    """The effective spill configuration recorded on every Route Decision (ADR-0010)."""

    spill: bool
    hook_cap: int | None
    reserve: int | None
    policy_limit: int | None
    effective: int | None

    @property
    def unbounded(self) -> bool:
        return self.effective is None

    def to_record(self) -> dict:
        return {
            "spill": self.spill,
            "unbounded": self.unbounded,
            "hook_cap": self.hook_cap,
            "reserve": self.reserve,
            "policy_limit": self.policy_limit,
            "effective_inline_chars": self.effective,
        }


@dataclass(frozen=True)
class SkillContent:
    """One verified Skill's delivered content: its identity, directory and ``SKILL.md``."""

    identity: ResolvedSkillVersion
    path: Path
    body: str


@dataclass(frozen=True)
class Pack:
    """An assembled Pack, its Delivery mode, its verified contents and the budget it used."""

    text: str
    delivery: PackDelivery
    contents: tuple[SkillContent, ...]
    budget: Budget

    @property
    def hashes(self) -> tuple[str, ...]:
        """The Resolved Skill Version hashes the Pack supplied, for the Session Ledger."""
        return tuple(content.identity.version for content in self.contents)


def resolve_budget(hook: HookConfig, policy: Mapping | None) -> tuple[Budget, tuple[str, ...]]:
    """Resolve the effective inline budget, refusing a policy that raises the runtime budget.

    Returns ``(budget, problems)``. A policy value above the runtime-derived budget is refused
    (the runtime budget is the ceiling the Adapter may not inflate); a policy value at or below
    it lowers the budget and is honoured.
    """
    runtime = hook.inline_budget
    limit, problems = _policy_limit(policy)
    base = {"spill": hook.spill, "hook_cap": hook.hook_cap, "reserve": hook.reserve,
            "policy_limit": limit}
    if problems:
        return Budget(effective=runtime, **base), problems
    if runtime is not None and limit is not None and limit > runtime:
        return Budget(effective=runtime, **base), (
            f"limits.max_injected_chars {limit} raises the runtime inline budget {runtime}",)
    if runtime is None:
        return Budget(effective=None, **base), ()
    effective = runtime if limit is None else min(runtime, limit)
    return Budget(effective=effective, **base), ()


def delivery_for(chars: int, budget: Budget) -> PackDelivery:
    """Inline at or below the effective budget; by reference above it; always inline unbounded."""
    if budget.unbounded or chars <= budget.effective:
        return PackDelivery.INLINE
    return PackDelivery.BY_REFERENCE


def read_skill(store: Path | str, row: Mapping) -> tuple[SkillContent | None, tuple[str, ...]]:
    """Read and verify one Skill's content, or ``(None, problems)`` naming the source.

    The delivered bytes must reproduce the Store-resolved canonical package hash and the
    recorded ``SKILL.md`` hash; anything else rejects the Pack rather than delivering drift.
    """
    directory = Path(store) / row["path"]
    try:
        raw = (directory / "SKILL.md").read_bytes()
    except OSError as exc:
        return None, (f"cannot read SKILL.md for {row['id']} at {directory}: {exc}",)

    package_sha, _skill_md_sha, _count, _rels = skill_audit.hash_package(directory)
    delivered_sha = hashlib.sha256(raw).hexdigest()
    problems: list[str] = []
    if package_sha != row.get("package_sha256"):
        problems.append(f"content hash mismatch for {row['id']} at {directory}: "
                        f"package {package_sha} != store {row.get('package_sha256')}")
    if delivered_sha != row.get("skill_md_sha256"):
        problems.append(f"content hash mismatch for {row['id']} at {directory}: "
                        f"SKILL.md {delivered_sha} != store {row.get('skill_md_sha256')}")
    try:
        # Strict, so the delivered text's own encoding is the bytes the hash attests.
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"SKILL.md for {row['id']} at {directory} is not valid UTF-8: {exc}")
        body = ""
    if problems:
        return None, tuple(problems)
    return SkillContent(
        identity=ResolvedSkillVersion(id=row["id"], name=row["name"],
                                      version=row["package_sha256"]),
        path=directory,
        body=body,
    ), ()


def build_pack(*, store: Path | str, profile: str, primary: ResolvedSkillVersion,
               identities: Mapping[str, Mapping], denied: Sequence[str] = (),
               budget: Budget) -> tuple[Pack | None, tuple[str, ...]]:
    """Assemble the complete Pack for ``primary``, or ``(None, problems)`` failing it closed.

    The Primary's Dependency Closure is resolved against the verified Store Manifest; a missing,
    denied or cyclic closure fails the Pack closed with the reason recorded (ADR-0006/B7). Every
    content in the Pack is verified before any text is rendered, so a Pack is complete or absent.
    """
    ids, problems = dependency_closure(primary.id, identities, denied)
    if problems:
        return None, problems

    contents: list[SkillContent] = []
    for ident in ids:
        content, content_problems = read_skill(store, identities[ident])
        if content_problems:
            return None, content_problems
        contents.append(content)

    inline = render(profile, contents, PackDelivery.INLINE)
    delivery = delivery_for(len(inline), budget)
    text = inline if delivery is PackDelivery.INLINE \
        else render(profile, contents, PackDelivery.BY_REFERENCE)
    return Pack(text=text, delivery=delivery, contents=tuple(contents), budget=budget), ()


def render(profile: str, contents: Sequence[SkillContent], delivery: PackDelivery) -> str:
    """Render the Pack: framing and the Primary at the head, the manifest at the tail."""
    parts = [_FRAMING.format(marker=PACK_MARKER, profile=profile)]
    for content in contents:
        identity = content.identity
        parts.append(f"## {identity.name} ({identity.id} @ {identity.version})\n")
        parts.append(content.body.rstrip("\n") + "\n")
    parts.append(_manifest(profile, contents, delivery))
    return "\n".join(parts)


def _manifest(profile: str, contents: Sequence[SkillContent], delivery: PackDelivery) -> str:
    primary = contents[0].identity
    lines = [
        "--- pack manifest ---",
        f"primary={primary.id}@{primary.version}",
        f"profile={profile} delivery={delivery.value}",
        "closure: " + ", ".join(content.identity.id for content in contents),
        "skills:",
    ]
    lines.extend(f"- {content.identity.id}@{content.identity.version} path={content.path}"
                 for content in contents)
    return "\n".join(lines) + "\n"


def _policy_limit(policy: Mapping | None) -> tuple[int | None, tuple[str, ...]]:
    """The policy's ``limits.max_injected_chars``, or ``(None, problems)`` when malformed."""
    if not isinstance(policy, Mapping):
        return None, ()
    limits = policy.get("limits")
    if limits is None:
        return None, ()
    if not isinstance(limits, Mapping):
        return None, ("limits must be an object",)
    limit = limits.get("max_injected_chars")
    if limit is None:
        return None, ()
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return None, (f"limits.max_injected_chars must be a positive integer; got {limit!r}",)
    return limit, ()
