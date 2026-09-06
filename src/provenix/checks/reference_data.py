"""PVX004 and PVX005 — reference-data provenance."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..finding import Finding, Severity
from ..model import RemoteAsset, Workflow
from ..parsers import refdata
from . import check

#: How many example URLs the human-readable formats show. The rest go to
#: `Finding.detail`, which only the JSON output renders (MVP.md section 4).
_EXAMPLES = 3


@check(
    id="PVX004",
    severity=Severity.CRITICAL,
    title="Reference data fetched from a mutable URL",
)
def mutable_reference_url(wf: Workflow) -> list[Finding]:
    """Flag reference data fetched from a URL whose content can change.

    A branch name or a `/latest/` path means the bytes behind the URL can be
    replaced without the URL changing, so a rerun silently uses different
    reference data.

    Documentation links and CI test fixtures are excluded upstream, in
    parsers/refdata.py. In the Phase 0 corpus that distinction is the whole
    check: of every `/latest/` and `/master/` URL found, all but one were
    documentation links sitting in comments.
    """
    findings: list[Finding] = []
    for asset in wf.assets:
        segment = refdata.mutable_segment(asset.url)
        if segment is None:
            continue
        findings.append(
            Finding(
                id="PVX004",
                severity=Severity.CRITICAL,
                file=asset.file,
                line=asset.line,
                problem=(
                    f"Reference data is fetched from a mutable URL: the path segment "
                    f"'{segment}' is a branch or a moving alias, so the downloaded content "
                    f"can change without the URL changing."
                ),
                fix=(
                    f"Point at an immutable revision instead of '{segment}' — a release tag, "
                    f"a commit SHA, or a versioned archive such as a Zenodo DOI."
                ),
                evidence=f"{asset.context} = {asset.url}",
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)


@check(
    id="PVX005",
    severity=Severity.CRITICAL,
    title="Reference data fetched with no checksum verification",
)
def unverified_reference_data(wf: Workflow) -> list[Finding]:
    """Flag downloaded reference data that is never checksum-verified.

    Aggregated to one finding per source file (MVP.md section 4). Phase 0
    found 113 reference databases declared in a single nf-core config with no
    checksum anywhere; 113 identical findings would bury every other result in
    the report even though each one is true.
    """
    by_file: dict[Path, list[RemoteAsset]] = defaultdict(list)
    for asset in wf.assets:
        by_file[asset.file].append(asset)

    findings: list[Finding] = []
    for path in sorted(by_file, key=lambda p: p.as_posix()):
        if wf.checksum_verified.get(path, False):
            continue
        assets = sorted(by_file[path], key=lambda a: (a.line, a.url))
        urls: list[str] = []
        for asset in assets:
            if asset.url not in urls:
                urls.append(asset.url)
        if not urls:
            continue

        examples = ", ".join(urls[:_EXAMPLES])
        more = f" (+{len(urls) - _EXAMPLES} more)" if len(urls) > _EXAMPLES else ""
        # "declared here" rather than "fetched here": some of these are base
        # paths like `igenomes_base = 's3://ngi-igenomes/igenomes/'` rather
        # than individual files. The audit claim is the same either way.
        count = f"{len(urls)} reference data URL{'s' if len(urls) != 1 else ''}"

        findings.append(
            Finding(
                id="PVX005",
                severity=Severity.CRITICAL,
                file=path,
                line=assets[0].line,
                problem=(
                    f"{count} declared here, none with checksum verification. A corrupted or "
                    f"substituted download would go unnoticed and change results silently."
                ),
                fix=(
                    "Record an expected md5 or sha256 for each download and verify it after "
                    "fetching, e.g. 'sha256sum -c refs.sha256'."
                ),
                evidence=f"{examples}{more}",
                detail=tuple(urls),
            )
        )
    return sorted(findings, key=lambda f: f.sort_key)
