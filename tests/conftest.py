"""Shared test fixtures.

Credential samples for PVX030 are **assembled at run time**, never written as
literals in a committed file.

A secret scanner cannot distinguish a fake credential from a live one — that
is the whole point of matching on shape — so committing a realistic-looking
key produces a genuine alert on a value that was never real. GitHub flagged
exactly that on this repository: a synthetic Google API key in a test fixture.
Rotating it was meaningless; the fix is not to commit values of that shape at
all.

So the committed fixtures carry only generic `password = "<literal>"` cases,
which match no vendor pattern, and the vendor shapes are built here from
fragments and written to a temporary directory.
"""

from __future__ import annotations

import shutil
import string
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _google_api_key() -> str:
    """`AIza` + 35 characters. Assembled, so no literal exists in the repo."""
    body = "SyD-" + string.digits + string.ascii_lowercase[:21]
    assert len(body) == 35
    return "AIza" + body


def _aws_access_key_id() -> str:
    """AWS's own documented example key, split so it is not a literal here."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _github_token() -> str:
    return "ghp_" + string.ascii_letters[:20] + string.digits + "abcdef"


def _private_key_header() -> str:
    return "-----BEGIN " + "RSA PRIVATE KEY" + "-----"


#: Every value planted by `pipeline_with_secrets`, plus the literal committed
#: in the bad fixtures. None of these may appear in any output format
#: (design.md section 8).
def planted_secrets() -> tuple[str, ...]:
    return (
        _google_api_key(),
        _aws_access_key_id(),
        _github_token(),
        _private_key_header(),
        "hunter2-not-a-real-password",
    )


def _write_secret_files(root: Path, engine: str) -> None:
    if engine == "nextflow":
        target = root / "conf" / "planted.config"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "params {\n"
            f"    aws_access_key_id = '{_aws_access_key_id()}'\n"
            f"    google_api_key    = '{_google_api_key()}'\n"
            f"    github_token      = '{_github_token()}'\n"
            "    // A reference, not a secret. Must NOT be flagged.\n"
            "    from_env          = \"${System.getenv('CI_TOKEN')}\"\n"
            "}\n",
            encoding="utf-8",
        )
    else:
        target = root / "config" / "planted.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"aws_access_key_id: '{_aws_access_key_id()}'\n"
            f"google_api_key: '{_google_api_key()}'\n"
            f"github_token: '{_github_token()}'\n"
            f"private_key: '{_private_key_header()}'\n"
            "# A reference, not a secret. Must NOT be flagged.\n"
            'from_env: "${ENV_TOKEN}"\n',
            encoding="utf-8",
        )


@pytest.fixture
def pipeline_with_secrets(tmp_path: Path):
    """Copy a bad fixture and plant vendor-shaped credentials into the copy.

    Returns a callable taking "nextflow" or "snakemake".
    """

    def build(engine: str) -> Path:
        source = FIXTURES / f"{engine}_bad"
        destination = tmp_path / f"{engine}_bad"
        shutil.copytree(source, destination)
        _write_secret_files(destination, engine)
        return destination

    return build
