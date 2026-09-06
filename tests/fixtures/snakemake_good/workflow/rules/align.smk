# A rule nested inside a conditional. Its body sits at 8 spaces, not 4.
if config["aligner"] == "bowtie2":

    rule bowtie2_align:
        input:
            "reads/{sample}.fq.gz",
        output:
            "aligned/{sample}.bam",
        threads: 8
        wrapper:
            "v7.2.0/bio/bowtie2/align"

else:

    rule bwa_align:
        input:
            "reads/{sample}.fq.gz",
        output:
            "aligned/{sample}.bam",
        conda:
            "../envs/bwa.yaml"
        shell:
            "bwa mem {input} > {output}"
