"""PVX010 — stochastic tools invoked without a seed."""

from __future__ import annotations

import re

from ..finding import Finding, Severity
from ..model import Workflow
from ..parsers import tools
from . import check

#: A version-capture heredoc. Its body probes every tool in the process
#: (`bowtie2: $(bowtie2 --version)`), which is not an analysis invocation.
#: Leaving it in produced four false positives on the Phase 0 corpus,
#: including one for `BOWTIE2_BUILD`, a process that only builds an index.
_VERSIONS_BLOCK = re.compile(
    r"cat\s*<<-?\s*['\"]?END_VERSIONS.*?END_VERSIONS", re.DOTALL | re.IGNORECASE
)

#: A bare version or help probe on its own, for scripts that write versions
#: without a heredoc.
_VERSION_PROBE = re.compile(r"^.*\B--(?:version|help)\b.*$", re.MULTILINE)


def _analysis_commands(script: str, ext_args: str) -> str:
    """The command text with version-probing removed.

    `ext_args` is included because Phase 0 found every seed in the nf-core
    corpus is set in `conf/modules.config` rather than in the process script —
    for example `withName: 'PRESEQ_LCEXTRAP' { ext.args = '... -seed 1' }`.
    A check reading only the script flags every correctly seeded process.
    """
    cleaned = _VERSIONS_BLOCK.sub(" ", script)
    cleaned = _VERSION_PROBE.sub(" ", cleaned)
    return f"{cleaned}\n{ext_args}"


@check(
    id="PVX010",
    severity=Severity.HIGH,
    title="Stochastic tool invoked with no seed set",
)
def unseeded_stochastic_tool(wf: Workflow) -> list[Finding]:
    """Flag a randomised tool whose RNG is never fixed.

    The command text searched is the script body *plus* the resolved
    `ext.args`. Phase 0 found that every seed in the nf-core corpus is set in
    `conf/modules.config` rather than in the process script — for example
    `withName: 'PRESEQ_LCEXTRAP' { ext.args = '-verbose -bam -seed 1' }` —
    so a check reading only the script would flag every correctly seeded
    process in the ecosystem.
    """
    known = tools.load()
    findings: list[Finding] = []

    for process in wf.processes:
        if process.synthetic:
            continue
        command = _analysis_commands(process.script, process.ext_args)
        if not command.strip():
            continue

        for tool in known:
            if not tool.is_invoked_in(command):
                continue
            if tool.is_seeded_in(command):
                continue
            example = tool.flags[0]
            findings.append(
                Finding(
                    id="PVX010",
                    severity=Severity.HIGH,
                    file=process.file,
                    line=process.line,
                    problem=(
                        f"'{tool.name}' is invoked with no seed set, so a rerun can "
                        f"produce different results. {tool.why}"
                    ),
                    fix=(
                        f"Set the seed explicitly, e.g. add '{example} 1' to the command "
                        f"or to the process arguments."
                    ),
                    evidence=f"{process.name} invokes {tool.name}",
                )
            )
    return sorted(findings, key=lambda f: f.sort_key)
