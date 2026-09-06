"""Container image reference classification.

Shared by both parsers so that PVX001 and PVX002 mean the same thing on a
Nextflow `container` directive and a Snakemake `container:` directive.

The PINNED / BARE_VERSION boundary implements MVP.md section 4, "PVX002 — what
counts as pinned", which was set from the Phase 0 evidence: zero `@sha256:`
references across five nf-core pipelines, yet 238 containers digest-addressed
through a URL path and 381 more carrying a build hash in the tag.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from ..model import ContainerRef, Pinning

#: Tags that move. Docker resolves a missing tag to `:latest`, so NO_TAG is
#: the same failure by another route; both are PVX001, both CRITICAL.
MUTABLE_TAGS = frozenset(
    {
        "latest",
        "main",
        "master",
        "dev",
        "devel",
        "develop",
        "development",
        "edge",
        "nightly",
        "head",
        "current",
        "release",
        "releases",
        "stable",
        "unstable",
        "testing",
        "snapshot",
        "rolling",
    }
)

#: URI schemes a container reference may carry. Stripped before parsing.
_SCHEMES = ("docker://", "oras://", "shub://", "library://", "https://", "http://")

#: A digest embedded in a registry URL path, as Seqera community containers
#: use: .../blobs/sha256/e0/e05ce34b...faf01/data
_URL_DIGEST = re.compile(r"/blobs/sha256/[0-9a-f]{2,}/?[0-9a-f]{32,}", re.IGNORECASE)

#: The canonical form, `image@sha256:<64 hex>`.
_AT_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}", re.IGNORECASE)

#: A content-hash tag, optionally with a build number suffix. Covers Wave
#: (`db98f81f55b64113`) and BioContainers mulled images, whose tag is a hash of
#: the exact package versions plus a build index:
#: `mulled-v2-1021c2bc...:b0c847e4fb89c343b04036e33b2daa19c4152cf5-0`.
#: Both are content-addressed and immutable.
_HEX_TAG = re.compile(r"^[0-9a-f]{12,}(-\d+)?$", re.IGNORECASE)

#: A conda build hash inside a biocontainer tag: `1.17--h00cdaf9_0`.
_BUILD_HASH_TAG = re.compile(r"--[0-9a-z_]+", re.IGNORECASE)

#: Values that are file paths, not images. nf-core ships
#: `conf/containers_conda_lock_files_*.config` whose `container =` values are
#: conda lock *files*. Treating those as untagged images was a Phase 0 false
#: positive; they are excluded here rather than in the check.
_NOT_AN_IMAGE_SUFFIX = (".txt", ".yml", ".yaml", ".json", ".config", ".nf", ".lock")


def looks_like_image(value: str) -> bool:
    """True when `value` could be a container image reference.

    Deliberately permissive on shape and strict on the known non-image cases:
    a missed image is a miss, a misread lock file is a false critical.
    """
    value = value.strip()
    if not value or value == "null":
        return False
    if any(ch.isspace() for ch in value):
        return False
    if ".conda-lock" in value:
        return False
    lowered = value.lower()
    if lowered.endswith(_NOT_AN_IMAGE_SUFFIX):
        return False
    return not (value.startswith(("$", "{")) or "${" in value)


def is_unresolved(value: str) -> bool:
    """True when the reference is assembled at runtime and cannot be read."""
    return "${" in value or value.strip().startswith("$")


def _strip_scheme(ref: str) -> str:
    for scheme in _SCHEMES:
        if ref.lower().startswith(scheme):
            return ref[len(scheme) :]
    return ref


def _decode(ref: str) -> str:
    """Percent-decode an image reference.

    Singularity image URLs on depot.galaxyproject.org encode the tag separator:
    `.../singularity/msisensor-pro%3A1.3.0--hfef96ef_0` is
    `msisensor-pro:1.3.0--hfef96ef_0`. Read literally it looks untagged, which
    made seven correctly pinned nf-core containers register as CRITICAL
    findings until this was added.
    """
    return unquote(ref) if "%" in ref else ref


def split_tag(ref: str) -> tuple[str, str | None]:
    """Split an image reference into (name, tag).

    Splits on the last colon that follows the last slash, so a registry port
    (`localhost:5000/img:1.0`) does not confuse the tag.
    """
    body = _decode(_strip_scheme(ref))
    last_slash = body.rfind("/")
    tail = body[last_slash + 1 :]
    if ":" not in tail:
        return body, None
    name_part, _, tag = tail.rpartition(":")
    return body[: last_slash + 1] + name_part, tag or None


def classify(ref: str) -> tuple[Pinning, str | None]:
    """Classify a raw image reference. Returns (pinning, tag)."""
    ref = ref.strip()
    if is_unresolved(ref):
        return Pinning.UNRESOLVED, None

    # Digest, in either the canonical or the URL-path form. Both are content
    # addressed and immutable.
    if _AT_DIGEST.search(ref) or _URL_DIGEST.search(ref):
        return Pinning.PINNED, None

    name, tag = split_tag(ref)

    # A Singularity image URL ending in a filename rather than name:tag —
    # e.g. .../myimage.sif — carries no version signal we can read.
    if tag is None:
        if ref.lower().startswith(("https://", "http://")) and name.lower().endswith(
            (".sif", ".simg", ".img")
        ):
            return Pinning.UNRESOLVED, None
        return Pinning.NO_TAG, None

    lowered = tag.lower()
    if lowered in MUTABLE_TAGS:
        return Pinning.MUTABLE, tag

    # A build hash makes the tag immutable in practice: biocontainers
    # `0.12.1--hdfd78af_0`, Wave `db98f81f55b64113`.
    if _BUILD_HASH_TAG.search(tag) or _HEX_TAG.match(tag):
        return Pinning.PINNED, tag

    return Pinning.BARE_VERSION, tag


#: Schemes that identify a Singularity/Apptainer image. Everything else is an
#: OCI registry reference, which Docker and Podman pull.
_SINGULARITY_SCHEMES = ("oras://", "shub://", "library://", "https://", "http://")


def infer_branch(ref: str) -> str:
    """Name the container runtime a reference serves.

    Inferred from the reference itself rather than from its position in the
    ternary, so it stays correct for the Groovy closure form and for nested
    conditionals like nf-core/rnaseq's ribodetector module, which carries four
    references across two nested ternaries.
    """
    lowered = ref.strip().lower()
    if lowered.startswith("docker://"):
        return "Docker"
    if lowered.startswith(_SINGULARITY_SCHEMES):
        return "Singularity"
    return "Docker"


def make_ref(raw: str, file: Path, line: int, branch: str | None = None) -> ContainerRef | None:
    """Build a `ContainerRef`, or None when `raw` is not an image at all."""
    if not looks_like_image(raw):
        if is_unresolved(raw):
            return ContainerRef(
                raw=raw, file=file, line=line, pinning=Pinning.UNRESOLVED, branch=branch
            )
        return None
    pinning, tag = classify(raw)
    return ContainerRef(raw=raw, file=file, line=line, pinning=pinning, tag=tag, branch=branch)
