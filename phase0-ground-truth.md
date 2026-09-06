# Provenix — Phase 0: ground truth

Research only. No parser written. Gate for Phase 1.

Corpus cloned to a scratch dir, **not vendored into this repo** (design.md §10).

---

## 1. The corpus

Cloned `--depth 1` on 2026-09-06. Every line reference below is against these
exact commits.

| Repo | Commit | Last commit | Files to parse |
|---|---|---|---|
| nf-core/rnaseq | `1f03b53` | 2026-07-24 | 107 `.nf`, 140 `.config` |
| nf-core/sarek | `8ccac7a` | 2026-08-12 | 199 `.nf`, 62 `.config` |
| nf-core/mag | `56abab5` | 2026-08-01 | 123 `.nf`, 65 `.config` |
| nf-core/viralrecon | `fa23078` | 2026-07-27 | 101 `.nf`, 70 `.config` |
| nf-core/ampliseq | `d56f90c` | 2026-07-24 | 139 `.nf`, 47 `.config` |
| snakemake-workflows/dna-seq-gatk-variant-calling | `cffa77a` | 2021-05-02 | 8 `.smk`, 1 Snakefile |
| snakemake-workflows/rna-seq-star-deseq2 | `aa6b17e` | 2025-12-18 | 6 `.smk`, 1 Snakefile |
| snakemake-workflows/chipseq | `1345df8` | 2021-06-04 | 11 `.smk`, 1 Snakefile |
| snakemake-workflows/rna-seq-kallisto-sleuth | `46fb01a` | 2026-08-17 | 14 `.smk`, 2 Snakefile |
| vanheeringen-lab/seq2science | `ca3ca03` | 2026-06-22 | 26 `.smk`, 7 Snakefile |

Totals: **482 Nextflow processes**, **285 Snakemake rules**, **477 conda env YAMLs**.

Deliberately mixed maintenance states: two Snakemake workflows are frozen at
2021, three are current. That spread is what exposes the nf-core version-capture
migration and the unpinned-conda cases.

---

## 2. Four findings that change the design

These are the reason Phase 0 exists. Each one would have caused mass false
positives if I had written the parser from the spec alone.

### 2.1 nf-core processes carry *no* resource, publish or error directives

Counted across all 482 processes in the five nf-core pipelines:

| Inline directive | Count |
|---|---|
| `container` | 481 |
| `conda` | 476 |
| `label` | 508 |
| `cpus` | **0** |
| `memory` | 3 |
| `time` | **0** |
| `publishDir` | **0** |
| `errorStrategy` | **0** |

They all live in `conf/base.config` and `conf/modules.config`, bound to
processes by `withLabel:` and `withName:` selectors.

`nf_rnaseq/conf/base.config:11-19` — global defaults applying to every process:

```groovy
process {
    cpus   = { 1      * task.attempt }
    memory = { 6.GB   * task.attempt }
    time   = { 4.h    * task.attempt }

    errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
    maxRetries    = 1
```

`nf_rnaseq/conf/base.config:33-37` — per-label overrides:

```groovy
    withLabel:process_low {
        cpus   = { 2     * task.attempt }
        memory = { 12.GB * task.attempt }
        time   = { 4.h   * task.attempt }
    }
```

**Consequence.** PVX013, PVX021 and PVX022 have *zero* in-process signal in
Nextflow. A per-process check produces **482 false positives per pipeline** and
482 true negatives. The parser must merge config selectors into `Process` before
any check runs. This is not optional.

### 2.2 The seed is in the config, not the script

`nf_rnaseq/modules/nf-core/preseq/lcextrap/main.nf` — the script has no seed,
only `$args`:

```groovy
    script:
    def args = task.ext.args ?: ''
    """
    preseq
        lc_extrap
        $args
```

`nf_rnaseq/conf/modules/preseq.config:2-3` — the seed is here:

```groovy
    withName: 'PRESEQ_LCEXTRAP' {
        ext.args   = '-verbose -bam -seed 1 -seg_len 100000000'
```

Every seed found in the corpus is set this way:
`nf_rnaseq/conf/modules/strandedness.config:3`, `nf_rnaseq/conf/test.config:46,49,52`,
`nf_mag/conf/modules.config:890,1032`.

**Consequence.** PVX010 against `Process.script` alone flags every correctly
seeded nf-core process. `ext.args` must be resolved into the process model.

### 2.3 nf-core is mid-migration between two version-capture mechanisms

| Pipeline | files with `versions.yml` | files with `topic: versions` |
|---|---|---|
| rnaseq | 10 | 77 |
| sarek | 3 | 123 |
| mag | 68 | 31 |
| viralrecon | 96 | 0 |
| ampliseq | 106 | 102 |

Old form — `nf_viralrecon/modules/nf-core/bowtie2/align/main.nf:25,74-79`:

```groovy
    path  "versions.yml"                , emit: versions
...
    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        bowtie2: $(echo $(bowtie2 --version 2>&1) | sed 's/^.*bowtie2-align-s version //')
    END_VERSIONS
```

New form — `nf_rnaseq/modules/nf-core/fastqc/main.nf:16`:

```groovy
    tuple val("${task.process}"), val('fastqc'), eval('fastqc --version | sed "/FastQC v/!d; s/.*v//"'), emit: versions_fastqc, topic: versions
```

**Consequence.** PVX011 written against `versions.yml` only reports
nf-core/sarek — 123 modules on the new form, 3 on the old — as having almost no
version capture. Both forms must be recognised.

### 2.4 No nf-core container uses `@sha256:`, but most are digest-pinned anyway

`@sha256:` occurrences across all five nf-core pipelines: **0**.

But `nf_sarek/modules/nf-core/bwamem2/mem/main.nf:6-8`:

```groovy
    container "${workflow.containerEngine in ['singularity', 'apptainer'] && !task.ext.singularity_pull_docker_container
        ? 'https://community-cr-prod.seqera.io/docker/registry/v2/blobs/sha256/e0/e05ce34b46ad42810eb29f74e4e304c0cb592b2ca15572929ed8bbaee58faf01/data'
        : 'community.wave.seqera.io/library/bwa-mem2_htslib_samtools:db98f81f55b64113'}"
```

The Singularity branch *is* content-addressed by SHA-256 — the digest is in the
URL path, not in `@sha256:` syntax. The Docker branch is a Wave build hash.

Registry families across all 482 processes:

| Form | Count | Pinned? |
|---|---|---|
| `https://community-cr-prod.seqera.io/.../blobs/sha256/<64hex>/data` | 238 | digest, URL form |
| `community.wave.seqera.io/library/<tool>:<buildhash>` | 238 | immutable build hash |
| `https://depot.galaxyproject.org/singularity/<tool>:<ver>--<build>` | 195 | version+build |
| `biocontainers/<tool>:<ver>--<build>` | 135 | version+build |
| `quay.io/biocontainers/<tool>:<ver>--<build>` | 51 | version+build |
| `qiime2/qiime2:2026.4` | 28 | version tag only |
| `nf-core/pipesidle:0.1.0-beta` | 13 | version tag only |

**Consequence.** PVX002 as literally written in MVP.md ("not pinned to a
digest (`@sha256:...`)") fires on **100% of every nf-core pipeline including
the exemplary ones**. That contradicts MVP.md §5, which expects nf-core to score
well on PVX002 with no work. See §5 for the decision I need from you.

---

## 3. Every check, in real syntax, both engines

File references are `<repo>/<path>:<line>` against the commits in §1.

### PVX001 — mutable or missing container tag · CRITICAL

**Nextflow.** Directive `container` at process indent. Three real shapes:

*Shape A — multi-line ternary, `?` trailing (`nf_rnaseq/modules/nf-core/fastqc/main.nf:6-8`), 261 occurrences:*
```groovy
    container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container ?
        'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0' :
        'biocontainers/fastqc:0.12.1--hdfd78af_0' }"
```

*Shape B — multi-line ternary, `?` leading (`nf_rnaseq/modules/nf-core/salmon/quant/main.nf:6-8`), 175 occurrences:*
```groovy
    container "${workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container
        ? 'https://depot.galaxyproject.org/singularity/salmon:1.10.3--h6dccd9a_2'
        : 'biocontainers/salmon:1.10.3--h6dccd9a_2'}"
```

*Shape C — plain single line (`nf_ampliseq/modules/local/qiime2_alphararefaction.nf:5`), 46 occurrences:*
```groovy
    container "qiime2/qiime2:2026.4"
```

Also in config — `nf_ampliseq/conf/containers_docker_amd64.config:1`:
```groovy
process { withName: 'FASTQC' { container = 'community.wave.seqera.io/library/fastqc:0.12.1--5cb1a2fa2f18c7c2' } }
```

A single-line regex captures **46 of 482**. The ternary must be read as a block
yielding **two** image references, both of which need checking.

Mutable-tag hits in nf-core: **0**. Untagged: **0**. As MVP.md §5 predicts,
nf-core scores clean here.

**Snakemake.** `container:` is a **module-level directive at column 0**, not a
rule field. Real PVX001 true positives, 3 of 5 workflows:

- `sm_rnaseq_star/workflow/Snakefile:19` — `container: "docker://continuumio/miniconda3"`
- `sm_chipseq/workflow/Snakefile:12` — `container: "docker://continuumio/miniconda3"`
- `sm_kallisto_sleuth/workflow/Snakefile:14` — `container: "docker://continuumio/miniconda3"`

**No tag at all → implicit `:latest`.** Textbook critical finding, verified true.

Clean by comparison:
- `sm_dnaseq_gatk/workflow/rules/common.smk:11` — `container: "continuumio/miniconda3:4.8.2"` (tagged, and note **no `docker://` prefix**)
- `sm_seq2science/seq2science/rules/configuration_generic.smk:4` — `container: "docker://quay.io/biocontainers/seq2science:0.7.2--pypyhdfd78af_0"`

Parser trap — `sm_chipseq/workflow/rules/common.smk:9` is a **commented-out**
container line. Comments must be stripped before matching.

`containerized:` (the other Snakemake keyword): 0 occurrences in this corpus.
Per-rule `container:` at rule indent: 0 occurrences. Both still need support.

### PVX002 — container not digest-pinned · CRITICAL

Covered in §2.4. `@sha256:` count in the corpus: **0**. Digest-in-URL count: 238.

**Snakemake.** Zero digest-pinned containers. All five use tags or nothing.

### PVX003 — unpinned conda dependency · CRITICAL

Scanned all 477 env YAMLs: **2,869 pinned deps, 10 unpinned**.

**Pin shapes that must all count as pinned:**

| Shape | Count | Example |
|---|---|---|
| `name=ver` | 1883 | `dask=2022.2.1` |
| `channel::name=ver` | 855 | `bioconda::salmon=1.10.3` |
| `name =ver` (space) | 75 | `r-base =3.5` |
| `name ==ver` | 13 | `curl ==7.71` |
| `_name=ver` (leading underscore) | 9 | `_openmp_mutex=4.5` |
| `channel::name=ver=build` | 2 | `bioconda::strelka=2.9.10=h9ee0642_1` |
| `channel::name=ver=*` | — | `bioconda::bcftools=1.23=*` |
| `name >=ver,<ver` | 3 | range constraints |

**Real true positives** — `sm_chipseq/workflow/envs/featurecounts_deseq2.yaml:7-12`:
```yaml
dependencies:
  - r-base =3.5
  - bioconductor-deseq2 =1.20
  - bioconductor-vsn          # line 7  unpinned
  - r-ggplot2                 # line 8  unpinned
  - r-rcolorbrewer            # line 9  unpinned
  - r-pheatmap                # line 10 unpinned
  - r-lattice                 # line 11 unpinned
  - bioconductor-biocparallel # line 12 unpinned
```
Six genuine findings. The only place in the corpus with real unpinned deps.

**The 4 remaining hits are false positives my naive scan produced** —
`nf_ampliseq/modules/local/envs/pipesidle-0-1-0-beta.yml:9-11` and
`sm_seq2science/seq2science/envs/pytxi.yaml:9-11`:
```yaml
dependencies:
  - pip
  - pip:
      - git+https://github.com/jwdebelius/q2-sidle.git@0.1.0-beta
```
- Bare `- pip` is the conda idiom that enables the nested `pip:` section, not a dependency to pin.
- `- pip:` is a **mapping**, its value a nested list. Pin syntax there is `==` or a git ref.
- `git+https://...@0.1.0-beta` **is** pinned, by git tag.

PVX003 must handle nested `pip:` lists, treat `git+...@ref` as pinned, and skip
the bare `pip` sentinel.

**Nextflow conda directive, two forms:**
- File reference, 424 occurrences — `nf_rnaseq/modules/nf-core/fastqc/main.nf:5`: `conda "${moduleDir}/environment.yml"` → resolve `${moduleDir}` to the module dir and read the YAML.
- Inline spec list, 53 occurrences — `nf_ampliseq/modules/local/*`: `conda "bioconda::bioconductor-dada2=1.38.0 conda-forge::r-base=4.5.2 conda-forge::r-digest=0.6.39"` → space-separated, parse directly.

Pinned example — `nf_rnaseq/modules/nf-core/salmon/quant/environment.yml:6-7`:
```yaml
dependencies:
  - bioconda::salmon=1.10.3
```

**Snakemake conda directive** — 186 of 191 use the **wrapped two-line form**:
```python
    conda:
        "../envs/salmon.yaml"
```
Only 5 are single-line. The wrapped form is the norm and must be the primary
case. Paths are relative to the `.smk` file.

### PVX004 — reference data from a mutable URL · CRITICAL

**The dominant signal in this corpus is noise.** Of every `/latest/`, `/master/`,
`/main/` URL found, all but one are **documentation links inside comments**:

```
nf_ampliseq/conf/base.config:27                    https://www.nextflow.io/docs/latest/config.html      <- comment
nf_ampliseq/modules/local/sidle_align.nf:19        https://q2-sidle.readthedocs.io/en/latest/...        <- comment
sm_kallisto_sleuth/config/config.yaml:180          https://github.com/.../blob/main/...                 <- comment
sm_seq2science/seq2science/rules/trackhub.smk:106  https://github.com/.../blob/master/...               <- comment
```

**The one genuine true positive** — `nf_ampliseq/conf/ref_databases.config:416`
(and `:424` for the `zehr-nifh=2.5.0` entry):
```groovy
'zehr-nifh' {
    title = "Zehr lab nifH database - version 2.5.0"
    file = [ "https://raw.githubusercontent.com/moyn413/nifHdada2/master/nifH_dada2_v2.0.5.fasta",
             "https://raw.githubusercontent.com/moyn413/nifHdada2/master/nifH_dada2_phylum_v2.0.5.csv" ]
```
A reference database fetched from a **git branch**. `master` moves; the file
content can change under the same URL.

To reach zero false positives PVX004 must: strip comments first; require the URL
to be **assigned to a data key** (`file =`, `fasta =`, `shfile =`) or appear in a
download command; and denylist doc hosts. `raw.githubusercontent.com/.../master/`
is a data fetch; `github.com/.../blob/master/` is a web page and almost always a
comment.

**Snakemake** download mechanisms — no `HTTP.remote`/`storage()` in this corpus;
all downloads are `wget`/`curl` inside `shell:`:

- `sm_kallisto_sleuth/.test/three_prime/workflow/Snakefile:26` — `"wget https://zenodo.org/records/10572746/files/quant_seq_test_data.tar.gz 2>{log} "` — Zenodo record, immutable, correctly **not** a finding.
- `sm_kallisto_sleuth/workflow/rules/ref.smk:66-68` — **implicit string concatenation across three lines**:
```python
    shell:
        "(curl -L ftp://ftp.ebi.ac.uk/pub/databases/Pfam/releases/"
        "Pfam{params.release}/Pfam-A.{wildcards.ext}.gz | "
        "gzip -d > {output}) 2> {log}"
```
  `{params.release}` comes from `config/config.yaml:60` (`pfam: "37.1"`).
  Resolvable only by following config → **UNRESOLVED**, skip.
- `sm_seq2science/seq2science/rules/get_fastq.smk:194` — `shell("wget {url} -O {output} ...")` — URL is a Python variable → **UNRESOLVED**, skip.

### PVX005 — reference data fetched with no checksum · CRITICAL

Across all 10 pipelines, checksum verification of downloaded data occurs
**exactly once**: `nf_mag/modules/nf-core/checkm2/databasedownload/main.nf:48`
(`--checksum ${checksum}`).

`nf_ampliseq/conf/ref_databases.config` declares **113 reference databases**
(`file = [...]` entries) with **zero** checksum fields. The four `md5` matches in
that file are a comment at line 434 and filenames that happen to contain `.md5.`
(`2024.09.taxonomy.md5.tsv.gz`, lines 529/536/543) — not verification.

`nf_ampliseq/conf/ref_databases.config:529` also fetches reference data over
plain **`http://`** (`http://ftp.microbio.me/greengenes_release/...`).

**Snakemake:** zero checksum verification in all five workflows.

This check is *correct* everywhere it fires, but it fires on essentially every
download site in every real pipeline. See §5 for the volume decision.

### PVX010 — stochastic tool with no seed · HIGH

Covered in §2.2. Seed flag spellings observed in the corpus:

| Flag | Count | Where |
|---|---|---|
| `--seed 1` | 17 | rnaseq configs, mag |
| `-seed 1` | 4 | `nf_rnaseq/conf/modules/preseq.config:3` (preseq uses a single dash) |
| `--seed 42` | 2 | `nf_mag/modules/nf-core/adapterremoval/main.nf:40,67` |
| `--random-seed=100` | 1 | `nf_rnaseq/modules/nf-core/umitools/dedup/main.nf:32` |
| `--seed ${params.metabat_rng_seed}` | 1 | `nf_mag/conf/modules.config:890` |
| `--random-seed ${params.semibin_rng_seed}` | 1 | `nf_mag/conf/modules.config:1032` |
| `--preseq-seed 1`, `--tin-seed 1`, `--junction-saturation-seed 1` | 3 | `nf_rnaseq/conf/modules/rustqc.config:9-10` |

Seed values come from params — `nf_mag/nextflow.config:144,187` set
`semibin_rng_seed = 1` and `metabat_rng_seed = 1`.

`nf_mag/conf/modules.config:1032` is conditional:
`params.semibin_rng_seed == 0 ? "" : "--random-seed ${params.semibin_rng_seed}"`
— the seed applies only when non-zero. Statically the string is present; that is
the honest answer and the right one.

Flag names are **per-tool and inconsistent** (`-seed`, `--seed`, `--random-seed`,
`--preseq-seed`). The `data/stochastic_tools.yml` map in design.md §1 is the
correct structure; it must be curated from evidence, and this corpus supplies the
first entries.

**Snakemake:** no seed flags found in any of the five workflows. Stochastic tools
present without seeds: kraken2 (16 files), bbmap (9), seqtk (5).

### PVX011 — no tool version capture · HIGH

**Nextflow:** covered in §2.3. Two forms, both required.

**Snakemake:** version provenance comes from `conda:` **or** `wrapper:`.

| Workflow | rules | `conda:` | `wrapper:` |
|---|---|---|---|
| sm_dnaseq_gatk | 30 | 5 | 26 |
| sm_rnaseq_star | 25 | 14 | 10 |
| sm_chipseq | 69 | 21 | 39 |
| sm_kallisto_sleuth | 74 | 53 | 18 |
| sm_seq2science | 87 | 104 | 0 |

`wrapper:` pins the environment via the wrapper repo tag —
`sm_rnaseq_star/workflow/rules/align.smk:24-25`:
```python
    wrapper:
        "v7.2.0/bio/star/align"
```
All 88 wrapper references in the corpus are version-pinned; **none** use
`master/bio/...`. Tag shapes are both `v7.2.0/bio/...` and `0.74.0/bio/...`.

**A rule with `wrapper:` and no `conda:` is not a finding.** In sm_dnaseq_gatk
that would be 26 of 30 rules wrongly flagged.

### PVX012 — source not version-controlled / no version recorded · HIGH

**Nextflow.** `nf_rnaseq/nextflow.config:386,464` — the `manifest` block:
```groovy
manifest {
    name            = 'nf-core/rnaseq'
    ...
    version         = '3.26.0'
    doi             = 'https://doi.org/10.5281/zenodo.1400710'
}
```
All five nf-core pipelines have `manifest.version`. Clean, easy to check. Note
the block spans ~80 lines with nested `contributors = [ [ ... ] ]` lists — a
brace-depth scan must handle nesting, not stop at the first `}`.

**Snakemake.** There is **no standard version declaration**. 0 of 5 workflows
record a version in `config/config.yaml`. The one exception is outside the
workflow spec: `sm_seq2science` is a Python package with `setup.py` and
`pyproject.toml` carrying `__version__`.

So for Snakemake the only reliable signal is git presence. See §5.

### PVX013 — no resource directives · HIGH

**Nextflow:** covered in §2.1. Zero inline `cpus`. Must resolve
`label` → `withLabel:` and the `process { }` global block in `conf/base.config`.
A correct implementation yields **zero findings** on all five nf-core pipelines.

**Snakemake:** genuinely sparse, and there is no `base.config` equivalent.

| Workflow | rules | `threads:` | `resources:` |
|---|---|---|---|
| sm_dnaseq_gatk | 30 | 2 | 3 |
| sm_rnaseq_star | 25 | 6 | 1 |
| sm_chipseq | 69 | 13 | 2 |
| sm_kallisto_sleuth | 74 | 19 | 3 |
| sm_seq2science | 87 | 49 | 60 |

Syntax is a bare-colon scalar, not a keyword-arg block:
`sm_rnaseq_star/workflow/rules/align.smk:23` — `threads: 24`.
Values can be callables: `threads: get_aligner_threads()` (6 occurrences).

Snakemake defaults `threads: 1` when absent, and a profile can supply
`--default-resources`. sm_chipseq at 13/69 is a real gap; the check is honest
here but will be high-volume on Snakemake.

### PVX021 — no errorStrategy / retries · MEDIUM

**Nextflow.** Zero inline `errorStrategy` across 482 processes. Set globally at
`nf_rnaseq/conf/base.config:18`:
```groovy
    errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
    maxRetries    = 1
```
Plus opt-in labels at `base.config:57-63` — `withLabel:error_ignore` and
`withLabel:error_retry`. Same resolution requirement as PVX013.

**Snakemake.** `retries:` at rule indent, 11 occurrences, only 2 of 5 workflows:
`sm_kallisto_sleuth/workflow/rules/ref.smk:131` — `retries: 3`;
`sm_seq2science/seq2science/rules/get_fastq.smk:29,101,144,187,216` — `retries: 2`.

Sensibly, both use it exactly where it matters — on download rules.

### PVX022 — publishDir allows silent overwrite · MEDIUM

**Nextflow.** Zero inline `publishDir`. 638 occurrences of
`mode: params.publish_dir_mode` in config vs **1** literal `mode: 'copy'`.

`nf_rnaseq/conf/modules/preseq.config:4-9`:
```groovy
        publishDir = [
            [
                path: { "${params.outdir}/${task.ext.publish_prefix}${params.aligner}/preseq" },
                mode: params.publish_dir_mode,
                pattern: "*.txt"
            ],
```
Note `publishDir` here is a **list of maps**, not one map.

The value resolves from `nf_rnaseq/nextflow.config:134` — `publish_dir_mode = 'copy'`.
All five nf-core pipelines default to `'copy'`.

So PVX022 requires a param lookup: `mode: params.X` → find `X` in the `params`
block. Without it the mode is UNRESOLVED for 638 of 639 publish specs.

Newer nf-core also uses the workflow output DSL —
`nf_rnaseq/nextflow.config:161`: `workflow.output.mode = params.publish_dir_mode`.

**Snakemake** has no `publishDir` concept. Outputs are declared paths; the
nearest analogue is `protected()` / `temp()` wrappers on outputs.
**This check does not map onto Snakemake.** See §5.

### PVX023 — non-deterministic ordering · MEDIUM

**Snakemake** is Python, so this is findable. Real hits:

`sm_seq2science/seq2science/rules/trimming.smk:12` — glob result used unsorted:
```python
def se_fastq(wildcards):
    local_fastqs = glob.glob(os.path.join(config["fastq_dir"], f'{wildcards.sample}*...'))
    if len(local_fastqs) == 1:
        return local_fastqs[0]
```
Also `sm_seq2science/seq2science/rules/qc.smk:57`.

**But the adjacent case is a false positive** — `trimming.smk:20-23`:
```python
def pe_fastq(wildcards):
    local_fastqs = glob.glob(os.path.join(config["fastq_dir"], f'...'))
    if len(local_fastqs) == 2:
        local_fastqs.sort()      # <- sorted three lines later
```
A "glob without sorted on the same line" rule flags this. Correctness requires
dataflow analysis: is the glob result sorted anywhere before use?

Second false-positive source: `dict.items()` / `.keys()` iteration, which is
**insertion-ordered and deterministic** in Python 3.7+. There are 8+ such lines
in the corpus (`sm_chipseq/workflow/scripts/macs2_merged_expand.py:140`,
`sm_kallisto_sleuth/workflow/scripts/goatools-go-enrichment-analysis.py:23,220`).
Flagging them is wrong.

Genuinely non-deterministic constructs are only: `set()` iteration, `glob.glob`,
`os.listdir`, `os.walk`, `os.scandir`.

**Nextflow** has no equivalent construct that is statically visible. Groovy map
iteration is insertion-ordered; channel order is a runtime property.

See §5 — I recommend cutting or downgrading this one.

### PVX030 — credential-shaped literal · SECURITY

**Zero true positives across all 10 pipelines.** Expected: these are public
repos; a committed secret would have been caught.

**The false-positive source is large and must be handled.** GitHub Actions
workflow files are full of credential-shaped lines:
```
nf_ampliseq/.github/workflows/awsfulltest.yml:32            access_token: ${{ secrets.TOWER_ACCESS_TOKEN }}
nf_ampliseq/.github/workflows/clean-up.yml:24               repo-token: "${{ secrets.GITHUB_TOKEN }}"
nf_ampliseq/.github/workflows/release-announcements.yml:45  BSKY_PASSWORD: ${{ secrets.BSKY_PASSWORD }}
```
These are **references**, not secrets. The rule must be: the right-hand side is
a literal containing no `${`, `{{`, `$(`, or `os.environ`. Excluding `.github/`
from the walk is a reasonable second guard.

PVX030 has **no positive validation from this corpus** and needs a synthetic
fixture, per design.md §8 and §10.

### PVX020 — hardcoded absolute path · MEDIUM

**Zero hits across all 10 pipelines**, including a broadened search for
`/home`, `/Users`, `/data`, `/scratch`, `/mnt`, `/nfs`, `/opt`, `/srv`,
`/projects`, and for bind-mount options in configs.

The check is legitimate — it targets private/internal pipelines, which is where
Provenix's actual users are — but it gets **no validation from public
best-practice pipelines** and needs a synthetic fixture.

---

## 4. Parsing approach — the decision scope.md §6 asks for

**Recommendation: structural scan plus targeted extraction, plus a config
resolution pass.** This matches design.md §5, with one addition that Phase 0
showed is mandatory.

*Nextflow.* Line scan tracking brace depth. `process NAME {` at column 0 opens a
process — 482 of 482 match this, no exceptions found. Directives at the first
indent level. The `container` ternary needs a small multi-line continuation
reader that collects quoted string literals until brace balance closes.

*Snakemake.* Python `ast` cannot be relied on alone: `rule x:` is not valid
Python and `ast.parse` fails on any `.smk` file — Snakemake's own parser rewrites
the file first. So: line scan on `^(rule|checkpoint|module|use rule)\s+\w+\s*:`
at column 0, then 4-space-indented field names, handling the wrapped
`conda:` / newline / `"path"` form which is 186 of 191 cases. Module-level
`container:` and `configfile:` at column 0. `ast` is still worth using for the
*scripts* under `workflow/scripts/`, since those are plain Python.

**The addition: a config resolution pass between parse and check.** Findings
§2.1, §2.2 and PVX022 all mean the Nextflow `Process` model is incomplete until
`conf/*.config` selectors are merged in:

```
parse .nf     -> processes with name, label, container, conda, script
parse .config -> global process{} block, withLabel: blocks, withName: blocks, params{}
resolve       -> merge into each Process by name and label; resolve params.X lookups
                 -> Process now has cpus, memory, time, errorStrategy, publishDir, ext_args
checks run on the resolved model
```

Without this pass PVX010, PVX013, PVX021 and PVX022 are unusable on Nextflow.
It stays inside the parser, so `checks/` remains engine-agnostic as design.md §4
requires.

**Limits to document in the README:**
- Container refs assembled from variables cannot be resolved (`UNRESOLVED`, skipped)
- URLs built by string interpolation from config cannot be resolved (`sm_kallisto_sleuth/workflow/rules/ref.smk:66`)
- URLs held in Python variables cannot be resolved (`sm_seq2science/.../get_fastq.smk:194`)
- `include:` is not followed; all workflow files under the root are walked instead
- Snakemake profiles (`--default-resources`, `--retries`) are not read, so PVX013/PVX021 may over-report on workflows that rely on a profile
- Conditional Groovy directives are read as text; a directive present but conditionally disabled reads as present

---

## 5. Decisions I need from you before Phase 1

Five points where the spec meets the evidence. My recommendation on each; I have
not implemented any of them.

**1. PVX002 fires on 100% of nf-core (§2.4).** MVP.md §5 says nf-core should
score well here with no work; MVP.md §4 says flag anything without `@sha256:`.
Those conflict against real syntax.
*Recommendation:* treat as digest-pinned — (a) `@sha256:<64hex>`,
(b) a `blobs/sha256/<64hex>/` URL path, (c) a `<tool>:<ver>--<buildhash>`
biocontainer tag, (d) a Wave `<tool>:<16hex>` tag. Everything else — bare version
tags like `qiime2/qiime2:2026.4` — is a finding. This keeps nf-core clean while
still catching genuinely floating images. If you'd rather keep PVX002 literal, it
should drop to HIGH, because at CRITICAL it makes every report on every nf-core
pipeline open with ~480 findings.

**2. PVX005 is true but very high volume.** 113 findings on ampliseq's reference
config alone, and near-total absence of checksums is the industry norm.
*Recommendation:* keep it CRITICAL — it is exactly the audit gap the tool exists
to name, and it is never a false positive — but report it **once per
reference-data source**, not once per URL, so a pipeline gets a handful of
findings rather than hundreds. Say so in the README.

**3. PVX023 cannot be done cleanly.** The one real hit sits three lines from a
near-identical false positive that is correctly sorted.
*Recommendation:* **cut it from the MVP**, per scope.md §6 and MVP.md §11. It
needs dataflow analysis to be honest, it has no Nextflow analogue, and it is
worth 3 points. I would rather ship 14 checks that are right. If you want it
kept, restrict it to `set()` iteration and unsorted `os.listdir`/`os.walk` only —
never `glob.glob`, never `dict.items()` — and accept that it will mostly find
nothing.

**4. PVX022 has no Snakemake analogue.** Snakemake has no `publishDir`.
*Recommendation:* make it Nextflow-only and have `provenix list-checks` state
which engines each check applies to. Silently returning zero on Snakemake would
misrepresent coverage in an audit report.

**5. PVX012 on Snakemake has no in-repo signal.** 0 of 5 workflows record a
version anywhere a workflow spec defines.
*Recommendation:* satisfy the check if **either** a git repo with a resolvable
commit is present **or** a version is recorded in config; report the commit in
the finding. For Nextflow, additionally require `manifest.version`, which all
five nf-core pipelines have.

Also worth noting: **PVX020 and PVX030 got zero hits across the whole corpus.**
Both stay in — they target private pipelines, which is the real user — but they
have no real-world positive validation and will rest entirely on synthetic
fixtures. That is a known weakness of the Phase 1/2 gates as written.

---

## 6. Gate

scope.md Phase 0 gate: *"you can state, with file examples, exactly what each of
the 15 checks looks like in real code for both engines."*

Met. §3 covers all 15 checks against both engines with file and line references
into the ten pipelines at the commits in §1.

Also discharged: scope.md §6's obligation to re-check for an existing
cross-engine reproducibility auditor. Two searches found none — only
single-engine linters and workflow-comparison papers. The gap in MVP.md §2
holds. Absence of search results is not proof.

No parser code written.
