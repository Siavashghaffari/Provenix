# Provenix — project scope

Read this together with `MVP.md` and `design.md`.

- **This file** sets the boundaries: what to build, what never to build, when a
  phase is finished, and what to do when something goes wrong.
- **`MVP.md`** gives the checks, output formats and requirements.
- **`design.md`** gives the code structure.

Where they disagree: this file wins on scope, MVP.md wins on check behaviour,
design.md wins on structure.

---

## 1. What you are building

A static analyser that reads bioinformatics workflow source code and reports
reproducibility and audit risks, without running anything.

The value is not the linting. It is that no existing tool is cross-engine or
compliance-framed, and the Nextflow vendor has no incentive to build one.

## 2. Build this

| Item | Requirement |
|---|---|
| Engines | Nextflow DSL2 and Snakemake. Only these two |
| Analysis | Static only. Never execute a pipeline |
| Checks | The 13 in MVP.md section 4. Not more. (`PVX023` cut after Phase 0) |
| Outputs | Terminal, JSON, self-contained HTML |
| CLI | `provenix check <path>` with `--format` and `--fail-on` |
| Package name | `provenix` on PyPI. Verified available 5 Sep 2026 |
| License | MIT |
| Tests | Fixture pipelines with known findings, passing offline |
| CI | Lint and tests on Python 3.10, 3.11, 3.12 |
| Docs | README with a real report from a named public pipeline |

## 3. Do not build this

Hard boundaries. Do not add them, do not suggest adding them mid-build, and do
not quietly include them because they seemed useful.

| Excluded | Reason |
|---|---|
| Executing pipelines | This is the core safety property. People will point it at untrusted repos |
| WDL and CWL | Phase 4. Two engines is enough to prove cross-engine value |
| Auto-fix | Changing someone's pipeline is a different product with different risk |
| A hosted service or web UI | The HTML report is a file, not an app. No accounts, no backend |
| Cost or runtime performance analysis | Different problem, different tool, Seqera already does it |
| More checks than MVP.md lists | Every extra check is a false-positive risk against the tool's credibility |
| An LLM in the analysis path | Findings must be deterministic and explainable. A model that sometimes flags things is not an audit tool |

That last one matters. If you think a check needs a model, it is not ready to
be a check. Stop and tell me.

## 4. Phases and gates

Stop at the end of each phase, show what works, and wait before continuing.

**Phase 0 — ground truth**
Before writing any parser, collect real pipelines to test against: at least 5
nf-core pipelines and 5 public Snakemake workflows. Read their actual container,
conda, resource and reference-data syntax.
*Gate: you can state, with file examples, exactly what each of the 14 checks
looks like in real code for both engines.*

**Phase 1 — Nextflow, container / dependency / reference-data checks**
`PVX001` to `PVX005`. Terminal output only.
*Gate: runs on 5 real nf-core pipelines without crashing; zero false positives
on the corpus; every finding that fires manually verified as a true positive.
A check that fires nothing on the corpus is not thereby unvalidated — public
best-practice pipelines are expected to be clean on several of these.*

**Phase 2 — Snakemake, same checks**
The same five checks against Snakemake syntax.
*Gate: same, on 5 real Snakemake workflows.*

**Phase 3 — remaining checks and output formats**
High, medium and security checks. JSON and HTML output. Scoring. Exit codes.
*Gate: all three formats work, exit code behaves in CI, score is stable.*

**Phase 4 — package and publish**
pyproject, README with a real report, CI.
*Gate: `pip install provenix` on a clean machine, then a working run using only
the README.*

## 5. Acceptance criteria

Done when all six hold:

1. Runs on 10 real public pipelines, 5 per engine, without crashing
2. Zero false positives on that corpus, every finding that fires verified by
   hand. A check need not fire on the corpus to be validated; `PVX020` and
   `PVX030` are fixture-validated by design, and the README says why
3. Every finding names file, line, problem and fix
4. Same input produces byte-identical output every time
5. Exit code respects `--fail-on`, so it works as a CI gate
6. The README report was produced by actually running the tool on a named
   public pipeline

## 6. When things go wrong

**A check has a high false-positive rate.** Downgrade its severity, or cut it.
Do not ship it noisy and do not add special cases until it looks clean on your
five test pipelines. A critical finding that is wrong destroys trust in every
other finding.

**Parsing Nextflow or Snakemake properly turns out to be hard.** It will be.
Both are general-purpose languages, not config formats. Decide explicitly
between regex-plus-heuristics and real parsing, tell me which and why, and
document the limits of the approach in the README. Do not pretend to
understand more of the language than you do.

**A check needs runtime information to be accurate.** Cut it. This is a static
tool. Do not add a "just run it once" escape hatch.

**You are tempted to add an LLM to classify ambiguous cases.** Stop and tell
me. Deterministic output is a hard requirement, not a preference.

**You discover an existing tool that already does cross-engine reproducibility
auditing.** Stop and tell me before building further. It changes whether this
project is worth doing.

## 7. Rules that apply throughout

- Never execute a pipeline, or shell out to anything that could
- Never print a matched secret value. Location only
- Never write example output in the README that you have not produced by
  running the tool
- Verify engine syntax against real pipelines, not from memory
- Findings are deterministic and ordered. It is an audit tool
- Every finding carries a fix. A finding with no fix is a bug
