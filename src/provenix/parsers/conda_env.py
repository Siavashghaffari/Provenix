"""Conda environment parsing, shared by both engines.

Hand-scanned rather than handed to PyYAML, because every finding must carry a
line number and PyYAML's safe_load discards them. The `dependencies:` block is
a flat list of scalars with one nested mapping (`pip:`), which is well within
what a line scan handles correctly.

Phase 0 scanned 477 environment files and found 2,869 pinned dependencies
against 10 unpinned. Four of those 10 were false positives produced by a naive
scan, and the rules below exist to kill each one:

  dependencies:
    - pip                      <- the conda idiom that enables the pip section,
    - pip:                        not a dependency to pin
        - git+https://github.com/x/y.git@0.1.0-beta   <- pinned, by git ref
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Dependency, EnvFile

#: Version constraint operators. Phase 0 pin shapes, by frequency:
#: `name=ver` (1883), `channel::name=ver` (855), `name =ver` (75),
#: `name ==ver` (13), `channel::name=ver=build` (2).
_CONSTRAINT = re.compile(r"[=<>!~]")

#: A pip requirement pinned by git ref: `git+https://host/repo.git@v1.2.3`.
_GIT_REF = re.compile(r"^(?:git\+|https?://).+@[^/@]+$")

#: The bare `pip` sentinel. Listing it unpinned is universal conda practice
#: and pinning it changes nothing about scientific reproducibility.
_PIP_SENTINEL = "pip"

_DEPS_KEY = re.compile(r"^(\s*)dependencies\s*:\s*$")
_LIST_ITEM = re.compile(r"^(\s*)-\s*(.*?)\s*$")
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:")


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing YAML comment. Only ` #` counts, so `a#b` survives."""
    idx = value.find(" #")
    if idx >= 0:
        value = value[:idx]
    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def dependency_name(spec: str) -> str:
    """The package name, with any channel prefix removed.

    `bioconda::salmon=1.10.3` -> `salmon`, `qiime2/label/r2021.4::q2cli` -> `q2cli`.
    """
    body = spec.split("::")[-1]
    return re.split(r"[\s=<>!~\[]", body, maxsplit=1)[0].strip()


def is_pinned(spec: str, *, is_pip: bool = False) -> bool:
    """True when `spec` fixes a version."""
    spec = spec.strip()
    if not spec:
        return True
    if is_pip and _GIT_REF.match(spec):
        return True
    body = spec.split("::")[-1]
    return bool(_CONSTRAINT.search(body))


def parse_env_file(path: Path) -> EnvFile:
    """Read one conda environment YAML into an `EnvFile` with line numbers."""
    env = EnvFile(path=path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return env

    in_deps = False
    deps_indent = 0
    pip_indent: int | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue

        header = _DEPS_KEY.match(raw)
        if header is not None:
            in_deps = True
            deps_indent = len(header.group(1))
            pip_indent = None
            continue

        if not in_deps:
            continue

        # A new key at or left of `dependencies:` ends the block.
        if _TOP_LEVEL_KEY.match(raw) and len(raw) - len(raw.lstrip()) <= deps_indent:
            in_deps = False
            continue

        item = _LIST_ITEM.match(raw)
        if item is None:
            continue

        indent = len(item.group(1))
        value = _unquote(_strip_inline_comment(item.group(2)))
        if not value:
            continue

        # Leaving the nested pip block.
        if pip_indent is not None and indent <= pip_indent:
            pip_indent = None

        # `- pip:` opens the nested mapping of pip requirements.
        if value.rstrip().rstrip(":") == _PIP_SENTINEL and value.rstrip().endswith(":"):
            pip_indent = indent
            continue

        is_pip = pip_indent is not None

        # The bare `pip` sentinel is not a dependency to pin.
        if not is_pip and value == _PIP_SENTINEL:
            continue

        env.dependencies.append(
            Dependency(
                raw=value,
                name=dependency_name(value),
                file=path,
                line=number,
                pinned=is_pinned(value, is_pip=is_pip),
                is_pip=is_pip,
            )
        )

    return env


def parse_inline_spec(spec: str, file: Path, line: int) -> EnvFile:
    """Parse an inline conda directive value.

    Nextflow allows `conda "bioconda::salmon=1.10.3 conda-forge::sed=4.7"` —
    53 occurrences in the Phase 0 corpus — where the packages are
    space-separated on one line rather than in a file.
    """
    env = EnvFile(path=file, inline=True, line=line)
    for token in spec.split():
        token = token.strip()
        if not token or token == _PIP_SENTINEL:
            continue
        env.dependencies.append(
            Dependency(
                raw=token,
                name=dependency_name(token),
                file=file,
                line=line,
                pinned=is_pinned(token),
            )
        )
    return env
