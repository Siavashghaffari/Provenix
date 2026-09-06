process SUBSAMPLE {
    // PVX010: seqtk sample with no -s seed.
    // PVX011: no versions emit.
    // PVX013 / PVX021: no resources and no errorStrategy anywhere in this pipeline.
    container 'biocontainers/seqtk:1.4--he4a0461_1'

    script:
    """
    seqtk sample $reads 10000 > sub.fq
    """
}

process HARDCODED_PATH {
    container 'biocontainers/bwa:0.7.18--he4a0461_1'

    script:
    """
    # PVX020: a path that only exists on one machine.
    bwa mem "/home/alice/refs/genome.fa" $reads > out.sam
    """
}
