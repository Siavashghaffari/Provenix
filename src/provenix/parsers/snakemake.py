"""Snakemake parser: structural scan plus targeted extraction.

Line scanning, not `ast`. design.md section 5 carries the correction and the
reason: a `.smk` file is not valid Python, `rule align:` is not a Python
statement, and `ast.parse` raises `SyntaxError` on essentially every Snakemake
file in the ground-truth corpus. Snakemake's own loader rewrites the file into
Python before compiling it.

Three shapes from Phase 0 drive the design:

*`container:` is module-level.* In 4 of the 5 corpus workflows it sits at
column 0 and applies to every rule, unlike Nextflow's per-process directive.
`sm_chipseq/workflow/rules/common.smk:9` has a commented-out one, so comments
must go first.

*Directive values wrap.* 186 of 191 `conda:` directives put the path on the
following line. That is the primary case, not the exception.

*Rule bodies are not always at four spaces.* 929 directives sit at 4, but 185
sit at 8 and 46 at 12, because rules appear inside `if` blocks. Indentation is
therefore tracked relative to the rule header rather than assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import (
    ConfigEntry,
    ContainerRef,
    Engine,
    Pinning,
    Process,
    RemoteAsset,
    SourceInfo,
    Workflow,
)
from . import conda_env, images, refdata
from ._scan import Line, merge_adjacent_literals, scan_python, string_literals

#: Directories that never contain pipeline logic.
SKIP_DIRS = frozenset(
    {".git", ".github", ".snakemake", "node_modules", "__pycache__", ".venv", ".pytest_cache"}
)

#: Workflow file names. Snakemake accepts any of these as rule sources.
WORKFLOW_NAMES = frozenset({"Snakefile", "snakefile"})
WORKFLOW_SUFFIXES = (".smk", ".rules")

#: CI fixture locations, excluded from reference-data scanning the same way
#: `conf/test*.config` is on the Nextflow side.
_TEST_PATH = re.compile(r"(^|/)\.?tests?(/|$)|(^|/)\.test(/|$)", re.IGNORECASE)

_RULE_HEAD = re.compile(r"^(\s*)(rule|checkpoint)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:")
_USE_RULE = re.compile(r"^(\s*)use\s+rule\s+([A-Za-z_*][A-Za-z0-9_]*)")
_MODULE_CONTAINER = re.compile(r"^(container(?:ized)?)\s*:\s*(.*)$")
_DIRECTIVE = re.compile(r"^(\s*)([a-z_][a-z0-9_]*)\s*:\s*(.*)$")
_DOWNLOAD = re.compile(r"\b(wget|curl|aria2c|axel|rsync)\b")

#: Rule directives whose value is captured for scanning.
_SCRIPT_KEYS = frozenset({"shell", "run", "script", "notebook"})


def parse(root: Path) -> Workflow:
    """Parse a Snakemake workflow rooted at `root` into the normalised model."""
    workflow = Workflow(root=root, engine=Engine.SNAKEMAKE)

    for path in _workflow_files(root):
        _parse_workflow_file(path, workflow)

    for path in _config_files(root):
        _parse_config_yaml(path, workflow)

    _apply_profile_defaults(root, workflow)
    _apply_global_container(workflow)
    workflow.source = _read_source_info(root)
    return workflow


def _apply_global_container(workflow: Workflow) -> None:
    """A pinned module-level container gives every rule version provenance.

    Snakemake's `container:` at column 0 applies to the whole workflow, so
    when it names a pinned image every rule runs in a known software
    environment whether or not it also declares `conda:`. seq2science pins
    `seq2science:0.7.2--pypyhdfd78af_0` this way and 39 of its rules carry no
    `conda:` directive; reporting those as unprovenanced was wrong 39 times.

    An unpinned global container earns nothing. `docker://continuumio/miniconda3`
    is a base image, not a pinned toolchain.
    """
    pinned = any(
        ref.pinning is Pinning.PINNED
        for process in workflow.processes
        if process.synthetic
        for ref in process.containers
    )
    if not pinned:
        return
    for process in workflow.processes:
        if not process.synthetic:
            process.emits_versions = True


#: Profile keys that establish a workflow-wide default, the Snakemake
#: analogue of nf-core's global `process { }` block in conf/base.config.
_PROFILE_RETRIES = re.compile(r"^\s*(retries|restart-times)\s*:\s*(\S+)")
_PROFILE_RESOURCES = re.compile(r"^\s*(default-resources|default_resources|set-threads)\s*:")


def _apply_profile_defaults(root: Path, workflow: Workflow) -> None:
    """Merge Snakemake profile defaults into every rule.

    Without this, Snakemake is held to a stricter standard than Nextflow:
    nf-core sets `errorStrategy` once in conf/base.config and every process
    inherits it, and a profile is where a Snakemake workflow does the same
    thing. None of the five Phase 0 corpus workflows ships one, so this
    changes nothing there, but a private pipeline that uses a profile would
    otherwise be reported as having no retry policy on every rule.
    """
    retries: str | None = None
    resources = False

    for path in _walk(root):
        if path.suffix not in (".yaml", ".yml"):
            continue
        parts = [p.lower() for p in path.parts]
        if "profiles" not in parts and "profile" not in parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _PROFILE_RETRIES.match(line)
            if match is not None and retries is None:
                retries = f"profile {match.group(1)}: {match.group(2)}"
            if _PROFILE_RESOURCES.match(line):
                resources = True

    if retries is None and not resources:
        return

    for process in workflow.processes:
        if process.synthetic:
            continue
        if retries is not None and not process.error_strategy:
            process.error_strategy = retries
        if resources:
            process.resources.setdefault("resources", "profile default-resources")


def _walk(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"), key=lambda p: p.as_posix())
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts)
    ]


def _workflow_files(root: Path) -> list[Path]:
    return [
        path
        for path in _walk(root)
        if path.name in WORKFLOW_NAMES or path.suffix in WORKFLOW_SUFFIXES
    ]


def _config_files(root: Path) -> list[Path]:
    """Config YAMLs. Environment files are reached through `conda:` instead."""
    found: list[Path] = []
    for path in _walk(root):
        if path.suffix not in (".yaml", ".yml"):
            continue
        parts = [p.lower() for p in path.parts]
        if "envs" in parts or "env" in parts:
            continue
        if path.name.startswith("environment."):
            continue
        if "config" in parts or path.name.startswith("config"):
            found.append(path)
    return found


def _is_test_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return bool(_TEST_PATH.search(relative))


# --------------------------------------------------------------------------
# Workflow files
# --------------------------------------------------------------------------


def _parse_workflow_file(path: Path, workflow: Workflow) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = scan_python(text)

    index = 0
    while index < len(lines):
        line = lines[index]

        # Module-level `container:` / `containerized:` at column 0 applies to
        # every rule in the workflow.
        module_container = _MODULE_CONTAINER.match(line.code)
        if module_container is not None:
            value, consumed = _value_of(lines, index, module_container.group(2), indent=0)
            _add_containers(
                value,
                path,
                line.number,
                workflow,
                name=f"workflow:{module_container.group(1)}",
                synthetic=True,
            )
            index += consumed
            continue

        head = _RULE_HEAD.match(line.masked)
        if head is not None:
            index = _parse_rule(
                name=head.group(3),
                header_indent=len(head.group(1)),
                path=path,
                lines=lines,
                start=index,
                workflow=workflow,
            )
            continue

        use_rule = _USE_RULE.match(line.masked)
        if use_rule is not None:
            index = _parse_rule(
                name=f"use rule {use_rule.group(2)}",
                header_indent=len(use_rule.group(1)),
                path=path,
                lines=lines,
                start=index,
                workflow=workflow,
            )
            continue

        index += 1

    _note_checksum_state(path, lines, workflow)


def _parse_rule(
    *,
    name: str,
    header_indent: int,
    path: Path,
    lines: list[Line],
    start: int,
    workflow: Workflow,
) -> int:
    """Read one rule block. Returns the index after it."""
    process = Process(name=name, file=path, line=lines[start].number)

    body_indent = _body_indent(lines, start, header_indent)
    if body_indent is None:
        workflow.processes.append(process)
        return start + 1

    index = start + 1
    script_parts: list[str] = []

    while index < len(lines):
        line = lines[index]
        if not line.code.strip():
            index += 1
            continue
        indent = len(line.code) - len(line.code.lstrip())
        if indent <= header_indent:
            break  # dedented out of the rule

        if indent != body_indent:
            index += 1
            continue

        directive = _DIRECTIVE.match(line.code)
        if directive is None:
            index += 1
            continue

        keyword = directive.group(2)
        value, consumed = _value_of(lines, index, directive.group(3), indent=body_indent)

        if keyword in ("container", "containerized"):
            _attach_containers(value, path, line.number, process)
        elif keyword == "conda":
            _register_conda(value, path, line.number, workflow)
            process.conda = value or None
            # MVP.md section 4: for Snakemake, a `conda:` directive is the
            # version-capture mechanism. Normalised here so the check stays
            # engine-agnostic.
            process.emits_versions = True
        elif keyword == "wrapper":
            literals = string_literals(value)
            if literals:
                process.wrapper = literals[0]
            # A wrapper tag pins the environment through the wrapper repo.
            # All 88 wrapper references in the Phase 0 corpus are version
            # pinned; treating them as missing provenance would wrongly flag
            # 26 of dna-seq-gatk-variant-calling's 30 rules.
            process.emits_versions = True
        elif keyword == "params":
            process.ext_args = f"{process.ext_args} {value}".strip()
        elif keyword in _SCRIPT_KEYS:
            script_parts.append(value)
            # Recorded per directive so a finding points at the shell block
            # that fetches the URL, not at the rule header several lines up.
            if not _is_test_path(path, workflow.root):
                _collect_script_downloads(value, path, line.number, workflow)
        elif keyword == "threads":
            process.resources["threads"] = value.strip()
        elif keyword == "resources":
            process.resources["resources"] = value.strip()
        elif keyword == "retries":
            process.error_strategy = f"retries: {value.strip()}"

        index += consumed

    process.script = "\n".join(script_parts)
    # A rule with no shell/run/script/notebook/wrapper executes nothing. The
    # canonical case is `rule all`, which only names the workflow's targets.
    process.runs_command = bool(script_parts) or process.wrapper is not None
    workflow.processes.append(process)
    return index


def _body_indent(lines: list[Line], start: int, header_indent: int) -> int | None:
    """The indentation of this rule's directives.

    Taken from the first indented line after the header rather than assumed to
    be four spaces, because rules nested inside `if` blocks push their bodies
    to 8 or 12.
    """
    for line in lines[start + 1 :]:
        if not line.code.strip():
            continue
        indent = len(line.code) - len(line.code.lstrip())
        if indent <= header_indent:
            return None
        return indent
    return None


def _value_of(lines: list[Line], index: int, inline: str, *, indent: int) -> tuple[str, int]:
    """Collect a directive's value, which usually sits on following lines.

    186 of the 191 `conda:` directives in the corpus are written as

        conda:
            "../envs/star.yaml"

    so the wrapped form is the primary case. Adjacent string literals on
    consecutive lines are concatenated the way Python concatenates them, which
    is how `sm_kallisto_sleuth/workflow/rules/ref.smk:66-68` builds a URL.
    """
    parts: list[str] = []
    if inline.strip():
        parts.append(inline.strip())
    consumed = 1

    while index + consumed < len(lines):
        line = lines[index + consumed]
        if not line.code.strip():
            consumed += 1
            continue
        line_indent = len(line.code) - len(line.code.lstrip())
        if line_indent <= indent:
            break
        parts.append(line.code.strip())
        consumed += 1

    return " ".join(parts), max(consumed, 1)


def _attach_containers(value: str, path: Path, line: int, process: Process) -> None:
    for ref in _container_refs(value, path, line):
        process.containers.append(ref)


def _add_containers(
    value: str,
    path: Path,
    line: int,
    workflow: Workflow,
    *,
    name: str,
    synthetic: bool,
) -> None:
    refs = _container_refs(value, path, line)
    if not refs:
        return
    process = Process(name=name, file=path, line=line, synthetic=synthetic)
    process.containers.extend(refs)
    workflow.processes.append(process)


def _container_refs(value: str, path: Path, line: int) -> list[ContainerRef]:
    """Classify the image references in a Snakemake container directive."""
    literals = string_literals(value)
    if not literals:
        return []

    if len(literals) == 1:
        candidates = literals
    else:
        candidates = [
            literal
            for literal in literals
            if ("/" in literal or ":" in literal) and images.looks_like_image(literal)
        ]
        if not candidates:
            return []

    conditional = len(candidates) > 1
    refs: list[ContainerRef] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        branch = images.infer_branch(candidate) if conditional else None
        ref = images.make_ref(candidate, path, line, branch=branch)
        if ref is not None:
            refs.append(ref)
    return refs


def _register_conda(value: str, path: Path, line: int, workflow: Workflow) -> None:
    """Resolve a `conda:` directive to an environment file.

    An unquoted value is a Python variable, not a path —
    `sm_kallisto_sleuth/workflow/rules/ref.smk:129` writes `conda:` followed by
    `enrichment_env`. Those cannot be resolved statically and are skipped
    rather than guessed at.
    """
    literals = string_literals(value)
    if not literals:
        return
    spec = literals[0].strip()
    if not spec or "{" in spec:
        return

    if spec.lower().endswith((".yml", ".yaml")):
        candidate = (path.parent / spec).resolve()
        already_read = any(env.path == candidate and not env.inline for env in workflow.env_files)
        if candidate.is_file() and not already_read:
            workflow.env_files.append(conda_env.parse_env_file(candidate))
        return

    if "::" in spec or "=" in spec:
        workflow.env_files.append(conda_env.parse_inline_spec(spec, path, line))


def _collect_script_downloads(script: str, path: Path, line: int, workflow: Workflow) -> None:
    """Record URLs fetched by download commands inside a rule body."""
    if not _DOWNLOAD.search(script):
        return
    # Resolve implicit string concatenation first, or a URL split across
    # source lines is read truncated at the quote boundary.
    script = merge_adjacent_literals(script)
    for url in refdata.find_urls(script):
        if "{" in url or "$" in url:
            continue  # interpolated from params or a wildcard: UNRESOLVED
        if not refdata.is_reference_data("", url):
            continue
        workflow.assets.append(
            RemoteAsset(url=url, file=path, line=line, context="download command")
        )


# --------------------------------------------------------------------------
# Config YAML
# --------------------------------------------------------------------------

_YAML_ENTRY = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$")


def _parse_config_yaml(path: Path, workflow: Workflow) -> None:
    """Scan a config YAML for reference-data URLs.

    Hand-scanned rather than loaded with PyYAML so that every finding keeps a
    line number.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    is_test = _is_test_path(path, workflow.root)
    has_checksum = False

    for number, raw in enumerate(text.splitlines(), start=1):
        code = raw.split("#", 1)[0] if not _inside_quotes_hash(raw) else raw
        if not code.strip():
            continue

        entry = _YAML_ENTRY.match(code)
        key = entry.group(2) if entry else ""
        if entry is not None:
            workflow.config.append(
                ConfigEntry(key=key, value=entry.group(3).strip(), file=path, line=number)
            )

        if refdata.CHECKSUM_KEYS.search(code):
            has_checksum = True

        for url in refdata.find_urls(code):
            if "{" in url:
                continue
            asset = RemoteAsset(url=url, file=path, line=number, context=key or "config")
            if is_test or refdata.is_test_asset(key, url):
                if not refdata.is_documentation(url) and not refdata.is_container_image(key, url):
                    workflow.excluded_test_urls.append(asset)
                continue
            if not refdata.is_reference_data(key, url):
                continue
            workflow.assets.append(asset)

    workflow.checksum_verified[path] = has_checksum


def _inside_quotes_hash(raw: str) -> bool:
    """True when a `#` in this line sits inside a quoted string."""
    hash_index = raw.find("#")
    if hash_index < 0:
        return False
    before = raw[:hash_index]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _note_checksum_state(path: Path, lines: list[Line], workflow: Workflow) -> None:
    code_text = "\n".join(line.code for line in lines)
    masked_text = "\n".join(line.masked for line in lines)
    workflow.checksum_verified[path] = refdata.has_checksum_evidence(code_text, masked_text)


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def _read_source_info(root: Path) -> SourceInfo:
    """Read git state without starting a subprocess.

    Snakemake has no standard in-repo version declaration — 0 of the 5 corpus
    workflows record one — so git is the only signal here (MVP.md section 4,
    "PVX012 — two signals").
    """
    from .nextflow import _read_source_info as read_git

    return read_git(root, Workflow(root=root, engine=Engine.SNAKEMAKE))
