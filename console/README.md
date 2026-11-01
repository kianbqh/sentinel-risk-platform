# Analyst console

A shared risk-review workspace built with Go, gqlgen, PostgreSQL and React. Kafka alerts become cases; analysts can list and filter signals, assign a case, record a FRAUD/BENIGN/UNCERTAIN label and resolve it. Every change adds an audit event. Labels are unique per analyst and case, so a correction updates that analyst's current label while preserving its history.

The data comes from the platform's credit-card replay, SMS classifier and explicitly synthetic ATO generator. There is no separate console training dataset.

The backend verifies HS256 JWT signatures/expiry and enforces authentication on queue and case operations. Tokens stay in frontend memory. Analysts share one workspace. `internal/loaders` batches event and label queries per GraphQL request; the real-PostgreSQL test verifies one query for each history type across eight cases.

Run the entire stack from the repository root with `bash scripts/up.sh` and populate it with `bash scripts/demo.sh`. The UI is `http://localhost:13040`, with GraphQL proxied at `/graphql`. The direct API uses port18041.

Validation outputs are in `../results/tests/`, `../results/integration/` and `../results/load/`. The load runner has separate read and write phases: full histories for fifty alerts, then two hundred persisted label writes across ten independent cases. It validates each HTTP/GraphQL body and reads back the actor IDs, final labels and all new audit events. The JSONL files retain every request latency and failure.
