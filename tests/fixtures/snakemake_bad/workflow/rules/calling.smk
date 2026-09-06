rule call_variants:
    input:
        "aligned/{sample}.bam",
    output:
        "calls/{sample}.vcf",
    # PVX002: bare version tag, no digest and no build hash.
    container:
        "docker://broadinstitute/gatk:4.5.0.0"
    conda:
        "../envs/gatk.yaml"
    shell:
        "gatk HaplotypeCaller -I {input} -O {output}"


rule fetch_reference:
    output:
        "resources/genome.fa",
    shell:
        "wget https://raw.githubusercontent.com/example/refs/master/genome.fa -O {output}"
