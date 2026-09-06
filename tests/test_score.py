"""Score, output formats and exit codes.

The score formula is tested separately so a change to it fails loudly
(design.md section 10). Its deductions are fixed by MVP.md section 7 and must
not drift between releases.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from provenix import engine
from provenix.checks import findings_from, run_all
from provenix.cli import _should_fail, main
from provenix.finding import Finding, Severity
from provenix.report import html, json_out
from provenix.score import MAX_SCORE, compute

FIXTURES = Path(__file__).parent / "fixtures"
NF_GOOD = FIXTURES / "nextflow_good"
NF_BAD = FIXTURES / "nextflow_bad"
SM_BAD = FIXTURES / "snakemake_bad"

#: Assembled at run time by conftest.py, never committed as literals — a
#: secret scanner cannot tell a fake vendor-shaped key from a live one, and
#: committing one produces a real alert on a value that was never real.
from conftest import planted_secrets


def _finding(severity: Severity) -> Finding:
    return Finding(
        id="PVX001",
        severity=severity,
        file=Path("x.nf"),
        line=1,
        problem="p",
        fix="f",
    )


# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity,deduction",
    [
        (Severity.CRITICAL, 15),
        (Severity.HIGH, 7),
        (Severity.MEDIUM, 3),
        (Severity.SECURITY, 20),
    ],
)
def test_deductions_are_fixed(severity: Severity, deduction: int) -> None:
    """MVP.md section 7. Changing these changes what every score means."""
    assert severity.deduction == deduction
    assert compute([_finding(severity)]).value == MAX_SCORE - deduction


def test_no_findings_scores_full_marks() -> None:
    assert compute([]).value == MAX_SCORE


def test_score_floors_at_zero() -> None:
    assert compute([_finding(Severity.SECURITY)] * 20).value == 0


def test_score_is_order_independent() -> None:
    findings = [_finding(Severity.CRITICAL), _finding(Severity.MEDIUM), _finding(Severity.HIGH)]
    assert compute(findings).value == compute(list(reversed(findings))).value


def test_not_applicable_checks_never_deduct() -> None:
    """A check that did not run cannot lower the score (MVP.md section 4a)."""
    workflow = engine.parse(SM_BAD, engine.detect(SM_BAD))
    outcomes = run_all(workflow)
    skipped = [o for o in outcomes if not o.applicable]
    assert skipped, "expected PVX022 to be not applicable to Snakemake"
    assert all(not o.findings for o in skipped)


def test_clean_fixtures_score_full_marks() -> None:
    """PVX012 aside: this repository has no commits, so git state is absent."""
    for root in (NF_GOOD, FIXTURES / "snakemake_good"):
        workflow = engine.parse(root, engine.detect(root))
        outcomes = run_all(workflow, disabled=["PVX012"])
        assert compute(findings_from(outcomes), outcomes).value == MAX_SCORE, root.name


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def _json_for(root: Path) -> dict:
    workflow = engine.parse(root, engine.detect(root))
    return json_out.build(workflow, run_all(workflow))


def test_json_has_no_timestamp() -> None:
    """Two runs must diff cleanly, so nothing volatile is included."""
    text = json.dumps(_json_for(NF_BAD))
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)


def test_json_is_byte_identical_across_runs() -> None:
    def render() -> str:
        workflow = engine.parse(NF_BAD, engine.detect(NF_BAD))
        buffer = io.StringIO()
        json_out.render(workflow, run_all(workflow), stream=buffer)
        return buffer.getvalue()

    assert render() == render()


def test_json_reports_skipped_checks() -> None:
    document = _json_for(SM_BAD)
    skipped = [c for c in document["checks"] if not c["applicable"]]
    assert any(c["id"] == "PVX022" for c in skipped)
    assert all(c["skipped_reason"] for c in skipped)


def test_json_carries_full_detail_for_aggregated_findings() -> None:
    """Human formats show three examples; JSON carries the whole list."""
    document = _json_for(SM_BAD)
    aggregated = [f for f in document["findings"] if f["id"] in ("PVX005", "PVX013", "PVX021")]
    assert any("detail" in f for f in aggregated)


def test_json_records_excluded_ci_urls() -> None:
    document = _json_for(FIXTURES / "snakemake_good")
    assert document["excluded"]["ci_test_urls"]


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def test_html_is_self_contained() -> None:
    """No CDN, no webfont, no script. It must open from a filesystem in five
    years and may be attached to a regulatory submission (design.md 9)."""
    workflow = engine.parse(NF_BAD, engine.detect(NF_BAD))
    document = html.build(workflow, run_all(workflow))
    assert "<script" not in document.lower()
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', document)
    assert "@import" not in document
    assert "<style>" in document


def test_html_escapes_content() -> None:
    workflow = engine.parse(NF_BAD, engine.detect(NF_BAD))
    document = html.build(workflow, run_all(workflow))
    body = document.split("<body>", 1)[1]
    # No raw angle brackets from finding text leaking into markup.
    assert "<script>" not in body


def test_html_shows_skipped_checks() -> None:
    workflow = engine.parse(SM_BAD, engine.detect(SM_BAD))
    document = html.build(workflow, run_all(workflow))
    assert "PVX022" in document
    assert "Checks not run" in document


# --------------------------------------------------------------------------
# Secrets never leave the process
# --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["nextflow", "snakemake"])
def test_no_secret_value_appears_in_any_output(pipeline_with_secrets, which: str) -> None:
    """The guarantee in design.md section 8, asserted across every format.

    Runs against a copy of the bad fixture with vendor-shaped credentials
    planted into it — an AWS key ID, a Google API key, a GitHub token and a
    private key header — so the high-confidence shapes are exercised without
    any of them existing as a literal in the repository.
    """
    from provenix.report import terminal

    root = pipeline_with_secrets(which)
    workflow = engine.parse(root, engine.detect(root))
    outcomes = run_all(workflow)
    findings = findings_from(outcomes)

    secret_findings = [f for f in findings if f.id == "PVX030"]
    assert len(secret_findings) >= 4, "every planted vendor shape should be detected"

    rendered = []
    for renderer in (terminal.render, json_out.render, html.render):
        buffer = io.StringIO()
        renderer(workflow, outcomes, stream=buffer)
        rendered.append(buffer.getvalue())

    for output in rendered:
        for secret in planted_secrets():
            assert secret not in output, f"{secret[:8]}... leaked into output"


def test_committed_fixtures_contain_no_vendor_shaped_secret() -> None:
    """Guards the fix for a real GitHub secret-scanning alert.

    A synthetic Google API key in a test fixture was flagged on push. It was
    never a live credential, but a scanner cannot know that, so the rule is
    that no committed file carries a value matching a provider pattern.
    """
    import re

    patterns = re.compile(
        r"AIza[0-9A-Za-z_-]{35}"
        r"|gh[pousr]_[A-Za-z0-9]{36,}"
        r"|xox[abprs]-[A-Za-z0-9-]{10,}"
        r"|[sr]k_live_[A-Za-z0-9]{20,}"
        r"|AKIA[0-9A-Z]{16}"
    )
    repo = Path(__file__).resolve().parents[1]
    # secrets.py defines these patterns; it is the one file allowed to.
    allowed = {repo / "src" / "provenix" / "checks" / "secrets.py"}

    offenders = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path in allowed:
            continue
        if any(part in {".git", "dist", "__pycache__", ".ruff_cache"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".yml", ".yaml", ".config", ".nf", ".smk", ".md", ".toml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if patterns.search(text):
            offenders.append(str(path.relative_to(repo)))

    assert not offenders, f"vendor-shaped credential literals committed: {offenders}"


def test_secret_reference_is_not_flagged() -> None:
    """`${System.getenv('CI_TOKEN')}` names a secret; it is not one."""
    workflow = engine.parse(NF_BAD, engine.detect(NF_BAD))
    findings = [f for f in findings_from(run_all(workflow)) if f.id == "PVX030"]
    lines = {f.line for f in findings if f.file.name == "creds.config"}
    assert 7 not in lines, "the env-var reference must not be reported"


# --------------------------------------------------------------------------
# Exit codes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "threshold,severities,expected",
    [
        ("critical", [Severity.CRITICAL], True),
        ("critical", [Severity.HIGH], False),
        ("critical", [Severity.SECURITY], True),
        ("high", [Severity.HIGH], True),
        ("high", [Severity.MEDIUM], False),
        ("medium", [Severity.MEDIUM], True),
        ("none", [Severity.SECURITY], False),
    ],
)
def test_fail_on_threshold(threshold: str, severities: list[Severity], expected: bool) -> None:
    assert _should_fail([_finding(s) for s in severities], threshold) is expected


def test_cli_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(NF_GOOD), "--disable", "PVX012"]) == 0
    assert main(["check", str(NF_BAD)]) == 1
    assert main(["check", str(NF_BAD), "--fail-on", "none"]) == 0
    capsys.readouterr()


def test_cli_rejects_missing_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(FIXTURES / "does_not_exist")]) == 2
    assert "provenix:" in capsys.readouterr().err


def test_cli_writes_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out" / "report.html"
    main(["check", str(NF_BAD), "--format", "html", "--output", str(target)])
    capsys.readouterr()
    assert target.is_file()
    assert target.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_reported_version_matches_packaging() -> None:
    """`provenix --version` must not drift from pyproject.toml.

    A report that stamps the wrong tool version is the opposite of what an
    audit tool is for, so the version has exactly one source of truth.
    """
    import provenix

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert declared is not None

    reported = provenix.__version__
    if reported.endswith("+source"):
        pytest.skip("running from a source tree; the installed-metadata path is covered in CI")
    assert reported == declared.group(1)
