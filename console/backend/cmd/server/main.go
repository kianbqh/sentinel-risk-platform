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

	"github.com/kianbqh/sentinel-risk-platform/console/backend/graph"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/alerts"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/auth"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/health"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/server"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/store"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/telemetry"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	dbURL := os.Getenv("DATABASE_URL")
	secret := os.Getenv("JWT_SECRET")
	if dbURL == "" || len(secret) < 32 {
		log.Fatal("DATABASE_URL and JWT_SECRET (at least 32 bytes) are required")
	}
	cost, _ := strconv.Atoi(env("BCRYPT_COST", "12"))
	maxConns, _ := strconv.Atoi(env("DB_MAX_CONNS", "20"))

	if err := store.Migrate(dbURL); err != nil {
		log.Fatalf("migrate: %v", err)
	}
	ctx, cancelConsumer := context.WithCancel(context.Background())
	defer cancelConsumer()
	shutdownTracing, err := telemetry.InitTracing(ctx)
	if err != nil {
		log.Fatalf("tracing: %v", err)
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = shutdownTracing(shutdownCtx)
	}()
	st, err := store.New(ctx, dbURL, int32(maxConns))
	if err != nil {
		log.Fatalf("db: %v", err)
	}
	checks := map[string]health.Check{}
	if bootstrap := os.Getenv("KAFKA_BOOTSTRAP_SERVERS"); bootstrap != "" {
		checks["kafka"] = func(ctx context.Context) error {
			return alerts.CheckKafka(ctx, bootstrap, env("ALERTS_TOPIC", "sentinel.alerts.v1"))
		}
	}
	h := server.New(&graph.Resolver{Store: st, Auth: auth.Manager{Secret: []byte(secret), TTL: 24 * time.Hour, Cost: cost}},
		env("CORS_ORIGIN", "http://localhost:13040"), checks)
	srv := &http.Server{Addr: ":" + env("PORT", "8080"), Handler: h, ReadHeaderTimeout: 5 * time.Second}
	if bootstrap := os.Getenv("KAFKA_BOOTSTRAP_SERVERS"); bootstrap != "" {
		go func() {
			if err := alerts.Consume(ctx, bootstrap, env("ALERTS_TOPIC", "sentinel.alerts.v1"), env("ALERTS_GROUP", "sentinel-console-v1"), st); err != nil && ctx.Err() == nil {
				log.Fatalf("alerts consumer: %v", err)
			}
		}()
	}

	go func() {
		log.Printf("listening on %s (GraphQL at /graphql)", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatal(err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	cancelConsumer()
	sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(sctx)
	st.DB.Close()
}
