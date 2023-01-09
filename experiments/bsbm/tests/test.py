from io import BytesIO
import os
from pathlib import Path
import unittest
import click
from click.testing import CliRunner

import pandas as pd
import numpy as np
from scipy.stats import norm, kstest
import seaborn as sns

# from matplotlib_terminal import plt
import matplotlib.pyplot as plt

from sklearn.preprocessing import LabelEncoder

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import sys
directory = os.path.abspath(__file__)
sys.path.append(os.path.join(Path(directory).parent.parent.parent.parent, "rsfb"))

@click.group
def cli():
    pass

from utils import load_config
from query import exec_query

WORKDIR = Path(__file__).parent
CONFIG = load_config("experiments/bsbm/config.yaml")["generation"]
SPARQL_ENDPOINT = CONFIG["sparql"]["endpoint"]
STATS_SIGNIFICANCE_LEVEL = 1 - CONFIG["stats"]["confidence_level"]

COUNTRIES_EXPECTED_WEIGHT = {"US": 0.40, "UK": 0.10, "JP": 0.10, "CN": 0.10, "DE": 0.05, "FR": 0.05, "ES": 0.05, "RU": 0.05, "KR": 0.05, "AT": 0.05}
LANGTAGS_EXPECTED_WEIGHT = {"en": 0.50, "ja": 0.10, "zh": 0.10, "de": 0.05, "fr": 0.05, "es": 0.05, "ru": 0.05, "kr": 0.05, "at": 0.05}

WATDIV_BOOST_MU = 0.5
WATDIV_BOOST_SIGMA = 0.5/3.0 

def query(queryfile):
    saveAs = f"{Path(queryfile).parent}/{Path(queryfile).stem}.csv"

    if os.path.exists(saveAs):
        with open(saveAs, "r") as fp:
            header = fp.readline().strip().replace('"', '').split(",")
            result = pd.read_csv(saveAs, parse_dates=[h for h in header if "date" in h])
            return result
    else:
        with open(queryfile, "r") as fp:
            query_text = fp.read()
            _, result = exec_query(query_text, SPARQL_ENDPOINT, error_when_timeout=True)
            header = BytesIO(result).readline().decode().strip().replace('"', '').split(",")
            result = pd.read_csv(BytesIO(result), parse_dates=[h for h in header if "date" in h])
            result.to_csv(saveAs, index=False)

        return result

def dist_test(data: pd.Series, dist: str, figname=None, **kwargs):
    """Test whether the a sample follows normal distribution

        One sample, two-sided Kolmogorov-Smirnov for goodness of fit :
        H0: The test sample is drawn from normal distribution (equal mean)
        H1: The test sample is not drawn from normal distribution (different mean)
        pvalue < alpha = reject H0

    Args:
        data (pd.Series): [description]
        figname ([type], optional): [description]. Defaults to None.

    Returns:
        [type]: [description]
    """

    dist_dict = {
        "norm": lambda x: norm.cdf(x, loc=kwargs["loc"], scale=kwargs["scale"]),
        "uniform": lambda x: uniform.cdf(x, loc=kwargs["loc"], scale=kwargs["scale"])
    }

    if isinstance(data, list):
        data = pd.Series(data)

    if not np.issubdtype(data.dtype, np.number):
        data = pd.Series(LabelEncoder().fit_transform(data), name="producers")

    try: 
        _, pvalue = kstest(data, dist_dict.get(dist))

        if figname is not None and pvalue < STATS_SIGNIFICANCE_LEVEL:
            figfile = f"{figname}.png"
            if not os.path.exists(figfile):
                fig = data.plot(kind="hist", edgecolor="black")
                data.plot(kind="kde", ax=fig, secondary_y=True)
                plt.savefig(figfile)

        plt.close()

        return pvalue
    except ValueError:
        return np.nan

############
## Test suites
############ 

class TestGenerationTemplate(unittest.TestCase):

    def assertListEqual(self, first, second, msg=None) -> None:

        self.assertIsInstance(first, list, msg="First argument should be a list.")

        if isinstance(second, list):
            return super().assertListEqual(first, second, msg)
        else:
            for item in first:
                self.assertEqual(item, second, msg=msg)


    def assertListAlmostEqual(self, first, second, msg, places=None, delta=None):
        self.assertIsInstance(first, list, msg="First argument should be a list.")
        self.assertEqual(len(first), len(second))
        for item1, item2 in zip(first, second):
            self.assertAlmostEqual(item1, item2, msg=msg, places=places, delta=delta)

    def assertListGreater(self, first, second, msg):
        self.assertIsInstance(first, list, msg="First argument should be a list.")

        if isinstance(second, list):
            for item1, item2 in zip(first, second):
                self.assertGreater(item1, item2, msg=msg)
        else:
            for item in first:
                self.assertGreater(item, second, msg)
    
    def assertListGreaterEqual(self, first, second, msg):
        self.assertIsInstance(first, list, msg="First argument should be a list.")

        if isinstance(second, list):
            for item1, item2 in zip(first, second):
                self.assertGreaterEqual(item1, item2, msg=msg)
        else:
            for item in first:
                self.assertGreaterEqual(item, second, msg)
    
    def assertListLess(self, first, second, msg):
        self.assertIsInstance(first, list, msg="First argument should be a list.")

        if isinstance(second, list):
            for item1, item2 in zip(first, second):
                self.assertLess(item1, item2, msg=msg)
        else:
            for item in first:
                self.assertLess(item, second, msg)
    
    def assertListLessEqual(self, first, second, msg):
        self.assertIsInstance(first, list, msg="First argument should be a list.")

        if isinstance(second, list):
            for item1, item2 in zip(first, second):
                self.assertLessEqual(item1, item2, msg=msg)
        else:
            for item in first:
                self.assertLessEqual(item, second, msg)

class TestGenerationGlobal(TestGenerationTemplate):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.system(f"rm {WORKDIR}/global/*.png")
        os.system(f"rm {WORKDIR}/global/*.csv")    

    def test_global_langtags(self):
        """Test whether langtags across the dataset matches expected frequencies .
        """

        tolerance = 0.03

        queryfile = f"{WORKDIR}/global/test_global_langtags.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        data = np.arange(CONFIG["schema"]["vendor"]["params"]["vendor_n"])
        _, edges = np.histogram(data, CONFIG["n_batch"])
        edges = edges[1:].astype(int)
        
        result["batchId"] = result["batchId"].apply(lambda x: np.argwhere((x <= edges)).min().item())

        proportions = result.groupby(["batchId"])["lang"].value_counts(normalize=True).to_frame("proportion") 

        expected_proportion = pd.DataFrame.from_dict(LANGTAGS_EXPECTED_WEIGHT, orient="index", columns=["expected_proportion"])
        expected_proportion.index.name = "lang"

        test = proportions.join(expected_proportion, on=["lang"]).round(2)
        test.to_csv(f"{Path(queryfile).parent}/test_global_langtags_final.csv")
        
        self.assertListAlmostEqual(
            test["proportion"].to_list(), test["expected_proportion"].to_list(),
            delta=tolerance,
            msg="The frequency for language tags should match config's."
        )

    def test_global_countries(self):
        """Test whether countroes across the dataset matches expected frequencies .
        """

        tolerance = 0.08

        queryfile = f"{WORKDIR}/global/test_global_countries.sparql"
        result = query(queryfile)
        result.replace("http://downlode.org/rdf/iso-3166/countries#", "", regex=True, inplace=True)

        data = np.arange(CONFIG["schema"]["vendor"]["params"]["vendor_n"])
        _, edges = np.histogram(data, CONFIG["n_batch"])
        edges = edges[1:].astype(int)
        
        result["batchId"] = result["batchId"].apply(lambda x: np.argwhere((x <= edges)).min().item())

        proportions = result.groupby(["batchId"])["country"].value_counts(normalize=True).to_frame("proportion") 

        expected_proportion = pd.DataFrame.from_dict(COUNTRIES_EXPECTED_WEIGHT, orient="index", columns=["expected_proportion"])
        expected_proportion.index.name = "country"

        test = proportions.join(expected_proportion, on=["country"]).round(2)
        test.to_csv(f"{Path(queryfile).parent}/test_global_countries_final.csv")
        
        self.assertListAlmostEqual(
            test["proportion"].to_list(), test["expected_proportion"].to_list(),
            delta=tolerance,
            msg="The frequency for bsbm:country should match config's."
        )

class TestGenerationProduct(TestGenerationTemplate):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.system(f"rm {WORKDIR}/product/*.png")
        os.system(f"rm {WORKDIR}/product/*.csv")    

    def test_product_rel_feature(self):
        """Test whether the Product-ProductFeature is Many to Many and ProductFeature-Product is Many to Many
        """

        queryfile = f"{WORKDIR}/product/test_product_nb_feature.sparql"
        result = query(queryfile)

        relation_lhs = result["groupProductFeature"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        relation_lhs.to_csv(f"{Path(queryfile).parent}/test_product_rel_nb_feature.csv")

        self.assertListEqual(
            relation_lhs.to_list(), 1,
            "Every product should have 1..n producer"
        )

        relation_rhs = result.explode("groupProductFeature").groupby("groupProductFeature")["localProduct"].count()

        self.assertListGreaterEqual(
            relation_rhs.to_list(), 1,
            "Every producer should have 1..n products"
        )
    
    def test_product_dist_nb_feature(self):
        """Test whether the features across products follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/product/test_product_nb_feature.sparql"
        result = query(queryfile)
        result["groupProductFeature"] = result["groupProductFeature"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        normal_test_result = dist_test(result["groupProductFeature"], "norm", mu=WATDIV_BOOST_MU, sigma=WATDIV_BOOST_SIGMA, figname=f"{Path(queryfile).parent}/test_product_nb_feature")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupProductFeature"]).to_csv(f"{Path(queryfile).parent}/test_product_nb_feature_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "ProductFeature should follow Normal Distribution across Product. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

    def test_product_rel_producer(self):
        """Test whether the relationship Product-Producer is Many to One and Producer-Product is One to Many.
        """

        queryfile = f"{WORKDIR}/product/test_product_nb_producer.sparql"
        result = query(queryfile)
        
        relation_lhs = result["groupProducer"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        relation_lhs.to_csv(f"{Path(queryfile).parent}/test_rel_product_nb_producer.csv")

        self.assertListEqual(
            relation_lhs.to_list(), 1,
            "Every product should have 1 producer"
        )

        relation_rhs = result.explode("groupProducer").groupby("groupProducer")["localProduct"].count()

        self.assertListGreaterEqual(
            relation_rhs.to_list(), 1,
            "Every producer should have 1..n products"
        )
        
    def test_normal_product_nb_producer(self):
        """Test whether the number of producers follows normal distribution .
        """

        queryfile = f"{WORKDIR}/product/test_product_nb_producer.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        data = np.arange(CONFIG["schema"]["vendor"]["params"]["vendor_n"])
        _, edges = np.histogram(data, CONFIG["n_batch"])
        edges = edges[1:].astype(int)

        result["batchId"] = result["batchId"].apply(lambda x: np.argwhere((x <= edges)).min().item())
        result["groupProducer"] = result["groupProducer"].apply(lambda x: x.split("|"))

        group_producer_by_batches = result.groupby("batchId")["groupProducer"] \
            .aggregate(lambda x: np.concatenate(x.to_numpy())) \
            .to_frame("groupProducer") \
            .reset_index()

        normal_test_result = group_producer_by_batches.apply(
            lambda row: dist_test(row["groupProducer"], figname=f"{Path(queryfile).parent}/{Path(queryfile).stem}_batch{row['batchId']}"), 
            axis=1
        )
        
        normal_test_result \
            .to_frame("pvalue").set_index(group_producer_by_batches["batchId"]) \
            .to_csv(f"{Path(queryfile).parent}/test_product_nb_producer_normaltest.csv")

        self.assertListGreaterEqual(
            normal_test_result.to_list(), STATS_SIGNIFICANCE_LEVEL,
            "Producers should follow Normal Distribution for each batch. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

    def test_product_numeric_props_range(self):
        """Test whether productPropertyNumeric matches expected frequencies .
        """

        queryfile = f"{WORKDIR}/product/test_product_numeric_props.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        minVals = result.groupby("prop")["propVal"].min()
        maxVals = result.groupby("prop")["propVal"].max()

        expected_data = pd.DataFrame.from_dict({
            "productPropertyNumeric1": {"min": 1, "max": 2000},
            "productPropertyNumeric2": {"min": 1, "max": 2000},
            "productPropertyNumeric3": {"min": 1, "max": 2000},
            "productPropertyNumeric4": {"min": 1, "max": 2000},
            "productPropertyNumeric5": {"min": 1, "max": 2000}
        }).T
        
        self.assertListGreaterEqual(
            minVals.to_list(), expected_data["min"],
            "The min value for productPropertyNumeric must be greater or equal to WatDiv config's ."
        )
            
        self.assertListLessEqual(
            maxVals, expected_data["max"],
            "The max value for productPropertyNumeric must be less or equal to WatDiv config's ."
        )

    def test_product_numeric_props_frequency(self):
        """Test whether productPropertyNumeric approximately matches expected frequencies .
        """

        tolerance = 0.1

        queryfile = f"{WORKDIR}/product/test_product_numeric_props.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        nbProducts = result["localProduct"].nunique()
        frequencies = (result.groupby("prop")["propVal"].count() / nbProducts).round(2)

        expected_data = {
            "productPropertyNumeric1": 1.0,
            "productPropertyNumeric2": 1.0,
            "productPropertyNumeric3": 1.0,
            "productPropertyNumeric4": CONFIG["schema"]["product"]["params"]["productPropertyNumeric4_p"],
            "productPropertyNumeric5": CONFIG["schema"]["product"]["params"]["productPropertyNumeric5_p"]
        }
        
        self.assertListAlmostEqual(
            frequencies.to_list(), list(expected_data.values()),
            delta=tolerance,
            msg="The frequency for productPropertyNumeric should match config's."
        )
                
    def test_product_numeric_props_normal(self):
        """Test whether productPropertyNumeric follows Normal distribution .
        """

        queryfile = f"{WORKDIR}/product/test_product_numeric_props.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        normal_test_data = result.groupby("prop")["propVal"].aggregate(list).to_frame("propVal").reset_index()
        normal_test_result = normal_test_data.apply(
            lambda row: dist_test(row["propVal"], figname=f"{Path(queryfile).parent}/{Path(queryfile).stem}_productPropertyNumeric{row['prop']}"), 
            axis=1
        )
        
        normal_test_result \
            .to_frame("pvalue").set_index(normal_test_data["prop"]) \
            .to_csv(f"{Path(queryfile).parent}/test_product_numeric_props_normaltest.csv")

        self.assertGreaterEqual(
            normal_test_result.to_list(), STATS_SIGNIFICANCE_LEVEL,
            "productPropertyNumeric should follow Normal Distribution for each batch. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

    def test_product_textual_props_frequency(self):
        """Test whether productPropertyTextual approximately matches expected frequencies .
        """

        tolerance = 0.1

        queryfile = f"{WORKDIR}/product/test_product_textual_props.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        nbProducts = result["localProduct"].nunique()
        frequencies = (result.groupby("prop")["propVal"].count() / nbProducts).round(2)

        expected_data = {
            "productPropertyTextual1": 1.0,
            "productPropertyTextual2": 1.0,
            "productPropertyTextual3": 1.0,
            "productPropertyTextual4": CONFIG["schema"]["product"]["params"]["productPropertyTextual4_p"],
            "productPropertyTextual5": CONFIG["schema"]["product"]["params"]["productPropertyTextual5_p"]
        }
        
        self.assertListAlmostEqual(
            frequencies.to_list(), list(expected_data.values()),
            delta=tolerance,
            msg="The frequency for productPropertyTextual should match config's."
        )
                
    def test_product_textual_props_normal(self):
        """Test whether productPropertyTextual follows Normal distribution .
        """

        queryfile = f"{WORKDIR}/product/test_product_textual_props.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        normal_test_data = result.groupby("prop")["propVal"].aggregate(list).to_frame("propVal").reset_index()
        normal_test_result = normal_test_data.apply(
            lambda row: dist_test(row["propVal"], figname=f"{Path(queryfile).parent}/{Path(queryfile).stem}productPropertyTextual{row['prop']}"), 
            axis=1
        )
        
        normal_test_result \
            .to_frame("pvalue").set_index(normal_test_data["prop"]) \
            .to_csv(f"{Path(queryfile).parent}/test_product_textual_props_normaltest.csv")

        self.assertTrue(
            (normal_test_result >= STATS_SIGNIFICANCE_LEVEL).all(),
            "productPropertyTextual should follow Normal Distribution for each batch. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

class TestGenerationVendor(TestGenerationTemplate):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.system(f"rm {WORKDIR}/vendor/*.png")
        os.system(f"rm {WORKDIR}/vendor/*.csv")    

    def test_offer_rel_product(self):
        """Test whether the relationship Offer-Product is Many to One and Product-Offer is One to Many
        """

        queryfile = f"{WORKDIR}/vendor/test_vendor_nb_product.sparql"
        result = query(queryfile)

        result["groupProduct"] = result["groupProduct"].apply(lambda x: x.split("|"))
        result =  result \
            .groupby("localOffer")["groupProduct"].aggregate(lambda x: np.concatenate(x.to_numpy())) \
            .reset_index()

        relation_lhs = result["groupProduct"].apply(lambda x: np.unique(x).size)

        relation_lhs.to_csv(f"{Path(queryfile).parent}/test_vendor_rel_nb_product.csv")

        self.assertListEqual(
            relation_lhs.to_list(), 1,
            "Every Offer should have 1 product"
        )

        relation_rhs = relation_lhs.explode("groupProduct").groupby("groupProduct")["localProduct"].count()

        self.assertListGreaterEqual(
            relation_rhs.to_list(), 1,
            "Every Product should have 1..n Offer"
        )
    
    def test_vendor_normal_nb_product(self):
        """Test whether the products across vendor follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/vendor/test_vendor_nb_product.sparql"
        result = query(queryfile)
        result["groupProduct"] = result["groupProduct"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)
        
        normal_test_result = dist_test(result["groupProduct"], figname=f"{Path(queryfile).parent}/test_vendor_nb_product_across_vendor")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupProduct"]).to_csv(f"{Path(queryfile).parent}/test_vendor_nb_product_across_vendor_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "Products should follow Normal Distribution across vendors. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
    
    def test_vendor_rel_offer(self):
        """Test whether the relationship Vendor-Offer is One to Many, and Offer-Vendor is Many to One.
        """

        queryfile = f"{WORKDIR}/vendor/test_vendor_nb_offer.sparql"
        result = query(queryfile)
        result["groupOffer"] = result["groupOffer"] \
            .apply(lambda x: x.split("|"))
        
        result = result.groupby("localOffer")["groupOffer"]\
            .aggregate(lambda x: np.concatenate(x.to_numpy())) \
            .reset_index()
        print(result) 

        relation_lhs = result["groupOffer"].apply(lambda x: np.unique(x).size)
        relation_lhs.to_csv(f"{Path(queryfile).parent}/test_vendor_rel_nb_offer_lhs.csv")

        self.assertListEqual(
            relation_lhs.to_list(), 1,
            "Every Offer should have 1 product"
        )

        relation_rhs = result.explode("groupOffer").groupby("groupOffer")["localOffer"].count()
        relation_rhs.to_csv(f"{Path(queryfile).parent}/test_vendor_rel_nb_offer_rhs.csv")

        self.assertListGreaterEqual(
            relation_rhs.to_list(), 1,
            "Every Product should have 1..n Offer"
        )
    
    def test_vendor_nb_offer_across_vendor(self):
        """Test whether the products across vendor follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/vendor/test_vendor_nb_offer.sparql"
        result = query(queryfile)
        result["groupOffer"] = result["groupOffer"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        normal_test_result = dist_test(result["groupOffer"], figname=f"{Path(queryfile).parent}/test_vendor_nb_offer_across_vendor")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupOffer"]).to_csv(f"{Path(queryfile).parent}/test_vendor_nb_offer_across_vendor_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "Offers should follow Normal Distribution across vendors. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

    def test_vendor_nb_vendor(self):
        """Test whether the number of producers follows normal distribution .
        """

        data = np.arange(CONFIG["schema"]["vendor"]["params"]["vendor_n"])
        _, edges = np.histogram(data, CONFIG["n_batch"])
        edges = edges[1:].astype(int)

        result = query(f"{WORKDIR}/vendor/test_vendor_nb_vendor.sparql")
        result["batchId"] = result["batchId"].apply(lambda x: np.argwhere((x <= edges)).min().item())
        
        nbVendor = result.groupby("batchId")["nbVendor"].sum().cumsum()

        expected = edges + 1

        for i, test in nbVendor.items():
            self.assertEqual(test, expected[i])

class TestGenerationRatingSite(TestGenerationTemplate):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.system(f"rm {WORKDIR}/ratingsite/*.png")
        os.system(f"rm {WORKDIR}/ratingsite/*.csv")    

    def test_ratingsite_nb_product_per_ratingsite(self):
        """Test whether the products per ratingsite follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_product.sparql"
        result = query(queryfile)

        result["groupProduct"] = result["groupProduct"] \
            .apply(lambda x: x.split("|"))

        normal_test_result = result.apply(
            lambda row: dist_test(row["groupProduct"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_product_per_ratingsite_{row['ratingsiteId']}"),
            axis = 1
        )

        normal_test_result \
            .to_frame("pvalue").set_index(result["ratingsiteId"]) \
            .to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_product_per_ratingsite_normaltest.csv")
        
        self.assertTrue(
            (normal_test_result >= STATS_SIGNIFICANCE_LEVEL).all(),
            "Products should follow Normal Distribution for each ratingsite. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
    
    def test_ratingsite_nb_product_across_ratingsite(self):
        """Test whether the products across ratingsite follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_product.sparql"
        result = query(queryfile)
        result["groupProduct"] = result["groupProduct"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)
        
        normal_test_result = dist_test(result["groupProduct"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_product_across_ratingsite")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupProduct"]).to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_product_across_ratingsite_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "Products should follow Normal Distribution across vendors. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
    
    def test_ratingsite_nb_review_per_ratingsite(self):
        """Test whether the reviews per ratingsite follows normal distribution.

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_review.sparql"
        result = query(queryfile)
        result["groupReview"] = result["groupReview"] \
            .apply(lambda x: x.split("|"))
        
        normal_test_result = result.apply(
            lambda row: dist_test(row["groupReview"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_review_per_ratingsite_{row['ratingsiteId']}"),
            axis = 1
        )

        normal_test_result \
            .to_frame("pvalue").set_index(result["ratingsiteId"]) \
            .to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_review_per_ratingsite_normaltest.csv")
        
        self.assertTrue(
            (normal_test_result >= STATS_SIGNIFICANCE_LEVEL).all(),
            "Review should follow Normal Distribution for each ratingsite. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
    
    def test_ratingsite_nb_review_across_ratingsite(self):
        """Test whether the products across ratingsite follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_review.sparql"
        result = query(queryfile)
        result["groupReview"] = result["groupReview"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        normal_test_result = dist_test(result["groupReview"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_review_across_ratingsite")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupReview"]).to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_review_across_ratingsite_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "Review should follow Normal Distribution across vendors. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

    def test_ratingsite_nb_person_per_ratingsite(self):
        """Test whether the reviews per ratingsite follows normal distribution.

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_person.sparql"
        result = query(queryfile)
        result["groupReviewer"] = result["groupReviewer"] \
            .apply(lambda x: x.split("|"))
        
        normal_test_result = result.apply(
            lambda row: dist_test(row["groupReviewer"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_person_per_ratingsite_{row['ratingsiteId']}"),
            axis = 1
        )

        normal_test_result \
            .to_frame("pvalue").set_index(result["ratingsiteId"]) \
            .to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_person_per_ratingsite_normaltest.csv")
        
        self.assertTrue(
            (normal_test_result >= STATS_SIGNIFICANCE_LEVEL).all(),
            "Person should follow Normal Distribution for each ratingsite. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
    
    def test_ratingsite_nb_person_across_ratingsite(self):
        """Test whether the products across ratingsite follows normal distribution

        D’Agostino and Pearson’s method:
        H0: The test sample is drawn from normal distribution
        H0: The test sample is not drawn from normal distribution
        pvalue < alpha = reject H0

        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_nb_person.sparql"
        result = query(queryfile)
        result["groupReviewer"] = result["groupReviewer"] \
            .apply(lambda x: x.split("|")) \
            .apply(lambda x: np.unique(x).size)

        normal_test_result = dist_test(result["groupReviewer"], figname=f"{Path(queryfile).parent}/test_ratingsite_nb_person_across_ratingsite")
        pd.DataFrame([normal_test_result], columns=["pvalue"], index=["groupReviewer"]).to_csv(f"{Path(queryfile).parent}/test_ratingsite_nb_person_across_ratingsite_normaltest.csv")
        
        self.assertGreaterEqual(
            normal_test_result, STATS_SIGNIFICANCE_LEVEL,
            "Person should follow Normal Distribution across vendors. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )
        
    def test_ratingsite_nb_ratingsite(self):
        """Test whether the number of producers follows normal distribution .
        """

        data = np.arange(CONFIG["schema"]["ratingsite"]["params"]["ratingsite_n"])
        _, edges = np.histogram(data, CONFIG["n_batch"])
        edges = edges[1:].astype(int)

        result = query(f"{WORKDIR}/ratingsite/test_ratingsite_nb_ratingsite.sparql")
        result["batchId"] = result["batchId"].apply(lambda x: np.argwhere((x <= edges)).min().item())
        
        nbRatingSite = result.groupby("batchId")["nbRatingSite"].sum().cumsum()

        expected = edges + 1

        for i, test in nbRatingSite.items():
            self.assertEqual(test, expected[i])
    
    def test_ratingsite_ratings_range(self):
        """Test whether productPropertyNumeric matches expected frequencies .
        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_ratings.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        minVals = result.groupby("prop")["propVal"].min()
        maxVals = result.groupby("prop")["propVal"].max()

        expected_data = pd.DataFrame.from_dict({
            "rating1": {"min": 1, "max": 10},
            "rating2": {"min": 1, "max": 10},
            "rating3": {"min": 1, "max": 10},
            "rating4": {"min": 1, "max": 10}
        }).T
        
        self.assertTrue(
            np.greater_equal(minVals, expected_data["min"]).all(),
            "The min value for productPropertyNumeric must be greater or equal to WatDiv config's ."
        )
            
        self.assertTrue(
            np.less_equal(maxVals, expected_data["max"]).all(),
            "The max value for productPropertyNumeric must be less or equal to WatDiv config's ."
        )

    def test_ratingsite_ratings_frequency(self):
        """Test whether productPropertyNumeric approximately matches expected frequencies .
        """

        tolerance = 0.07

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_ratings.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        nbProducts = result["localReview"].nunique()
        frequencies = (result.groupby("prop")["propVal"].count() / nbProducts).round(2)

        expected_data = {
            "rating1": CONFIG["schema"]["ratingsite"]["params"]["rating1_p"],
            "rating2": CONFIG["schema"]["ratingsite"]["params"]["rating2_p"],
            "rating3": CONFIG["schema"]["ratingsite"]["params"]["rating3_p"],
            "rating4": CONFIG["schema"]["ratingsite"]["params"]["rating4_p"],
        }
        
        self.assertListAlmostEqual(
            frequencies.to_list(), list(expected_data.values()),
            delta=tolerance,
            msg="The frequency for bsbm:rating1..n should match config's."
        )
                
    def test_ratingsite_ratings_normal(self):
        """Test whether productPropertyNumeric follows Normal distribution .
        """

        queryfile = f"{WORKDIR}/ratingsite/test_ratingsite_ratings.sparql"
        result = query(queryfile)
        result.replace("http://www4.wiwiss.fu-berlin.de/bizer/bsbm/v01/vocabulary/", "", regex=True, inplace=True)

        normal_test_data = result.groupby("prop")["propVal"].aggregate(list).to_frame("propVal").reset_index()
        normal_test_result = normal_test_data.apply(
            lambda row: dist_test(row["propVal"], figname=f"{Path(queryfile).parent}/{Path(queryfile).stem}_{row['prop']}"), 
            axis=1
        )
        
        normal_test_result \
            .to_frame("pvalue").set_index(normal_test_data["prop"]) \
            .to_csv(f"{Path(queryfile).parent}/test_ratingsite_ratings_normaltest.csv")

        self.assertTrue(
            (normal_test_result >= STATS_SIGNIFICANCE_LEVEL).all(),
            "Ratings should follow Normal Distribution for each batch. Either (1) increase sample size, (2) decrease confidence level or (3) rely on visual check."
        )

if __name__ == "__main__":
    unittest.main()
    