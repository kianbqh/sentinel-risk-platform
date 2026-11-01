# Credit-card fraud batch pipeline

I use PySpark, scikit-learn and MLflow to build transaction features, compare supervised and unsupervised models, and choose a validation threshold. The selected logistic-regression model is also used by the shared API and Scala stream.

The data is [OpenML1597](https://www.openml.org/d/1597):284,807 European credit-card transactions,492 labeled frauds. Deduplication leaves283,726 rows. V1…V28 are anonymized PCA features. Amount, log_amount, hour_of_day and inclusive trailing-hour count/ratio/z-score complete the thirty-four-feature vector. Because there are no account/card IDs, the window covers the global transaction stream.

The latest20% is test; the last20% of the earlier period is validation. Timestamp ties remain together. Training fits preprocessing/model parameters; validation PR-AUC chooses the supervised model and validation labels select a threshold targeting90% precision. The retained local test PR-AUC of the selected model is **0.7275893300**.

Original batch metrics and model-comparison artifacts are in `reports/`. These measured source results are preserved; integration adds fresh Python/Scala score parity and actual API/Kafka checks under `../results/integration/`. The shipped serving artifact and metadata are in `../serving/model/` with checksums in `../contracts/artifacts.json`.

From this module, `bash run_all.sh` downloads/builds/trains the original pipeline with Python3.12+ and Java17. From the root, `bash scripts/up.sh` starts the integrated services without retraining. The [unified workflow](../.github/workflows/platform.yml) owns active CI.
