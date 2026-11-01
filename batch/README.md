# stripe-fraud-ml

[![Tests](https://github.com/kianbqh/stripe-fraud-ml/actions/workflows/tests.yml/badge.svg)](https://github.com/kianbqh/stripe-fraud-ml/actions/workflows/tests.yml)

I was curious how well a few familiar models could pick out fraud when almost every transaction is legitimate. This repo follows that question all the way through: turn the transaction stream into features, compare models, choose a threshold, and use the same model for batch and API scoring.

The stack is **PySpark, scikit-learn, MLflow, and FastAPI**. Everything runs locally.

## What's in here

- A Spark pipeline that checks data quality, removes duplicates, and builds one-hour transaction features.
- Logistic regression and gradient-boosted trees, compared by validation PR-AUC.
- Isolation Forest and K-Means, to see what turns up when the models train without fraud labels.
- MLflow runs with parameters and metrics, plus a packaged copy of the selected model.
- A batch scoring job and a FastAPI endpoint that share the model, feature order, and decision threshold.

## The data and the split

I use the [ULB/Worldline credit-card fraud dataset on OpenML](https://www.openml.org/d/1597): 284,807 transactions, including 492 frauds. The download runs automatically, with no account setup.

`V1` through `V28` are anonymized PCA features. I keep those and `Amount`, then add five features:

| Feature | What it captures |
|---|---|
| `log_amount` | `log(1 + Amount)` |
| `hour_of_day` | The hour within a repeating 24-hour cycle, measured from the first transaction |
| `txn_count_1h` | Transactions in the trailing hour, including the current timestamp |
| `amount_ratio_1h` | Amount divided by the trailing-hour mean plus 1 |
| `amount_z_1h` | Amount relative to the trailing-hour mean and standard deviation |

The dataset has no card or merchant IDs, so the window features describe the overall transaction stream. Transactions sharing a timestamp share the same window.

I split by time: the latest 20% is the test set, and the last 20% of the earlier period is validation. Transactions with the same timestamp stay together. Validation PR-AUC picks the supervised model; validation also sets the threshold targeting 90% precision. The test set then measures how that choice performs on later transactions.

## Run it

You'll need **Python 3.12+** and **Java 17**. On Windows, run the project inside WSL. A portable [Temurin JRE](https://adoptium.net/temurin/releases/?version=17) works too: set `JAVA_HOME` to its extracted directory and add `$JAVA_HOME/bin` to `PATH`.

From the project directory in a Linux or WSL shell:

```bash
# For a portable JRE:
# export JAVA_HOME=/path/to/jre
# export PATH="$JAVA_HOME/bin:$PATH"
python3 --version
java -version

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

bash run_all.sh
```

That downloads the data, builds the features, trains all four models, scores the test set, and runs the tests. Spark uses eight local threads by default; set `SPARK_MASTER` to change that, for example `SPARK_MASTER='local[4]' bash run_all.sh`.

After setting up the environment in WSL, you can also start a run from PowerShell:

```powershell
.\run_all.ps1 -Distribution Ubuntu
# For a portable Java installation, also pass its Linux path:
# .\run_all.ps1 -Distribution Ubuntu -JavaHome /path/to/jre
```

Use your installed distribution name in place of `Ubuntu`. The project drive should be mounted under `/mnt/<drive>` in WSL.

To run individual steps:

```bash
source .venv/bin/activate
export PYTHONPATH=src
export SPARK_LOCAL_IP=127.0.0.1

python -m fraudml.data
python -m fraudml.spark_features
python -m fraudml.train
python -m fraudml.batch_score
python -m pytest -q tests
```

## Results

Run on October 31, 2026, on my Windows machine through WSL2: Python 3.12.3, Spark 4.3.0, and eight local Spark threads.

After removing 1,081 duplicate rows, the pipeline kept **283,726 transactions**. The split was 181,586 for fitting, 45,396 for validation, and **56,744 for testing**, with 74 frauds in the test set.

| Model | Validation PR-AUC | Test PR-AUC | Test ROC-AUC | Test precision | Test recall |
|---|---:|---:|---:|---:|---:|
| **Logistic regression (selected)** | 0.776 | 0.728 | 0.986 | 84.7% | 67.6% |
| Gradient-boosted trees | 0.705 | 0.750 | 0.980 | 92.9% | 35.1% |

PR-AUC here is average precision. Precision and recall use each model's validation-tuned threshold.

Logistic regression won on validation. Its threshold flagged **59 test transactions**, catching **50 of the 74 frauds**, with 9 false positives.

The label-free models were interesting too. Isolation Forest reached **0.947 ROC-AUC** and 0.026 PR-AUC. K-Means put 13 test transactions in one cluster, all 13 labeled as fraud: **767x** the test set's base fraud rate and **17.6%** of its fraud cases. Nearest-centroid distance scored 0.954 ROC-AUC.

Feature processing took 213.5 seconds and model training took 127.6 seconds. The batch job scored all **56,744 test transactions in 0.72 seconds**, including Parquet I/O: about **78,275 rows/second**.

All **15 tests passed**. I also checked the running HTTP service against batch scores for the lowest- and highest-scoring test transactions; both scores and decisions matched.

The full numbers live in [reports/metrics.json](reports/metrics.json), with the Spark counts in [reports/spark_pipeline.json](reports/spark_pipeline.json), batch timing in [reports/batch_scoring.json](reports/batch_scoring.json), and HTTP responses in [reports/api_check.json](reports/api_check.json). [The run environment](reports/run_environment.json) records the package versions, dataset hash, and five completed MLflow runs. Runs use seed 42 and the time split described above.

## Try the scoring API

After training, start the server from an activated environment:

```bash
uvicorn fraudml.serve:app --app-dir src --host 127.0.0.1 --port 8000
```

Open [the interactive API docs](http://127.0.0.1:8000/docs), or check the loaded model:

```bash
curl http://127.0.0.1:8000/health
```

`POST /score` takes a `features` object containing the 34 prepared features listed in [models/model_meta.json](models/model_meta.json). It returns `fraud_score`, `flagged`, and `threshold`. The Spark pipeline supplies the window features before scoring.

To send a real row from the test set:

```bash
PYTHONPATH=src python scripts/score_example.py --row 0
```

I also check the lowest- and highest-scoring test transactions against the batch job, including requests with features in a different order:

```bash
PYTHONPATH=src python scripts/check_api.py
```

This writes the responses and comparison to `reports/api_check.json`.

For a batch of prepared transactions:

```bash
PYTHONPATH=src python -m fraudml.batch_score \
  --input data/features/test \
  --output reports/batch_scores.parquet
```

## Browse the experiments

```bash
mlflow ui --backend-store-uri sqlite:///mlflow.db
```

Open [MLflow at localhost:5000](http://127.0.0.1:5000) to compare the model runs. The selected model is logged in MLflow and saved as `models/model.joblib`, alongside its feature list, threshold, and test metrics.

## Around the repo

```text
src/fraudml/    Data download, features, training, metrics, and scoring
tests/         Window logic, time splits, thresholds, and API/batch agreement
models/        Selected model and metadata
reports/       Metrics, cluster profiles, and runtime summaries
run_all.sh     Full pipeline
run_all.ps1    PowerShell entry point through WSL
```

GitHub Actions runs the tests on each push and pull request. Downloaded data, the local Python environment, and MLflow's working files stay out of Git.
