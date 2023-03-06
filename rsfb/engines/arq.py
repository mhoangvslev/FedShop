# Import part
from io import BytesIO
import json
import os
import re
import time
import click
import glob
import subprocess
import pandas as pd
import numpy as np
from pathlib import Path

import sys

from rdflib import URIRef
import requests
sys.path.append(str(os.path.join(Path(__file__).parent.parent)))

from utils import kill_process, load_config, str2n3
from query import exec_query_on_endpoint, execute_query

import fedx

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
    os.system()

@cli.command()
@click.argument("eval-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.argument("engine-config", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.argument("query", type=click.Path(exists=True, file_okay=True, dir_okay=True))
@click.option("--out-result", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--out-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--query-plan", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--stats", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="/dev/null")
@click.option("--force-source-selection", type=click.Path(exists=False, file_okay=True, dir_okay=True), default="")
@click.option("--batch-id", type=click.INT, default=-1)
@click.pass_context
def run_benchmark(ctx: click.Context, eval_config, engine_config, query, out_result, out_source_selection, query_plan, stats, force_source_selection, batch_id):
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
    
    opt_source_selection_file = f"{Path(force_source_selection).parent}/{Path(force_source_selection).stem}.opt.csv"
    source_selection_df = pd.read_csv(opt_source_selection_file)
    
    config = load_config(eval_config)
    internal_endpoint_prefix=str(config["evaluation"]["engines"]["arq"]["internal_endpoint_prefix"])
    endpoint = re.search(r":(\d+)", config["generation"]["virtuoso"]["endpoints"][batch_id]).group(1)
    internal_endpoint_prefix=internal_endpoint_prefix.replace("8890", endpoint, 1)
    
    source_selection_combinations = source_selection_df \
        .applymap(lambda x: URIRef(f"{internal_endpoint_prefix}{x}").n3()) \
        .apply(lambda x: f"( {' '.join(x)} )", axis=1) \
        .to_list()
        
    values_clause_vars = [ f"?{col}" for col in source_selection_df.columns ]   
    values_clause = f"    VALUES ( {' '.join(values_clause_vars)} ) {{ {' '.join(source_selection_combinations)} }}\n"
    
    opt_source_selection_query_file = f"{Path(query).parent}/provenance.sparql.opt"
    service_query_file = f"{Path(query_plan).parent}/service.sparql"
    Path(service_query_file).touch()
    
    with    open(opt_source_selection_query_file, "r") as opt_source_selection_qfs, \
            open(query, "r") as query_fs:

        query_text = query_fs.read()
        select_clause = re.search(r"(SELECT(.*)[\S\s]+WHERE)", query_text).group(1)
        
        lines = opt_source_selection_qfs.readlines()
        insert_idx = [ line_idx for line_idx, line in enumerate(lines) if "WHERE" in line ][0]
        lines.insert(insert_idx+1, values_clause)
        
        out_query_text = "".join(lines)
        out_query_text = re.sub(r"SELECT(.*)[\S\s]+WHERE", select_clause, out_query_text)
        out_query_text = re.sub(r"(regex|REGEX)\s*\(\s*(\?\w+)\s*,", r"\1(lcase(str(\2)),", out_query_text)
        out_query_text = re.sub(r"(#)*(FILTER\s*\(\!bound)", r"\2", out_query_text)
        out_query_text = re.sub(r"#*(DEFINE|OFFSET)", r"##\1", out_query_text)
        out_query_text = re.sub(r"#*(ORDER|LIMIT)", r"\1", out_query_text)
        out_query_text = re.sub("GRAPH", "SERVICE", out_query_text)
        
        # footer = re.search(r"((ORDER|OFFSET|LIMIT).*[\S\s])", query_text).group(1)
        # out_query_text += footer
        
        with open(service_query_file, "w") as service_query_fs:
            service_query_fs.write(out_query_text)
            service_query_fs.close()
   
        # Execute results
        endpoint = config["evaluation"]["engines"]["arq"]["endpoint"]
        timeout = config["evaluation"]["timeout"]
        exec_time = None
        http_req = "N/A"
        result = None
        
        try:
            startTime = time.time()
            _, result = exec_query_on_endpoint(out_query_text, endpoint=endpoint, error_when_timeout=True, timeout=timeout)
            endTime = time.time()
            exec_time = (endTime-startTime)*1e3
        except:
            exec_time = "timeout"
            http_req = "timeout"
        
        # Write results
        if result is not None:
            with BytesIO(result) as header_stream, BytesIO(result) as data_stream: 
                header = header_stream.readline().decode().strip().replace('"', '').split(",")
                csvOut = pd.read_csv(data_stream, parse_dates=[h for h in header if "date" in h])
                csvOut.to_csv(out_result, index=False)
        else:
            Path(out_result).touch()
        
        # Write stats
        with open(stats, "w") as stats_fs:
            stats_fs.write("query,engine,instance,batch,attempt,exec_time,http_req\n")
            basicInfos = re.match(r".*/(\w+)/(q\w+)/instance_(\d+)/batch_(\d+)/attempt_(\d+)/stats.csv", stats)
            engine = basicInfos.group(1)
            queryName = basicInfos.group(2)
            instance = basicInfos.group(3)
            batch = basicInfos.group(4)
            attempt = basicInfos.group(5)
            stats_fs.write(",".join([queryName, engine, instance, batch, attempt, exec_time, http_req])+"\n") 
        
        # Write output source selection
        os.system(f"cp {force_source_selection} {out_source_selection}")

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
    pass

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
    pass

@cli.command()
@click.argument("datafiles", type=click.Path(exists=True, dir_okay=False, file_okay=True), nargs=-1)
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.option("--endpoint", type=str, default="http://localhost:8890/sparql", help="URL to a SPARQL endpoint")
@click.pass_context
def generate_config_file(ctx: click.Context, datafiles, outfile, endpoint):
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