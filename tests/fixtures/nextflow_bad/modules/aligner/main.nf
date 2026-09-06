process ALIGN_LATEST {
    // PVX001: mutable tag.
    container 'biocontainers/bwa:latest'

    script:
    """
    bwa mem
    """
}

process ALIGN_UNTAGGED {
    // PVX001: no tag, so Docker resolves :latest.
    container 'continuumio/miniconda3'

    script:
    """
    conda run bwa
    """
}

process ALIGN_BARE_TAG {
    // PVX002: bare version tag, no digest and no build hash.
    container 'qiime2/qiime2:2026.4'

    script:
    """
    qiime info
    """
}

process UNPINNED_INLINE {
    // PVX003: no version pin on an inline conda spec.
    conda "conda-forge::pigz"
    container 'biocontainers/pigz:2.8'

    script:
    """
    pigz -d
    """
}

process ENV_FILE {
    conda "${moduleDir}/environment.yml"
    container 'biocontainers/deseq2:1.20--h00cdaf9_0'

    script:
    """
    Rscript deseq.R
    """
}
