"""Engine detection and the run driver."""

from __future__ import annotations

from pathlib import Path

from .model import Engine, Workflow
from .parsers import nextflow, snakemake
from .paths import is_skipped


class DetectionError(Exception):
    """Raised when the engine cannot be determined from the source tree."""


#: Directories skipped when sniffing for engine markers.
_SKIP = frozenset({".git", ".github", "work", ".nextflow", "node_modules", "__pycache__", ".venv"})


def detect(root: Path) -> Engine:
    """Identify the workflow engine used under `root`.

    Ambiguity is an error, not a guess: a tree containing both engines would
    get half-parsed, and a half-parsed audit report is worse than none.
    """
    if not root.exists():
        raise DetectionError(f"path does not exist: {root}")
    if not root.is_dir():
        raise DetectionError(f"not a directory: {root}")

    nextflow_markers = _find(root, ("*.nf",), ("nextflow.config",))
    snakemake_markers = _find(root, ("*.smk",), ("Snakefile", "workflow/Snakefile"))

    if nextflow_markers and snakemake_markers:
        raise DetectionError(
            "ambiguous: found both Nextflow and Snakemake files "
            f"({nextflow_markers[0]} and {snakemake_markers[0]}). "
            "Point provenix at a single pipeline."
        )
    if nextflow_markers:
        return Engine.NEXTFLOW
    if snakemake_markers:
        return Engine.SNAKEMAKE
    raise DetectionError(
        f"no Nextflow (*.nf, nextflow.config) or Snakemake (Snakefile, *.smk) "
        f"files found under {root}"
    )


def _find(root: Path, patterns: tuple[str, ...], names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in names:
        candidate = root / name
        if candidate.is_file():
            found.append(name)
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            if is_skipped(path, root, _SKIP):
                continue
            if path.is_file():
                found.append(path.relative_to(root).as_posix())
                break
    return sorted(found)


def parse(root: Path, engine: Engine) -> Workflow:
    """Parse `root` with the parser for `engine`."""
    if engine is Engine.NEXTFLOW:
        return nextflow.parse(root)
    if engine is Engine.SNAKEMAKE:
        return snakemake.parse(root)
    raise DetectionError(f"no parser for {engine}")
