"""Parser unit tests.

Every case here is a shape taken from the Phase 0 ground-truth corpus, with
the repo and line it came from. Several are regressions: they are shapes that
produced a false positive during Phase 1 and must not come back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from provenix.model import Pinning
from provenix.parsers import conda_env, refdata
from provenix.parsers._scan import scan_groovy, string_literals
from provenix.parsers.images import classify, split_tag

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# Container image classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref,expected",
    [
        # Digest, canonical form.
        ("biocontainers/fastqc@sha256:" + "a" * 64, Pinning.PINNED),
        # Digest in a URL path — Seqera community containers. 238 occurrences
        # in the corpus; nf_sarek/modules/nf-core/bwamem2/mem/main.nf:7.
        (
            "https://community-cr-prod.seqera.io/docker/registry/v2/blobs/sha256/e0/"
            "e05ce34b46ad42810eb29f74e4e304c0cb592b2ca15572929ed8bbaee58faf01/data",
            Pinning.PINNED,
        ),
        # Biocontainer build hash. nf_rnaseq/modules/nf-core/fastqc/main.nf:8.
        ("biocontainers/fastqc:0.12.1--hdfd78af_0", Pinning.PINNED),
        ("https://depot.galaxyproject.org/singularity/salmon:1.10.3--h6dccd9a_2", Pinning.PINNED),
        # Wave build tag. nf_sarek bwamem2.
        (
            "community.wave.seqera.io/library/bwa-mem2_htslib_samtools:db98f81f55b64113",
            Pinning.PINNED,
        ),
        # oras:// scheme. nf_ampliseq/conf/containers_singularity_oras_amd64.config:1.
        ("oras://community.wave.seqera.io/library/fastqc:0.12.1--5c4bd442468d75dd", Pinning.PINNED),
        # REGRESSION: %3A is an encoded ':'. Read literally this looks untagged
        # and produced 7 false CRITICAL findings.
        # nf_sarek/modules/nf-core/msisensorpro/scan/main.nf:6.
        (
            "https://depot.galaxyproject.org/singularity/msisensor-pro%3A1.3.0--hfef96ef_0",
            Pinning.PINNED,
        ),
        # REGRESSION: mulled multi-package tag is a content hash plus a build
        # index. The trailing `-0` made this read as a bare version tag.
        (
            "biocontainers/mulled-v2-1021c2bc41756fa99bc402f461dad0d1c35358c1:"
            "b0c847e4fb89c343b04036e33b2daa19c4152cf5-0",
            Pinning.PINNED,
        ),
        # Bare version tags. nf_ampliseq qiime2 modules, nf_sarek parabricks.
        ("qiime2/qiime2:2026.4", Pinning.BARE_VERSION),
        ("nf-core/pipesidle:0.1.0-beta", Pinning.BARE_VERSION),
        ("nvcr.io/nvidia/clara/clara-parabricks:4.7.1-1", Pinning.BARE_VERSION),
        ("biocontainers/python:3.9", Pinning.BARE_VERSION),
        ("continuumio/miniconda3:4.8.2", Pinning.BARE_VERSION),
        # Mutable tags.
        ("biocontainers/bwa:latest", Pinning.MUTABLE),
        ("some/image:main", Pinning.MUTABLE),
        ("some/image:dev", Pinning.MUTABLE),
        # No tag. sm_rnaseq_star/workflow/Snakefile:19 uses this exact image.
        ("continuumio/miniconda3", Pinning.NO_TAG),
        ("docker://continuumio/miniconda3", Pinning.NO_TAG),
        # Built at runtime.
        ("${params.container}", Pinning.UNRESOLVED),
    ],
)
def test_image_classification(ref: str, expected: Pinning) -> None:
    assert classify(ref)[0] is expected


def test_registry_port_is_not_a_tag() -> None:
    name, tag = split_tag("localhost:5000/myimage:1.0")
    assert name == "localhost:5000/myimage"
    assert tag == "1.0"


# --------------------------------------------------------------------------
# Comment and string scanning
# --------------------------------------------------------------------------


def test_url_double_slash_is_not_a_comment() -> None:
    """`https://` must survive comment stripping."""
    line = scan_groovy("    container 'https://depot.galaxyproject.org/singularity/x:1--h0'")[0]
    assert "https://depot.galaxyproject.org" in line.code


def test_line_comment_is_stripped() -> None:
    line = scan_groovy("params.x = 1 // https://www.nextflow.io/docs/latest/config.html")[0]
    assert "nextflow.io" not in line.code


def test_block_comment_is_stripped() -> None:
    lines = scan_groovy("/*\n  https://example.org/latest/ref.fa\n*/\nparams.y = 2")
    assert all("example.org" not in line.code for line in lines)


def test_masked_hides_string_contents_but_keeps_braces() -> None:
    """Brace counting must not be confused by braces inside strings."""
    line = scan_groovy("""process { tag "a{b}c" }""")[0]
    assert line.masked.count("{") == 1
    assert line.masked.count("}") == 1


def test_string_literals_extracts_ternary_branches() -> None:
    gstring = (
        "${ workflow.containerEngine == 'singularity' ? "
        "'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0' : "
        "'biocontainers/fastqc:0.12.1--hdfd78af_0' }"
    )
    literals = string_literals(gstring)
    assert "singularity" in literals
    assert "biocontainers/fastqc:0.12.1--hdfd78af_0" in literals


# --------------------------------------------------------------------------
# Conda dependency pinning
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,pinned",
    [
        ("dask=2022.2.1", True),
        ("bioconda::salmon=1.10.3", True),
        ("r-base =3.5", True),
        ("curl ==7.71", True),
        ("_openmp_mutex=4.5", True),
        ("bioconda::strelka=2.9.10=h9ee0642_1", True),
        ("bioconda::bcftools=1.23=*", True),
        ("python >=3.8,<3.11", True),
        ("bioconductor-vsn", False),
        ("r-ggplot2", False),
        ("conda-forge::pigz", False),
    ],
)
def test_conda_pin_shapes(spec: str, pinned: bool) -> None:
    assert conda_env.is_pinned(spec) is pinned


def test_git_ref_counts_as_pinned_for_pip() -> None:
    """REGRESSION: a naive scan called this unpinned. The `@tag` is the pin."""
    spec = "git+https://github.com/jwdebelius/q2-sidle.git@0.1.0-beta"
    assert conda_env.is_pinned(spec, is_pip=True) is True


def test_channel_prefix_stripped_from_name() -> None:
    assert conda_env.dependency_name("qiime2/label/r2021.4::q2cli=2021.4.0") == "q2cli"


def test_pip_sentinel_is_not_a_dependency(tmp_path: Path) -> None:
    """REGRESSION: bare `- pip` before a `pip:` block is conda idiom, not a dep."""
    env = tmp_path / "environment.yml"
    env.write_text(
        "dependencies:\n"
        "  - pip\n"
        "  - pip:\n"
        "      - git+https://github.com/x/y.git@v1.0\n"
        "  - salmon=1.10.3\n",
        encoding="utf-8",
    )
    parsed = conda_env.parse_env_file(env)
    names = [d.name for d in parsed.dependencies]
    assert "pip" not in names
    assert all(d.pinned for d in parsed.dependencies)


# --------------------------------------------------------------------------
# Reference data URLs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,segment",
    [
        # The one genuine PVX004 hit in the corpus:
        # nf_ampliseq/conf/ref_databases.config:416.
        ("https://raw.githubusercontent.com/moyn413/nifHdada2/master/nifH.fasta", "master"),
        ("https://raw.githubusercontent.com/nf-core/test-datasets/refs/heads/mag/x.csv", "mag"),
        ("https://ref.example.org/db/latest/genes.gtf", "latest"),
        ("https://example.org/releases/latest/download/db.tar.gz", "latest"),
        # A commit SHA in the ref position is a genuine pin.
        (
            "https://raw.githubusercontent.com/nf-core/test-datasets/"
            "7f1614baeb0ddf66e60be78c3d9fa55440465ac8/samplesheet.csv",
            None,
        ),
        # refs/tags/<tag> is a pin.
        ("https://raw.githubusercontent.com/x/y/refs/tags/v1.2.0/db.fa", None),
        # Versioned paths are stable. nf_mag/nextflow.config.
        ("https://data.gtdb.aau.ecogenomic.org/releases/release232/232.0/pkg.tar.gz", None),
        ("https://zenodo.org/records/7401545/files/checkm_data.tar.gz", None),
    ],
)
def test_mutable_segment(url: str, segment: str | None) -> None:
    assert refdata.mutable_segment(url) == segment


@pytest.mark.parametrize(
    "key,url,is_data",
    [
        ("file", "https://ndownloader.figshare.com/files/34994569", True),
        ("gff", "ftp://ftp.ncbi.nlm.nih.gov/genomes/x.gff.gz", True),
        # Metadata, not data.
        ("homePage", "https://github.com/nf-core/rnaseq", False),
        ("doi", "https://doi.org/10.5281/zenodo.1400710", False),
        # Documentation hosts.
        ("x", "https://www.nextflow.io/docs/latest/config.html", False),
        ("x", "https://q2-sidle.readthedocs.io/en/latest/reconstruction.html", False),
        # A GitHub blob URL is a web page; a raw URL is data.
        ("x", "https://github.com/x/y/blob/master/README.md", False),
        ("x", "https://github.com/x/y/raw/abc123/data.fa", True),
        # REGRESSION: container images are not reference data. Without this,
        # every Singularity https:// container counted as an unverified
        # download and PVX005 fired on every per-platform config.
        ("container", "https://depot.galaxyproject.org/singularity/x:1--h0", False),
        (
            "x",
            "https://community-cr-prod.seqera.io/docker/registry/v2/blobs/sha256/e0/ab/data",
            False,
        ),
        # CI test fixtures are out of scope by design.
        (
            "pipelines_testdata_base_path",
            "https://raw.githubusercontent.com/nf-core/test-datasets/",
            False,
        ),
    ],
)
def test_is_reference_data(key: str, url: str, is_data: bool) -> None:
    assert refdata.is_reference_data(key, url) is is_data


def test_checksum_key_with_prefix_is_detected() -> None:
    """REGRESSION: `\\b` misses `ref_md5` because `_` is a word character."""
    assert refdata.has_checksum_evidence("", "ref_md5 = 'd41d8cd98f00b204e9800998ecf8427e'")
    assert refdata.has_checksum_evidence("", "genome_sha256: abc")
    assert refdata.has_checksum_evidence("sha256sum -c refs.sha256", "")


def test_filename_containing_md5_is_not_verification() -> None:
    """nf_ampliseq fetches `2024.09.taxonomy.md5.tsv.gz`. That is not a check.

    The URL sits inside a string literal, which the masked text blanks out, so
    only the code text sees it and only the key pattern would match.
    """
    masked = "            file = [ '', '' ]"
    assert not refdata.has_checksum_evidence("", masked)
