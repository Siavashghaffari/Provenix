"""Path helpers for walking a pipeline.

Every directory classification Provenix makes — "skip this", "this is a
profile", "this is a config" — must look only *inside* the pipeline root.

`Path.parts` on an absolute path includes every ancestor above the root, so
matching a skip list against it lets a directory the user does not control
decide what gets parsed. GitHub Actions checks out to
`/home/runner/work/<repo>/<repo>`, and `work` is in the Nextflow skip set: every
file under the checkout was skipped, the parse returned an empty `Workflow`,
and the tool reported a clean pipeline having read nothing.

That is the worst failure mode this project has. An audit tool that says
"clean" because it saw no files is worse than one that crashes, and it is not
only a CI problem: `provenix check` on any pipeline living under a directory
named `work`, `.venv`, `node_modules`, `.github`, `config`, `env` or
`profiles` was silently affected.

These helpers take the root explicitly so the question can only be asked about
the part of the path the pipeline actually owns.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path


def relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    """The components of `path` beneath `root`.

    Falls back to the full path when `path` lies outside `root`, which the
    directory walks never produce but a caller could. Same shape of fallback
    as `parsers.nextflow._is_test_path`.
    """
    try:
        return path.relative_to(root).parts
    except ValueError:
        return path.parts


def relative_posix(path: Path, root: Path) -> str:
    """`path` relative to `root` as a forward-slash string."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def is_skipped(path: Path, root: Path, skip: Collection[str]) -> bool:
    """True when any directory *inside the pipeline* is in `skip`."""
    return any(part in skip for part in relative_parts(path, root))


def has_part(path: Path, root: Path, names: Collection[str]) -> bool:
    """True when any component inside the pipeline matches, case-insensitively.

    Used for classifying `config/`, `envs/` and `profiles/` directories, which
    have the same ancestor problem: a checkout under `/home/me/config/` must
    not make every file in the pipeline look like a config file.
    """
    return any(part.lower() in names for part in relative_parts(path, root))
