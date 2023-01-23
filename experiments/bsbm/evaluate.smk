import numpy as np
import pandas as pd
import os
import time
import requests
import glob

import sys
smk_directory = os.path.abspath(workflow.basedir)
sys.path.append(os.path.join(Path(smk_directory).parent, "rsfb"))

from utils import load_config

WORK_DIR = "experiments/bsbm"
CONFIG = load_config(f"{WORK_DIR}/config.yaml")

CONFIG_GEN = CONFIG["generation"]
CONFIG_EVAL = CONFIG["evaluation"]

SPARQL_ENDPOINT = CONFIG_GEN["sparql"]["endpoint"]

SPARQL_COMPOSE_FILE = CONFIG_GEN["sparql"]["compose-file"]
SPARQL_CONTAINER_NAME = CONFIG_GEN["sparql"]["container-name"]

N_QUERY_INSTANCES = CONFIG_GEN["n_query_instances"]
N_BATCH = CONFIG_GEN["n_batch"]

# Config per batch
N_VENDOR=CONFIG_GEN["schema"]["vendor"]["params"]["vendor_n"]
N_RATINGSITE=CONFIG_GEN["schema"]["ratingsite"]["params"]["ratingsite_n"]

FEDERATION_COUNT=N_VENDOR+N_RATINGSITE

QUERY_DIR = f"{WORK_DIR}/queries"
MODEL_DIR = f"{WORK_DIR}/model"
BENCH_DIR = f"{WORK_DIR}/benchmark/evaluation"
TEMPLATE_DIR = f"{MODEL_DIR}/watdiv"

# ======== USEFUL FUNCTIONS =========

def wait_for_container(endpoint, outfile, wait=1):
    endpoint_ok = False
    attempt=1
    print(f"Waiting for {endpoint}...")
    while(not endpoint_ok):
        print(f"Attempt {attempt} ...")
        try: endpoint_ok = ( requests.get(endpoint).status_code == 200 )
        except: pass
        attempt += 1
        time.sleep(wait)

    with open(f"{outfile}", "w+") as f:
        f.write("OK")
        f.close()

def restart_virtuoso(status_file):
    shell(f"docker-compose -f {SPARQL_COMPOSE_FILE} up -d {SPARQL_CONTAINER_NAME}")
    wait_for_container(SPARQL_ENDPOINT, status_file, wait=1)
    return status_file

def prerequisite_for_engine(wildcards):
    engine = str(wildcards.engine)
    if engine == "fedx":
        return f"{BENCH_DIR}/fedx/virtuoso-batch{N_BATCH-1}-ok.txt"
    return "unknown"

# ======== RULES =========

rule all:
    input: expand("{benchDir}/metrics.csv", benchDir=BENCH_DIR)

rule merge_metrics:
    input: 
        expand(
            "{{benchDir}}/{engine}/{query}/{instance_id}/batch_{batch_id}/{mode}/stats.csv", 
            engine=CONFIG_EVAL["engines"],
            query=[Path(os.path.join(QUERY_DIR, f)).resolve().stem for f in os.listdir(QUERY_DIR) if "_" not in f],
            instance_id=range(N_QUERY_INSTANCES),
            batch_id=range(N_BATCH),
            mode=["default", "ideal"]
        )
    output: "{benchDir}/metrics.csv"
    run: pd.concat((pd.read_csv(f) for f in input)).to_csv(f"{output}", index=False)

rule measure_default_stats:
    threads: 1
    retries: 3
    input: 
        query=expand("{workDir}/benchmark/generation/{{query}}/{{instance_id}}/injected.sparql", workDir=WORK_DIR),
        engine_config="{benchDir}/{engine}/config/{batch_id}/{engine}.conf",
        prerequisite=prerequisite_for_engine
    output: 
        results="{benchDir}/{engine}/{query}/{instance_id}/batch_{batch_id}/default/results",
        stats="{benchDir}/{engine}/{query}/{instance_id}/batch_{batch_id}/default/stats.csv"
    params:
        eval_config=expand("{workDir}/config.yaml", workDir=WORK_DIR)
    shell:
        "python rsfb/engines/{wildcards.engine}.py run-benchmark {params.eval_config} {input.engine_config} {input.query} {output.results} {output.stats}"
 
rule measure_ideal_source_selection_stats:
    threads: 1
    retries: 3
    input: 
        query=expand("{workDir}/benchmark/generation/{{query}}/{{instance_id}}/injected.sparql", workDir=WORK_DIR),
        ideal_ss=expand("{workDir}/benchmark/generation/{{query}}/{{instance_id}}/batch_{{batch_id}}/provenance.csv", workDir=WORK_DIR),
        engine_config="{benchDir}/{engine}/config/{batch_id}/{engine}.conf",
        prerequisite=prerequisite_for_engine
    output: 
        results="{benchDir}/{engine}/{query}/{instance_id}/batch_{batch_id}/ideal/results",
        stats="{benchDir}/{engine}/{query}/{instance_id}/batch_{batch_id}/ideal/stats.csv",
    params:
        eval_config=expand("{workDir}/config.yaml", workDir=WORK_DIR)
    shell: 
        "python rsfb/engines/{wildcards.engine}.py run-benchmark {params.eval_config} {input.engine_config} {input.query} {output.results} {output.stats} --ideal-ss {input.ideal_ss}"

rule generate_federation_declaration:
    output: "{benchDir}/{engine}/config/{batch_id}/{engine}.conf"
    run: 
        ratingsite_data_files = [ f"{MODEL_DIR}/dataset/ratingsite{i}.nq" for i in range(N_RATINGSITE) ]
        vendor_data_files = [ f"{MODEL_DIR}/dataset/vendor{i}.nq" for i in range(N_VENDOR) ]

        batchId = int(wildcards.batch_id)
        ratingsiteSliceId = np.histogram(np.arange(N_RATINGSITE), N_BATCH)[1][1:].astype(int)[batchId]
        vendorSliceId = np.histogram(np.arange(N_VENDOR), N_BATCH)[1][1:].astype(int)[batchId]
        batch_files = ratingsite_data_files[:ratingsiteSliceId] + vendor_data_files[:vendorSliceId]

        os.system(f"python rsfb/engines/{wildcards.engine}.py generate-config-file {' '.join(batch_files)} {output} --endpoint {SPARQL_ENDPOINT}")

rule ingest_virtuoso:
    threads: 1
    input: 
        vendor=expand("{modelDir}/virtuoso/ingest_vendor_batch{lastBatch}.sh", modelDir=MODEL_DIR, lastBatch=N_BATCH-1),
        ratingsite=expand("{modelDir}/virtuoso/ingest_ratingsite_batch{lastBatch}.sh", modelDir=MODEL_DIR, lastBatch=N_BATCH-1),
        virtuoso_status=expand("{benchDir}/{{engine}}/virtuoso-up.txt", benchDir=BENCH_DIR)
    output: "{benchDir}/{engine}/virtuoso-batch{lastBatch}-ok.txt"
    run: 
        proc = subprocess.run(f"docker exec {SPARQL_CONTAINER_NAME} ls /usr/local/virtuoso-opensource/share/virtuoso/vad | wc -l", shell=True, capture_output=True)
        nFiles = int(proc.stdout.decode())
        expected_nFiles = len(glob.glob(f"{MODEL_DIR}/dataset/*.nq"))
        if nFiles != expected_nFiles: raise RuntimeError(f"Expecting {expected_nFiles} *.nq files in virtuoso container, got {nFiles}!") 
        os.system(f'sh {input.vendor} bsbm && sh {input.ratingsite} && echo "OK" > {output}')

rule restart_virtuoso:
    priority: 5
    threads: 1
    output: "{benchDir}/{engine}/virtuoso-up.txt"
    run: restart_virtuoso(output)
