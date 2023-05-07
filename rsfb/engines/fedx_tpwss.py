# Import part
from io import BytesIO
import json
import os
import re
import shutil
import click
import glob
import subprocess
import pandas as pd
import numpy as np
from pathlib import Path

import sys
sys.path.append(str(os.path.join(Path(__file__).parent.parent)))

from utils import load_config, rsfb_logger, str2n3, create_stats
logger = rsfb_logger(Path(__file__).name)

import fedx

# Example of use : 
# python3 utils/generate-engine-config-file.py experiments/bsbm/model/vendor test/out.ttl

# Goal : Generate a configuration file for RDF4J to set the use of named graph as endpoint thanks to data file

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
    app_config = load_config(eval_config)["evaluation"]["engines"]["fedx_tpwss"]
    app = app_config["dir"]
    jar = os.path.join(app, "FedX-1.0-SNAPSHOT.jar")
    lib = os.path.join(app, "lib/*")
    
    #if not os.path.exists(app) or not os.path.exists(jar) or os.path.exists(lib):
    oldcwd = os.getcwd()
    os.chdir(Path(app).parent)
    os.system("mvn clean && mvn install dependency:copy-dependencies package")
    os.chdir(oldcwd)

def exec_fedx(eval_config, engine_config, query, out_result, out_source_selection, query_plan, stats, force_source_selection, batch_id):
    config = load_config(eval_config)
    app_config = config["evaluation"]["engines"]["fedx_tpwss"]
    app = app_config["dir"]
    jar = os.path.join(app, "FedX-1.0-SNAPSHOT.jar")
    lib = os.path.join(app, "lib/*")
    timeout = int(config["evaluation"]["timeout"])

    args = [engine_config, query, out_result, out_source_selection, query_plan, stats, str(timeout), force_source_selection]
    args = " ".join(args)
    #timeoutCmd = f'timeout --signal=SIGKILL {timeout}' if timeout != 0 else ""
    timeoutCmd = ""
    cmd = f'{timeoutCmd} java -classpath "{jar}:{lib}" org.example.FedX {args}'.strip()

    logger.debug("=== FedX ===")
    logger.debug(cmd)
    logger.debug("============")
    
    fedx_proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:        
        fedx_proc.wait(timeout)
        if fedx_proc.returncode == 0:
            logger.info(f"{query} benchmarked sucessfully")
            #if os.stat(out_result).st_size == 0:
            #    logger.error(f"{query} yield no results!")
            #    Path(out_result).touch()
            #    Path(out_source_selection).touch()
            #    Path(query_plan).touch()
            #    raise RuntimeError(f"{query} yield no results!")
        else:
            logger.error(f"{query} reported error")    
            Path(out_result).touch()
            Path(out_source_selection).touch()
            Path(query_plan).touch()
            if not os.path.exists(stats):
                create_stats(stats, "error_runtime")                  
    except subprocess.TimeoutExpired: 
        logger.exception(f"{query} timed out!")
        create_stats(stats, "timeout")
        Path(out_result).touch()
        Path(out_source_selection).touch()
        Path(query_plan).touch()                   
    finally:
        os.system('pkill -9 -f "FedX-1.0-SNAPSHOT.jar"')
        #kill_process(fedx_proc.pid)

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
@click.option("--noexec", is_flag=True, default=False)
@click.pass_context
def run_benchmark(ctx: click.Context, eval_config, engine_config, query, out_result, out_source_selection, query_plan, stats, force_source_selection, batch_id, noexec):
    """ Evaluate injected.sparql on Virtuoso. 
    1. Transform the injected.sparql into
        VALUES ?tp1 ... ?tpn { (s1, s2 ... sn) (s1, s2 ... sn) } .
        SERVICE ?tp1 { ... } .
        SERVICE ?tp2 { ... } .
        ...
    2. Execute the transformed query in virtuoso
    3. Mesure execution time, compare the results with results.csv in generation phase
    """
            
    exec_fedx(
        eval_config, engine_config, query, 
        str(out_result), "/dev/null", str(query_plan), 
        str(stats), str(force_source_selection), batch_id
    )
    
    shutil.copy(force_source_selection, out_source_selection)
        

@cli.command()
@click.argument("infile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.pass_context
def transform_results(ctx: click.Context, infile, outfile):
    ctx.invoke(fedx.transform_results, infile=infile, outfile=outfile)

@cli.command()
@click.argument("infile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("prefix-cache", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.pass_context
def transform_provenance(ctx: click.Context, infile, outfile, prefix_cache):
    shutil.copy(infile, outfile)
    #ctx.invoke(fedx.transform_provenance, infile=infile, outfile=outfile, prefix_cache=prefix_cache)

@cli.command()
@click.argument("datafiles", type=click.Path(exists=True, dir_okay=False, file_okay=True), nargs=-1)
@click.argument("outfile", type=click.Path(exists=False, file_okay=True, dir_okay=False))
@click.argument("eval-config", type=click.Path(exists=True, dir_okay=False, file_okay=True))
@click.argument("batch_id", type=click.INT)
@click.argument("endpoint", type=str)
@click.pass_context
def generate_config_file(ctx: click.Context, datafiles, outfile, eval_config, batch_id, endpoint):
    ctx.invoke(fedx.generate_config_file, datafiles=datafiles, outfile=outfile, eval_config=eval_config, endpoint=endpoint)
    

if __name__ == "__main__":
    cli()