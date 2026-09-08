"""Check-level tests, driven by the fixture pipelines.

design.md section 10: the clean fixture is the false-positive regression
guard, and false positives are the thing that kills this tool. That test is
the one to assert hardest on.
"""

from __future__ import annotations

import io
import re
import shutil
from pathlib import Path

import pytest

from provenix import engine
from provenix.checks import findings_from, registry, run_all
from provenix.finding import Finding, Severity
from provenix.model import Engine
from provenix.report import terminal

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "nextflow_good"
BAD = FIXTURES / "nextflow_bad"
REPO_ROOT = Path(__file__).resolve().parents[1]


#: PVX012 asks whether the pipeline is under version control with a resolvable
#: HEAD. The fixtures live inside this repository rather than in one of their
#: own, so they inherit whatever git state the checkout happens to have: its
#: commits, its tags, whether HEAD resolves at all. PVX012's result therefore
#: says something about the checkout, not about the fixture, and asserting on
#: it here would make these tests depend on how the repository was cloned.
#: It is disabled in fixture assertions and covered directly, against explicit
#: SourceInfo values, in test_provenance_signals below.
_GIT_DEPENDENT = ["PVX012"]


def analyse(root: Path, disabled: list[str] = ()) -> list[Finding]:
    workflow = engine.parse(root, engine.detect(root))
    return findings_from(run_all(workflow, disabled=disabled))


# --------------------------------------------------------------------------
# The false-positive guard
# --------------------------------------------------------------------------


def test_clean_pipeline_produces_no_findings() -> None:
    """The single most important test in the suite.

    The clean fixture deliberately contains every construct that caused a
    false positive during Phase 1: percent-encoded tags, mulled images,
    digest-in-URL containers, conda lock files, documentation links in
    comments, `pip:` blocks, test-data branch refs, and a checksummed
    reference download.
    """
    findings = analyse(GOOD, disabled=_GIT_DEPENDENT)
    assert findings == [], "\n".join(f"{f.id} {f.file}:{f.line} {f.problem}" for f in findings)


def test_engine_detection() -> None:
    assert engine.detect(GOOD) is Engine.NEXTFLOW


# --------------------------------------------------------------------------
# The bad fixture triggers every Phase 1 check
# --------------------------------------------------------------------------


@pytest.mark.parametrize("check_id", ["PVX001", "PVX002", "PVX003", "PVX004", "PVX005"])
def test_bad_pipeline_triggers(check_id: str) -> None:
    assert any(f.id == check_id for f in analyse(BAD)), f"{check_id} did not fire"


def test_bad_pipeline_severities() -> None:
    by_id = {f.id: f.severity for f in analyse(BAD)}
    assert by_id["PVX001"] is Severity.CRITICAL
    assert by_id["PVX002"] is Severity.HIGH  # widened after Phase 0
    assert by_id["PVX003"] is Severity.CRITICAL
    assert by_id["PVX004"] is Severity.CRITICAL
    assert by_id["PVX005"] is Severity.CRITICAL


def test_pvx005_aggregates_per_file() -> None:
    """One finding per source file, not one per URL (MVP.md section 4)."""
    findings = [f for f in analyse(BAD) if f.id == "PVX005"]
    assert len({f.file for f in findings}) == len(findings)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_repeated_runs_are_byte_identical() -> None:
    def render(root: Path) -> str:
        workflow = engine.parse(root, engine.detect(root))
        buffer = io.StringIO()
        terminal.render(workflow, run_all(workflow), stream=buffer)
        return buffer.getvalue()

    assert render(BAD) == render(BAD)


def test_findings_are_sorted() -> None:
    findings = analyse(BAD)
    assert [f.sort_key for f in findings] == sorted(f.sort_key for f in findings)


# --------------------------------------------------------------------------
# Registry and Finding invariants
# --------------------------------------------------------------------------


def test_every_registered_check_is_documented_in_mvp() -> None:
    """design.md section 6: the registry must not drift from the spec."""
    spec_text = (REPO_ROOT / "MVP.md").read_text(encoding="utf-8")
    for check in registry():
        assert check.id in spec_text, f"{check.id} is not documented in MVP.md"


def test_pvx023_is_not_registered() -> None:
    """Cut after Phase 0. Removed, not disabled."""
    assert "PVX023" not in {c.id for c in registry()}


def test_every_check_declares_at_least_one_engine() -> None:
    for check in registry():
        assert check.engines, f"{check.id} applies to no engine"


def test_finding_requires_a_fix() -> None:
    with pytest.raises(ValueError, match="fix is required"):
        Finding(
            id="PVX001",
            severity=Severity.CRITICAL,
            file=Path("x.nf"),
            line=1,
            problem="something is wrong",
            fix="   ",
        )


def test_every_finding_carries_file_line_problem_and_fix() -> None:
    for finding in analyse(BAD):
        assert finding.file is not None
        assert finding.line > 0
        assert finding.problem.strip()
        assert finding.fix.strip()


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def test_report_lists_skipped_checks_visibly() -> None:
    """A silent zero must not look like a clean result (MVP.md section 4a)."""
    workflow = engine.parse(GOOD, engine.detect(GOOD))
    buffer = io.StringIO()
    terminal.render(workflow, run_all(workflow, disabled=["PVX002"]), stream=buffer)
    output = buffer.getvalue()
    assert "NOT APPLICABLE" in output
    assert "PVX002" in output


def test_report_has_no_ansi_when_not_a_tty() -> None:
    workflow = engine.parse(BAD, engine.detect(BAD))
    buffer = io.StringIO()
    terminal.render(workflow, run_all(workflow), stream=buffer)
    assert not re.search(r"\x1b\[", buffer.getvalue())


# --------------------------------------------------------------------------
# Phase 2: Snakemake
# --------------------------------------------------------------------------

SM_GOOD = FIXTURES / "snakemake_good"
SM_BAD = FIXTURES / "snakemake_bad"


def test_snakemake_engine_detection() -> None:
    assert engine.detect(SM_GOOD) is Engine.SNAKEMAKE


def test_clean_snakemake_workflow_produces_no_findings() -> None:
    """The Snakemake half of the false-positive guard.

    The clean fixture carries every Snakemake shape that could misfire: a
    commented-out container directive, a `conda:` pointing at a Python
    variable, a rule nested in an `if` block with its body at 8 spaces, a
    wrapper-pinned rule with no conda, a shell command whose URL is split
    across three implicitly concatenated string literals, and a checksummed
    reference download.
    """
    findings = analyse(SM_GOOD, disabled=_GIT_DEPENDENT)
    assert findings == [], "\n".join(f"{f.id} {f.file}:{f.line} {f.problem}" for f in findings)


@pytest.mark.parametrize("check_id", ["PVX001", "PVX002", "PVX003", "PVX004", "PVX005"])
def test_bad_snakemake_workflow_triggers(check_id: str) -> None:
    assert any(f.id == check_id for f in analyse(SM_BAD)), f"{check_id} did not fire"


def test_snakemake_rules_nested_in_conditionals_are_parsed() -> None:
    """Rules inside `if config[...]:` blocks have bodies at 8 spaces.

    A parser assuming 4 would miss them. In the ground-truth corpus this is
    56 of seq2science's 143 rules.
    """
    workflow = engine.parse(SM_GOOD, Engine.SNAKEMAKE)
    names = {p.name for p in workflow.processes}
    assert "bowtie2_align" in names
    assert "bwa_align" in names


def test_conda_pointing_at_a_python_variable_is_skipped() -> None:
    """`conda:` followed by a bare name is a variable, not a path."""
    workflow = engine.parse(SM_GOOD, Engine.SNAKEMAKE)
    registered = {env.path.name for env in workflow.env_files}
    assert "enrichment.yaml" not in registered
    assert "fastqc.yaml" in registered


def test_commented_out_container_is_ignored() -> None:
    """sm_chipseq/workflow/rules/common.smk:9 has one of these for real."""
    workflow = engine.parse(SM_GOOD, Engine.SNAKEMAKE)
    raws = [ref.raw for p in workflow.processes for ref in p.containers]
    assert "docker://continuumio/miniconda3" not in raws


def test_url_split_across_string_literals_is_unresolved() -> None:
    """A URL built by implicit concatenation plus `{params}` cannot be read.

    Reading it line by line yields a truncated URL that does not exist, which
    would be a false finding pointing at a URL nobody wrote.
    """
    workflow = engine.parse(SM_GOOD, Engine.SNAKEMAKE)
    assert not any("Pfam" in asset.url for asset in workflow.assets)


def test_excluded_ci_urls_are_recorded_not_dropped() -> None:
    """Nothing is dropped without the report saying so (MVP.md section 4a)."""
    workflow = engine.parse(SM_GOOD, Engine.SNAKEMAKE)
    assert workflow.excluded_test_urls

    buffer = io.StringIO()
    terminal.render(workflow, run_all(workflow), stream=buffer)
    output = buffer.getvalue()
    assert "INFO" in output
    assert "excluded from PVX004/005" in output


def test_snakemake_findings_are_deterministic() -> None:
    def render(root: Path) -> str:
        workflow = engine.parse(root, engine.detect(root))
        buffer = io.StringIO()
        terminal.render(workflow, run_all(workflow), stream=buffer)
        return buffer.getvalue()

    assert render(SM_BAD) == render(SM_BAD)


def test_both_engines_share_one_check_implementation() -> None:
    """design.md section 4: a check is written once and serves both engines.

    Compared over the checks that apply to both. PVX022 is Nextflow-only by
    declaration, which is the point of the applicability mechanism rather than
    an exception to it.
    """
    # PVX012 applies to both engines but its *criteria* differ by design:
    # git state is the engine-agnostic primary signal, and `manifest.version`
    # is an additional Nextflow-only one (MVP.md section 4). In a repository
    # with commits, Nextflow can therefore fire where Snakemake does not.
    engine_specific_criteria = {"PVX012"}
    cross_engine = {
        spec.id for spec in registry() if spec.engines >= {Engine.NEXTFLOW, Engine.SNAKEMAKE}
    } - engine_specific_criteria
    nextflow_ids = {f.id for f in analyse(BAD)} & cross_engine
    snakemake_ids = {f.id for f in analyse(SM_BAD)} & cross_engine
    assert nextflow_ids == snakemake_ids
    assert len(nextflow_ids) >= 10


def test_provenance_signals() -> None:
    """PVX012's three branches, driven directly rather than through git."""
    from provenix.checks.provenance import unrecorded_source_version
    from provenix.model import UNRESOLVED, SourceInfo

    workflow = engine.parse(GOOD, engine.detect(GOOD))

    workflow.source = SourceInfo(is_repo=False)
    assert [f.id for f in unrecorded_source_version(workflow)] == ["PVX012"]

    workflow.source = SourceInfo(is_repo=True, commit=UNRESOLVED)
    assert [f.id for f in unrecorded_source_version(workflow)] == ["PVX012"]

    # Nextflow additionally wants a declared manifest version.
    workflow.source = SourceInfo(is_repo=True, commit="a" * 40, declared_version=None)
    assert [f.id for f in unrecorded_source_version(workflow)] == ["PVX012"]

    workflow.source = SourceInfo(is_repo=True, commit="a" * 40, declared_version="1.2.0")
    assert unrecorded_source_version(workflow) == []


# --------------------------------------------------------------------------
# Location independence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("engine_name", ["nextflow", "snakemake"])
@pytest.mark.parametrize("ancestor", ["work", ".venv", "node_modules", ".github", "config", "env"])
def test_findings_do_not_depend_on_ancestor_directory_names(
    tmp_path: Path, engine_name: str, ancestor: str
) -> None:
    """A pipeline must analyse identically wherever it happens to live.

    The directory skip lists and the config/env/profile classifiers were
    matched against `Path.parts` of an *absolute* path, which includes every
    ancestor above the pipeline root. A checkout under a directory named
    `work` — which is precisely where GitHub Actions puts one,
    `/home/runner/work/<repo>/<repo>` — matched the Nextflow skip set, so
    every file was skipped, the parse returned an empty Workflow, and the tool
    reported a clean pipeline having read nothing at all.

    Reporting "clean" because no files were seen is the worst failure this
    tool can have, so the guarantee is asserted directly: same pipeline, same
    findings, regardless of the names of the directories above it.
    """
    source = FIXTURES / f"{engine_name}_bad"
    expected = {(f.id, f.line) for f in analyse(source, disabled=_GIT_DEPENDENT)}
    assert expected, "the bad fixture must produce findings from its committed location"

    relocated = tmp_path / ancestor / "pipeline"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, relocated)

    actual = {(f.id, f.line) for f in analyse(relocated, disabled=_GIT_DEPENDENT)}
    assert actual == expected, (
        f"findings changed when the pipeline lived under {ancestor!r}: "
        f"missing={sorted(expected - actual)} unexpected={sorted(actual - expected)}"
    )


@pytest.mark.parametrize(
    "engine_name,marker,expected",
    [
        ("nextflow", "modules/main.nf", Engine.NEXTFLOW),
        ("snakemake", "rules/a.smk", Engine.SNAKEMAKE),
    ],
)
def test_engine_detection_does_not_depend_on_ancestor_directory_names(
    tmp_path: Path, engine_name: str, marker: str, expected: Engine
) -> None:
    """Detection must find a pipeline that has no top-level marker file.

    `engine.detect` looks for `nextflow.config` / `Snakefile` by name first and
    only then globs for `*.nf` / `*.smk`. A pipeline with just the globbed
    files, sitting under a directory named `work`, was undetectable: the glob
    results were all discarded by the skip list before `is_file()` ran.
    """
    root = tmp_path / "work" / "pipeline"
    target = root / marker
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "// placeholder\n" if engine_name == "nextflow" else "rule a:\n", encoding="utf-8"
    )

    assert engine.detect(root) is expected


def test_profile_detection_does_not_depend_on_ancestor_directory_names(tmp_path: Path) -> None:
    """A config YAML must not become a Snakemake profile by ancestry.

    `_apply_profile_defaults` treats a YAML as a workflow profile when a
    `profiles/` directory is on its path. Matched against the absolute path, a
    pipeline living under any directory called `profiles` had every one of its
    YAMLs read as a profile — so a stray `retries:` key silently suppressed
    PVX021 for the whole workflow.
    """
    root = tmp_path / "profiles" / "pipeline"
    (root / "workflow").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "workflow" / "Snakefile").write_text(
        'rule a:\n    output:\n        "out.txt",\n    shell:\n        "touch {output}"\n',
        encoding="utf-8",
    )
    # Not a profile: it is the pipeline's own config, and it happens to carry a
    # key that only means something inside a profile.
    (root / "config" / "config.yaml").write_text("retries: 5\n", encoding="utf-8")

    findings = analyse(root, disabled=_GIT_DEPENDENT)
    assert any(f.id == "PVX021" for f in findings), (
        "config/config.yaml was mistaken for a workflow profile, so its `retries:` "
        "suppressed PVX021"
    )
