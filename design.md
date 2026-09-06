# Provenix — design

Third of three files. Read alongside `MVP.md` and `scope.md`.

- `scope.md` — boundaries, phases, what never to build
- `MVP.md` — the checks, outputs, requirements
- **this file** — how the code is structured and why

Where they disagree: scope wins on boundaries, MVP.md wins on check behaviour,
this file wins on structure.

---

## 1. Shape of the repo

```
provenix/
  pyproject.toml
  README.md
  LICENSE
  .github/workflows/ci.yml
  src/provenix/
    __init__.py
    cli.py                    argument parsing only
    engine.py                 detect engine, drive the run
    finding.py                the Finding type and severity enum
    model.py                  Workflow, Process, ConfigEntry, EnvFile, SourceInfo
    score.py                  the scoring formula, in one place
    checks/
      __init__.py             the check registry
      containers.py           PVX001, PVX002
      dependencies.py         PVX003
      reference_data.py       PVX004, PVX005
      determinism.py          PVX010
      provenance.py           PVX011, PVX012
      resources.py            PVX013, PVX021
      portability.py          PVX020, PVX022
      secrets.py              PVX030
    parsers/
      __init__.py
      nextflow.py
      snakemake.py
    report/
      terminal.py
      json_out.py
      html.py
      template.html
    data/
      stochastic_tools.yml    tool -> seed flag map for PVX010
  tests/
    test_checks.py
    test_parsers.py
    test_score.py
    fixtures/
      nextflow_bad/           a pipeline that triggers every check
      nextflow_good/          a pipeline that triggers none
      snakemake_bad/
      snakemake_good/
```

## 2. The central abstraction

Everything produces `Finding` objects. Checks emit them, reporters consume
them, nothing else crosses the boundary.

```python
@dataclass(frozen=True)
class Finding:
    id: str              # "PVX001"
    severity: Severity   # CRITICAL | HIGH | MEDIUM | SECURITY
    file: Path
    line: int
    problem: str         # one line, what is wrong
    fix: str             # one line, what to change
    evidence: str        # the offending snippet, never a secret value
```

Two rules:

**A check returns a list of Findings and nothing else.** No printing, no
formatting, no I/O beyond reading the files it was given.

**`fix` is mandatory.** A Finding without an actionable fix does not get
constructed. Enforce it in `__post_init__`.

## 3. Data flow

```
cli.check(path)
  |
  +-- engine.detect(path)         -> NEXTFLOW | SNAKEMAKE | ambiguous -> error
  |
  +-- parsers.<engine>.parse(path) -> Workflow
  |     a normalised, engine-agnostic model (section 4)
  |
  +-- for each registered check:
  |     check(workflow) -> list[Finding]
  |
  +-- sort findings: severity, then file, then line, then id
  +-- score.compute(findings) -> int
  |
  +-- report.<format>.render(findings, score)
  +-- exit code from --fail-on
```

The checks never see engine-specific syntax. That is the whole reason the
tool can be cross-engine without doubling every check.

## 4. The normalised model

This is the most important design decision in the project. Get it right and
each check is written once for both engines.

```python
@dataclass
class Workflow:
    root: Path
    engine: Engine
    processes: list[Process]     # Nextflow process / Snakemake rule
    config: list[ConfigEntry]    # nextflow.config / Snakemake config.yaml
    env_files: list[EnvFile]     # conda YAMLs
    source: SourceInfo           # git tag, commit, dirty state

@dataclass
class Process:
    name: str
    file: Path
    line: int
    container: list[ContainerRef]  # see the correction below
    conda: list[EnvFile]           # likewise: a conda directive can branch
    resources: dict[str, str]      # cpus, memory, time
    error_strategy: str | None
    publish: list[PublishSpec]
    script: str                    # the shell body, for tool and URL scanning
    emits_versions: bool

@dataclass(frozen=True)
class ContainerRef:
    raw: str                     # the reference exactly as written
    file: Path
    line: int
    pinning: Pinning             # PINNED | BARE_VERSION | MUTABLE | NO_TAG | UNRESOLVED
    tag: str | None
    branch: str | None           # "Singularity" | "Docker", when conditional
```

**Correction, recorded in Phase 1.** This section originally declared
`container: str | None` and `conda: str | None`, one raw value each. That is
wrong against real Nextflow, for the same reason the `ast.parse` claim in
section 5 was wrong, and it is left on record rather than quietly amended.

A container directive is normally a *ternary on the container engine* and
names two different images:

```groovy
container "${ workflow.containerEngine == 'singularity' ?
    'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0' :
    'biocontainers/fastqc:0.12.1--hdfd78af_0' }"
```

436 of 482 processes in the Phase 0 corpus are shaped this way, and
`nf-core/rnaseq`'s `ribodetector` module nests a second ternary on
`task.accelerator` inside each branch, giving **four** references from one
directive. Keeping one string would mean classifying one branch and discarding
the rest — a false negative on the check that matters most, since a pipeline
whose Docker branch is unpinned is unreproducible for everyone running Docker.

`conda` has the same shape, though it is rare: exactly one directive in 477
branches, `ribodetector` again, selecting between `environment.gpu.yml` and
`environment.yml`. Reading only the first branch skipped *both* environments,
so `conda` is a list too.

`branch` exists so a finding can say which runtime is affected. It is inferred
from the reference itself — a `docker://` or bare registry path is Docker, an
`oras://`, `shub://`, `library://` or `https://` reference is Singularity —
not from its position in the ternary, so it survives the Groovy closure form
(`container = { cond ? 'a' : 'b' }`, used in `conf/arm.config`) and nesting.
Findings read "The Docker branch is affected; the Singularity branch is
pinned" rather than printing two references side by side and leaving the
reader to work it out.

Every field carries `file` and `line` so findings can point at real code.
A parser that loses line numbers makes the whole tool useless.

Where the engines genuinely differ, put the difference in the parser, not in a
conditional inside a check. If you find yourself writing
`if workflow.engine == NEXTFLOW` inside `checks/`, the model is wrong.

## 5. Parsing: the hard part, decided up front

Nextflow and Snakemake are general-purpose languages. Nextflow is Groovy-based;
Snakemake is Python. Full parsing is out of reach for an MVP.

**Approach: structural scan plus targeted extraction.**

Walk the files line by line, track block boundaries by brace and indentation
depth, and extract only the directives the model needs. Do not attempt to
evaluate expressions, resolve variables, or follow includes into a full AST.

**Correction, recorded in Phase 0.** This section originally read: *"Snakemake
is the easier half. It is Python, so `ast` parses the file and rule bodies are
keyword arguments. Use `ast` where it works, and fall back to line scanning
inside rule bodies."*

That is wrong, and it matters enough to leave the reason on record. A `.smk`
file is **not valid Python**. `rule align:` is not a Python statement, and
`ast.parse` raises `SyntaxError` on essentially every Snakemake file in the
Phase 0 corpus. Snakemake's own loader rewrites the file into Python before
compiling it; it does not hand `.smk` to `ast` directly. Rule bodies are also
not keyword arguments — they are indented directive blocks, and 186 of the 191
`conda:` directives in the corpus put the value on the *following* line.

**So: line scanning for `.smk` and `Snakefile`.** Match
`^(rule|checkpoint|module|use rule)\s+\w+\s*:` at column 0, then read
4-space-indented directive names, handling the wrapped
`conda:` / newline / `"path"` form as the primary case. Module-level
`container:` and `configfile:` sit at column 0.

**`ast` is still the right tool for `workflow/scripts/`.** Those are plain
Python files and parse cleanly. Use it there and nowhere else.

Neither engine is meaningfully "the easier half". Snakemake is a smaller
surface — 285 rules across five workflows against 482 Nextflow processes — but
Nextflow needs the config resolution pass below, and Snakemake needs indentation
tracking through real Python control flow.

**Config resolution pass, added after Phase 0.** Nextflow processes are
incomplete as written: across 482 nf-core processes there are zero inline
`cpus`, `time`, `publishDir` or `errorStrategy` directives, and seeds live in
`ext.args` in `conf/modules.config` rather than in the process script. Between
parsing and checking, merge config into the model:

```
parse .nf     -> processes: name, label, container, conda, script
parse .config -> global process{} block, withLabel:/withName: selectors, params{}
resolve       -> merge into each Process by name and label; resolve params.X
                 -> Process now has cpus, memory, time, error_strategy,
                    publish, ext_args
checks        -> run on the resolved model
```

Without this pass `PVX010`, `PVX013`, `PVX021` and `PVX022` produce a false
positive on every nf-core process. The pass lives in the parser, so `checks/`
stays engine-agnostic as section 4 requires.

**Be honest about the limits.** A container reference built by string
interpolation from a variable cannot be resolved statically. When a value
cannot be determined, record it as `UNRESOLVED` rather than guessing, and have
checks skip unresolved values rather than flagging them. A false critical is
worse than a miss.

Document these limits in the README. "Provenix cannot see container references
assembled at runtime" is a fair limitation. Silently mis-flagging them is not.

## 6. The check registry

Checks register themselves with a decorator, so adding one touches one file:

```python
@check(id="PVX001", severity=Severity.CRITICAL,
       title="Container uses a mutable tag")
def mutable_container_tag(wf: Workflow) -> list[Finding]:
    ...
```

The registry gives you three things for free: `provenix list-checks`, per-check
disabling via `--disable PVX013`, and a test that asserts every registered ID
appears in MVP.md.

## 7. Determinism

Non-negotiable, because it is an audit tool.

- Sort findings explicitly before rendering: severity, file, line, id. Never
  rely on filesystem or dict ordering
- Walk directories in sorted order
- No timestamps in JSON output, so two runs diff cleanly. Put the run time in
  the HTML report only, where it is metadata rather than content
- No randomness anywhere

Write a test that runs the analyser twice on the same fixture and asserts
byte-identical JSON.

## 8. The secrets check

`PVX030` is the one check that can cause harm if done carelessly.

- Match on shape: AWS key IDs, `-----BEGIN ... PRIVATE KEY-----`, common token
  prefixes, `password =` assignments with a non-empty literal
- **Never put the matched text in `evidence`.** Set evidence to the redacted
  form, for example `password = "***"`
- Never log it, never write it to JSON, never render it to HTML
- A test must assert that a known fake secret in a fixture does not appear
  anywhere in any output format

## 9. The HTML report

One self-contained file. No CDN links, no external CSS, no fonts, no
JavaScript that fetches anything. It has to open from a filesystem in five
years, and it may be attached to a regulatory submission.

Inline everything. Keep it plain: a header with the pipeline name, score and
date, then findings grouped by severity in a table. No charts.

## 10. Testing design

**Fixture pipelines are the core of the suite.** Four small pipelines under
`tests/fixtures/`: a deliberately bad and a deliberately clean one per engine.
The bad ones trigger every check, the clean ones trigger none.

**Assert on the clean fixtures hardest.** A test that the good pipeline
produces zero findings is the false-positive regression guard, and false
positives are the thing that kills this tool.

**Test the score formula separately** so a change to it fails loudly.

**Test determinism** with the double-run byte-comparison described in section 7.

**Real pipelines are not in the test suite.** They are the Phase 1 and 2 gates,
run by hand and reported to me. Do not vendor someone else's pipeline into the
repo.

## 11. Conventions

- Python 3.10+, type hints on every public function
- `from __future__ import annotations` at the top of every module
- No dependency that executes anything. `click` or `argparse`, `pyyaml`,
  standard library. Keep the dependency list short, it is a security-adjacent
  tool
- Never `subprocess` a workflow engine. Reading `git` metadata is the only
  external call, and it must degrade to `UNRESOLVED` when git is absent
- Line length 100, ruff for linting
