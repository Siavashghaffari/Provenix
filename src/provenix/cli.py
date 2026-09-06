"""Command line interface. Argument parsing only — no analysis logic here."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, engine
from .checks import findings_from, registry, run_all
from .finding import Severity
from .report import html, json_out, terminal
from .score import compute

FORMATS = ("terminal", "json", "html")

#: `--fail-on` names the *lowest* severity that should fail the build. Ordered
#: most severe first, matching the report; `none` never fails.
FAIL_LEVELS = ("security", "critical", "high", "medium", "none")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provenix",
        description=(
            "Static reproducibility and audit analysis for bioinformatics workflows. "
            "Never executes the pipeline."
        ),
    )
    parser.add_argument("--version", action="version", version=f"provenix {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check_cmd = sub.add_parser("check", help="analyse a pipeline")
    check_cmd.add_argument("path", type=Path, help="path to the pipeline root")
    check_cmd.add_argument(
        "--format",
        choices=FORMATS,
        default="terminal",
        help="output format (default: terminal)",
    )
    check_cmd.add_argument(
        "--fail-on",
        choices=FAIL_LEVELS,
        default="critical",
        help=(
            "exit 1 when a finding at this severity or above is present "
            "(default: critical). 'none' always exits 0"
        ),
    )
    check_cmd.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="write the report to FILE instead of stdout",
    )
    check_cmd.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="ID",
        help="skip a check by ID, e.g. --disable PVX013 (repeatable)",
    )

    sub.add_parser("list-checks", help="list every registered check")
    return parser


def _should_fail(findings: list, threshold: str) -> bool:
    """True when any finding is at or above the `--fail-on` threshold.

    'Above' means more severe, which is ordered by score deduction: security
    (20), critical (15), high (7), medium (3).
    """
    if threshold == "none":
        return False
    limit = Severity.from_label(threshold).rank
    return any(finding.severity.rank <= limit for finding in findings)


def cmd_check(args: argparse.Namespace) -> int:
    root = args.path.resolve()
    try:
        detected = engine.detect(root)
        workflow = engine.parse(root, detected)
    except engine.DetectionError as exc:
        print(f"provenix: {exc}", file=sys.stderr)
        return 2

    outcomes = run_all(workflow, disabled=args.disable)
    findings = findings_from(outcomes)

    renderers = {"terminal": terminal.render, "json": json_out.render, "html": html.render}
    render = renderers[args.format]

    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w", encoding="utf-8", newline="\n") as handle:
                render(workflow, outcomes, stream=handle)
        except OSError as exc:
            print(f"provenix: cannot write {args.output}: {exc}", file=sys.stderr)
            return 2
        score = compute(findings, outcomes)
        print(
            f"provenix: wrote {args.format} report to {args.output} "
            f"({len(findings)} findings, score {score.value}/100)",
            file=sys.stderr,
        )
    else:
        render(workflow, outcomes)

    return 1 if _should_fail(findings, args.fail_on) else 0


def cmd_list_checks(_: argparse.Namespace) -> int:
    for spec in registry():
        engines = ", ".join(sorted(str(e) for e in spec.engines))
        print(f"{spec.id}  {spec.severity.label:<9} [{engines}]  {spec.title}")
        if spec.na_reason:
            print(f"          n/a: {spec.na_reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "list-checks":
        return cmd_list_checks(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
