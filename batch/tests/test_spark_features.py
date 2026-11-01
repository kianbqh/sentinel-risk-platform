import pytest

pyspark = pytest.importorskip("pyspark")

from fraudml.spark_features import build_features, get_spark, quality_report, time_split  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    s = get_spark("test")
    yield s
    s.stop()


def _df(spark):
    rows = [(0.0, 10.0, "0"), (1800.0, 30.0, "0"), (3000.0, 20.0, "1"), (7300.0, 5.0, "0"), (7300.0, 5.0, "0")]
    return spark.createDataFrame(rows, ["Time", "Amount", "Class"])


def test_quality_report_counts_duplicates(spark):
    rep = quality_report(_df(spark))
    assert rep == {"rows": 5, "null_cells": 0, "duplicate_rows": 1}


def test_velocity_window(spark):
    out = {r["Time"]: r for r in build_features(_df(spark).dropDuplicates()).collect()}
    assert out[0.0]["txn_count_1h"] == 1
    assert out[3000.0]["txn_count_1h"] == 3      # 0, 1800, 3000 within the last hour
    assert out[7300.0]["txn_count_1h"] == 1      # previous txns are > 1h old
    assert out[3000.0]["label"] == 1
    assert out[3000.0]["hour_of_day"] == 0


def test_time_split_is_out_of_time(spark):
    feats = build_features(_df(spark).dropDuplicates())
    train, test, cutoff = time_split(feats, 0.5)
    assert train.agg({"Time": "max"}).first()[0] <= cutoff < test.agg({"Time": "min"}).first()[0]
