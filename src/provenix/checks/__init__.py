"""The check registry.

Adding a check touches one file: decorate it and it appears in `list-checks`,
in `--disable`, and in the run. Registration order never affects output —
checks are always iterated in sorted ID order.

Applicability is first-class (MVP.md section 4a). A check declares the engines
it applies to; one that does not apply is not run and is *reported* as
NOT_APPLICABLE with a reason. A silent zero is worse than a visible N/A,
because a reader cannot tell a clean result from an absent one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..finding import Finding, Severity
from ..model import Engine, Workflow

CheckFunc = Callable[[Workflow], list[Finding]]


@dataclass(frozen=True)
class CheckSpec:
    id: str
    severity: Severity
    title: str
    engines: frozenset[Engine]
    func: CheckFunc
    #: Why this check does not apply to the engines it excludes. Shown in the
    #: report so an absent check is visible and explained.
    na_reason: str = ""

    def applies_to(self, engine: Engine) -> bool:
        return engine in self.engines


@dataclass(frozen=True)
class CheckOutcome:
    """What became of one check in one run."""

    spec: CheckSpec
    applicable: bool
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    skipped_reason: str = ""


_REGISTRY: dict[str, CheckSpec] = {}

ALL_ENGINES = frozenset({Engine.NEXTFLOW, Engine.SNAKEMAKE})


def check(
    *,
    id: str,
    severity: Severity,
    title: str,
    engines: Iterable[Engine] = ALL_ENGINES,
    na_reason: str = "",
) -> Callable[[CheckFunc], CheckFunc]:
    """Register a check. See MVP.md section 4 for the authoritative check list."""

    def decorate(func: CheckFunc) -> CheckFunc:
        if id in _REGISTRY:
            raise ValueError(f"duplicate check id: {id}")
        _REGISTRY[id] = CheckSpec(
            id=id,
            severity=severity,
            title=title,
            engines=frozenset(engines),
            func=func,
            na_reason=na_reason,
        )
        return func

    return decorate


def registry() -> list[CheckSpec]:
    """Every registered check, in ID order."""
    _load_all()
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def run_all(workflow: Workflow, disabled: Iterable[str] = ()) -> list[CheckOutcome]:
    """Run every applicable check. Deterministic in order and content."""
    disabled_set = {d.strip().upper() for d in disabled if d.strip()}
    outcomes: list[CheckOutcome] = []

    for spec in registry():
        if spec.id in disabled_set:
            outcomes.append(
                CheckOutcome(spec=spec, applicable=False, skipped_reason="disabled by --disable")
            )
            continue
        if not spec.applies_to(workflow.engine):
            reason = spec.na_reason or f"not applicable to {workflow.engine}"
            outcomes.append(CheckOutcome(spec=spec, applicable=False, skipped_reason=reason))
            continue
        findings = spec.func(workflow)
        outcomes.append(
            CheckOutcome(
                spec=spec,
                applicable=True,
                findings=tuple(sorted(findings, key=lambda f: f.sort_key)),
            )
        )
    return outcomes


def findings_from(outcomes: Iterable[CheckOutcome]) -> list[Finding]:
    """Every finding across outcomes, in the canonical sort order."""
    collected: list[Finding] = []
    for outcome in outcomes:
        collected.extend(outcome.findings)
    return sorted(collected, key=lambda f: f.sort_key)


_LOADED = False


def _load_all() -> None:
    """Import the check modules so their decorators run."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from . import (  # noqa: F401
        containers,
        dependencies,
        determinism,
        portability,
        provenance,
        reference_data,
        resources,
        secrets,
    )
