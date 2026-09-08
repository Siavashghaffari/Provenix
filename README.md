# Provenix

Static reproducibility and audit analysis for bioinformatics workflows.

Provenix reads pipeline source and answers one question:

> Would this pipeline produce the same results if someone reran it in two
> years, and could you prove to an auditor what actually ran?

It works on **Nextflow** and **Snakemake**. It never executes the pipeline,
needs no cloud credentials, and needs no historical run data.

```bash
provenix check ./my-pipeline
```

---

## Why this exists

Every existing linter is tied to one engine and checks style or template
conformance, not reproducibility.

| Tool | Scope | Checks |
|---|---|---|
| `nf-core lint` | Nextflow, nf-core pipelines only | Conformance to the nf-core template |
| `awslabs/linter-rules-for-nextflow` | Nextflow only | DSL style and correctness |
| `snakemake --lint` | Snakemake only | Snakemake best practices |
| `nf-prov` | Nextflow only | Emits provenance *after* a run |

The gap is cross-engine, pre-run, compliance-framed analysis.

---

## A real report

Produced by running `provenix check` on **nf-core/viralrecon** at commit
`fa23078`, unedited:

```
provenix  viralrecon  [nextflow]
70 processes, 70 environments, 13 checks run

HIGH  (4)
  PVX002  modules/local/collapse_primers/main.nf:6
    Both the Singularity and Docker branches are affected. Container image is pinned
    only by the version tag ':3.13'. A version tag can be rebuilt and repushed, so the
    same tag may resolve to different content later.
    evidence: https://depot.galaxyproject.org/singularity/python:3.13 | quay.io/biocontainers/python:3.13
    fix: Pin to a digest (image@sha256:...), which is the strongest guarantee. A tag
         carrying a build hash, e.g. 'samtools:1.17--h00cdaf9_0', is also accepted.
  PVX002  modules/local/filter_blastn/main.nf:6
    ... (three more, same shape: ubuntu:24.04)

INFO
  10 reference URLs in CI test profiles excluded from PVX004/005
  (test fixtures, not analysis inputs; 6 files)

4 findings (4 high)
score 72/100
```

nf-core/viralrecon scores well because nf-core pins containers by build hash
and declares resources centrally. The four findings are real: `python:3.13`
and `ubuntu:24.04` are bare version tags that can be rebuilt and repushed.

---

## Install

```bash
pip install provenix
```

Python 3.10 or newer. No other runtime dependencies.

## Usage

```bash
provenix check <path>                      # terminal report
provenix check <path> --format json        # machine-readable, for CI
provenix check <path> --format html --output report.html
provenix check <path> --fail-on high       # exit 1 at this severity or above
provenix check <path> --disable PVX013     # skip a check
provenix list-checks                       # every check and the engines it applies to
```

**Exit codes.** `0` when nothing at or above `--fail-on` (default `critical`)
was found, `1` otherwise, `2` on a usage or detection error. That is what
makes it usable as a CI gate.

**Output formats.** The JSON output carries no timestamp, so two runs on the
same source diff cleanly. The HTML report is a single self-contained file with
no CDN link, no webfont and no JavaScript: it has to open from a filesystem in
five years and may be attached to a regulatory submission.

**Dependencies.** None. Standard library only, and Provenix starts no
subprocesses at all — not even to read git, which it does by parsing `.git`
directly. It is meant to be pointed at untrusted repositories.

---

## The checks

13 checks. Each finding names a file, a line, what is wrong and what to change.

### Critical — results will silently change

| ID | Check |
|---|---|
| `PVX001` | Container image uses a mutable tag (`:latest`, `:main`) or no tag |
| `PVX003` | Conda dependency without a pinned version |
| `PVX004` | Reference data fetched from a mutable URL (a branch, `/latest/`) |
| `PVX005` | Reference data fetched with no checksum verification |

### High — reproducibility or auditability is lost

| ID | Check |
|---|---|
| `PVX002` | Container pinned only by a bare version tag, no digest or build hash |
| `PVX010` | Stochastic tool invoked with no seed set |
| `PVX011` | No tool version capture |
| `PVX012` | Pipeline version not recorded |
| `PVX013` | Process has no resource directives |

### Medium — portability and operational risk

| ID | Check |
|---|---|
| `PVX020` | Hardcoded absolute path outside the workdir |
| `PVX021` | No `errorStrategy` (Nextflow) or `retries` (Snakemake) |
| `PVX022` | `publishDir` mode allows silent overwrite — *Nextflow only* |

### Security

| ID | Check |
|---|---|
| `PVX030` | Credential-shaped literal in config or source |

`PVX030` reports **location only**. The matched value never appears in the
terminal, the JSON, the HTML, or any log line. A test asserts this across all
three formats.

### What counts as a pinned container

`PVX002` does not require a `@sha256:` digest, because almost no real pipeline
uses one. Across five nf-core pipelines there are **zero** `@sha256:`
references, yet 934 of their container references are immutable in practice.
An image counts as pinned if it has a digest (in `@sha256:` form *or* in a
registry URL path, as Seqera community containers use), or a tag carrying a
build hash such as `samtools:1.17--h00cdaf9_0`. `PVX002` fires only on a bare
version tag, at HIGH. A genuinely mutable tag is `PVX001`, and stays critical.

---

## The score

A single 0–100 number, so it can be tracked over time and compared across
pipelines. Start at 100 and subtract per finding:

| Severity | Deduction |
|---|---|
| security | 20 |
| critical | 15 |
| high | 7 |
| medium | 3 |

Floored at 0. **This formula is fixed and will not change between releases** —
a score whose meaning drifts is worthless.

Checks that do not apply to an engine never deduct, and the report lists them
explicitly with a reason, so a score is always read against a known
denominator. A silent zero is worse than a visible N/A: a reader cannot tell a
clean result from an absent one.

Note that the score saturates. A pipeline with many findings floors at 0, so
it distinguishes "clean" from "not clean" better than it ranks two poor
pipelines against each other.

---

## Limits

Provenix uses a **structural scan plus targeted extraction**, not a full
parser. Nextflow is Groovy and Snakemake is Python; both are general-purpose
languages, and evaluating them is out of scope for a tool that must never
execute anything. Where a value cannot be determined it is recorded as
`UNRESOLVED` and **skipped**, because a false critical is worse than a miss.

These are the specific things Provenix cannot see. Each was found against real
pipelines, not imagined.

**Values assembled at runtime.**
- A container reference built from a variable — `container "${params.image}"` —
  cannot be resolved and is not assessed.
- A URL built by string interpolation from config is not assessed. For
  example, `rna-seq-kallisto-sleuth` builds a Pfam download URL from
  `"...releases/" "Pfam{params.release}/..."`; the release number lives in
  `config.yaml` and the URL is skipped.
- A URL held in a Python variable — `shell("wget {url} ...")` in `seq2science`
  — is not assessed.

**Scope of the walk.**
- Snakemake `include:` directives are not followed. Every workflow file under
  the root is walked instead, which covers the same ground and more.
- Nextflow config files are all read, including profile-specific ones that a
  given run might not activate. A directive present but conditionally disabled
  reads as present.
- Snakemake profiles are read for `retries` and `default-resources` defaults,
  but other profile settings are ignored.

**What is deliberately excluded, and reported when it happens.**
- CI test fixtures. `conf/test*.config`, `pipelines_testdata_base_path` and
  paths under `tests/` or `.test/` fetch test data, not analysis inputs.
  Excluding them is a judgement call, so the report prints an `INFO` line
  counting what was skipped and why. Nothing is dropped silently.
- `.github/` is not scanned at all. It is full of `${{ secrets.X }}`
  references and contains no pipeline logic.

**Aggregated findings.** `PVX005`, `PVX013` and `PVX021` are systemic: where
they fire they usually fire on most of a file. They report **one finding per
source file** with up to three examples; the full list is in the JSON output
under `detail`. Without this, one Snakemake workflow produced 132 identical
`PVX021` findings and every Snakemake pipeline floored the score at 0.

**PVX010 is conservative by design.** The stochastic-tool map in
`src/provenix/data/stochastic_tools.yml` lists only tools where either
nf-core sets a seed in a production path, or the tool's purpose is random
subsampling. Tools with a fixed default seed — bowtie2, mafft, fasttree,
vsearch, iqtree, raxml — are deliberately absent, because absence of the flag
does not mean the run is irreproducible. Including them produced 9 false
positives.

**PVX020 and PVX030 are validated on fixtures, not on public pipelines.**
Both produce zero findings across the entire public corpus. That is the
correct result, not a gap: nf-core and the public Snakemake workflows are
community-reviewed, and nobody merges an AWS key or a `/home/alice/refs` path
into them. The target user is a private in-house pipeline, which is exactly
where both live.

---

## Validation

Provenix is developed against ten real public pipelines, pinned to exact
commits. They are not vendored into this repository; they are the manual gate
for each phase.

| Engine | Pipelines |
|---|---|
| Nextflow | nf-core/rnaseq, sarek, mag, viralrecon, ampliseq |
| Snakemake | dna-seq-gatk-variant-calling, rna-seq-star-deseq2, chipseq, rna-seq-kallisto-sleuth, seq2science |

Every finding on that corpus has been verified by hand. The automated suite
uses four fixture pipelines — a deliberately clean and a deliberately bad one
per engine. The clean fixtures assert **zero findings** and are the
false-positive regression guard; they contain every construct that caused a
false positive during development, including percent-encoded container tags,
mulled multi-package images, conda lock files, documentation links in
comments, and Snakemake rules nested inside `if` blocks.

Output is deterministic: the same input produces byte-identical output, and a
test asserts it.

---

## License

MIT.
