# Import part
from io import BytesIO
import json
import os
import random
import re
import shutil
import time
import click
import glob
import pandas as pd
from pathlib import Path

import sys

from rdflib import URIRef
import requests
from tqdm import tqdm
sys.path.append(str(os.path.join(Path(__file__).parent.parent)))

from algebra.rdflib_algebra import add_service_to_triple_blocks, add_values_with_placeholders
from utils import load_config, fedshop_logger, create_stats
from query import export_query, exec_query_on_endpoint, parse_query_proc
from rdflib.plugins.sparql.algebra import traverse

logger = fedshop_logger(Path(__file__).name)

import fedup

# How to use
# 1. Duplicate this file and rename the new file with <engine>.py
# 2. Implement all functions
# 3. Register the engine in config.yaml, under evaluation.engines section
# 
# Note: when you update the signature of any of these functions, you also have to update their signature in other engines

@click.group
def cli():
    pass

@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.pass_context
def prerequisites(ctx: click.Context, eval_config):
    """Obtain prerequisite artifact for engine, e.g, compile binaries, setup dependencies, etc.

    Args:
        eval_config (_type_): _description_
    """
    config = load_config(eval_config)

    # Download and install Jena
    current_pwd = os.getcwd()
    os.chdir(config["evaluation"]["engines"]["rsa"]["dir"])
    os.system("sh setup.sh")
    os.chdir(current_pwd)

    #ctx.invoke(warmup, eval_config=eval_config)
    
    # Compile and install fedup
    ctx.invoke(fedup.prerequisites, eval_config=eval_config)
    
@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option("--engine-config", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null") # Engine config is not needed
@click.option("--repeat", type=click.INT, default=1)
@click.option("--batch-id", type=click.INT, default=-1)
@click.pass_context
def warmup(ctx: click.Context, eval_config, engine_config, repeat, batch_id):
    
    def ping(url):
        try:
            return requests.get(endpoint, params={"query": "ASK {?s ?p ?o}"}).status_code
        except:
            return -1

    # Probe and start Jena 
    config = load_config(eval_config)
    endpoint = config["evaluation"]["engines"]["rsa"]["endpoint"]    
        
    if ping(endpoint) == -1:
        compose_file = config["evaluation"]["engines"]["rsa"]["compose_file"]
        service_name = config["evaluation"]["engines"]["rsa"]["service_name"]
        if os.system(f"docker compose -f {compose_file} up -d {service_name}") != 0:
            raise RuntimeError("Could not setup Jena Docker Container...")
    
    while ping(endpoint) != 200:
        logger.debug("Waiting for Jena...")
        time.sleep(1)
    
    # Warm up the server
    config = load_config(eval_config) 
    queries = glob.glob("experiments/bsbm/benchmark/generation/q*/instance*/injected.sparql")
    random.shuffle(queries)
    for query in tqdm(queries):
        for batch_id in range(config["generation"]["n_batch"]):
            force_source_selection = f"{Path(query).parent}/batch_{batch_id}/provenance.csv"
            for _ in range(repeat):
                success = False
                while not success:
                    try:
                        ctx.invoke(run_benchmark, eval_config=eval_config, engine_config=engine_config, query=query, force_source_selection=force_source_selection, batch_id=batch_id)
                        success = True
                    except:
                        # Wait so that server could release resources
                        time.sleep(1)

@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.argument("query", type=click.Path(exists=False, file_okay=True, dir_okay=True)) # Engine config is not needed
@click.argument("query-plan", type=click.Path(exists=False, file_okay=True, dir_okay=True))
@click.argument("force-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True))
@click.pass_context
def create_service_query_manually(ctx: click.Context, eval_config, query, query_plan, force_source_selection):
    """Create a SERVICE query that will be sent to Jena

    Args:
        eval_config (_type_): _description_
        query (_type_): _description_
        query_plan (_type_): _description_
        force_source_selection (_type_): _description_

    Returns:
        _type_: _description_
    """
    opt_source_selection_file = f"{Path(force_source_selection).parent}/{Path(force_source_selection).stem}.opt.csv"
    source_selection_df = pd.read_csv(opt_source_selection_file)
    
    eval_config = load_config(eval_config)
    proxy_mapping_file = eval_config["generation"]["virtuoso"]["proxy_mapping"]
    proxy_mapping = {}
    with open(proxy_mapping_file, "r") as proxy_mapping_fs:
        proxy_mapping = json.load(proxy_mapping_fs)
    
    source_selection_combinations = source_selection_df \
        .applymap(lambda x: URIRef(proxy_mapping[x]).n3()) \
        .apply(lambda x: f"( {' '.join(x)} )", axis=1) \
        .to_list()
        
    query_algebra, query_options = parse_query_proc(queryfile=query)
    
    inline_data = dict(zip(source_selection_df.columns, source_selection_combinations)) 
    query_algebra = traverse(query_algebra, visitPost=lambda node: add_values_with_placeholders(node, inline_data))
    query_algebra = traverse(query_algebra, visitPost=lambda node: add_service_to_triple_blocks(node, inline_data))
    
    export_query(query_algebra, query_options, outfile=query_plan)
    
@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.argument("query", type=click.Path(exists=False, file_okay=True, dir_okay=True)) # Engine config is not needed
@click.argument("query-plan", type=click.Path(exists=False, file_okay=True, dir_okay=True))
@click.option("--force-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True))
@click.option("--batch-id", type=click.INT)
@click.pass_context
def create_service_query(ctx: click.Context, eval_config, query, query_plan, force_source_selection, batch_id):
    """Create a SERVICE query using FedUP

    Args:
        eval_config (_type_): _description_
        query (_type_): _description_
        query_plan (_type_): _description_
        force_source_selection (_type_): _description_

    Returns:
        _type_: _description_
    """
    conf = load_config(eval_config)
    fedup_dir = conf["evaluation"]["engines"]["rsa"]["fedup_dir"]
    proxy_mapping_file = conf["generation"]["virtuoso"]["proxy_mapping"]
    proxy_mapping_file = os.path.realpath(proxy_mapping_file)
    proxy_host = conf["evaluation"]["proxy"]["host"]
    proxy_port = conf["evaluation"]["proxy"]["port"]
        
    query = os.path.realpath(query)
    query_plan = os.path.realpath(query_plan)
    
    summary_file = os.path.realpath(f"{fedup_dir}/summaries/fedshop/batch{batch_id}/fedup-id")
    federation_file = os.path.realpath(f"{fedup_dir}/config/fedshop/endpoints_batch{batch_id}.txt")
    
    Path(federation_file).parent.mkdir(parents=True, exist_ok=True)
    with open(federation_file, "w") as f, open(proxy_mapping_file, "r") as proxy_mapping_fs:
        proxy_mapping = json.load(proxy_mapping_fs)
        federation_members = conf["generation"]["virtuoso"]["federation_members"]
        for federation_member_iri in federation_members[f"batch{batch_id}"].values():
            #f.write(f"{proxy_mapping[federation_member_iri]}\n") # TODO Use this line for other engines
            f.write(f"{federation_member_iri}\n")
    
    os.chdir(fedup_dir)
    # -Dhttp.proxyHost={proxy_host} -Dhttp.proxyPort={proxy_port} 
    cmd = f'mvn exec:java -Dmain.class="fr.gdd.fedup.utils.QuerySourceSelectionExplain" -Dhttp.proxyHost="{proxy_host}" -Dhttp.proxyPort="{proxy_port}" -Dhttp.nonProxyHosts="" -Dhttps.proxyHost="{proxy_host}" -Dhttps.proxyPort="{proxy_port}" -Dexec.args="--query={query} --summary={summary_file} --output={query_plan} --federation={federation_file} --format=union --mapping={proxy_mapping_file}"'
    logger.debug(f"{cmd}")
    os.system(cmd)
    
@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.argument("engine-config", type=click.Path(exists=False, file_okay=True, dir_okay=True))
@click.argument("query", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option("--out-result", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--out-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--query-plan", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--stats", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--force-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="")
@click.option("--batch-id", type=click.INT, default=-1)
@click.option("--noexec", is_flag=True, default=False)
@click.pass_context
def run_benchmark(ctx: click.Context, eval_config, engine_config, query, out_result, out_source_selection, query_plan, stats, force_source_selection, batch_id, noexec):
    """Execute the workload instance then its associated source selection query.
    
    Expected output:
    - results.txt: containing the results for the query
    - source_selection.txt: containing the source selection for the query
    - stats.csv: containing the execution time, http_requests for the query

    Args:
        ctx (click.Context): _description_
        eval_config (_type_): _description_
        engine_config (_type_): _description_
        query (_type_): _description_
        out_result (_type_): _description_
        out_source_selection (_type_): _description_
        query_plan (_type_): _description_
        stats (_type_): _description_
        force_source_selection (_type_): _description_
        batch_id (_type_): _description_
    """
    
    if force_source_selection == "":
        raise RuntimeError("You must provide reference source selection for this engine.")
    
    config = load_config(eval_config)
    Path(query_plan).touch(exist_ok=True)
   
    # Execute results
    endpoint = config["evaluation"]["engines"]["rsa"]["endpoint"]
    timeout = config["evaluation"]["timeout"]
    exec_time = None
    
    force_source_selection_df = pd.read_csv(force_source_selection).dropna(axis=1, how="all")
    response, result = None, None
    
    proxy_server = config["evaluation"]["proxy"]["endpoint"]
    proxy_port = re.search(r":(\d+)", proxy_server).group(1)
    proxy_sparql_endpoint = proxy_server + "sparql"
    
    # Reset the proxy stats
    if requests.get(proxy_server + "reset").status_code != 200:
        raise RuntimeError("Could not reset statistics on proxy!")
    
    startTime = time.time()

    # In case there is only one source for all triple patterns, send the original query to Virtuoso.
    # In such case, it doesn't make sense to send a federated version of the query, i.e, with SERVICE clause.
    
    # if len(force_source_selection_df) == 1 and force_source_selection_df.iloc[0, :].nunique() == 1 : 
    #     default_graph = force_source_selection_df.iloc[0, :].unique().item()
    #     with open(query, "r") as qfs:
    #         query_text = qfs.read()
    #         response, result = exec_query_on_endpoint(query_text, proxy_sparql_endpoint, error_when_timeout=True, timeout=timeout, default_graph=default_graph)
    # else:
    out_query_text = ctx.invoke(create_service_query, eval_config=eval_config, query=query, query_plan=query_plan, force_source_selection=force_source_selection)
    response, result = exec_query_on_endpoint(out_query_text, endpoint, error_when_timeout=True, timeout=timeout)
        
    endTime = time.time()
    exec_time = (endTime - startTime)*1e3
    
    with BytesIO(result) as header_stream, BytesIO(result) as data_stream: 
        header = header_stream.readline().decode().strip().replace('"', '').split(",")
        csvOut = pd.read_csv(data_stream, parse_dates=[h for h in header if "date" in h])                
        csvOut.to_csv(out_result, index=False)
        
    if csvOut.empty:
        raise RuntimeError("Query yields no results")
   
    # Write output source selection
    shutil.copyfile(force_source_selection, out_source_selection)
    
    # Write stats
    if stats != "/dev/null":
        with open(f"{Path(stats).parent}/exec_time.txt", "w") as exec_time_fs:
            exec_time_fs.write(str(exec_time))
        
        # Write proxy stats
        proxy_stats = json.loads(requests.get(proxy_server + "get-stats").text)
        
        with open(f"{Path(stats).parent}/http_req.txt", "w") as http_req_fs:
            http_req = proxy_stats["NB_HTTP_REQ"]
            http_req_fs.write(str(http_req))
            
        with open(f"{Path(stats).parent}/ask.txt", "w") as http_ask_fs:
            http_ask = proxy_stats["NB_ASK"]
            http_ask_fs.write(str(http_ask))
            
        with open(f"{Path(stats).parent}/data_transfer.txt", "w") as data_transfer_fs:
            data_transfer = proxy_stats["DATA_TRANSFER"]
            data_transfer_fs.write(str(data_transfer))
        
        logger.info(f"Writing stats to {stats}")
        create_stats(stats)
    
    

@cli.command()
@click.argument("infile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.pass_context
def transform_results(ctx: click.Context, infile, outfile):
    """Transform the result from the engine's specific format to virtuoso csv format

    Args:
        ctx (click.Context): _description_
        infile (_type_): Path to engine result file
        outfile (_type_): Path to the csv file
    """
    shutil.copy(infile, outfile)

@cli.command()
@click.argument("infile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("prefix-cache", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.pass_context
def transform_provenance(ctx: click.Context, infile, outfile, prefix_cache):
    """Transform the source selection from engine's specific format to virtuoso csv format

    Args:
        ctx (click.Context): _description_
        infile (_type_): _description_
        outfile (_type_): _description_
        prefix_cache (_type_): _description_
    """
    shutil.copy(infile, outfile)
    
@cli.command()
@click.argument("datafiles", type=click.Path(exists=True, dir_okay=False, file_okay=True), nargs=-1)
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("eval-config", type=click.Path(exists=True, dir_okay=False, file_okay=True))
@click.argument("batch_id", type=click.INT)
@click.argument("endpoint", type=str)
@click.pass_context
def generate_config_file(ctx: click.Context, datafiles, outfile, eval_config, batch_id, endpoint):
    """Generate the config file for the engine

    Args:
        ctx (click.Context): _description_
        datafiles (_type_): _description_
        outfile (_type_): _description_
        endpoint (_type_): _description_
    """
    pass

if __name__ == "__main__":
    cli()