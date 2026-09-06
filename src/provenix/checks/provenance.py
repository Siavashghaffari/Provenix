"""PVX011 and PVX012 — provenance of tools and of the pipeline itself."""

from __future__ import annotations

from ..finding import Finding, Severity
from ..model import UNRESOLVED, Engine, Workflow
from . import check


@check(
    id="PVX011",
    severity=Severity.HIGH,
    title="No tool version capture",
)
def no_version_capture(wf: Workflow) -> list[Finding]:
    """Flag a process that records nothing about the tool versions it ran.

    What counts as capture is normalised in the parsers, so this check never
    sees engine syntax (design.md section 4). Both engines have more than one
    valid mechanism and missing any of them is costly:

    Nextflow is mid-migration. The older form emits `versions.yml`; the newer
    one declares `eval(...)` output with `topic: versions`. nf-core/sarek has
    123 modules on the new form and 3 on the old, so checking only for
    `versions.yml` would report almost the whole pipeline as unprovenanced.

    Snakemake records versions through `conda:` or through `wrapper:`, whose
    tag pins the environment via the wrapper repository. In
    dna-seq-gatk-variant-calling, 26 of 30 rules use `wrapper:` and no
    `conda:`; treating those as missing would be wrong 26 times over.
    """
    findings: list[Finding] = []
    for process in wf.processes:
        if process.synthetic or process.emits_versions or not process.runs_command:
            continue

        if wf.engine is Engine.NEXTFLOW:
            fix = (
                "Emit tool versions from the process: add a `versions.yml` output, or "
                "an `eval('tool --version')` output tuple with `topic: versions`."
            )
        else:
            fix = (
                "Pin the software for this rule: add a `conda:` environment file or a "
                "versioned `wrapper:` tag."
            )

        findings.append(
            Finding(
                id="PVX011",
                severity=Severity.HIGH,
                file=process.file,
                line=process.line,
                problem=(
                    f"'{process.name}' records no tool version, so a report cannot say "
                    f"which software actually produced its results."
                ),
                fix=fix,
                evidence=process.name,
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)


@check(
    id="PVX012",
    severity=Severity.HIGH,
    title="Pipeline version not recorded",
)
def unrecorded_source_version(wf: Workflow) -> list[Finding]:
    """Flag a pipeline whose own version cannot be established.

    Two signals, per MVP.md section 4. Git state is primary and engine
    agnostic: a repository with a resolvable commit answers "what code ran".
    A declared version is the additional Nextflow signal — `manifest.version`
    in `nextflow.config`, which all five nf-core pipelines carry.

    Snakemake has no standard in-repo version declaration and 0 of the 5
    corpus workflows record one, so its absence there is expected and is not
    on its own a finding.
    """
    source = wf.source
    root_file = wf.root / (
        "nextflow.config" if wf.engine is Engine.NEXTFLOW else "workflow/Snakefile"
    )
    anchor = root_file if root_file.exists() else wf.root

    if not source.is_repo:
        return [
            Finding(
                id="PVX012",
                severity=Severity.HIGH,
                file=anchor,
                line=1,
                problem=(
                    "The pipeline source is not under version control, so there is no "
                    "way to establish which code produced a given result."
                ),
                fix=(
                    "Put the pipeline in a git repository and tag the revision used for "
                    "each analysis."
                ),
                evidence="no .git directory found",
            )
        ]

    if source.commit == UNRESOLVED:
        return [
            Finding(
                id="PVX012",
                severity=Severity.HIGH,
                file=anchor,
                line=1,
                problem=(
                    "The git repository has no resolvable HEAD commit, so the exact "
                    "revision of the pipeline cannot be recorded in a report."
                ),
                fix="Commit the working tree so HEAD names a specific revision.",
                evidence="git HEAD could not be resolved",
            )
        ]

    if wf.engine is Engine.NEXTFLOW and not source.declared_version:
        return [
            Finding(
                id="PVX012",
                severity=Severity.HIGH,
                file=anchor,
                line=1,
                problem=(
                    "No pipeline version is declared in the manifest, so a run cannot "
                    "report which release of the pipeline it used."
                ),
                fix="Add `version` to the `manifest { }` block in nextflow.config.",
                evidence=f"git commit {source.commit[:8]} present, manifest.version absent",
            )
        ]

    return []
