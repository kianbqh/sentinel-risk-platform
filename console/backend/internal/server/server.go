// Package server wires the GraphQL handler, auth middleware, CORS and health endpoints.
package server

import (
	"context"
	"net/http"

	"github.com/99designs/gqlgen/graphql/handler"
	"github.com/99designs/gqlgen/graphql/handler/extension"
	"github.com/99designs/gqlgen/graphql/handler/transport"

	"github.com/kianbqh/sentinel-risk-platform/console/backend/graph"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/health"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/loaders"
)

func New(res *graph.Resolver, corsOrigin string, readiness ...map[string]health.Check) http.Handler {
	gql := handler.New(graph.NewExecutableSchema(graph.Config{Resolvers: res}))
	gql.AddTransport(transport.POST{})
	gql.AddTransport(transport.GET{})
	gql.Use(extension.Introspection{})
	gql.Use(extension.FixedComplexityLimit(200))

	mux := http.NewServeMux()
	var query http.Handler = loaders.Middleware(res.Store, gql)
	mux.Handle("/graphql", http.MaxBytesHandler(res.Auth.Middleware(query), 1<<20))
	checks := map[string]health.Check{"database": func(ctx context.Context) error { return res.Store.DB.Ping(ctx) }}
	for _, extra := range readiness {
		for name, check := range extra {
			checks[name] = check
		}
	}
	probes := health.Handler{Checks: checks}
	mux.HandleFunc("/healthz", probes.Live)
	mux.HandleFunc("/readyz", probes.Ready)
	mux.HandleFunc("/metrics", probes.Metrics)
	return cors(corsOrigin, mux)
}

func cors(origin string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
