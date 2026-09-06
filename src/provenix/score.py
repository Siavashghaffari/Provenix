"""The scoring formula, in one place.

Fixed by MVP.md section 7 and deliberately dull: start at 100, subtract a
fixed amount per finding, floor at 0. The formula must stay stable across
releases — a score whose meaning shifts between versions cannot be tracked
over time, which is the only reason to have a single number at all.

    critical  -15      high  -7      medium  -3      security  -20

The deductions live on `Severity` so there is exactly one definition of them.

Not-applicable checks never deduct, and the report states which checks were
skipped, so a score is always read against a known denominator (MVP.md
section 4a).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .checks import CheckOutcome
from .finding import Finding, Severity

#: Bumped only if the formula itself changes. Emitted in JSON so a consumer
#: can tell whether two scores are comparable.
FORMULA_VERSION = 1

MAX_SCORE = 100


@dataclass(frozen=True)
class Score:
    value: int
    counts: dict[Severity, int]
    checks_run: int
    checks_skipped: int

    @property
    def deductions(self) -> int:
        return MAX_SCORE - self.value


def compute(findings: Iterable[Finding], outcomes: Iterable[CheckOutcome] = ()) -> Score:
    """Score a run. Deterministic, and independent of finding order."""
    counts: dict[Severity, int] = {severity: 0 for severity in Severity}
    total = 0
    for finding in findings:
        counts[finding.severity] += 1
        total += finding.severity.deduction

    outcomes = list(outcomes)
    return Score(
        value=max(0, MAX_SCORE - total),
        counts=counts,
        checks_run=sum(1 for o in outcomes if o.applicable),
        checks_skipped=sum(1 for o in outcomes if not o.applicable),
    )
