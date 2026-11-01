# fullstack-go-graphql

I wanted a simple way to keep application statuses and their history together, and a concrete place to explore GraphQL's N+1 query problem. This app pairs a Go API with PostgreSQL and a React interface.

Users register or log in, add job applications, move them through `SAVED → APPLIED → OA → INTERVIEW → OFFER/REJECTED`, open each application's status history, and search or filter the list. Changes and their history are written together in a database transaction.

| Layer | Stack |
|---|---|
| API | Go 1.26, **gqlgen** (schema-first GraphQL: queries, mutations, enums, input types, field resolvers), `net/http`, complexity limit, CORS, graceful shutdown |
| Auth | **bcrypt** password hashing, **JWT** (HS256, expiry required) bearer-token middleware, per-user row-level authorization (tenant isolation) |
| Data | **PostgreSQL 17** via **pgx v5** connection pool; versioned **golang-migrate** SQL migrations embedded in the binary (CITEXT, ENUM, CHECK constraints, composite index); transactional writes with an audit table |
| Performance | per-request **dataloader** batches the `Application.events` N+1 into one `= ANY($1)` query |
| Frontend | **React 19 + TypeScript + Vite**, typed fetch GraphQL client, Vitest + Testing Library; served by nginx, which proxies `/query` |
| Ops | multi-stage Dockerfiles (distroless non-root Go image), Docker Compose with persistent PostgreSQL data and database health checks, GitHub Actions CI (gqlgen drift check, gofmt, vet, `go test -race` against real PostgreSQL, frontend tests/build, authenticated Compose lifecycle check) |

## Run (exact commands)
```bash
docker compose up -d --build        # http://localhost:3000 (UI), http://localhost:8080 (GraphQL playground), /healthz
# For a Docker daemon without bridge DNS/NAT, build separately, then use the runtime override:
#   DOCKER_BUILDKIT=0 docker build --network host -t "${COMPOSE_PROJECT_NAME:-fullstack-go-graphql}-backend:local" backend
#   DOCKER_BUILDKIT=0 docker build --network host -t "${COMPOSE_PROJECT_NAME:-fullstack-go-graphql}-frontend:local" frontend
#   docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d --no-build

# backend tests (need Postgres on localhost:5432; each run creates and drops its own database)
(cd backend && go test ./... -count=1 -v)
# frontend tests + production build
(cd frontend && npm ci && npm test && npm run build)

# load benchmark
go install github.com/rakyll/hey@v0.1.4
bash loadtest/run.sh 30s 50
# A/B: dataloader off vs on, same binary and data, 3 alternating rounds
mkdir -p .tmp
(cd backend && go build -o ../.tmp/jobsrv ./cmd/server)
PORT=8081 URL=http://localhost:8081/query bash loadtest/ab_dataloader.sh "$PWD/.tmp/jobsrv" 3
```

The A/B server uses a separate port so it can run alongside the Compose API. Both benchmarks seed a fresh account with 50 applications and check the nested GraphQL response before and after each load run. Raw `hey` reports contain HTTP status counts and latency distributions. Override `URL`, `HEY`, or `BENCH_SCRATCH` to use an existing local toolchain.

For different local ports, set `UI_PORT`, `API_PORT`, and `POSTGRES_PORT` before starting Compose. For example, `UI_PORT=3006 API_PORT=8086 POSTGRES_PORT=5436 docker compose up -d --build` serves the UI on port 3006. The host-network override uses these same variables.

Example query:
```graphql
{ applications(status: OA, search: "stripe", limit: 20) { id company role status events { status at } }
  stats { total byStatus { status count } } }
```

## Local results

Measured locally on 2026-10-31 in WSL 2 on a **12th Gen Intel(R) Core(TM) i7-12700KF**, with **20 logical CPUs visible**. The load generator, Go API, and PostgreSQL ran on the same machine. Docker used host networking; the Compose measurements went through the nginx UI proxy.

All **16 tests passed**: 4 Go auth unit tests, 8 real PostgreSQL integration tests (with the race detector), and 4 Vitest tests. `gofmt`, `go vet`, gqlgen drift checking, and both production image builds passed. The unavailable-database negative check failed as required. Handwritten Go statement coverage was **72.4%** (207/286; generated gqlgen files excluded, startup entry point included).

The served Compose stack passed authenticated register/login, create, status update/history, search/filter, stats, and delete checks through `/query` on the UI proxy.

### Dataloader comparison

Each mode ran for 20 seconds with 50 clients, alternating off/on for three rounds, using the same binary and a fresh account with 50 applications. The query returns 20 applications with nested status events plus stats. Values below are medians of the three per-run statistics; response counts are summed.

| Mode | Requests/s | p50 | p95 | HTTP/transport failures |
| --- | ---: | ---: | ---: | ---: |
| Dataloader off | 3,553 | 13.9 ms | 16.9 ms | 0 / 208,101 |
| Dataloader on | **6,821** | 6.8 ms | 13.8 ms | 0 / 406,019 |

Batching changed median throughput by **+92.0%** in this workload. The request-scoped loader replaces individual event lookups with one `WHERE application_id = ANY($1)` query.

### Compose stack

Both runs used 50 clients and the actual nginx → Go → PostgreSQL path:

| Workload | Target duration | Requests/s | p50 | p95 | p99 | HTTP/transport failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Read query | 30 s | 5,812 | 7.9 ms | 16.3 ms | 20.7 ms | 0 / 174,430 |
| Transactional create + status event | 15 s | 184 | 216.6 ms | 565.4 ms | 1320.7 ms | 0 / 2,830 |

The benchmarks check GraphQL setup and nested responses before and after each run. [Raw results and the summary](results/benchmark_summary.json), [test and coverage details](results/checks.json), and [exact software/image identities](results/environment.json) are saved with the code.

A read-only PostgreSQL check found **2,831** new benchmark applications and the same number of status events: exactly **2,830** timed create responses plus one preflight create. This separately verifies the transactional writes.

## Layout
```
backend/  graph/schema.graphqls  graph/schema.resolvers.go  internal/{auth,store,loaders,server}  migrations/*.sql  cmd/server
frontend/ src/{api.ts,App.tsx,AuthForm.tsx,Board.tsx,App.test.tsx}  nginx.conf.template
loadtest/ run.sh  ab_dataloader.sh  list_query.json      results/  raw hey output
```
