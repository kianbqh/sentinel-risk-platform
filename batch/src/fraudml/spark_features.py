"""PySpark feature pipeline: data-quality checks, velocity features, time-based split."""
import json
import os
import sys
import time

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from .config import FEATURES_DIR, LABEL, RAW_PATH, REPORTS_DIR

WINDOW_SECONDS = 3600


def get_spark(app: str = "fraud-features") -> SparkSession:
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    return (
        SparkSession.builder.appName(app)
        .master(os.environ.get("SPARK_MASTER", "local[8]"))
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.driver.memory", "3g")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )


def quality_report(df: DataFrame) -> dict:
    total = df.count()
    nulls = df.select([F.sum(F.col(c).isNull().cast("int")).alias(c) for c in df.columns]).first().asDict()
    distinct = df.dropDuplicates().count()
    return {
        "rows": total,
        "null_cells": int(sum(v or 0 for v in nulls.values())),
        "duplicate_rows": total - distinct,
    }


def build_features(df: DataFrame) -> DataFrame:
    """Add amount / time-of-day / 1-hour velocity features.

    The dataset has no card or merchant id, so velocity is computed over the
    global transaction stream: a range window over the previous hour.
    """
    df = df.withColumn(LABEL, F.col("Class").cast("int")).drop("Class")
    df = df.withColumn("log_amount", F.log1p("Amount"))
    df = df.withColumn("hour_of_day", F.floor((F.col("Time") % 86400) / 3600).cast("int"))
    # Single global ordering (no partition key exists in this dataset). 285k rows fits on one node.
    w = Window.orderBy("Time").rangeBetween(-WINDOW_SECONDS, 0)
    df = (
        df.withColumn("txn_count_1h", F.count(F.lit(1)).over(w))
        .withColumn("_mean_1h", F.avg("Amount").over(w))
        .withColumn("_std_1h", F.stddev_pop("Amount").over(w))
    )
    df = df.withColumn("amount_ratio_1h", F.col("Amount") / (F.col("_mean_1h") + F.lit(1.0)))
    df = df.withColumn(
        "amount_z_1h",
        F.when(F.col("_std_1h") > 0, (F.col("Amount") - F.col("_mean_1h")) / F.col("_std_1h")).otherwise(F.lit(0.0)),
    )
    return df.drop("_mean_1h", "_std_1h")


def time_split(df: DataFrame, train_frac: float = 0.8):
    """Out-of-time split: earliest 80% of transactions train, latest 20% test."""
    cutoff = df.approxQuantile("Time", [train_frac], 0.0)[0]
    return df.filter(F.col("Time") <= cutoff), df.filter(F.col("Time") > cutoff), cutoff


def run(raw_path: str = str(RAW_PATH), out_dir: str = str(FEATURES_DIR)) -> dict:
    t0 = time.perf_counter()
    spark = get_spark()
    raw = spark.read.parquet(raw_path)
    dq = quality_report(raw)
    feats = build_features(raw.dropDuplicates())
    train, test, cutoff = time_split(feats)
    train.write.mode("overwrite").parquet(f"{out_dir}/train")
    test.write.mode("overwrite").parquet(f"{out_dir}/test")
    stats = {
        **dq,
        "rows_after_dedup": feats.count(),
        "train_rows": train.count(),
        "test_rows": test.count(),
        "train_fraud": train.filter(F.col(LABEL) == 1).count(),
        "test_fraud": test.filter(F.col(LABEL) == 1).count(),
        "time_cutoff_seconds": cutoff,
        "spark_version": spark.version,
        "runtime_seconds": round(time.perf_counter() - t0, 1),
    }
    spark.stop()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "spark_pipeline.json").write_text(json.dumps(stats, indent=2))
    print("[spark]", json.dumps(stats, indent=2))
    return stats


if __name__ == "__main__":
    run()
