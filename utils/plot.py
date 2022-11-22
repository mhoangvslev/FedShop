import glob
import os
from pathlib import Path
import click
import subprocess
from io import StringIO
import seaborn as sns
import pandas as pd
import ast
from fitter import Fitter, get_common_distributions, get_distributions
import pylab
import numpy as np

@click.group
def cli():
    pass

def execute_query(queryfile, endpoint):
    endpoint_proc = subprocess.run( 
        f"python utils/query.py execute-query {queryfile} --endpoint {endpoint}", 
        capture_output=True, shell=True
    )
    
    if endpoint_proc.returncode != 0:
        raise RuntimeError(endpoint_proc.stderr.decode())
    
    data = endpoint_proc.stdout.decode().splitlines()

    result = pd.read_csv(StringIO("\n".join(data[:-1])))
    records = ast.literal_eval(data[-1])

    return result, records

class PlotFitter(Fitter):
    def __init__(self, data, xmin=None, xmax=None, bins=100, distributions=None, timeout=30, density=True):
        super().__init__(data, xmin, xmax, bins, distributions, timeout, density)
        self._density = density
        
    def summary(self, Nbest=5, lw=2, plot=True, method="sumsquare_error", clf=True, figout=None):
        """Plots the distribution of the data and Nbest distribution"""
        if plot:
            if clf: pylab.clf()
            self.hist()
            self.plot_pdf(Nbest=Nbest, lw=lw, method=method)
            pylab.grid(True)
            pylab.xlabel("count_value")
            pylab.ylabel("frequency")
            if figout is not None:
                pylab.savefig(figout)

        Nbest = min(Nbest, len(self.distributions))
        try:
            names = self.df_errors.sort_values(by=method).index[0:Nbest]
        except:  # pragma: no cover
            names = self.df_errors.sort(method).index[0:Nbest]
        return self.df_errors.loc[names]

@cli.command()
@click.argument("queryfile", type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.option("--csvout", type=click.Path(file_okay=True, dir_okay=False))
@click.option("--fitout", type=click.Path(file_okay=True, dir_okay=False))
@click.option("--fitfig", type=click.Path(file_okay=True, dir_okay=False))
@click.option("--endpoint", type=str, default="http://localhost:8890/sparql/", help="SPARQL endpoint")
def plot_entitytype_distribution(queryfile, csvout, fitout, fitfig, endpoint):
    result, _ = execute_query(queryfile, endpoint)
    if result.empty:
        raise RuntimeError(f"{queryfile} returns no result...")

    if fitout is not None:
        data = result[result.columns[1]].values
        # label_to_number = defaultdict(partial(next, count(1)))
        # data = [label_to_number[label] for label in data]

        fitter = PlotFitter(data, distributions=get_common_distributions())
        fitter.fit()
        fit_result = fitter.summary(Nbest=5, plot=True, method="sumsquare_error", figout=fitfig)
        fit_result.to_csv(fitout)
        print(fitter.get_best(method="sumsquare_error"))
    
    if csvout is not None:
        result.to_csv(csvout, index=False)
    else:
        print(result)  

@cli.command()
@click.argument("benchdir")
def plot_ss_performance_per_query(benchdir):
    all_records = glob.glob(os.path.join(benchdir, "*.rec.csv"))
    all_dumps = glob.glob(os.path.join(benchdir, "*.dump.csv"))

    for dumpfile in all_dumps:
        dump = pd.read_csv(dumpfile)
        print(dumpfile)
        print(dump)
        tpwss = dump.apply(lambda x: np.sum(x.nunique()))
        print(tpwss)
        break
    
    virtuoso_exec_rec = pd.concat((pd.read_csv(f) for f in all_records)).set_index("query")
    print(virtuoso_exec_rec)
  
if __name__ == "__main__":
    cli()