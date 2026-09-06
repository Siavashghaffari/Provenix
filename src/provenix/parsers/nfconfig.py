"""Nextflow config resolution.

The pass design.md section 5 records as mandatory. Across the 482 nf-core
processes in the Phase 0 corpus there are zero inline `cpus`, `time`,
`publishDir` or `errorStrategy` directives, and zero seeds in process scripts.
All of it lives in `conf/base.config` and `conf/modules.config`, bound to
processes by `withLabel:` and `withName:` selectors:

    process {
        cpus   = { 1 * task.attempt }              // global default
        withLabel:process_low { cpus = { 2 * task.attempt } }
        withName:'PRESEQ_LCEXTRAP' { ext.args = '-verbose -bam -seed 1' }
    }

Without this merge, PVX010, PVX013, PVX021 and PVX022 would report a false
positive on every process of every well-formed pipeline. It lives in the
parser so `checks/` stays engine-agnostic, as design.md section 4 requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..model import ConfigEntry, Process, PublishSpec, Workflow
from ._scan import Line

_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.+)$")
_WITH_SELECTOR = re.compile(r"^\s*with(Label|Name)\s*:")
_PROCESS_BLOCK = re.compile(r"^\s*process\s*\{\s*$")
_PARAMS_BLOCK = re.compile(r"^\s*params\s*\{\s*$")
_PUBLISH_MODE = re.compile(r"""mode\s*:\s*(?:'([^']*)'|"([^"]*)"|params\.([A-Za-z0-9_]+))""")

#: Directive keys the model tracks. Everything else in a config is ignored.
_RESOURCE_KEYS = frozenset({"cpus", "memory", "time", "disk", "accelerator"})


@dataclass
class Selector:
    """One `process { }` scope: the global block, or a `withX:` selector."""

    kind: str  # "global" | "label" | "name"
    pattern: str
    file: Path
    settings: dict[str, tuple[str, int]] = field(default_factory=dict)


def collect(
    path: Path, lines: list[Line]
) -> tuple[list[Selector], dict[str, str], list[ConfigEntry]]:
    """Read `process { }` selectors and the `params { }` block from one config."""
    selectors: list[Selector] = []
    params: dict[str, str] = {}
    entries: list[ConfigEntry] = []

    stack: list[str] = []
    current: Selector | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        masked = line.masked

        if _PROCESS_BLOCK.match(masked):
            stack.append("process")
            current = Selector(kind="global", pattern="", file=path)
            selectors.append(current)
            index += 1
            continue

        if _PARAMS_BLOCK.match(masked):
            stack.append("params")
            index += 1
            continue

        selector = _WITH_SELECTOR.match(masked)
        if selector is not None and "process" in stack:
            _, _, tail = line.code.strip().partition(":")
            pattern = tail.rsplit("{", 1)[0].strip().strip("'\"")
            stack.append("selector")
            current = Selector(
                kind="label" if selector.group(1) == "Label" else "name",
                pattern=pattern,
                file=path,
            )
            selectors.append(current)
            index += 1
            continue

        assignment = _ASSIGNMENT.match(line.code)
        if assignment is not None:
            key = assignment.group(1)
            value, consumed = accumulate_value(lines, index, assignment.group(2))
            entries.append(ConfigEntry(key=key, value=value, file=path, line=line.number))
            if stack and stack[-1] == "params":
                params.setdefault(key, value)
            elif current is not None and stack and stack[-1] in ("process", "selector"):
                current.settings.setdefault(key, (value, line.number))
            index += consumed
            continue

        closes = masked.count("}") - masked.count("{")
        while closes > 0 and stack:
            popped = stack.pop()
            if popped == "selector":
                current = next((s for s in reversed(selectors) if s.kind == "global"), None)
            elif popped == "process":
                current = None
            closes -= 1

        index += 1

    return selectors, params, entries


def accumulate_value(lines: list[Line], index: int, inline: str) -> tuple[str, int]:
    """Collect an assignment value, which may span lines.

    `publishDir` in nf-core is a *list of maps* spread over a dozen lines, so
    accumulation continues until brackets, braces and parentheses balance.
    """
    parts = [inline]
    masked_parts = [lines[index].masked.split("=", 1)[-1]]
    consumed = 1
    max_span = 40

    def balanced(text: str) -> bool:
        return (
            text.count("[") == text.count("]")
            and text.count("{") == text.count("}")
            and text.count("(") == text.count(")")
        )

    while consumed < max_span and not balanced(" ".join(masked_parts)):
        if index + consumed >= len(lines):
            break
        parts.append(lines[index + consumed].code.strip())
        masked_parts.append(lines[index + consumed].masked)
        consumed += 1

    return " ".join(p for p in parts if p), consumed


def _regex_matches(pattern: str, candidates: list[str]) -> bool:
    if not any(ch in pattern for ch in ".*+?[]()^$"):
        return False
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return any(compiled.fullmatch(candidate) for candidate in candidates)


def selector_matches(selector: Selector, process: Process) -> bool:
    """True when a `withLabel:` / `withName:` selector applies to `process`."""
    for alternative in selector.pattern.split("|"):
        alternative = alternative.strip().strip("'\"")
        if not alternative:
            continue
        if selector.kind == "label":
            if alternative in process.labels or _regex_matches(alternative, process.labels):
                return True
            continue
        # `withName` patterns are regexes over the fully qualified process
        # path, e.g. '.*:ALIGN_STAR:STAR_ALIGN'. The model holds the bare
        # process name, so the last path segment is what can be compared.
        tail = alternative.rsplit(":", 1)[-1]
        if tail == process.name or _regex_matches(tail, [process.name]):
            return True
    return False


def resolve(workflow: Workflow, selectors: list[Selector], params: dict[str, str]) -> None:
    """Merge config settings into every process, in Nextflow's own order.

    Global `process { }` defaults first, then `withLabel:` selectors, then
    `withName:` selectors, which is the precedence Nextflow applies.
    """
    order = {"global": 0, "label": 1, "name": 2}
    ranked = sorted(selectors, key=lambda s: order[s.kind])

    for process in workflow.processes:
        if process.synthetic:
            continue

        applied: dict[str, tuple[str, Path, int]] = {}
        for selector in ranked:
            if selector.kind != "global" and not selector_matches(selector, process):
                continue
            for key, (value, line) in selector.settings.items():
                applied[key] = (value, selector.file, line)

        for key, (value, file, line) in applied.items():
            leaf = key.split(".")[-1]
            if leaf in _RESOURCE_KEYS:
                process.resources.setdefault(leaf, value)
            elif leaf == "errorStrategy":
                process.error_strategy = process.error_strategy or value
            elif leaf == "maxRetries":
                process.resources.setdefault("maxRetries", value)
            elif leaf == "args":
                process.ext_args = f"{process.ext_args} {value}".strip()
            elif leaf == "publishDir":
                for mode in publish_modes(value, params):
                    process.publish.append(PublishSpec(mode=mode, file=file, line=line))


def publish_modes(value: str, params: dict[str, str]) -> list[str | None]:
    """Every `mode:` in a publishDir value, resolving `params.X` lookups.

    638 of the 639 publishDir specs in the Phase 0 corpus write
    `mode: params.publish_dir_mode` rather than a literal, so without the
    param lookup the mode is unresolved almost everywhere.
    """
    modes: list[str | None] = []
    for match in _PUBLISH_MODE.finditer(value):
        literal = match.group(1) or match.group(2)
        if literal:
            modes.append(literal)
            continue
        resolved = params.get(match.group(3), "").strip().strip("'\"")
        modes.append(resolved or None)
    if not modes:
        modes.append(None)
    return modes
