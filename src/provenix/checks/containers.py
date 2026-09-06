"""PVX001 and PVX002 — container image pinning."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..finding import Finding, Severity
from ..model import ContainerRef, Pinning, Workflow
from . import check


def _group_by_directive(wf: Workflow) -> list[tuple[Path, int, list[ContainerRef]]]:
    """Group container references by the directive that declared them.

    The nf-core ternary names one logical image once per runtime, so an
    unpinned image would otherwise produce two identical findings on one line.
    Grouping also lets the finding say *which* runtime is affected.
    """
    grouped: dict[tuple[Path, int], list[ContainerRef]] = defaultdict(list)
    order: list[tuple[Path, int]] = []
    for process in wf.processes:
        for ref in process.containers:
            key = (ref.file, ref.line)
            if key not in grouped:
                order.append(key)
            if not any(existing.raw == ref.raw for existing in grouped[key]):
                grouped[key].append(ref)
    return [(path, line, grouped[(path, line)]) for path, line in order]


def _branch_phrase(failing: list[ContainerRef], total: list[ContainerRef]) -> str:
    """Describe which runtime branch is affected, in words a reader can act on.

    "The Docker branch is affected; the Singularity branch is pinned" tells
    someone what to change. Two references side by side do not.

    Only a branch that is genuinely PINNED is described as pinned. Five
    directives in the Phase 0 corpus pair an unpinned Docker reference with a
    Singularity `.img` URL that carries no readable tag; calling that
    UNRESOLVED branch "pinned" would be a false statement in an audit report.
    """
    named: list[str] = []
    for ref in failing:
        if ref.branch and ref.branch not in named:
            named.append(ref.branch)
    if not named:
        return ""

    clean: list[str] = []
    for ref in total:
        if ref in failing or not ref.branch or ref.branch in named:
            continue
        if ref.pinning is Pinning.PINNED and ref.branch not in clean:
            clean.append(ref.branch)

    affected = " and ".join(named)
    if clean:
        return f"The {affected} branch is affected; the {' and '.join(clean)} branch is pinned. "
    if len(named) > 1:
        return f"Both the {affected} branches are affected. "
    return f"The {affected} branch is affected. "


@check(
    id="PVX001",
    severity=Severity.CRITICAL,
    title="Container uses a mutable tag or no tag",
)
def mutable_container_tag(wf: Workflow) -> list[Finding]:
    """Flag images whose tag can be repointed at different content.

    An untagged image is the same failure: Docker resolves it to `:latest`.
    """
    findings: list[Finding] = []
    for path, line, refs in _group_by_directive(wf):
        failing = [r for r in refs if r.pinning in (Pinning.MUTABLE, Pinning.NO_TAG)]
        if not failing:
            continue

        prefix = _branch_phrase(failing, refs)
        tags = [f"':{r.tag}'" for r in failing if r.tag]
        if tags:
            cause = f"uses the mutable tag {tags[0]}"
            fix = (
                "Pin to an immutable reference: a digest (image@sha256:...) or a tag "
                "carrying a build hash, e.g. 'samtools:1.17--h00cdaf9_0'."
            )
        else:
            cause = "has no tag and resolves to ':latest'"
            fix = (
                "Add an immutable reference: a digest (image@sha256:...) or a tag "
                "carrying a build hash, e.g. 'continuumio/miniconda3:4.8.2'."
            )

        findings.append(
            Finding(
                id="PVX001",
                severity=Severity.CRITICAL,
                file=path,
                line=line,
                problem=(
                    f"{prefix}Container image {cause}, so a rerun can pull different "
                    f"software than the original run."
                ),
                fix=fix,
                evidence=" | ".join(r.raw for r in failing),
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)


@check(
    id="PVX002",
    severity=Severity.HIGH,
    title="Container pinned only by a bare version tag",
)
def undigested_container(wf: Workflow) -> list[Finding]:
    """Flag images pinned by a bare version tag: no digest, no build hash.

    HIGH rather than critical, and only for bare tags. Phase 0 found zero
    `@sha256:` references across five nf-core pipelines while 934 of their
    container references were digest-addressed by URL or carried a build hash.
    Treating those as unpinned would condemn the entire nf-core ecosystem and
    make the report unreadable. See MVP.md section 4, "PVX002 — what counts as
    pinned".
    """
    findings: list[Finding] = []
    for path, line, refs in _group_by_directive(wf):
        failing = [r for r in refs if r.pinning is Pinning.BARE_VERSION]
        if not failing:
            continue

        prefix = _branch_phrase(failing, refs)
        tag = next((r.tag for r in failing if r.tag), None)
        findings.append(
            Finding(
                id="PVX002",
                severity=Severity.HIGH,
                file=path,
                line=line,
                problem=(
                    f"{prefix}Container image is pinned only by the version tag ':{tag}'. "
                    f"A version tag can be rebuilt and repushed, so the same tag may "
                    f"resolve to different content later."
                ),
                fix=(
                    "Pin to a digest (image@sha256:...), which is the strongest "
                    "guarantee. A tag carrying a build hash, e.g. "
                    "'samtools:1.17--h00cdaf9_0', is also accepted."
                ),
                evidence=" | ".join(r.raw for r in failing),
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)
