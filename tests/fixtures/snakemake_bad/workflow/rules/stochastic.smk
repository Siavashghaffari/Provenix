rule subsample:
    input:
        "reads/{sample}.fq.gz",
    output:
        "sub/{sample}.fq.gz",
    conda:
        "../envs/gatk.yaml"
    shell:
        # PVX010: seqtk sample with no -s seed.
        "seqtk sample {input} 10000 | gzip > {output}"


rule hardcoded:
    output:
        "out/x.txt",
    conda:
        "../envs/gatk.yaml"
    shell:
        # PVX020: a machine-specific absolute path.
        "cp '/mnt/lab-share/refs/panel.bed' {output}"
