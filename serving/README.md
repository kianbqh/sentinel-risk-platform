# Fraud Scoring Cloud Deploy

I built this service to explore what happens after a fraud model leaves a training notebook: request validation, batch inference, container limits, rolling updates, and CPU-based autoscaling. It serves the logistic regression pipeline from [stripe-fraud-ml](https://github.com/kianbqh/stripe-fraud-ml) through FastAPI, with Docker Compose for a single service and Kubernetes manifests for replicas and HPA.

The included model was trained locally on the ULB credit-card fraud dataset. Its held-out PR-AUC is **0.72758933**, ROC-AUC is **0.98579161**, and validation-selected decision threshold is **0.9999722812**. Model metadata and hashes are in [`model/model_meta.json`](model/model_meta.json) and [`evidence/model-provenance.json`](evidence/model-provenance.json).

## Service

| Endpoint | Behavior |
|---|---|
| `POST /score` | Scores one transaction in the model's feature order |
| `POST /score/batch` | Scores 1-512 transactions in one prediction call |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Model availability and smoke inference |
| `GET /metrics` | Prometheus request counts, latency histogram, and flagged transactions |

Missing features, empty batches, and nonfinite values return 422; oversized batches return 413. Each container runs one worker so its Prometheus counters cover all requests handled by that container. Kubernetes scales replicas from two to six at a 60% CPU target.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest --capture=sys -q tests

docker compose up -d --build
python scripts/smoke_api.py http://localhost:8000
curl -s -X POST http://localhost:8000/score \
  -H 'content-type: application/json' -d @loadtest/sample_txn.json
docker compose down
```

The image runs as UID 10001. Compose limits the service to one CPU and 512 MB. `MODEL_DIR`, `MAX_BATCH`, `THRESHOLD_OVERRIDE`, `PORT`, and `LOG_LEVEL` configure the service. The API accepts the 34 engineered features already consumed by the trained model; feature construction lives in the training project.

For a private WSL Docker daemon without bridge networking, the optional `docker-compose.host.yml` override uses host networking and port 8004:

```bash
docker compose -f docker-compose.yml -f docker-compose.host.yml up -d --build
python scripts/smoke_api.py http://localhost:8004
```

## Kubernetes

Install Docker, kind, and kubectl, then run:

```bash
bash scripts/kind_up.sh
kubectl -n fraud get pods,svc,hpa,pdb
bash scripts/k8s_loadtest.sh 150s 50 results/k8s_hey_150s_c50.txt
bash scripts/k8s_rolling_loadtest.sh 90s 20 results/k8s_hey_rolling_restart.txt
kind delete cluster --name fraud
```

The local configuration pins Kubernetes 1.37.0 and uses nftables. It gives cold image unpacking a five-minute bootstrap window. The manifests include startup, readiness, and liveness probes, a read-only root filesystem, dropped capabilities, a disruption budget, and a five-second preStop drain. Rolling updates keep all existing replicas available while a new replica becomes ready. The hey Job sends requests through the ClusterIP Service from inside the cluster.

## Local validation

Measured on 2026-10-31 PDT in Ubuntu 24.04 on WSL2, with an i7-12700KF, 20 logical CPUs, and 15 GiB RAM. Compose used the host-network profile on port 8004, one worker, a one-CPU limit, and 512 MB RAM. The load generator and local Kubernetes node shared the machine. Exact environment and raw outputs are in [`results/`](results/).

| Workload | Requests/s | p50 | p95 | p99 | Successful / attempted | Network errors |
|---|---:|---:|---:|---:|---:|---:|
| Compose, single transaction, 50 clients, 30 s | 1,246.61 | 38.2 ms | 49.1 ms | 82.1 ms | 37,422 / 37,422 | 0 |
| Compose, batch of 64, 20 clients, 20 s | 471.86 | 40.6 ms | 52.3 ms | 89.4 ms | 9,448 / 9,448 | 0 |
| kind, keep-alive, 50 clients, 150 s | 2,157.73 | 21.6 ms | 36.4 ms | 51.4 ms | 323,732 / 323,732 | 0 |
| kind, rolling restart, 20 clients, 90 s | 1,424.98 | 13.5 ms | 22.8 ms | 33.2 ms | 128,266 / 128,267 | 1 |
| kind, new connection per request, 50 clients, 150 s | 13.28 | 4,013.3 ms | 4,045.1 ms | 4,063.5 ms | 2,000 / 2,000 | 0 |

Batch throughput was **30,199 transactions/s**. Compose reached 99.13% CPU during the single-transaction test, with 107.3 MiB memory usage.

The HPA actually scaled **2 → 4 → 6 Ready pods**. Persistent connections kept most traffic on the initial two pods, so the keep-alive throughput reflects that connection pattern. Opening a new connection for every request exposed the local DNS lookup cost instead: DNS lookup averaged 3.7422 s and API CPU stayed low. The 22-second rolling update finished while the load Job remained active; one request experienced a connection reset.

[`benchmark-summary.json`](results/benchmark-summary.json) includes both HTTP status counts and transport failures. Regenerate it with `python scripts/summarize_results.py`.

- API tests: **11 passed**, covering readiness, single/batch agreement, missing/nonfinite features, batch limits, and metrics.
- Docker Compose: healthy container; live single and 64-transaction batch requests passed. The sample batch flagged one transaction.
- Terraform 1.17.1 with AWS provider 6.71.0: `fmt -check`, `init -backend=false`, and `validate` passed.

## AWS infrastructure

`infra/terraform/` defines ECR with immutable image tags and scan-on-push, a VPC with two public subnets, an ALB, ECS Fargate tasks, CloudWatch logs, deployment rollback, and target-tracking autoscaling. The checked-in provider lock file records the validated provider version.

To deploy in an AWS account:

```bash
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
# Configure AWS CLI credentials and select a new immutable image tag.
TAG=experiment-001 REGION=us-west-2 bash infra/deploy_aws.sh
terraform -chdir=infra/terraform destroy
```

The deployment command creates billable resources. Local validation covers the configuration; AWS deployment and performance are a separate step.

CI runs the API suite, builds and exercises the container, deploys to kind and scores through the Service, then checks Terraform formatting and validation.
