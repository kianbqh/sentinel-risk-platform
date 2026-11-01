# Local validation

Run date: 2026-10-31. Environment: Windows / WSL2; load generator, API and PostgreSQL on the same machine; 12th Gen Intel(R) Core(TM) i7-12700KF; 20 logical CPUs; Docker 29.9.2 with host networking.

## Checks

- 12 Go tests passed with `-race -count=1` against PostgreSQL 17.11.
- `gofmt`, `go vet ./...`, and gqlgen generation/hash drift checks passed.
- Connecting the integration test suite to unavailable port 1 exited nonzero and reported PostgreSQL required.
- 4 Vitest tests passed, including status history and failed update/delete error handling. TypeScript/Vite production build passed.
- Both multi-stage production Docker images built successfully, including the distroless non-root Go runtime.
- Authenticated lifecycle and history/search/filter/stats/delete checks passed through the served nginx UI proxy.
- Code coverage used `-coverpkg=./...`. Duplicate set-mode blocks were merged before counting statements. All handwritten Go: 207/286 (72.38%); service packages without the command entry point: 207/260 (79.62%). Generated gqlgen files are excluded from both scopes.

## Workloads

Six native-server A/B runs, ordered off/on three times, used the same Go binary and fixed 50-application dataset. Each timed run lasted 20 seconds with 50 clients. Each run saved its unmodified hey report. Table quantiles are medians of run-level quantiles.

Compose read and create runs were configured for 30 and 15 seconds, respectively, with 50 clients. They targeted the nginx proxy on port 3006. Checked GraphQL pre/postflight requests surrounded timed traffic. Reported failure counts cover HTTP non-200 and transport failures; the raw reports are linked from [benchmark_summary.json](benchmark_summary.json). A subsequent read-only PostgreSQL query verified 2,831 new Bench applications and matching audit events, exactly the 2,830 timed HTTP200 create responses plus one preflight create.

The measurements used a G-backed WSL 2 disk.

See [checks.json](checks.json), [environment.json](environment.json), and [benchmark_summary.json](benchmark_summary.json) for the exact records.
