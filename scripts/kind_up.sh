#!/usr/bin/env bash
# Create a local kind cluster, load the images, install metrics-server (for HPA) and deploy the app.
set -euo pipefail
cd "$(dirname "$0")/.."
kind get clusters | grep -qx fraud || kind create cluster --name fraud --image kindest/node:v1.37.0 --config scripts/kind-config.yaml --wait 180s
load() { kind load docker-image "$1" --name fraud; }
docker image inspect fraud-scoring:1.0.0 >/dev/null 2>&1 || docker build -t fraud-scoring:1.0.0 .
docker image inspect registry.k8s.io/metrics-server/metrics-server:v0.9.1 >/dev/null 2>&1 || docker pull registry.k8s.io/metrics-server/metrics-server:v0.9.1
docker image inspect hey:local >/dev/null 2>&1 || docker build -t hey:local -f loadtest/Dockerfile.hey loadtest
load fraud-scoring:1.0.0; load registry.k8s.io/metrics-server/metrics-server:v0.9.1; load hey:local
kubectl apply -f scripts/metrics-server-kind.yaml >/dev/null
kubectl apply -k k8s
kubectl -n fraud rollout status deploy/fraud-api --timeout=180s
kubectl -n kube-system rollout status deploy/metrics-server --timeout=180s
