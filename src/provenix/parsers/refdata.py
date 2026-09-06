"""Reference-data URL classification, shared by both engines.

Phase 0's central finding for PVX004: in the ground-truth corpus, nearly every
`/latest/`, `/master/` and `/main/` URL is a documentation link inside a
comment, and exactly one is a real data fetch. Comment stripping happens in
`_scan`; this module decides which of the surviving URLs actually name
reference data, and whether the URL can move.

Two exclusions are deliberate and documented in the README:

*Metadata keys.* `homePage`, `doi`, `license` and `citation` carry URLs that
are not data. 13 `homePage` and 10 `doi` values in the corpus.

*Test data.* `pipelines_testdata_base_path`, `test_data_base` and paths under
`conf/test*.config` fetch CI fixtures, not the reference data a production run
depends on. Some are branch refs and would fire — nf-core/ampliseq points at
`refs/heads/ampliseq` while nf-core/rnaseq pins a commit SHA — but a finding
about CI fixtures is not a finding about whether the pipeline reproduces, and
mixing the two costs the report its credibility.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlparse

#: Path segments that mean "whatever is newest", wherever they appear.
MUTABLE_SEGMENTS = frozenset(
    {
        "latest",
        "current",
        "master",
        "main",
        "dev",
        "devel",
        "develop",
        "head",
        "stable",
        "release",
        "newest",
        "trunk",
        "default",
    }
)

#: Hosts that serve documentation, never reference data.
DOC_HOSTS = frozenset(
    {
        "www.nextflow.io",
        "nextflow.io",
        "readthedocs.io",
        "docs.qiime2.org",
        "bioconductor.org",
        "www.bioconductor.org",
        "doi.org",
        "dx.doi.org",
        "nf-co.re",
        "www.nf-co.re",
        "en.wikipedia.org",
        "www.biorxiv.org",
        "biorxiv.org",
        "pubmed.ncbi.nlm.nih.gov",
        "stackoverflow.com",
        "json-schema.org",
        "schema.org",
        "anaconda.org",
        "docs.seqera.io",
        "www.ebi.ac.uk",
        "snakemake.readthedocs.io",
        "academic.oup.com",
        "genome-source.gi.ucsc.edu",
    }
)

#: Config keys whose URL value is metadata, not data.
METADATA_KEYS = frozenset(
    {
        "homepage",
        "doi",
        "license",
        "citation",
        "description",
        "title",
        "dbversion",
        "author",
        "email",
        "url_docs",
        "docsurl",
        "manifest",
        "helpmessage",
        "schema",
    }
)

#: Substrings in a key or URL that mark CI test fixtures.
TEST_MARKERS = ("testdata", "test_data", "test-datasets", "test-data", "testdatasets")

#: Config keys whose URL value is a container image, not reference data.
#: nf-core writes Singularity images as https:// URLs, so without this every
#: container in every per-platform config would be counted as an unverified
#: reference download.
CONTAINER_KEYS = frozenset({"container", "containeroptions", "containerengine"})

#: Hosts that serve container images rather than reference data.
CONTAINER_HOSTS = frozenset(
    {
        "depot.galaxyproject.org",
        "community-cr-prod.seqera.io",
        "community.wave.seqera.io",
        "wave.seqera.io",
        "quay.io",
        "registry.hub.docker.com",
        "index.docker.io",
        "docker.io",
        "ghcr.io",
        "nvcr.io",
        "public.ecr.aws",
        "mirror.gcr.io",
        "gcr.io",
    }
)

#: A git commit SHA used as a ref: 7 to 40 hex characters.
_SHA_REF = re.compile(r"^[0-9a-f]{7,40}$")

#: Evidence that a file verifies what it downloads. The command forms are
#: matched against source text; the key form is matched against masked text so
#: that a *filename* containing `md5` — nf-core/ampliseq fetches
#: `2024.09.taxonomy.md5.tsv.gz` — is not mistaken for verification.
CHECKSUM_COMMANDS = re.compile(
    r"\b(md5sum|sha1sum|sha256sum|sha512sum|shasum|openssl\s+dgst)\b|--checksum\b"
)
#: Prefixes and suffixes are allowed, so `ref_md5 =` and `genome_sha256:` both
#: count. A leading `\b` misses those: `_` is a word character, so there is no
#: word boundary before `md5` in `ref_md5`.
CHECKSUM_KEYS = re.compile(
    r"[A-Za-z0-9_-]*(md5|sha1|sha256|sha512|checksum)[A-Za-z0-9_-]*\s*[=:]",
    re.IGNORECASE,
)

_URL = re.compile(r"(?:https?|ftp|ftps|s3|gs|az)://[^\s'\"<>,;\]\}\)]+")


def find_urls(text: str) -> list[str]:
    """Every URL-shaped token in `text`."""
    return [m.group(0).rstrip(".,;") for m in _URL.finditer(text)]


def is_test_asset(key: str, url: str) -> bool:
    """True when this URL is CI test data rather than pipeline reference data."""
    haystack = f"{key.lower()} {url.lower()}"
    return any(marker in haystack for marker in TEST_MARKERS)


def is_documentation(url: str) -> bool:
    """True when the URL points at a document rather than at data."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = parsed.netloc.lower()
    if not host:
        return True
    if host in DOC_HOSTS or any(host.endswith("." + d) for d in DOC_HOSTS):
        return True
    # A GitHub web page is documentation; a raw or release asset is data.
    if host in ("github.com", "www.github.com"):
        path = parsed.path
        return "/raw/" not in path and "/releases/download/" not in path
    return False


def is_container_image(key: str, url: str) -> bool:
    """True when this URL is a container image rather than reference data."""
    if key.lower().strip().split(".")[-1] in CONTAINER_KEYS:
        return True
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return host in CONTAINER_HOSTS


def is_reference_data(key: str, url: str) -> bool:
    """True when `url`, attached to config key `key`, names reference data."""
    if key.lower().strip() in METADATA_KEYS:
        return False
    if is_container_image(key, url):
        return False
    if is_documentation(url):
        return False
    return not is_test_asset(key, url)


def mutable_segment(url: str) -> str | None:
    """The path segment that makes this URL mutable, or None if it is stable.

    `raw.githubusercontent.com` gets special handling: its path is
    `/<owner>/<repo>/<ref>/<file...>`, so the ref sits at a known position and
    a commit SHA there is a genuine pin. `refs/heads/<branch>` is a branch.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower()
    segments = [s for s in PurePosixPath(parsed.path).parts if s not in ("/", "")]

    if host in ("raw.githubusercontent.com", "objects.githubusercontent.com"):
        ref_index = 2
        if len(segments) > ref_index:
            # `.../refs/heads/<branch>/...` names a branch explicitly.
            if segments[ref_index] == "refs" and len(segments) > ref_index + 2:
                if segments[ref_index + 1] in ("heads", "remotes"):
                    return segments[ref_index + 2]
                return None  # refs/tags/<tag> is a pin
            ref = segments[ref_index]
            if _SHA_REF.match(ref):
                return None
            return ref
        return None

    for segment in segments:
        if segment.lower() in MUTABLE_SEGMENTS:
            return segment
    return None


def has_checksum_evidence(code_text: str, masked_text: str) -> bool:
    """True when a file shows any sign of verifying what it downloads."""
    return bool(CHECKSUM_COMMANDS.search(code_text) or CHECKSUM_KEYS.search(masked_text))
