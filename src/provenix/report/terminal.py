"""Terminal report.

Grouped by severity. Each finding gives ID, file, line, the problem and the
fix, because a finding a reader cannot act on is a bug (MVP.md section 8).

Skipped checks are listed explicitly. A check that produced nothing because it
never ran must not look like a check that produced nothing because the pipeline
is clean (MVP.md section 4a).
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import TextIO

from ..checks import CheckOutcome, findings_from
from ..finding import Finding, Severity
from ..model import Workflow
from ..score import MAX_SCORE, compute

_COLOURS = {
    Severity.SECURITY: "\033[95m",
    Severity.CRITICAL: "\033[91m",
    Severity.HIGH: "\033[93m",
    Severity.MEDIUM: "\033[96m",
}
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_colour(stream: object) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def render(
    workflow: Workflow,
    outcomes: list[CheckOutcome],
    stream: TextIO | None = None,
) -> None:
    """Write the report for `outcomes` to `stream` (default stdout)."""
    out = stream if stream is not None else sys.stdout
    colour = _use_colour(out)

    def paint(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if colour else text

    findings = findings_from(outcomes)
    write = out.write

    write(f"\n{paint('provenix', _BOLD)}  {workflow.root.name}  ")
    write(f"[{workflow.engine}]\n")
    ran = [o for o in outcomes if o.applicable]
    write(
        paint(
            f"{sum(1 for p in workflow.processes if not p.synthetic)} processes, "
            f"{len(workflow.env_files)} environments, "
            f"{len(ran)} checks run\n",
            _DIM,
        )
    )

    if not findings:
        write("\nNo findings.\n")
    else:
        grouped: dict[Severity, list[Finding]] = defaultdict(list)
        for finding in findings:
            grouped[finding.severity].append(finding)

        for severity in sorted(grouped, key=lambda s: s.rank):
            items = grouped[severity]
            header = f"{severity.label.upper()}  ({len(items)})"
            write(f"\n{paint(header, _COLOURS.get(severity, ''))}\n")
            for finding in items:
                location = f"{workflow.rel(finding.file)}:{finding.line}"
                write(f"  {paint(finding.id, _BOLD)}  {location}\n")
                write(f"    {finding.problem}\n")
                if finding.evidence:
                    write(paint(f"    evidence: {finding.evidence}\n", _DIM))
                write(f"    {paint('fix:', _BOLD)} {finding.fix}\n")

    # Nothing is dropped without the report saying so. CI test fixtures are
    # excluded from PVX004/PVX005 by design; a silent exclusion is the same
    # failure mode as a silent zero on a non-applicable check.
    excluded = workflow.excluded_test_urls
    if excluded:
        files = len({asset.file for asset in excluded})
        write(f"\n{paint('INFO', _DIM)}\n")
        write(
            paint(
                f"  {len(excluded)} reference URL{'s' if len(excluded) != 1 else ''} in CI "
                f"test profiles excluded from PVX004/005 "
                f"(test fixtures, not analysis inputs; {files} file"
                f"{'s' if files != 1 else ''})\n",
                _DIM,
            )
        )

    skipped = [o for o in outcomes if not o.applicable]
    if skipped:
        write(f"\n{paint('NOT APPLICABLE', _DIM)}\n")
        for outcome in skipped:
            write(paint(f"  {outcome.spec.id}  {outcome.skipped_reason}\n", _DIM))

    score = compute(findings, outcomes)
    if findings:
        counts = ", ".join(
            f"{len(grouped[s])} {s.label}" for s in sorted(grouped, key=lambda s: s.rank)
        )
        plural = "s" if len(findings) != 1 else ""
        write(f"\n{len(findings)} finding{plural} ({counts})\n")
    else:
        write("\n0 findings\n")
    write(f"{paint(f'score {score.value}/{MAX_SCORE}', _BOLD)}\n")
