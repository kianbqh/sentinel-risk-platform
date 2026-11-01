#!/usr/bin/env bash
# Deploy to YOUR AWS account (needs: aws CLI v2 configured, docker, terraform). Costs money while running
# Tear down with: terraform -chdir=infra/terraform destroy
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${TAG:-1.0.0}; REGION=${REGION:-us-west-2}
terraform -chdir=infra/terraform init
# 1) create the ECR repo first so we have somewhere to push
terraform -chdir=infra/terraform apply -auto-approve -target=aws_ecr_repository.api -var region=$REGION -var image_tag=$TAG
REPO=$(terraform -chdir=infra/terraform output -raw ecr_repository_url)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:$TAG" . && docker push "$REPO:$TAG"
# 2) everything else (VPC, ALB, ECS Fargate service, autoscaling)
terraform -chdir=infra/terraform apply -auto-approve -var region=$REGION -var image_tag=$TAG
URL=$(terraform -chdir=infra/terraform output -raw api_url)
echo "Waiting for $URL/health/ready ..."
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 "$URL/health/ready"; then exit 0; fi
  sleep 10
done
echo "Service did not become ready; inspect ECS service events and CloudWatch logs." >&2
exit 1
