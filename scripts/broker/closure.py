"""Resolving the Authorised Closure (CONTEXT.md, ADR-0004, ADR-0013).

The Closure is the policy's authorised seeds — the Foundation Set, the Brokered Allowlist and
the Preferred Skills — extended by the declared Dependencies the verified Store Manifest
carries, then intersected with the policy by removing anything it Denies. A declared
dependency the policy does not deny is admitted by the Skill that declares it; a dependency
the policy Denies never is. A Skill the manifest does not carry, and a dependency cycle, are
recorded as problems rather than silently dropped (ADR-0006/B7 fails such a closure closed
before delivery).
"""

from __future__ import annotations

from collections.abc import Iterable

from .types import ResolvedSkillVersion


def dependency_closure(primary_id: str, identities: dict[str, dict],
                       denied: Iterable[str] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The Primary plus its transitive declared Dependencies, or the problems that close it.

    The order is deterministic and puts the Primary first, so a Pack's head is the Primary's
    opening. A Dependency the manifest does not carry, one the policy Denies, and a cycle are
    each recorded rather than silently dropped (ADR-0006/B7).
    """
    denied = set(denied)
    ordered: list[str] = []
    seen: set[str] = set()
    problems: list[str] = []

    def visit(ident: str, path: tuple[str, ...]) -> None:
        if ident in path:
            problems.append("dependency cycle: " + " -> ".join((*path, ident)))
            return
        if ident in seen:
            return
        seen.add(ident)
        if ident in denied:
            problems.append(f"dependency not authorised: {ident}")
            return
        row = identities.get(ident)
        if row is None:
            problems.append(f"unknown dependency: {ident}")
            return
        ordered.append(ident)
        for dependency in sorted(row.get("dependencies", ())):
            visit(dependency, (*path, ident))

    visit(primary_id, ())
    return tuple(ordered), tuple(sorted(problems))


def authorised_closure(policy: dict, identities: dict[str, dict]) -> tuple[
        tuple[ResolvedSkillVersion, ...], tuple[str, ...]]:
    """Return ``(closure, problems)``: the sorted Closure and any resolution problems."""
    resolved: dict[str, dict] = {}
    problems: list[str] = []

    def visit(ident: str, path: tuple[str, ...]) -> None:
        if ident in path:
            problems.append("dependency cycle: " + " -> ".join((*path, ident)))
            return
        if ident in resolved:
            return
        row = identities.get(ident)
        if row is None:
            problems.append(f"unknown ID in closure: {ident}")
            return
        resolved[ident] = row
        for dependency in sorted(row.get("dependencies", ())):
            visit(dependency, (*path, ident))

    for ident in _seeds(policy):
        visit(ident, ())

    for denied in sorted(set(resolved) & set(policy.get("denied", ()))):
        del resolved[denied]

    closure = tuple(
        ResolvedSkillVersion(id=row["id"], name=row["name"], version=row["package_sha256"])
        for _, row in sorted(resolved.items())
    )
    return closure, tuple(sorted(problems))


def _seeds(policy: dict) -> list[str]:
    foundation = [entry["id"] for entry in policy.get("foundation", ())
                  if isinstance(entry, dict) and "id" in entry]
    return [*foundation, *policy.get("brokered", ()), *policy.get("preferred", ())]
