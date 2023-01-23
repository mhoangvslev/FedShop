import numpy as np
import pandas as pd
import os
from pathlib import Path
import glob
import time
import requests
import subprocess

import sys
smk_directory = os.path.abspath(workflow.basedir)
sys.path.append(os.path.join(Path(smk_directory).parent.parent, "rsfb"))

from utils import load_config

WORK_DIR = "experiments/bsbm"
CONFIG = load_config(f"{WORK_DIR}/config.yaml")["generation"]

SPARQL_ENDPOINT = CONFIG["sparql"]["endpoint"]
GENERATOR_ENDPOINT = CONFIG["generator"]["endpoint"]

SPARQL_COMPOSE_FILE = CONFIG["sparql"]["compose-file"]
SPARQL_CONTAINER_NAME = CONFIG["sparql"]["container-name"]

GENERATOR_COMPOSE_FILE = CONFIG["generator"]["compose-file"]
GENERATOR_CONTAINER_NAME = CONFIG["generator"]["container-name"]

N_QUERY_INSTANCES = CONFIG["n_query_instances"]
VERBOSE = CONFIG["verbose"]
N_BATCH = CONFIG["n_batch"]

# Config per batch
N_VENDOR=CONFIG["schema"]["vendor"]["params"]["vendor_n"]
N_RATINGSITE=CONFIG["schema"]["ratingsite"]["params"]["ratingsite_n"]

FEDERATION_COUNT=N_VENDOR+N_RATINGSITE

QUERY_DIR = f"{WORK_DIR}/queries"
MODEL_DIR = f"{WORK_DIR}/model"
BENCH_DIR = f"{WORK_DIR}/benchmark/generation"
TEMPLATE_DIR = f"{MODEL_DIR}/watdiv"

#=================
# USEFUL FUNCTIONS
#=================

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

def deploy_virtuoso(status_file, restart=False):
    if restart:
        shell(f"docker-compose -f {SPARQL_COMPOSE_FILE} down --remove-orphans --volumes")
        time.sleep(2)
    shell(f"docker-compose -f {SPARQL_COMPOSE_FILE} up -d {SPARQL_CONTAINER_NAME}")
    wait_for_container(SPARQL_ENDPOINT, status_file, wait=1)
    return status_file

def start_generator(status_file):
    exec_cmd = CONFIG["generator"]["exec"]
    if "docker exec" in exec_cmd:
        shell(f"docker-compose -f {GENERATOR_COMPOSE_FILE} up -d {GENERATOR_CONTAINER_NAME}")
        wait_for_container(GENERATOR_ENDPOINT, status_file, wait=1)
    elif os.system(f"command -v {exec_cmd}") == 0:
        with open(status_file, "w+") as f:
            f.write(exec_cmd + "\n")
            f.close()

    return status_file

def generate_virtuoso_scripts(nqfiles, shfiles, batch_id, n_items):
    nq_files = [ os.path.basename(f) for f in nqfiles ]
    _, edges = np.histogram(np.arange(n_items), N_BATCH)
    edges = edges[1:].astype(int) + 1
    batch = edges[batch_id]
    with open(f"{shfiles}", "w+") as f:
        f.write(f"echo \"Writing ingest script for {batch_id}, slicing at {batch}-th source...\"\n")
        for nq_file in nq_files[:batch]:
            f.write(f"docker exec {SPARQL_CONTAINER_NAME} /usr/local/virtuoso-opensource/bin/isql-v \"EXEC=ld_dir('/usr/local/virtuoso-opensource/share/virtuoso/vad/', '{nq_file}', 'http://example.com/datasets/default');\"&&\n")
        f.write(f"docker exec {SPARQL_CONTAINER_NAME} /usr/local/virtuoso-opensource/bin/isql-v \"EXEC=rdf_loader_run(log_enable=>2);\" &&\n")
        f.write(f"docker exec {SPARQL_CONTAINER_NAME} /usr/local/virtuoso-opensource/bin/isql-v \"EXEC=checkpoint;\"&&\n")
        f.write("exit 0\n")
        f.close()

#=================
# PIPELINE
#=================

rule all:
    input: 
        expand(
            "{benchDir}/metrics.csv",
            benchDir=BENCH_DIR
        )

rule merge_metrics:
    priority: 1
    input: expand("{{benchDir}}/metrics_batch{batch_id}.csv", batch_id=range(N_BATCH))
    output: "{benchDir}/metrics.csv"
    run: pd.concat((pd.read_csv(f) for f in input)).to_csv(f"{output}", index=False)

rule compute_metrics:
    priority: 2
    threads: 1
    input: 
        expand(
            "{{benchDir}}/{query}/instance_{instance_id}/batch_{{batch_id}}/provenance.csv",
            query=[Path(os.path.join(QUERY_DIR, f)).resolve().stem for f in os.listdir(QUERY_DIR)],
            instance_id=range(N_QUERY_INSTANCES)
        )
    output: "{benchDir}/metrics_batch{batch_id}.csv"
    shell: "python rsfb/metrics.py compute-metrics {WORK_DIR}/config.yaml {output} {input}"

rule exec_provenance_query:
    priority: 3
    threads: 1
    input: 
        provenance_query="{benchDir}/{query}/{instance_id}/provenance.sparql",
        loaded_virtuoso="{benchDir}/virtuoso-batch{batch_id}-ok.txt"
    output: "{benchDir}/{query}/instance_{instance_id}/batch_{batch_id}/provenance.csv"
    params:
        endpoint=SPARQL_ENDPOINT
    shell: 
        'python rsfb/query.py execute-query {input.provenance_query} {output} --endpoint {params.endpoint}'

# rule test_generation_next_batches:
#     TODO

def check_file_presence(status_file):
    proc = subprocess.run(f'docker exec {SPARQL_CONTAINER_NAME} sh -c "ls /usr/local/virtuoso-opensource/share/virtuoso/vad/*.nq | wc -l"', shell=True, capture_output=True)
    nFiles = int(proc.stdout.decode())
    expected_files = glob.glob(f"{MODEL_DIR}/dataset/*.nq")
    expected_nFiles = len(expected_files)
    while nFiles != expected_nFiles: 
        print(f"Expecting {expected_nFiles} *.nq files in virtuoso container, got {nFiles}!") 
        deploy_virtuoso(status_file, restart=True)
       
        proc = subprocess.run(f'docker exec {SPARQL_CONTAINER_NAME} sh -c "ls /usr/local/virtuoso-opensource/share/virtuoso/vad/*.nq | wc -l"', shell=True, capture_output=True)
        nFiles = int(proc.stdout.decode())

def check_file_stats():
    cmd = 'stat -c "%Y" '
    proc = subprocess.run(f'docker exec {SPARQL_CONTAINER_NAME} sh -c "ls /usr/local/virtuoso-opensource/share/virtuoso/vad/*.nq | sort"', shell=True, capture_output=True)
    container_files = str(proc.stdout.decode()).split("\n")
    local_files = sorted(glob.glob(f"{MODEL_DIR}/dataset/*.nq"))

    for local_file, container_file in zip(local_files, container_files):
        
        if Path(local_file).stem != Path(container_file).stem:
            raise RuntimeError(f"Mismatch between local file {Path(local_file).stem} and container file {Path(container_file).stem}")

        ctn_mod_time = int(subprocess.run(f'docker exec {SPARQL_CONTAINER_NAME} {cmd} {container_file}', shell=True, capture_output=True).stdout.decode())
        local_mod_time = int(subprocess.run(f"{cmd} {local_file}", shell=True, capture_output=True).stdout.decode())

        if ctn_mod_time < local_mod_time:
            raise RuntimeError(f"Container file {container_file} is older than local file {local_file}")
            # shell(f"docker exec {SPARQL_CONTAINER_NAME} rm /usr/local/virtuoso-opensource/share/virtuoso/vad/{container_file}")
            # shell(f'docker cp {local_file} {SPARQL_CONTAINER_NAME}:/usr/local/virtuoso-opensource/share/virtuoso/vad/')

rule ingest_virtuoso_next_batches:
    priority: 4
    threads: 1
    input: 
        vendor=expand("{modelDir}/virtuoso/ingest_vendor_batch{{batch_id}}.sh", modelDir=MODEL_DIR),
        ratingsite=expand("{modelDir}/virtuoso/ingest_ratingsite_batch{{batch_id}}.sh", modelDir=MODEL_DIR),
        virtuoso_status="{benchDir}/virtuoso-batch{batch_id}-up.txt"
    output: "{benchDir}/virtuoso-batch{batch_id}-ok.txt"
    run: 
        check_file_presence(input.virtuoso_status)
        deploy_virtuoso(input.virtuoso_status, restart=True)
        check_file_stats()
        shell(f'sh {input.vendor} bsbm && sh {input.ratingsite}')
        shell(f"python -W ignore:UserWarning {WORK_DIR}/tests/test.py -v TestGenerationVendor.test_vendor_nb_sources")
        shell(f"python -W ignore:UserWarning {WORK_DIR}/tests/test.py -v TestGenerationRatingSite.test_ratingsite_nb_sources")
        shell(f'echo "OK" > {output}')

rule deploy_virtuoso:
    priority: 5
    threads: 1
    input:
        vendor=expand("{modelDir}/dataset/vendor{vendor_id}.nq", vendor_id=range(N_VENDOR), modelDir=MODEL_DIR),
        ratingsite=expand("{modelDir}/dataset/ratingsite{ratingsite_id}.nq", ratingsite_id=range(N_RATINGSITE), modelDir=MODEL_DIR)
    output: "{benchDir}/virtuoso-batch{batch_id}-up.txt"
    run: deploy_virtuoso(output)

rule make_virtuoso_ingest_command_for_vendor:
    priority: 5
    threads: 1
    input: expand("{modelDir}/dataset/vendor{vendor_id}.nq", vendor_id=range(N_VENDOR), modelDir=MODEL_DIR)
    output: "{modelDir}/virtuoso/ingest_vendor_batch{batch_id}.sh"
    run: generate_virtuoso_scripts(input, output, int(wildcards.batch_id), N_VENDOR)

rule make_virtuoso_ingest_command_for_ratingsite:
    priority: 5
    threads: 1
    input: expand("{modelDir}/dataset/ratingsite{ratingsite_id}.nq", ratingsite_id=range(N_RATINGSITE), modelDir=MODEL_DIR)
    output: "{modelDir}/virtuoso/ingest_ratingsite_batch{batch_id}.sh"
    run: generate_virtuoso_scripts(input, output, int(wildcards.batch_id), N_RATINGSITE)

rule build_provenance_query: 
    """
    From the data generated for the 10 shops (TODO: and the review datasets?), 
    for each query template created in the previous step, 
    we select 10 random values for the placeholders in the BSBM query templates.
    """
    priority: 6
    input: "{benchDir}/{query}/{instance_id}/injected.sparql",
    output: "{benchDir}/{query}/{instance_id}/provenance.sparql"
    shell: "python rsfb/query.py build-provenance-query {input} {output}"

rule instanciate_workload:
    priority: 7
    threads: 1
    input: 
        queryfile=expand("{queryDir}/{{query}}.sparql", queryDir=QUERY_DIR),
        workload_value_selection="{benchDir}/{query}/workload_value_selection.csv"
    output:
        value_selection_query="{benchDir}/{query}/{instance_id}/injected.sparql",
    shell:
        "python rsfb/query.py instanciate-workload {input.queryfile} {input.workload_value_selection} {output.value_selection_query} {wildcards.instance_id}"

rule create_workload_value_selection:
    priority: 8
    input: "{benchDir}/{query}/value_selection.csv"
    output: "{benchDir}/{query}/workload_value_selection.csv"
    params:
        n_query_instances = N_QUERY_INSTANCES
    shell:
        "python rsfb/query.py create-workload-value-selection {input} {output} {params.n_query_instances}"

rule exec_value_selection_query:
    priority: 9
    threads: 1
    input: "{benchDir}/{query}/value_selection.sparql"
    output: "{benchDir}/{query}/value_selection.csv"
    params:
        endpoint=SPARQL_ENDPOINT
    shell: "python rsfb/query.py execute-query {input} {output} --endpoint {params.endpoint}"

rule build_value_selection_query:
    priority: 10
    input: 
        queryfile=expand("{queryDir}/{{query}}.sparql", queryDir=QUERY_DIR),
        virtuoso_status="{benchDir}/virtuoso-batch0-ok.txt"
    output: "{benchDir}/{query}/value_selection.sparql"
    shell: "python rsfb/query.py build-value-selection-query {input.queryfile} {output}"

# rule all:
#     input: 
#         expand(
#             "{modelDir}/dataset/vendor{vendor_id}.nq", 
#             modelDir=MODEL_DIR,
#             vendor_id=range(N_VENDOR)
#         )


rule generate_ratingsites:
    priority: 12
    input: 
        status=expand("{workDir}/generator-ok.txt", workDir=WORK_DIR),
        product=ancient(CONFIG["schema"]["product"]["export_output_dir"])
    output: "{modelDir}/dataset/ratingsite{ratingsite_id}.nq"
    shell: "python rsfb/generate.py generate {WORK_DIR}/config.yaml ratingsite {output} --id {wildcards.ratingsite_id}"

rule generate_vendors:
    priority: 13
    input: 
        status=expand("{workDir}/generator-ok.txt", workDir=WORK_DIR),
        product=ancient(CONFIG["schema"]["product"]["export_output_dir"])
    output: "{modelDir}/dataset/vendor{vendor_id}.nq"
    shell: "python rsfb/generate.py generate {WORK_DIR}/config.yaml vendor {output} --id {wildcards.vendor_id}"

# rule all:
#     input: expand("{modelDir}/tmp/product/", modelDir=MODEL_DIR)

rule generate_products:
    priority: 14
    input: expand("{workDir}/generator-ok.txt", workDir=WORK_DIR)
    output: directory(CONFIG["schema"]["product"]["export_output_dir"]), 
    shell: 'python rsfb/generate.py generate {WORK_DIR}/config.yaml product {output}'

rule start_generator_container:
    output: "{workDir}/generator-ok.txt"
    run: start_generator(f"{output}")