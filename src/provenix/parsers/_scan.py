"""Comment and string-literal scanning, shared by both parsers.

Phase 0 established that this is where the false positives live. Two examples
from the ground-truth corpus:

- Almost every `/latest/` and `/master/` URL in real pipelines is a
  documentation link inside a comment, not a data fetch. Matching before
  stripping comments makes PVX004 useless.
- `https://depot.galaxyproject.org/...` contains `//`. A naive `//` comment
  stripper destroys the container reference it was meant to read.

So comment removal has to know about string literals, and `//` preceded by a
colon is never a comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Characters substituted for string-literal contents in `masked`. Chosen so
#: that braces, quotes and colons inside strings cannot affect structural
#: parsing.
_MASK = "\x00"


@dataclass(frozen=True)
class Line:
    """One source line, in three forms.

    `raw`     — exactly as written, for evidence.
    `code`    — comments removed, string contents intact, for value extraction.
    `masked`  — comments removed, string contents blanked, for brace counting
                and structural matching.
    """

    number: int
    raw: str
    code: str
    masked: str

    @property
    def stripped(self) -> str:
        return self.code.strip()


def scan_groovy(text: str) -> list[Line]:
    """Scan Nextflow/Groovy source into `Line` records.

    Handles `//` line comments, `/* */` block comments, single and double
    quoted strings, and triple-quoted script bodies. A `//` immediately
    preceded by `:` is treated as part of a URL scheme, not a comment.
    """
    return _scan(text, line_comment="//", block=("/*", "*/"), triples=('"""', "'''"))


def scan_python(text: str) -> list[Line]:
    """Scan Snakemake/Python source into `Line` records.

    Snakemake files are not valid Python (design.md section 5), so this is a
    lexical scan, not `ast`. `#` starts a comment outside a string.
    """
    return _scan(text, line_comment="#", block=None, triples=('"""', "'''"))


def _scan(
    text: str,
    *,
    line_comment: str,
    block: tuple[str, str] | None,
    triples: tuple[str, ...],
) -> list[Line]:
    lines: list[Line] = []
    in_block = False
    in_triple: str | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        code_chars: list[str] = []
        mask_chars: list[str] = []
        i = 0
        n = len(raw)
        quote: str | None = None

        while i < n:
            ch = raw[i]

            # Inside a triple-quoted string: content is preserved, look for the close.
            if in_triple is not None:
                if raw.startswith(in_triple, i):
                    code_chars.append(in_triple)
                    mask_chars.append(_MASK * len(in_triple))
                    i += len(in_triple)
                    in_triple = None
                else:
                    code_chars.append(ch)
                    mask_chars.append(_MASK)
                    i += 1
                continue

            # Inside a block comment: drop everything until the close.
            if in_block and block is not None:
                if raw.startswith(block[1], i):
                    in_block = False
                    i += len(block[1])
                else:
                    i += 1
                continue

            # Inside a single-line string literal.
            if quote is not None:
                code_chars.append(ch)
                mask_chars.append(_MASK if ch != quote else ch)
                if ch == "\\" and i + 1 < n:
                    code_chars.append(raw[i + 1])
                    mask_chars.append(_MASK)
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue

            # Outside any string or comment.
            opened_triple = next((t for t in triples if raw.startswith(t, i)), None)
            if opened_triple is not None:
                in_triple = opened_triple
                code_chars.append(opened_triple)
                mask_chars.append(_MASK * len(opened_triple))
                i += len(opened_triple)
                continue

            if block is not None and raw.startswith(block[0], i):
                in_block = True
                i += len(block[0])
                continue

            if raw.startswith(line_comment, i):
                # `://` is a URL scheme, not a comment. Only relevant for Groovy.
                if line_comment == "//" and i > 0 and raw[i - 1] == ":":
                    code_chars.append(raw[i : i + 2])
                    mask_chars.append(raw[i : i + 2])
                    i += 2
                    continue
                break  # rest of the line is a comment

            if ch in ("'", '"'):
                quote = ch
                code_chars.append(ch)
                mask_chars.append(ch)
                i += 1
                continue

            code_chars.append(ch)
            mask_chars.append(ch)
            i += 1

        lines.append(
            Line(
                number=number,
                raw=raw,
                code="".join(code_chars).rstrip(),
                masked="".join(mask_chars).rstrip(),
            )
        )

    return lines


def string_literals(text: str) -> list[str]:
    """Every single- or double-quoted literal in `text`, contents only.

    Used to pull the two image references out of a Nextflow container ternary.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    break
                buf.append(text[i])
                i += 1
            out.append("".join(buf))
            i += 1
            continue
        i += 1
    return out


def balanced_quotes(text: str) -> bool:
    """True when every quote in `text` is closed.

    Drives multi-line directive accumulation: a Nextflow container ternary is
    read by appending lines until the double and single quotes both balance.
    """
    single = 0
    double = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and double % 2 == 0:
            single += 1
        elif ch == '"' and single % 2 == 0:
            double += 1
        i += 1
    return single % 2 == 0 and double % 2 == 0


#: A closing quote followed only by whitespace then a matching opening quote.
#: That is Python implicit string concatenation, not two separate values.
_ADJACENT = re.compile(r"(['\"])[ \t]*\1")


def merge_adjacent_literals(text: str) -> str:
    """Join Python string literals that are implicitly concatenated.

    Snakemake shell blocks build long commands this way:

        shell:
            "(curl -L ftp://ftp.ebi.ac.uk/pub/databases/Pfam/releases/"
            "Pfam{params.release}/Pfam-A.{wildcards.ext}.gz | "
            "gzip -d > {output}) 2> {log}"

    Scanned line by line, a URL regex stops at the first closing quote and
    yields `ftp://ftp.ebi.ac.uk/pub/databases/Pfam/releases/` — a URL that
    does not exist, missing the interpolated part that makes it unresolvable.
    Merging first means the URL is seen whole, complete with its `{...}`
    placeholder, and correctly skipped as UNRESOLVED.
    """
    return _ADJACENT.sub("", text)
