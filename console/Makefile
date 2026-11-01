.PHONY: up up-hostnet down test-backend test-frontend bench ab-dataloader
up:            ; docker compose up -d --build
up-hostnet:    ; docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d --build
down:          ; docker compose down -v
test-backend:  ; cd backend && go test ./... -count=1
test-frontend: ; cd frontend && npm ci && npm test
bench:         ; loadtest/run.sh 30s 50
ab-dataloader: ; cd backend && go build -o /tmp/jobsrv ./cmd/server && cd .. && loadtest/ab_dataloader.sh /tmp/jobsrv 3
