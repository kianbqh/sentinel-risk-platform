# Deployment recipes

`k8s/` retains the scoring deployment, service, probes, rolling update and HPA examples. `terraform/` contains the AWS network, ECS/Fargate, ECR and load-balancer definitions imported with the original deployment history.

The integrated runtime is the root Compose stack. Terraform checks use:

```bash
terraform -chdir=deploy/terraform fmt -check -recursive
terraform -chdir=deploy/terraform init -backend=false -input=false
terraform -chdir=deploy/terraform validate
```

No AWS resources are applied by the platform runner or workflow. The CI job validates the definitions without a cloud account. Local integration evidence lives in `../results/`; historical deployment measurements remain in the separate numbered source working copy.
