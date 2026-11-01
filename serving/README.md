# Transaction serving artifacts

`model/` packages the selected StandardScaler/logistic-regression pipeline and its ordered feature metadata from the batch credit-card experiment. The source evaluation's test PR-AUC is0.7275893300, rounded to0.728 in the platform overview.

The active service is the shared root gateway. Its single/batch endpoints load these committed artifacts, preserve all thirty-four feature names and the validation-selected threshold, and publish canonical Kafka alerts when requested. `../contracts/artifacts.json` pins the shipped files by SHA256; `../scripts/verify_artifacts.py` checks them before Compose startup.

The source standalone FastAPI implementation remains here with its imported history. Kubernetes and Terraform files now live in `../deploy/`. Fresh platform inference and reconciliation evidence belongs to `../results/integration/`; old cloud-reference/load numbers are outside this checkout in the preserved numbered working copy.
