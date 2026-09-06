"""PVX003 — conda/mamba dependencies without a pinned version."""

from __future__ import annotations

from ..finding import Finding, Severity
from ..model import Workflow
from . import check


@check(
    id="PVX003",
    severity=Severity.CRITICAL,
    title="Conda dependency without a pinned version",
)
def unpinned_conda_dependency(wf: Workflow) -> list[Finding]:
    """Flag a dependency that resolves to whatever is newest at install time.

    An unpinned `bioconductor-deseq2` installs a different version next year,
    silently changing results. The pin shapes accepted as sufficient are in
    parsers/conda_env.py, which was written against the 2,869 pinned
    dependencies in the Phase 0 corpus.
    """
    findings: list[Finding] = []
    for env in wf.env_files:
        for dependency in env.dependencies:
            if dependency.pinned:
                continue
            where = "pip requirement" if dependency.is_pip else "conda dependency"
            # Name the branch when the conda directive was conditional, so the
            # reader knows which environment to edit.
            branch = f" ({env.branch} branch)" if env.branch else ""
            example = (
                f"{dependency.name}==1.2.3" if dependency.is_pip else f"{dependency.name}=1.2.3"
            )
            findings.append(
                Finding(
                    id="PVX003",
                    severity=Severity.CRITICAL,
                    file=dependency.file,
                    line=dependency.line,
                    problem=(
                        f"{where.capitalize()} '{dependency.name}'{branch} has no version "
                        f"pin, so a rebuild installs whatever version is current at that time."
                    ),
                    fix=f"Pin the version, e.g. '{example}'.",
                    evidence=dependency.raw,
                )
            )
    return sorted(findings, key=lambda f: f.sort_key)
