"""Provenix — static reproducibility analysis for bioinformatics workflows."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    #: Single source of truth: the version declared in pyproject.toml, read
    #: back from installed package metadata. Hardcoding it here as well let
    #: the two drift, and a report that stamps the wrong tool version is the
    #: opposite of what an audit tool is for.
    __version__ = _version("provenix")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
