// Package server wires the GraphQL handler, auth middleware, CORS and health endpoints.
package server

import (
	"net/http"
	"os"

	"github.com/99designs/gqlgen/graphql/handler"
	"github.com/99designs/gqlgen/graphql/handler/extension"
	"github.com/99designs/gqlgen/graphql/handler/transport"
	"github.com/99designs/gqlgen/graphql/playground"

	"github.com/kianbqh/fullstack-go-graphql/backend/graph"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/loaders"
)

func New(res *graph.Resolver, corsOrigin string) http.Handler {
	gql := handler.New(graph.NewExecutableSchema(graph.Config{Resolvers: res}))
	gql.AddTransport(transport.POST{})
	gql.AddTransport(transport.GET{})
	gql.Use(extension.Introspection{})
	gql.Use(extension.FixedComplexityLimit(200))

	mux := http.NewServeMux()
	var query http.Handler = loaders.Middleware(res.Store, gql)
	if os.Getenv("DISABLE_DATALOADER") == "1" { // for A/B benchmarking the N+1 fix
		query = gql
	}
	mux.Handle("/query", res.Auth.Middleware(query))
	mux.Handle("/", playground.Handler("GraphQL", "/query"))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := res.Store.DB.Ping(r.Context()); err != nil {
			http.Error(w, "db down", http.StatusServiceUnavailable)
			return
		}
		w.Write([]byte("ok"))
	})
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
