process FASTQC {
    tag "$meta.id"
    label 'process_low'

    conda "${moduleDir}/environment.yml"
    container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container ?
        'https://depot.galaxyproject.org/singularity/fastqc:0.12.1--hdfd78af_0' :
        'biocontainers/fastqc:0.12.1--hdfd78af_0' }"

    input:
    tuple val(meta), path(reads)

    output:
    path "versions.yml", emit: versions

    script:
    """
    fastqc $reads
    """
}

process DIGEST_PINNED {
    // Seqera community container: digest lives in the URL path, not in @sha256:
    container "${workflow.containerEngine in ['singularity', 'apptainer'] && !task.ext.singularity_pull_docker_container
        ? 'https://community-cr-prod.seqera.io/docker/registry/v2/blobs/sha256/e0/e05ce34b46ad42810eb29f74e4e304c0cb592b2ca15572929ed8bbaee58faf01/data'
        : 'community.wave.seqera.io/library/bwa-mem2_htslib_samtools:db98f81f55b64113'}"

    output:
    tuple val("${task.process}"), val('bwa-mem2'), eval('bwa-mem2 version'), topic: versions

    script:
    """
    bwa-mem2 mem
    """
}

process PERCENT_ENCODED {
    // %3A is an encoded ':'. This image is pinned, not untagged.
    container "https://depot.galaxyproject.org/singularity/msisensor-pro%3A1.3.0--hfef96ef_0"

    output:
    path "versions.yml", emit: versions

    script:
    """
    msisensor-pro scan
    """
}

process MULLED {
    // A mulled multi-package image. The tag is a content hash plus build index.
    container "biocontainers/mulled-v2-1021c2bc41756fa99bc402f461dad0d1c35358c1:b0c847e4fb89c343b04036e33b2daa19c4152cf5-0"

    output:
    tuple val("${task.process}"), val('samtools'), eval('samtools --version'), emit: versions

    script:
    """
    samtools view
    """
}

process INLINE_CONDA_PINNED {
    conda "bioconda::salmon=1.10.3 conda-forge::sed=4.7"
    container "biocontainers/salmon:1.10.3--h6dccd9a_2"

    output:
    path "versions.yml", emit: versions

    script:
    """
    salmon quant
    """
}
