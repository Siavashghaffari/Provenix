enrichment_env = "../envs/enrichment.yaml"


rule fastqc:
    input:
        "reads/{sample}.fq.gz",
    output:
        "qc/{sample}_fastqc.html",
    conda:
        "../envs/fastqc.yaml"
    shell:
        "fastqc {input} -o qc/"


rule enrichment:
    output:
        "results/enrichment.tsv",
    conda:
        enrichment_env
    script:
        "../scripts/enrichment.R"


rule get_pfam:
    output:
        "resources/pfam/Pfam-A.hmm",
    params:
        release=config["pfam"],
    shell:
        "(curl -L ftp://ftp.ebi.ac.uk/pub/databases/Pfam/releases/"
        "Pfam{params.release}/Pfam-A.hmm.gz | "
        "gzip -d > {output}) 2> {log}"
