"""PVX013 and PVX021 — resource declarations and failure handling."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ..finding import Finding, Severity
from ..model import Engine, Process, Workflow
from . import check

#: Any one of these establishes that a process has a resource envelope.
#: Nextflow fills them from `conf/base.config` selectors, Snakemake from
#: `threads:` / `resources:` on the rule or from a profile default.
_RESOURCE_KEYS = ("cpus", "memory", "time", "threads", "resources", "disk")

#: How many affected process names the human-readable formats show. The rest
#: go to `Finding.detail`, which only the JSON output renders.
_EXAMPLES = 3


def _aggregate(
    wf: Workflow,
    affected: list[Process],
    *,
    check_id: str,
    severity: Severity,
    problem: Callable[[int, int], str],
    fix: str,
) -> list[Finding]:
    """Group affected processes into one finding per source file.

    These two checks are systemic rather than local: where they fire at all
    they usually fire on most of a file. On the Phase 0 Snakemake corpus that
    is 132 of seq2science's 143 rules for PVX021 — every one of them true, and
    together completely unreadable. Worse, at 3 points apiece they floor the
    score at 0 for every Snakemake workflow, which destroys the one property
    the score exists for: comparability across pipelines (MVP.md section 7).

    Same treatment as PVX005: one finding per file, three example names, the
    full list in JSON only.
    """
    if not affected:
        return []

    total_by_file: dict[Path, int] = defaultdict(int)
    for process in wf.processes:
        if not process.synthetic:
            total_by_file[process.file] += 1

    by_file: dict[Path, list[Process]] = defaultdict(list)
    for process in affected:
        by_file[process.file].append(process)

    findings: list[Finding] = []
    for path in sorted(by_file, key=lambda p: p.as_posix()):
        group = sorted(by_file[path], key=lambda p: (p.line, p.name))
        names = [p.name for p in group]
        examples = ", ".join(names[:_EXAMPLES])
        more = f" (+{len(names) - _EXAMPLES} more)" if len(names) > _EXAMPLES else ""
        findings.append(
            Finding(
                id=check_id,
                severity=severity,
                file=path,
                line=group[0].line,
                problem=problem(len(group), total_by_file[path]),
                fix=fix,
                evidence=f"{examples}{more}",
                detail=tuple(names),
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)


@check(
    id="PVX013",
    severity=Severity.HIGH,
    title="Process has no resource directives",
)
def missing_resources(wf: Workflow) -> list[Finding]:
    """Flag processes with no declared CPU, memory or time envelope.

    A process with no envelope gets whatever the executor defaults to, which
    differs between machines, so a rerun can OOM where the original did not —
    a reproducibility failure that presents as a hardware problem.

    Reading only the process body would flag every nf-core process ever
    written: Phase 0 counted zero inline `cpus` and zero inline `time` across
    482 of them, because it all lives in `conf/base.config` behind
    `withLabel:` selectors. parsers/nfconfig.py merges that in first, and
    Snakemake profile defaults are merged the same way.
    """
    affected = [
        process
        for process in wf.processes
        if not process.synthetic
        and process.runs_command
        and not any(process.resources.get(key) for key in _RESOURCE_KEYS)
    ]

    if wf.engine is Engine.NEXTFLOW:
        fix = (
            "Declare cpus, memory and time — on the process, or via a `label` bound to a "
            "`withLabel:` block in conf/base.config."
        )
    else:
        fix = (
            "Add `threads:` and a `resources:` block to each rule, or set defaults with "
            "`default-resources` in a Snakemake profile."
        )

    return _aggregate(
        wf,
        affected,
        check_id="PVX013",
        severity=Severity.HIGH,
        problem=lambda n, total: (
            f"{n} of {total} processes here declare no cpus, memory or time, so a rerun "
            f"on different hardware can fail or behave differently."
        ),
        fix=fix,
    )


@check(
    id="PVX021",
    severity=Severity.MEDIUM,
    title="No failure handling defined",
)
def missing_error_strategy(wf: Workflow) -> list[Finding]:
    """Flag processes with no retry or error policy.

    Without one, a transient failure — a truncated download, a scheduler
    eviction — aborts the run, or leaves a partial output a later step
    consumes. Nextflow spells this `errorStrategy`, Snakemake `retries`; both
    parsers normalise onto `Process.error_strategy`, and both resolve their
    engine's global default first.
    """
    affected = [
        process
        for process in wf.processes
        if not process.synthetic and process.runs_command and not process.error_strategy
    ]

    if wf.engine is Engine.NEXTFLOW:
        fix = (
            "Set `errorStrategy` and `maxRetries`, on the process or globally in the "
            "`process { }` block of conf/base.config."
        )
    else:
        fix = (
            "Add `retries:` to rules that can fail transiently, or set a global default "
            "with `retries` in a Snakemake profile."
        )

    return _aggregate(
        wf,
        affected,
        check_id="PVX021",
        severity=Severity.MEDIUM,
        problem=lambda n, total: (
            f"{n} of {total} processes here define no retry or error policy, so a "
            f"transient failure aborts the run rather than being retried."
        ),
        fix=fix,
    )
