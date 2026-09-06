"""The normalised, engine-agnostic workflow model.

This is the abstraction that lets one check serve both engines (design.md
section 4). Parsers produce it; checks consume it and never see engine syntax.

Deviation from design.md section 4, now recorded there too: `Process.container`
is a *list* of `ContainerRef`, not a single string, and `Process.conda` is a
list for the same reason. See design.md section 4 for the full rationale.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

#: Sentinel for a value that cannot be determined statically — a container
#: assembled by string interpolation, a URL held in a variable. Checks skip
#: unresolved values rather than guessing. A false critical is worse than a
#: miss (design.md section 5).
UNRESOLVED = "UNRESOLVED"


class Engine(enum.Enum):
    NEXTFLOW = "nextflow"
    SNAKEMAKE = "snakemake"

    def __str__(self) -> str:
        return self.value


class Pinning(enum.Enum):
    """How firmly an image reference is pinned.

    The boundary between PINNED and BARE_VERSION is the Phase 0 finding that
    no nf-core container uses `@sha256:` yet almost all are immutable in
    practice. See MVP.md section 4, "PVX002 — what counts as pinned".
    """

    #: Digest, or a tag carrying a build hash. Immutable.
    PINNED = "pinned"
    #: A bare version tag: conventionally stable, not cryptographically so.
    BARE_VERSION = "bare_version"
    #: `:latest`, `:main`, `:dev` and friends. Moves under you.
    MUTABLE = "mutable"
    #: No tag at all, which Docker resolves to `:latest`.
    NO_TAG = "no_tag"
    #: Built at runtime from a variable. Not knowable statically.
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ContainerRef:
    """One container image reference, as written, with its verdict.

    `branch` names the runtime this reference serves when the directive is
    conditional — "Singularity" or "Docker". A finding that says *which*
    branch is unpinned is actionable; two references side by side are a
    puzzle. It is None when the directive names a single unconditional image.
    """

    raw: str
    file: Path
    line: int
    pinning: Pinning
    #: The tag portion, when one could be isolated. For reporting only.
    tag: str | None = None
    branch: str | None = None


@dataclass(frozen=True)
class Dependency:
    """One conda/pip dependency line from an environment file or inline spec."""

    raw: str
    name: str
    file: Path
    line: int
    pinned: bool
    #: True for entries under a nested `pip:` mapping.
    is_pip: bool = False


@dataclass
class EnvFile:
    """A conda environment YAML, or an inline conda spec treated as one."""

    path: Path
    dependencies: list[Dependency] = field(default_factory=list)
    #: True when this came from an inline `conda "a=1 b=2"` directive rather
    #: than a real file on disk.
    inline: bool = False
    #: Line of the inline directive, when `inline` is True.
    line: int = 0
    #: The conditional branch this environment serves, when the `conda`
    #: directive is a ternary. nf-core/rnaseq's ribodetector module selects
    #: between environment.gpu.yml and environment.yml on `task.accelerator`.
    branch: str | None = None


@dataclass(frozen=True)
class RemoteAsset:
    """A URL the workflow fetches data from."""

    url: str
    file: Path
    line: int
    #: The config key or command the URL was attached to, for evidence.
    context: str


@dataclass(frozen=True)
class PublishSpec:
    mode: str | None
    file: Path
    line: int


@dataclass
class Process:
    """A Nextflow process or a Snakemake rule, after config resolution."""

    name: str
    file: Path
    line: int
    containers: list[ContainerRef] = field(default_factory=list)
    conda: str | None = None
    labels: list[str] = field(default_factory=list)
    resources: dict[str, str] = field(default_factory=dict)
    error_strategy: str | None = None
    publish: list[PublishSpec] = field(default_factory=list)
    script: str = ""
    #: Merged `ext.args` from config selectors. Phase 0 found every nf-core
    #: seed lives here rather than in the script.
    ext_args: str = ""
    emits_versions: bool = False
    #: Snakemake `wrapper:` tag, which pins the environment via the wrapper
    #: repository. None for Nextflow.
    wrapper: str | None = None
    #: False for a rule that executes nothing — a Snakemake target rule
    #: like `rule all`, which only aggregates inputs. Such a rule needs no
    #: version capture, no resources and no retry policy, so the checks
    #: that ask for those skip it.
    runs_command: bool = True
    #: True for a container declared in config via a `withName:` selector
    #: rather than in a process body. Modelled as a process so checks see
    #: it, but excluded from process counts.
    synthetic: bool = False


@dataclass(frozen=True)
class ConfigEntry:
    key: str
    value: str
    file: Path
    line: int


@dataclass
class SourceInfo:
    """Git state. Degrades to UNRESOLVED when git is absent (design.md 11)."""

    is_repo: bool = False
    commit: str = UNRESOLVED
    tag: str | None = None
    dirty: bool | None = None
    #: `manifest.version` for Nextflow. Snakemake has no standard equivalent.
    declared_version: str | None = None


@dataclass
class Workflow:
    root: Path
    engine: Engine
    processes: list[Process] = field(default_factory=list)
    config: list[ConfigEntry] = field(default_factory=list)
    env_files: list[EnvFile] = field(default_factory=list)
    assets: list[RemoteAsset] = field(default_factory=list)
    #: Files scanned for reference data, mapped to whether that file contains
    #: any checksum verification. Drives PVX005's per-file aggregation.
    checksum_verified: dict[Path, bool] = field(default_factory=dict)
    #: URLs skipped because they belong to CI test profiles rather than to the
    #: analysis. Reported as an INFO line so the exclusion is visible: nothing
    #: is dropped without the report saying so.
    excluded_test_urls: list[RemoteAsset] = field(default_factory=list)
    source: SourceInfo = field(default_factory=SourceInfo)

    def rel(self, path: Path) -> str:
        """Path relative to the workflow root, forward slashes, for output."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()
