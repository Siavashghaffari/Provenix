# Provenix — MVP

Paste this whole file into Claude Code as the build brief, alongside `scope.md`
and `design.md`.

---

## 1. What to build

A static analyser for bioinformatics workflows that answers one question:

> Would this pipeline produce the same results if someone reran it in two
> years, and could you prove to an auditor what actually ran?

It reads pipeline source code. It never executes the pipeline, needs no cloud
credentials, and needs no historical run data.

```bash
provenix check ./my-pipeline
```

Output: a severity-scored report of reproducibility and audit risks, with file,
line, why it matters and how to fix it.

## 2. Why this gap exists

Every existing linter is tied to one engine and checks style or template
conformance, not reproducibility.

| Tool | Scope | Checks |
|---|---|---|
| `nf-core lint` | Nextflow, nf-core pipelines only | Conformance to the nf-core template |
| `awslabs/linter-rules-for-nextflow` | Nextflow only | DSL style and correctness |
| `snakemake --lint` | Snakemake only | Snakemake best practices |
| `nf-prov` | Nextflow only | Emits provenance *after* a run |
| Seqera Co-Scientist | Nextflow, inside their platform | Diagnoses failures at runtime |

The gap is cross-engine, pre-run, compliance-framed analysis. It is defensible
by incentive, not just by being unbuilt: the Nextflow vendor will never ship a
tool whose value is partly serving Snakemake and WDL users.

Verified by search on 5 September 2026. Absence of search results is not proof.
Re-check before investing heavily.

## 3. Scope of the MVP

**Engines:** Nextflow (DSL2) and Snakemake. WDL is phase 4, not MVP.

**Analysis:** static only. Parse files, never run the workflow.

## 4. The checks

Each check has an ID, a severity, and a fix. Do not add checks beyond this list
in the MVP.

### Critical — results will silently change

| ID | Check |
|---|---|
| `PVX001` | Container image uses a mutable tag (`:latest`, `:main`, `:dev`) or no tag |
| `PVX003` | Conda/mamba dependency without a pinned version (`bwa` not `bwa=0.7.17`) |
| `PVX004` | Reference data fetched from a mutable URL (`/latest/`, `/current/`, a branch name) |
| `PVX005` | Reference data fetched with no checksum verification |

### High — reproducibility or auditability is lost

| ID | Check |
|---|---|
| `PVX002` | Container image pinned only by a bare version tag, with no digest and no build hash |
| `PVX010` | Stochastic tool invoked with no seed set (a curated tool-to-flag map) |
| `PVX011` | No tool version capture (Nextflow: no `versions.yml` emit; Snakemake: no version pinning or `conda:` directive) |
| `PVX012` | Pipeline source not version-controlled, or no tag/commit recorded in config |
| `PVX013` | Process has no resource directives (`cpus`, `memory`, `time`), so a rerun can OOM differently |

### Medium — portability and operational risk

| ID | Check |
|---|---|
| `PVX020` | Hardcoded absolute path outside the workdir |
| `PVX021` | No `errorStrategy` (Nextflow) or `retries` (Snakemake) defined |
| `PVX022` | `publishDir` mode allows silent overwrite |

### Security

| ID | Check |
|---|---|
| `PVX030` | Credential-shaped literal in config or source (AWS key, token, password, private key header) |

`PVX030` is a static heuristic. Never print the matched secret value in output.
Report file and line only.

13 checks. The original list in this section numbered 14 — it was described
as 15, which was an off-by-one in the prose, not in the table.
`PVX023` (non-deterministic ordering) was then cut after Phase 0: the one
real instance in the ground-truth corpus sits three lines from a near-identical
case that is correctly sorted, so honest detection needs dataflow analysis. It
has no Nextflow analogue either. Cut rather than shipped noisy, per §11 and
`scope.md` §6.

### PVX002 — what counts as pinned

Phase 0 found zero `@sha256:` references across five nf-core pipelines, yet
their images are immutable in practice. Treat an image as **pinned** if it has:

- a `@sha256:<64hex>` digest, or
- a digest in the URL path, `.../blobs/sha256/<64hex>/...`, as Seqera
  community containers use, or
- a tag carrying a build hash: `<version>--<buildhash>` (biocontainers, e.g.
  `samtools:1.17--h00cdaf9_0`) or a Wave-style hex build tag.

`PVX002` fires only on a **bare version tag** with no digest and no build hash,
for example `qiime2/qiime2:2026.4`. Severity is HIGH, not critical: these images
are conventionally immutable but not cryptographically guaranteed. A genuinely
mutable tag (`:latest`, `:main`, no tag) is `PVX001` and stays CRITICAL.

The fix text must still name the digest as best practice.

### PVX005 — aggregate per source file

Reference data without checksum verification is near-universal, so one finding
per URL would bury the report. Emit **one finding per source file**: "113
reference URLs, none with checksum verification", naming up to 3 example URLs.
The full list goes in the JSON output only. Severity stays CRITICAL.

### PVX012 — two signals

Git state is the primary signal and is engine-agnostic: is this a git repo, is
`HEAD` tagged, is the tree dirty. `manifest.version` in `nextflow.config` is an
additional Nextflow-only signal. Snakemake has no standard version declaration —
0 of 5 workflows in the Phase 0 corpus record one — so absence of an in-repo
version is expected there and is not on its own a finding.

## 4a. Applicability

Not every check maps onto every engine. `PVX022` has no Snakemake analogue:
Snakemake has no `publishDir`, only declared output paths.

Applicability is first-class in the check registry. Each check declares the
engines it applies to. For a given run:

- Checks that do not apply are **not run** and are reported as `NOT_APPLICABLE`
  with a reason, for example "no Snakemake analogue".
- The report lists skipped checks explicitly. A silent zero is worse than a
  visible N/A, because a reader cannot tell a clean result from an absent one.
- The score is computed over applicable checks only, so a pipeline is neither
  quietly penalised nor quietly credited for a check that never ran.

`provenix list-checks` shows each check's applicable engines.

## 5. Verified facts to build against

**Nextflow** container syntax is a `container` directive on a process, and
config can set `process.container` or `withName:` selectors. Conda is a `conda`
directive or an environment YAML.

**Snakemake** rules declare `container:` and `conda:` directives; conda
environments are YAML files with a `dependencies:` list.

**nf-core modules** number roughly 2,000 and ship a pinned container plus a
`versions.yml` emit. A pipeline built from nf-core modules should score well on
`PVX001`, `PVX002` and `PVX011` with no work, which is a useful sanity check
during development.

Verify all of the above against real pipelines before writing the parsers.

## 6. Output formats

**Terminal (default).** Grouped by severity. Each finding: ID, severity, file
and line, one-line problem, one-line fix. Ends with a score and a count.

**JSON (`--format json`).** Machine-readable, stable schema, for CI.

**HTML (`--format html`).** A single self-contained file with no external
assets. This is the artefact someone attaches to a submission or hands to QA.

**Exit code.** `0` if no findings at or above the `--fail-on` threshold,
default `critical`. `1` otherwise. This is what makes it usable in CI.

## 7. The score

A single 0 to 100 number, so it can be tracked over time and compared across
pipelines.

Start from 100. Subtract per finding: critical 15, high 7, medium 3, security
20. Floor at 0. Document the formula in the README and keep it stable across
versions, since a score that shifts meaning between releases is worthless.

The score covers **applicable checks only**. A check reported `NOT_APPLICABLE`
never deducts, and the report states which checks were skipped and why, so a
score is always read against a known denominator.

## 8. Hard requirements

**Never execute the pipeline.** No `nextflow run`, no `snakemake`, no
subprocess that could invoke the workflow. This is a safety property: people
will point it at untrusted repos.

**Never print a secret.** `PVX030` reports location only.

**Every finding must be actionable.** File, line, what is wrong, what to change.
A finding with no fix is a bug.

**No false-positive tolerance on critical.** A critical finding that turns out
to be fine destroys trust in the whole report. When ambiguous, downgrade to
high, or skip.

**Deterministic.** Same input, same output, same order. It is an audit tool.

## 9. Done when

- `provenix check` runs on 10 real public pipelines: 5 nf-core, 5 Snakemake
- Zero false positives on that corpus, and every finding that fires is manually
  verified. A check is not required to fire on the public corpus to count as
  validated — see below
- The three output formats work and the exit code behaves
- The README shows a real report from a real public pipeline, named

`PVX020` (hardcoded absolute paths) and `PVX030` (credential-shaped literals)
produce zero findings across the whole public corpus. That is the correct
result, not a gap: nf-core and the public Snakemake workflows are
community-reviewed, and nobody merges an AWS key or a `/home/alice/ref` path
into them. The target user is a private in-house pipeline, which is exactly
where both live. Both checks are validated on fixtures, and the README must say
so and say why.

## 10. Out of scope

- Executing pipelines, or anything requiring runtime data
- WDL and CWL, phase 4
- Auto-fixing findings
- A hosted service, web UI beyond the static HTML report, or user accounts
- Cost or performance analysis
- Anything that requires cloud credentials

## 11. Rules for you, the builder

Never write example output in the README that you have not actually produced by
running the tool.

Verify Nextflow and Snakemake syntax against real pipelines before writing a
parser. Do not write regexes from memory of what the syntax looks like.

If a check cannot be implemented without a high false-positive rate, say so and
leave it out rather than shipping it noisy.
