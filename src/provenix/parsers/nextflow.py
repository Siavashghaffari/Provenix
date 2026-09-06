"""Nextflow DSL2 parser: structural scan plus targeted extraction.

Approach fixed in design.md section 5 and confirmed against the Phase 0 corpus.
Nextflow is Groovy; full parsing is out of reach and unnecessary. Track brace
depth on masked lines, pull out only the directives the model needs, and record
anything built at runtime as UNRESOLVED rather than guessing.

The one shape that needs real care is the container directive. 436 of 482
processes in the corpus write it as a multi-line ternary carrying two image
references:

    container "${ workflow.containerEngine == 'singularity' && !task.ext... ?
        'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0' :
        'biocontainers/fastqc:0.12.1--hdfd78af_0' }"

A single-line regex reads 46 of 482. So the value is accumulated across lines
until its quotes balance, then every string literal inside is classified, with
condition keywords like `'singularity'` filtered out by shape.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from ..model import (
    ConfigEntry,
    ContainerRef,
    Engine,
    Process,
    RemoteAsset,
    SourceInfo,
    Workflow,
)
from . import conda_env, images, nfconfig, refdata
from ._scan import Line, balanced_quotes, scan_groovy, string_literals

#: Directories that never contain pipeline logic.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".github",
        "work",
        ".nextflow",
        "node_modules",
        "__pycache__",
        ".venv",
        ".pytest_cache",
    }
)

#: Config files only active under `-profile test`. Excluded from reference-data
#: scanning; see refdata.py for why CI fixtures are out of scope.
_TEST_CONFIG = re.compile(r"(^|[_/])test[_.a-z0-9]*\.config$|(^|/)tests?/", re.IGNORECASE)

_PROCESS_HEAD = re.compile(r"^process\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
_DIRECTIVE = re.compile(r"^\s+([a-zA-Z][a-zA-Z0-9_]*)\s")
_SCRIPT_START = re.compile(r"^\s+(script|shell|exec)\s*:")
_LABEL = re.compile(r"^\s*label\s+['\"]([^'\"]+)['\"]")
_CONTAINER_DIRECTIVE = re.compile(r"^\s*container\s*[\s=]")
_CONDA_DIRECTIVE = re.compile(r"^\s*conda\s*[\s=]")
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.+)$")
_MANIFEST_VERSION = re.compile(r"^\s*version\s*=\s*['\"]([^'\"]+)['\"]")
_DOWNLOAD = re.compile(r"\b(wget|curl|aria2c|axel|rsync)\b")

#: Every nf-core version-capture form. All three are in live use:
#:   `path "versions.yml", emit: versions`  — the original mechanism
#:   `..., topic: versions`                 — the current one
#:   `..., emit: versions`                  — used where the topic cannot be.
#:      multiqc/main.nf notes that emitting to the topic would hang the
#:      pipeline forever, because its own input depends on that topic
#:      resolving. Matching only the first two reported it as unprovenanced.
_VERSION_CAPTURE = re.compile(r"versions\.yml|topic:\s*versions|emit:\s*versions")

#: A conda environment file path, quoted or not, with `${...}` tokens intact.
#: Excludes quotes and the ternary punctuation so it stops at path boundaries.
_ENV_FILE = re.compile(r"""[^\s"'?:,]+\.ya?ml\b""")


def parse(root: Path) -> Workflow:
    """Parse a Nextflow pipeline rooted at `root` into the normalised model."""
    workflow = Workflow(root=root, engine=Engine.NEXTFLOW)

    nf_files = _walk(root, (".nf",))
    config_files = _walk(root, (".config",))

    for path in nf_files:
        _parse_nf_file(path, workflow)

    selectors: list[nfconfig.Selector] = []
    params: dict[str, str] = {}
    for path in config_files:
        _parse_config_file(path, workflow)
        file_selectors, file_params, _ = nfconfig.collect(path, _scan_cache[path])
        selectors.extend(file_selectors)
        for key, value in file_params.items():
            params.setdefault(key, value)

    nfconfig.resolve(workflow, selectors, params)
    workflow.source = _read_source_info(root, workflow)
    _scan_cache.clear()
    return workflow


#: Scanned config lines, reused by the resolution pass so each file is read
#: and lexed once rather than twice.
_scan_cache: dict[Path, list[Line]] = {}


def _walk(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Every file under `root` with one of `suffixes`, in sorted order.

    Sorted because determinism is a hard requirement: same input, same output,
    same order (design.md section 7).
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in suffixes:
            found.append(path)
    return sorted(found, key=lambda p: p.as_posix())


# --------------------------------------------------------------------------
# .nf files
# --------------------------------------------------------------------------


def _parse_nf_file(path: Path, workflow: Workflow) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = scan_groovy(text)

    index = 0
    while index < len(lines):
        head = _PROCESS_HEAD.match(lines[index].masked)
        if head is None:
            index += 1
            continue
        process, index = _parse_process(head.group(1), path, lines, index, workflow)
        workflow.processes.append(process)

    _collect_script_downloads(path, lines, workflow)


def _parse_process(
    name: str, path: Path, lines: list[Line], start: int, workflow: Workflow
) -> tuple[Process, int]:
    """Read one process block. Returns the Process and the index after it."""
    process = Process(name=name, file=path, line=lines[start].number)

    depth = 0
    index = start
    script_lines: list[str] = []
    in_script = False

    while index < len(lines):
        line = lines[index]
        depth += line.masked.count("{") - line.masked.count("}")

        if index > start and _SCRIPT_START.match(line.masked):
            in_script = True

        if index > start and not in_script:
            if _CONTAINER_DIRECTIVE.match(line.masked):
                value, consumed = _accumulate(lines, index)
                for ref in _image_refs(value, path, line.number):
                    process.containers.append(ref)
                # The consumed lines are a directive *value*, not structure.
                # The closing `}"` of a container GString scans as a bare `}`
                # once the line is read on its own, which would drop the brace
                # depth to zero and end the process block right here — losing
                # the output declarations and the whole script body with it.
                index += consumed
                continue

            if _CONDA_DIRECTIVE.match(line.masked):
                value, consumed = _accumulate(lines, index)
                process.conda = value
                _register_conda(value, path, line.number, workflow)
                index += consumed
                continue

            label = _LABEL.match(line.code)
            if label is not None:
                process.labels.append(label.group(1))

        elif in_script:
            script_lines.append(line.code)

        # Version capture, normalised so the check never sees engine syntax.
        # nf-core is mid-migration between two mechanisms and both count:
        # the older `path "versions.yml", emit: versions` plus a heredoc, and
        # the newer `eval(...) , topic: versions` output tuple.
        if _VERSION_CAPTURE.search(line.code):
            process.emits_versions = True

        if depth <= 0 and index > start:
            index += 1
            break
        index += 1

    process.script = "\n".join(script_lines)
    return process, index


def _accumulate(lines: list[Line], index: int) -> tuple[str, int]:
    """Collect a directive value that may span lines.

    Appends source lines until every quote closes, which terminates correctly
    on all three container shapes found in Phase 0 — including the ternary,
    whose first line opens a double quote it does not close.
    """
    parts: list[str] = []
    consumed = 0
    max_span = 8  # generous; the deepest real case is 3 lines
    while index + consumed < len(lines) and consumed < max_span:
        parts.append(lines[index + consumed].code)
        consumed += 1
        joined = " ".join(parts)
        if balanced_quotes(joined):
            return joined, consumed
    return " ".join(parts), max(consumed, 1)


def _image_refs(directive_text: str, path: Path, line: int) -> list[ContainerRef]:
    """Pull image references out of a container directive value."""
    _, _, value = directive_text.partition("container")
    value = value.lstrip().lstrip("=").strip()

    return image_refs_from_value(value, path, line)


def image_refs_from_value(value: str, path: Path, line: int) -> list[ContainerRef]:
    """Classify every image reference in a container directive or assignment.

    A single literal is taken as written, so `container "ubuntu"` is correctly
    read as an untagged image. More than one literal means the value is an
    expression — a `"${...}"` GString or a Groovy `{ ... }` closure, both of
    which nf-core uses — and the condition keywords `'singularity'` and
    `'apptainer'` have to be filtered out by shape.
    """
    literals = string_literals(value)
    if not literals:
        return []

    # Unwrap a GString. `container "${ cond ? 'imgA' : 'imgB' }"` scans as a
    # single double-quoted literal whose *contents* are the expression, so the
    # two image references are one level down. The Groovy closure form,
    # `container = { cond ? 'imgA' : 'imgB' }`, has no outer quotes and needs
    # no unwrapping — both reach the same shape filter below.
    while len(literals) == 1 and "${" in literals[0]:
        inner = string_literals(literals[0])
        if not inner:
            break
        literals = inner

    if len(literals) == 1:
        candidates = literals
    else:
        candidates = [
            literal
            for literal in literals
            if ("/" in literal or ":" in literal) and images.looks_like_image(literal)
        ]
        if not candidates:
            return [
                ContainerRef(raw=value, file=path, line=line, pinning=images.Pinning.UNRESOLVED)
            ]

    # A directive naming more than one image is conditional, so each
    # reference gets the runtime it serves. A single reference has no branch.
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


def _register_conda(directive_text: str, path: Path, line: int, workflow: Workflow) -> None:
    """Resolve a `conda` directive to environment files or inline specs.

    A conda directive can be conditional, exactly like `container`. Only one
    case exists in the Phase 0 corpus —
    nf_rnaseq/modules/nf-core/ribodetector/main.nf:5 selects between
    `environment.gpu.yml` and `environment.yml` on `task.accelerator` — but
    reading only the first branch silently skipped both environments, so the
    directive is unwrapped the same way the container directive is.
    """
    _, _, value = directive_text.partition("conda")
    value = value.lstrip().lstrip("=").strip()
    specs = _conda_specs(value)
    conditional = len(specs) > 1

    for spec in specs:
        branch = _conda_branch(spec) if conditional else None

        if spec.lower().endswith((".yml", ".yaml")):
            resolved = _resolve_path(spec, path, workflow.root)
            if resolved is None or not resolved.is_file():
                continue
            if any(env.path == resolved and not env.inline for env in workflow.env_files):
                continue
            env = conda_env.parse_env_file(resolved)
            env.branch = branch
            workflow.env_files.append(env)
            continue

        if "${" in spec or "$" in spec:
            continue  # built at runtime, not knowable statically

        env = conda_env.parse_inline_spec(spec, path, line)
        env.branch = branch
        workflow.env_files.append(env)


def _conda_specs(value: str) -> list[str]:
    """Every environment file or inline spec a conda directive can select.

    Environment *files* are matched directly against the directive text rather
    than through string-literal extraction. Groovy permits nested double quotes
    inside a GString expression, and nf-core uses them:

        conda "${ task.accelerator ? "${moduleDir}/environment.gpu.yml"
                                   : "${moduleDir}/environment.yml" }"

    A quote-pairing scan reads those inner paths as *unquoted* text between
    literals, so both environments were silently skipped. Matching the path
    shape sidesteps the nesting entirely.
    """
    files: list[str] = []
    for match in _ENV_FILE.finditer(value):
        spec = match.group(0)
        if spec not in files:
            files.append(spec)
    if files:
        return files

    # No file reference, so this is an inline spec list.
    literals = string_literals(value)
    while len(literals) == 1 and "${" in literals[0]:
        inner = string_literals(literals[0])
        if not inner:
            break
        literals = inner
    if len(literals) == 1:
        return literals

    specs: list[str] = []
    for literal in literals:
        if ("::" in literal or "=" in literal) and literal not in specs:
            specs.append(literal)
    return specs


def _conda_branch(spec: str) -> str | None:
    """Label a conditional conda branch by the environment it selects."""
    stem = Path(spec).name.rsplit(".", 1)[0]
    lowered = stem.lower()
    if "gpu" in lowered:
        return "GPU"
    if "cpu" in lowered:
        return "CPU"
    if lowered in ("environment", "env"):
        return "default"
    return stem or None


def _resolve_path(spec: str, source: Path, root: Path) -> Path | None:
    """Resolve `${moduleDir}` / `${projectDir}` in a conda file reference."""
    value = spec.strip()
    value = value.replace("${moduleDir}", source.parent.as_posix())
    value = value.replace("$moduleDir", source.parent.as_posix())
    for token in ("${projectDir}", "$projectDir", "${baseDir}", "$baseDir"):
        value = value.replace(token, root.as_posix())
    if "${" in value or "$" in value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = source.parent / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _collect_script_downloads(path: Path, lines: list[Line], workflow: Workflow) -> None:
    """Record URLs fetched by download commands inside process scripts."""
    if _is_test_path(path, workflow.root):
        return
    for line in lines:
        if not _DOWNLOAD.search(line.code):
            continue
        for url in refdata.find_urls(line.code):
            if "${" in url or "{" in url:
                continue  # interpolated, UNRESOLVED
            if not refdata.is_reference_data("", url):
                continue
            workflow.assets.append(
                RemoteAsset(url=url, file=path, line=line.number, context="download command")
            )
    _note_checksum_state(path, lines, workflow)


# --------------------------------------------------------------------------
# .config files
# --------------------------------------------------------------------------


def _parse_config_file(path: Path, workflow: Workflow) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = scan_groovy(text)
    _scan_cache[path] = lines
    is_test = _is_test_path(path, workflow.root)

    for line in lines:
        assignment = _ASSIGNMENT.match(line.code)
        if assignment is None:
            continue
        key, value = assignment.group(1), assignment.group(2)

        workflow.config.append(
            ConfigEntry(key=key, value=value.strip(), file=path, line=line.number)
        )

        if key.split(".")[-1] == "container":
            for ref in image_refs_from_value(value, path, line.number):
                workflow.processes.append(_synthetic_process(key, path, line.number, ref))

        # nf-core overrides conda specs through `withName:` selectors too,
        # e.g. nf_rnaseq/conf/modules/prepare_genome.config:39.
        if key.split(".")[-1] == "conda":
            _register_conda(f"conda = {value}", path, line.number, workflow)

        for url in refdata.find_urls(value):
            if "${" in url or "{" in url:
                continue
            asset = RemoteAsset(url=url, file=path, line=line.number, context=key)
            if is_test or refdata.is_test_asset(key, url):
                # Out of scope, but recorded so the report can say so.
                if not refdata.is_documentation(url) and not refdata.is_container_image(key, url):
                    workflow.excluded_test_urls.append(asset)
                continue
            if not refdata.is_reference_data(key, url):
                continue
            workflow.assets.append(asset)

    if not is_test:
        _note_checksum_state(path, lines, workflow)

    version = _manifest_version(lines)
    if version is not None and workflow.source.declared_version is None:
        workflow.source.declared_version = version


def _synthetic_process(key: str, path: Path, line: int, ref: ContainerRef) -> Process:
    """A config-declared container, modelled as a process so checks see it.

    nf-core ships per-platform container configs — `containers_docker_amd64.config`
    and friends — that set `container` through `withName:` selectors rather than
    in a module. Those references need checking too.
    """
    process = Process(name=f"config:{key}", file=path, line=line, synthetic=True)
    process.containers.append(ref)
    return process


def _manifest_version(lines: list[Line]) -> str | None:
    """Read `manifest { version = '...' }`.

    The block nests — nf-core/rnaseq's spans 80 lines with a `contributors`
    list of maps — so this tracks brace depth rather than stopping at the
    first closing brace.
    """
    depth = 0
    inside = False
    for line in lines:
        if not inside and re.match(r"^\s*manifest\s*\{", line.masked):
            inside = True
            depth = 0
        if inside:
            depth += line.masked.count("{") - line.masked.count("}")
            match = _MANIFEST_VERSION.match(line.code)
            if match is not None:
                return match.group(1)
            if depth <= 0 and line.masked.count("}") > 0:
                inside = False
    return None


def _is_test_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    return bool(_TEST_CONFIG.search(relative))


def _note_checksum_state(path: Path, lines: list[Line], workflow: Workflow) -> None:
    code_text = "\n".join(line.code for line in lines)
    masked_text = "\n".join(line.masked for line in lines)
    workflow.checksum_verified[path] = refdata.has_checksum_evidence(code_text, masked_text)


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def _read_source_info(root: Path, workflow: Workflow) -> SourceInfo:
    """Read git state from the filesystem.

    Deliberately does not shell out. design.md section 11 allows reading git
    metadata, but the safety property in MVP.md section 8 is easier to defend
    absolutely: Provenix starts no subprocesses at all.
    """
    info = SourceInfo(declared_version=workflow.source.declared_version)
    git_dir = _find_git_dir(root)
    if git_dir is None:
        return info
    info.is_repo = True

    head_file = git_dir / "HEAD" if git_dir.is_dir() else None
    if head_file is not None and head_file.is_file():
        try:
            head = head_file.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return info
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            ref_path = git_dir / ref
            if ref_path.is_file():
                with contextlib.suppress(OSError):
                    info.commit = ref_path.read_text(encoding="utf-8", errors="replace").strip()
            else:
                info.commit = _packed_ref(git_dir, ref) or info.commit
        elif re.fullmatch(r"[0-9a-f]{40}", head):
            info.commit = head

    info.tag = _tag_for(git_dir, info.commit)
    return info


def _find_git_dir(root: Path) -> Path | None:
    """Locate the enclosing `.git`, walking upward as git itself does.

    A pipeline is often a subdirectory of a larger repository, and reporting
    "not under version control" for one would be wrong.
    """
    current = root.resolve()
    for candidate in [current, *current.parents]:
        git = candidate / ".git"
        if git.is_dir():
            return git
    return None


def _packed_ref(git_dir: Path, ref: str) -> str | None:
    packed = git_dir / "packed-refs"
    if not packed.is_file():
        return None
    try:
        for raw in packed.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.startswith("#") or not raw.strip():
                continue
            parts = raw.split(None, 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return parts[0].strip()
    except OSError:
        return None
    return None


def _tag_for(git_dir: Path, commit: str) -> str | None:
    """Find a tag pointing at `commit`, checking loose then packed refs."""
    if not commit or commit == "UNRESOLVED":
        return None
    tags_dir = git_dir / "refs" / "tags"
    if tags_dir.is_dir():
        for tag_file in sorted(tags_dir.rglob("*")):
            if not tag_file.is_file():
                continue
            try:
                if tag_file.read_text(encoding="utf-8", errors="replace").strip() == commit:
                    return tag_file.relative_to(tags_dir).as_posix()
            except OSError:
                continue
    packed = git_dir / "packed-refs"
    if packed.is_file():
        try:
            for raw in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = raw.split(None, 1)
                if (
                    len(parts) == 2
                    and parts[1].startswith("refs/tags/")
                    and parts[0].strip() == commit
                ):
                    return parts[1].strip()[len("refs/tags/") :]
        except OSError:
            return None
    return None
