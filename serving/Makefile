.PHONY: test docker compose-up compose-down kind-up loadtest-k8s tf-validate clean
test:            ; python -m pytest --capture=sys -q tests
docker:          ; docker build -t fraud-scoring:1.0.0 .
compose-up:      ; docker compose up -d --build
compose-down:    ; docker compose down
kind-up:         ; scripts/kind_up.sh
loadtest-k8s:    ; scripts/k8s_loadtest.sh 150s 50 results/k8s_hey_150s_c50.txt
tf-validate:     ; terraform -chdir=infra/terraform init -backend=false && terraform -chdir=infra/terraform validate
clean:           ; kind delete cluster --name fraud
