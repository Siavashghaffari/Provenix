"""JSON output, for CI.

Stable schema, and deliberately free of timestamps: two runs on the same
source must diff cleanly, so nothing that changes between identical runs is
included. The run time belongs in the HTML report, where it is metadata rather
than content (design.md section 7).
"""

from __future__ import annotations

import json
from typing import Any, TextIO

from ..checks import CheckOutcome, findings_from
from ..finding import Finding
from ..model import Workflow
from ..score import FORMULA_VERSION, compute

#: Bumped on any breaking change to the shape below, so a consumer can pin.
SCHEMA_VERSION = 1


def build(workflow: Workflow, outcomes: list[CheckOutcome]) -> dict[str, Any]:
    """Assemble the JSON document as a plain dict."""
    findings = findings_from(outcomes)
    score = compute(findings, outcomes)

    return {
        "schema_version": SCHEMA_VERSION,
        "score": {
            "value": score.value,
            "formula_version": FORMULA_VERSION,
            "counts": {
                severity.label: count
                for severity, count in sorted(score.counts.items(), key=lambda kv: kv[0].rank)
            },
        },
        "pipeline": {
            "name": workflow.root.name,
            "engine": str(workflow.engine),
            "processes": sum(1 for p in workflow.processes if not p.synthetic),
            "environments": len(workflow.env_files),
            "version": workflow.source.declared_version,
            "commit": workflow.source.commit if workflow.source.is_repo else None,
            "tag": workflow.source.tag,
        },
        "checks": [
            {
                "id": outcome.spec.id,
                "title": outcome.spec.title,
                "severity": outcome.spec.severity.label,
                "applicable": outcome.applicable,
                "skipped_reason": outcome.skipped_reason or None,
                "findings": len(outcome.findings),
            }
            for outcome in outcomes
        ],
        "findings": [_finding(workflow, f) for f in findings],
        "excluded": {
            "ci_test_urls": [
                {
                    "file": workflow.rel(asset.file),
                    "line": asset.line,
                    "url": asset.url,
                    "reason": "CI test fixture, not an analysis input",
                }
                for asset in sorted(
                    workflow.excluded_test_urls, key=lambda a: (a.file.as_posix(), a.line, a.url)
                )
            ]
        },
    }


def _finding(workflow: Workflow, finding: Finding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": finding.id,
        "severity": finding.severity.label,
        "file": workflow.rel(finding.file),
        "line": finding.line,
        "problem": finding.problem,
        "fix": finding.fix,
        "evidence": finding.evidence,
    }
    # The full list for an aggregated finding, e.g. every reference URL behind
    # a single PVX005. Kept out of the human-readable formats, which show
    # three examples (MVP.md section 4).
    if finding.detail:
        payload["detail"] = list(finding.detail)
    return payload


def render(workflow: Workflow, outcomes: list[CheckOutcome], stream: TextIO | None = None) -> None:
    import sys

    out = stream if stream is not None else sys.stdout
    document = build(workflow, outcomes)
    # sort_keys for byte-stability across Python versions and dict orderings.
    out.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    out.write("\n")
