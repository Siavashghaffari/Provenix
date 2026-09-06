"""The Finding type and the severity enum.

Everything in Provenix produces Findings. Checks emit them, reporters consume
them, nothing else crosses that boundary (design.md section 2).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class Severity(enum.Enum):
    """Severity levels, carrying their own score deduction.

    The deduction weights are fixed by MVP.md section 7 and must not drift
    between releases: a score that changes meaning is worthless.
    """

    CRITICAL = ("critical", 15)
    HIGH = ("high", 7)
    MEDIUM = ("medium", 3)
    SECURITY = ("security", 20)

    def __init__(self, label: str, deduction: int) -> None:
        self.label = label
        self.deduction = deduction

    @property
    def rank(self) -> int:
        """Sort key. Most costly first, so a leaked credential leads the report."""
        return -self.deduction

    @classmethod
    def from_label(cls, label: str) -> Severity:
        for member in cls:
            if member.label == label:
                return member
        raise ValueError(f"unknown severity: {label!r}")


@dataclass(frozen=True)
class Finding:
    """One reproducibility or audit risk, anchored to a file and line.

    `fix` is mandatory. A finding a reader cannot act on is a bug, not a
    finding, so construction fails rather than emitting one (design.md
    section 2).

    `evidence` must never contain a secret value. PVX030 sets it to a redacted
    form; see checks/secrets.py.
    """

    id: str
    severity: Severity
    file: Path
    line: int
    problem: str
    fix: str
    evidence: str = ""
    #: Overflow detail for aggregated findings, JSON output only. Kept out of
    #: the terminal and HTML reports, which show `evidence` and up to three
    #: examples instead (MVP.md section 4, PVX005).
    detail: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Finding.id is required")
        if not self.problem.strip():
            raise ValueError(f"{self.id}: problem is required")
        if not self.fix.strip():
            raise ValueError(f"{self.id}: fix is required — a finding with no fix is a bug")
        if self.line < 0:
            raise ValueError(f"{self.id}: line must be >= 0")

    @property
    def sort_key(self) -> tuple[int, str, int, str]:
        """Deterministic ordering: severity, file, line, id (design.md section 7)."""
        return (self.severity.rank, str(self.file).replace("\\", "/"), self.line, self.id)
