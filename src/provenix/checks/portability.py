"""PVX020 and PVX022 — portability and output safety."""

from __future__ import annotations

import re

from ..finding import Finding, Severity
from ..model import Engine, Workflow
from . import check

#: An absolute path rooted somewhere machine-specific. `/usr`, `/bin`, `/opt`
#: and `/etc` are deliberately absent: those are container-internal paths and
#: flagging them would fire on every process that names an interpreter.
_ABSOLUTE_PATH = re.compile(
    r"""['"](/(?:home|Users|scratch|mnt|nfs|data|projects|work|shared|lustre|gpfs)"""
    r"""/[A-Za-z0-9._/-]{2,})['"]"""
)

#: publishDir modes that let a rerun silently replace an existing output.
#: `copy`, `move` and `link` all overwrite in place; `symlink` and
#: `copyNoFollow` behave the same way for this purpose. Only the modes that
#: refuse to clobber are safe.
_OVERWRITING_MODES = frozenset({"move"})


@check(
    id="PVX020",
    severity=Severity.MEDIUM,
    title="Hardcoded absolute path outside the workdir",
)
def hardcoded_absolute_path(wf: Workflow) -> list[Finding]:
    """Flag a path that only exists on the machine it was written on.

    Produces zero findings on the whole public corpus, which is the expected
    result rather than a gap: nf-core and the public Snakemake workflows are
    community-reviewed and nobody merges `/home/alice/refs` into them. The
    target user is a private in-house pipeline, which is exactly where these
    live. Validated on fixtures; see MVP.md section 9.
    """
    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()

    for process in wf.processes:
        if process.synthetic:
            continue
        for match in _ABSOLUTE_PATH.finditer(f"{process.script}\n{process.ext_args}"):
            path = match.group(1)
            key = (str(process.file), process.line, path)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    id="PVX020",
                    severity=Severity.MEDIUM,
                    file=process.file,
                    line=process.line,
                    problem=(
                        f"'{process.name}' hardcodes the absolute path '{path}', which "
                        f"will not exist on another machine."
                    ),
                    fix=(
                        "Take the path from a parameter or config value so it can be set "
                        "per environment."
                    ),
                    evidence=path,
                )
            )

    for entry in wf.config:
        for match in _ABSOLUTE_PATH.finditer(entry.value):
            path = match.group(1)
            key = (str(entry.file), entry.line, path)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    id="PVX020",
                    severity=Severity.MEDIUM,
                    file=entry.file,
                    line=entry.line,
                    problem=(
                        f"Config value '{entry.key}' hardcodes the absolute path "
                        f"'{path}', which will not exist on another machine."
                    ),
                    fix="Make the path configurable rather than fixing it in the source.",
                    evidence=f"{entry.key} = {path}",
                )
            )

    return sorted(findings, key=lambda f: f.sort_key)


@check(
    id="PVX022",
    severity=Severity.MEDIUM,
    title="publishDir mode allows silent overwrite",
    engines=(Engine.NEXTFLOW,),
    na_reason="no Snakemake analogue: Snakemake has no publishDir, only declared output paths",
)
def overwriting_publish_mode(wf: Workflow) -> list[Finding]:
    """Flag a publish mode that replaces existing results without warning.

    Nextflow only. Snakemake has no `publishDir`; outputs are declared paths,
    and the nearest analogue is a `protected()` wrapper. Rather than return a
    silent zero there, the check declares itself not applicable so the report
    can say so — see MVP.md section 4a.

    `mode: 'move'` relocates the file out of the work directory, so the
    original run's output is gone and the run is no longer resumable or
    auditable. All five nf-core pipelines default to `'copy'`, which is why
    this fires on none of them.
    """
    findings: list[Finding] = []
    for process in wf.processes:
        if process.synthetic:
            continue
        for spec in process.publish:
            if spec.mode is None or spec.mode not in _OVERWRITING_MODES:
                continue
            findings.append(
                Finding(
                    id="PVX022",
                    severity=Severity.MEDIUM,
                    file=spec.file,
                    line=spec.line,
                    problem=(
                        f"'{process.name}' publishes with mode '{spec.mode}', which moves "
                        f"results out of the work directory and overwrites any earlier "
                        f"output in place."
                    ),
                    fix=(
                        "Use mode 'copy' so the work directory keeps the original, and "
                        "publish each run to its own output directory."
                    ),
                    evidence=f"mode: {spec.mode}",
                )
            )
    return sorted(findings, key=lambda f: f.sort_key)
