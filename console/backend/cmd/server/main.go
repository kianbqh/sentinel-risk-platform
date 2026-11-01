package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/kianbqh/fullstack-go-graphql/backend/graph"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/auth"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/server"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/store"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	dbURL := env("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/jobs?sslmode=disable")
	secret := env("JWT_SECRET", "dev-secret-change-me")
	cost, _ := strconv.Atoi(env("BCRYPT_COST", "12"))
	maxConns, _ := strconv.Atoi(env("DB_MAX_CONNS", "20"))

	if err := store.Migrate(dbURL); err != nil {
		log.Fatalf("migrate: %v", err)
	}
	ctx := context.Background()
	st, err := store.New(ctx, dbURL, int32(maxConns))
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	h := server.New(&graph.Resolver{Store: st, Auth: auth.Manager{Secret: []byte(secret), TTL: 24 * time.Hour, Cost: cost}},
		env("CORS_ORIGIN", "http://localhost:5173"))
	srv := &http.Server{Addr: ":" + env("PORT", "8080"), Handler: h, ReadHeaderTimeout: 5 * time.Second}

	go func() {
		log.Printf("listening on %s (GraphQL at /query, playground at /)", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(sctx)
	st.DB.Close()
}
