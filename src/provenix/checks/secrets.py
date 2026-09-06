"""PVX030 — credential-shaped literals.

The one check that can do harm if done carelessly (design.md section 8).
Rules, without exception:

- The matched text never reaches `evidence`, JSON, HTML, or a log line. Only
  the file, the line, and the *kind* of credential are reported.
- The right-hand side must be a literal. `${{ secrets.AWS_KEY }}` and
  `os.environ["TOKEN"]` are references to a secret, not a secret, and flagging
  them would fire on every CI file in every repository — the Phase 0 corpus
  has dozens across `.github/workflows/`.

A test asserts that a known fake secret placed in a fixture appears in no
output format.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..finding import Finding, Severity
from ..model import Workflow
from . import check

#: Interpolation and lookup forms. A value containing any of these names a
#: secret held elsewhere; it is not the secret.
_REFERENCE = re.compile(
    r"\$\{|\{\{|\$\(|<%|os\.environ|System\.getenv|process\.env|secrets\.|params\.|config\[",
    re.IGNORECASE,
)

#: Placeholder values that are obviously not real credentials.
_PLACEHOLDER = re.compile(
    r"^(|null|none|true|false|xxx+|change[_-]?me|your[_-].*|<.*>|\*+|\.\.\.|todo|example|"
    r"dummy|placeholder|test|secret|password|token)$",
    re.IGNORECASE,
)

#: High-confidence credential shapes, matched anywhere in a line.
_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key ID", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
    ("PyPI token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,}\b")),
)

#: A credential-shaped assignment: a secret-ish key with a literal value.
_ASSIGNMENT = re.compile(
    r"""(?P<key>[A-Za-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|apikey|token|"""
    r"""access[_-]?key|private[_-]?key|credential)[A-Za-z0-9_.-]*)"""
    r"""\s*[:=]\s*(?P<quote>['"])(?P<value>[^'"]{8,})(?P=quote)""",
    re.IGNORECASE,
)

#: Files worth scanning. `.github/` is excluded outright: it is full of
#: `${{ secrets.X }}` references and contains no pipeline logic.
_SCANNED_SUFFIXES = (".nf", ".config", ".smk", ".yaml", ".yml", ".json", ".sh", ".py")
_SKIP_DIRS = frozenset({".git", ".github", "__pycache__", ".venv", "node_modules", ".snakemake"})


def _is_reference(value: str) -> bool:
    return bool(_REFERENCE.search(value))


def _scannable(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in _SCANNED_SUFFIXES or path.name in ("Snakefile", "nextflow.config"):
            found.append(path)
    return found


@check(
    id="PVX030",
    severity=Severity.SECURITY,
    title="Credential-shaped literal in config or source",
)
def credential_literal(wf: Workflow) -> list[Finding]:
    """Flag a committed credential by shape, reporting location only.

    Produces zero findings on the whole public corpus. That is the expected
    result: nobody merges an AWS key into nf-core. The check exists for
    private in-house pipelines, and is validated on fixtures (MVP.md
    section 9).
    """
    findings: list[Finding] = []

    for path in _scannable(wf.root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            if len(line) > 4000:
                continue

            kind: str | None = None
            for label, pattern in _SHAPES:
                if pattern.search(line):
                    kind = label
                    break

            if kind is None:
                match = _ASSIGNMENT.search(line)
                if match is not None:
                    value = match.group("value")
                    if not _is_reference(value) and not _PLACEHOLDER.match(value.strip()):
                        kind = f"literal value assigned to '{match.group('key')}'"

            if kind is None:
                continue

            findings.append(
                Finding(
                    id="PVX030",
                    severity=Severity.SECURITY,
                    file=path,
                    line=number,
                    problem=(
                        f"A {kind} appears here. Committed credentials are readable by "
                        f"anyone with repository access and remain in git history after "
                        f"deletion."
                    ),
                    fix=(
                        "Remove the value from the source, rotate the credential, and "
                        "supply it at runtime from a secret store or environment variable."
                    ),
                    # Location only. The matched text is never included, in any
                    # output format. See the module docstring.
                    evidence="[redacted]",
                )
            )

    return sorted(findings, key=lambda f: f.sort_key)
