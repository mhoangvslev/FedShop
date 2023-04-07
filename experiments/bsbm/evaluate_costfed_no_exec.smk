import numpy as np
import pandas as pd
import os
from pathlib import Path
import glob
import time
import requests
import subprocess
import json
import re
from textops import cat, find_first_pattern

import sys
smk_directory = os.path.abspath(workflow.basedir)
sys.path.append(os.path.join(Path(smk_directory).parent.parent, "rsfb"))

from utils import rsfb_logger, load_config, get_docker_endpoint_by_container_name, get_docker_containers, check_container_status, write_empty_result, write_empty_stats, virtuoso_kill_all_transactions, wait_for_container
from utils import activate_one_container as utils_activate_one_container

#===============================
# EVALUATION PHASE:
# - Compile engines
# - Generate results and source selection for each engine
# - Generate metrics and stats for each engine
#===============================

CONFIGFILE = config["configfile"]

WORK_DIR = "experiments/bsbm"
CONFIG = load_config(CONFIGFILE)

CONFIG_GEN = CONFIG["generation"]
CONFIG_EVAL = CONFIG["evaluation"]

SPARQL_COMPOSE_FILE = CONFIG_GEN["virtuoso"]["compose_file"]
SPARQL_SERVICE_NAME = CONFIG_GEN["virtuoso"]["service_name"]
SPARQL_CONTAINER_NAMES = CONFIG_GEN["virtuoso"]["container_names"]

N_QUERY_INSTANCES = CONFIG_GEN["n_query_instances"]
N_BATCH = CONFIG_GEN["n_batch"]
LAST_BATCH = N_BATCH-1

# Config per batch
N_VENDOR=CONFIG_GEN["schema"]["vendor"]["params"]["vendor_n"]
N_RATINGSITE=CONFIG_GEN["schema"]["ratingsite"]["params"]["ratingsite_n"]

FEDERATION_COUNT=N_VENDOR+N_RATINGSITE

QUERY_DIR = f"{WORK_DIR}/queries"
MODEL_DIR = f"{WORK_DIR}/model"
BENCH_DIR = f"{WORK_DIR}/benchmark/evaluation"
TEMPLATE_DIR = f"{MODEL_DIR}/watdiv"

LOGGER = rsfb_logger(Path(__file__).name)

#=================
# USEFUL FUNCTIONS
#=================

def activate_one_container(batch_id):
    """ Activate one container while stopping all others
    """
    utils_activate_one_container(batch_id, SPARQL_COMPOSE_FILE, SPARQL_SERVICE_NAME, logger, f"{BENCH_DIR}/virtuoso-ok.txt")

def generate_federation_declaration(federation_declaration_file, engine, batch_id):
    sparql_endpoint = get_docker_endpoint_by_container_name(SPARQL_COMPOSE_FILE, SPARQL_SERVICE_NAME, SPARQL_CONTAINER_NAMES[LAST_BATCH])

    is_endpoint_updated = False
    if is_file_exists := os.path.exists(federation_declaration_file):
        with open(federation_declaration_file) as f:
            search_string = f'sd:endpoint "{sparql_endpoint}'
            is_endpoint_updated = search_string not in f.read()

    if is_endpoint_updated or not is_file_exists:
        logger.info(f"Rewriting {engine} configfile as it is updated!")
        ratingsite_data_files = [ f"{MODEL_DIR}/dataset/ratingsite{i}.nq" for i in range(N_RATINGSITE) ]
        vendor_data_files = [ f"{MODEL_DIR}/dataset/vendor{i}.nq" for i in range(N_VENDOR) ]

        batch_id = int(batch_id)
        ratingsiteSliceId = np.histogram(np.arange(N_RATINGSITE), N_BATCH)[1][1:].astype(int)[batch_id]
        vendorSliceId = np.histogram(np.arange(N_VENDOR), N_BATCH)[1][1:].astype(int)[batch_id]
        batch_files = ratingsite_data_files[:ratingsiteSliceId+1] + vendor_data_files[:vendorSliceId+1]

        activate_one_container(LAST_BATCH)
        shell(f"python rsfb/engines/{engine}.py generate-config-file {' '.join(batch_files)} {federation_declaration_file} {CONFIGFILE} {batch_id} {sparql_endpoint}")

#=================
# PIPELINE
#=================

rule all:
    input: expand("{benchDir}/metrics.csv", benchDir=BENCH_DIR)

rule merge_metrics:
    priority: 1
    input: expand("{{benchDir}}/metrics_batch{batch_id}.csv", batch_id=range(N_BATCH))
    output: "{benchDir}/metrics.csv"
    run: pd.concat((pd.read_csv(f) for f in input)).to_csv(f"{output}", index=False)

rule merge_batch_metrics:
    priority: 1
    input: 
        metrics="{benchDir}/eval_metrics_batch{batch_id}.csv",
        stats="{benchDir}/eval_stats_batch{batch_id}.csv"
    output: "{benchDir}/metrics_batch{batch_id}.csv"
    run:
        metrics_df = pd.read_csv(f"{input.metrics}")
        stats_df = pd.read_csv(f"{input.stats}")
        out_df = pd.merge(metrics_df, stats_df, on = ["query", "batch", "instance", "engine", "attempt"], how="left")
        out_df.to_csv(str(output), index=False)

rule merge_stats:
    input: 
        expand(
            "{{benchDir}}/{engine}/{query}/instance_{instance_id}/batch_{{batch_id}}/attempt_{attempt_id}/stats.csv", 
            engine=CONFIG_EVAL["engines"],
            query=[Path(os.path.join(QUERY_DIR, f)).resolve().stem for f in os.listdir(QUERY_DIR) if f.endswith(".sparql")],
            instance_id=range(N_QUERY_INSTANCES),
            attempt_id=range(CONFIG_EVAL["n_attempts"])
        )
    output: "{benchDir}/eval_stats_batch{batch_id}.csv"
    run: pd.concat((pd.read_csv(f) for f in input)).to_csv(f"{output}", index=False)

rule compute_metrics:
    priority: 2
    threads: 1
    input: 
        provenance=expand(
            "{{benchDir}}/{engine}/{query}/instance_{instance_id}/batch_{{batch_id}}/attempt_{attempt_id}/provenance.csv", 
            engine=CONFIG_EVAL["engines"],
            query=[Path(os.path.join(QUERY_DIR, f)).resolve().stem for f in os.listdir(QUERY_DIR) if f.endswith(".sparql")],
            instance_id=range(N_QUERY_INSTANCES),
            attempt_id=range(CONFIG_EVAL["n_attempts"])
        ),
        results=expand(
            "{{benchDir}}/{engine}/{query}/instance_{instance_id}/batch_{{batch_id}}/attempt_{attempt_id}/results.csv", 
            engine=CONFIG_EVAL["engines"],
            query=[Path(os.path.join(QUERY_DIR, f)).resolve().stem for f in os.listdir(QUERY_DIR) if f.endswith(".sparql")],
            instance_id=range(N_QUERY_INSTANCES),
            attempt_id=range(CONFIG_EVAL["n_attempts"])
        ),
    output: "{benchDir}/eval_metrics_batch{batch_id}.csv"
    shell: "python rsfb/metrics.py compute-metrics {CONFIGFILE} {output} {input.provenance}"

rule transform_provenance:
    input: "{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/source_selection.txt"
    output: "{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/provenance.csv"
    params:
        prefix_cache=expand("{workDir}/benchmark/generation/{{query}}/instance_{{instance_id}}/prefix_cache.json", workDir=WORK_DIR)
    run: 
        shell("python rsfb/engines/{wildcards.engine}.py transform-provenance {input} {output} {params.prefix_cache}")

rule transform_results:
    input: "{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/results.txt"
    output: "{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/results.csv"
    run:
        # Transform results
        shell("python rsfb/engines/{wildcards.engine}.py transform-results {input} {output}")
        if os.stat(str(output)).st_size > 0:
            expected_results = pd.read_csv(f"{WORK_DIR}/benchmark/generation/{wildcards.query}/instance_{wildcards.instance_id}/batch_{wildcards.batch_id}/results.csv").dropna(how="all", axis=1)
            expected_results = expected_results.reindex(sorted(expected_results.columns), axis=1)
            expected_results = expected_results \
                .sort_values(expected_results.columns.to_list()) \
                .reset_index(drop=True) 

            engine_results = pd.read_csv(str(output)).dropna(how="all", axis=1)
            engine_results = engine_results.reindex(sorted(engine_results.columns), axis=1)
            engine_results = engine_results \
                .sort_values(engine_results.columns.to_list()) \
                .drop_duplicates() \
                .reset_index(drop=True) 

            if not expected_results.equals(engine_results):
                logger.debug(expected_results)
                logger.debug("not equals to")
                logger.debug(engine_results)

                #write_empty_result(str(output))
                #write_empty_stats(f"{Path(str(input)).parent}/stats.csv", "error_mismatch_expected_results")
                logger.error(f"{wildcards.engine} does not produce the expected results")

rule evaluate_engines:
    """Evaluate queries using each engine's source selection on FedX.
    
    - Output: only statistics, no source-seleciton
    """
    threads: 1
    retries: 1
    input: 
        query=ancient(expand("{workDir}/benchmark/generation/{{query}}/instance_{{instance_id}}/injected.sparql", workDir=WORK_DIR)),
        engine_source_selection=ancient(expand("{workDir}/benchmark/generation/{{query}}/instance_{{instance_id}}/batch_{{batch_id}}/provenance.csv", workDir=WORK_DIR)),
        virtuoso_last_batch=ancient(expand("{workDir}/benchmark/generation/virtuoso_batch{batch_n}-ok.txt", workDir=WORK_DIR, batch_n=N_BATCH-1)),
        engine_status=ancient("{benchDir}/{engine}/{engine}-ok.txt"),
    output: 
        stats="{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/stats.csv",
        query_plan="{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/query_plan.txt",
        source_selection="{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/source_selection.txt",
        result_txt="{benchDir}/{engine}/{query}/instance_{instance_id}/batch_{batch_id}/attempt_{attempt_id}/results.txt",
    params:
        eval_config=expand("{workDir}/config.yaml", workDir=WORK_DIR),
        engine_config="{benchDir}/{engine}/config/batch_{batch_id}/{engine}.conf",
        last_batch=LAST_BATCH
    run: 
        activate_one_container(LAST_BATCH)

        engine = str(wildcards.engine)
        batch_id = int(wildcards.batch_id)
        engine_config = f"{WORK_DIR}/benchmark/evaluation/{engine}/config/batch_{batch_id}/{engine}.conf"
        generate_federation_declaration(engine_config, engine, batch_id)

        # Early stop if earlier attempts got timed out
        skipBatch = batch_id - 1
        same_file_previous_batch = f"{BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{wildcards.attempt_id}/results.txt"
        skipAttempt = int(wildcards.attempt_id)

        canSkip = False #batch_id > 0 and os.path.exists(same_file_previous_batch) and os.stat(same_file_previous_batch).st_size == 0
        skipReason = f"Skip evaluation because previous batch at {same_file_previous_batch} timed out or error"
        for attempt in range(CONFIG_EVAL["n_attempts"]):
            same_file_other_attempt = f"{BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{batch_id}/attempt_{attempt}/results.txt"
            logger.info(f"Checking {same_file_other_attempt} ...")
            if os.path.exists(same_file_other_attempt) and os.path.exists(same_file_other_attempt): #and os.stat(same_file_other_attempt).st_size == 0:
                skipBatch = batch_id
                skipAttempt = attempt
                skipReason = f"Skip evaluation because another attempt at {same_file_other_attempt} timed out"
                canSkip = True
                break

        skip_stats_file = f"{BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{skipAttempt}/stats.csv"
        previous_reason = str(skip_stats_file | cat() | find_first_pattern([r"(timeout)"]))

        if canSkip: # and previous_reason != "":
            logger.info(skipReason)
            #write_empty_stats(str(output.stats), previous_reason)
            #shell(f"cp {BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{previous_batch}/attempt_{wildcards.attempt_id}/stats.csv {output.stats}")
            shell(f"cp {BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{skipAttempt}/query_plan.txt {output.query_plan}")
            shell(f"cp {BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{skipAttempt}/source_selection.txt {output.source_selection}")
            shell(f"cp {BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{skipAttempt}/results.txt {output.result_txt}")
            shell(f"cp {BENCH_DIR}/{wildcards.engine}/{wildcards.query}/instance_{wildcards.instance_id}/batch_{skipBatch}/attempt_{skipAttempt}/stats.csv {output.stats}")
        else:
            virtuoso_kill_all_transactions(SPARQL_COMPOSE_FILE, SPARQL_SERVICE_NAME, LAST_BATCH)
            shell("python rsfb/engines/{engine}.py run-benchmark {params.eval_config} {params.engine_config} {input.query} --out-result {output.result_txt}  --out-source-selection {output.source_selection} --stats {output.stats} --force-source-selection {input.engine_source_selection} --query-plan {output.query_plan} --batch-id {batch_id}")

rule engines_prerequisites:
    output: "{benchDir}/{engine}/{engine}-ok.txt"
    params:
        eval_config=expand("{workDir}/config.yaml", workDir=WORK_DIR)
    shell: "python rsfb/engines/{wildcards.engine}.py prerequisites {params.eval_config} && echo 'OK' > {output}"
