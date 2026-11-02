package server

import (
	"context"
	"testing"

	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

// The suite's PostgreSQL is real; this verifies the span encloses an actual
// committed idempotent alert/case transaction, not a mocked database call.
func TestPostgresTransactionInheritsConsumerContext(t *testing.T) {
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	defer otel.SetTracerProvider(previous)
	defer provider.Shutdown(context.Background())
	ctx, parent := provider.Tracer("test").Start(context.Background(), "kafka.consume")
	added, err := st.Ingest(ctx, event("observability-real-db"))
	if err != nil || !added {
		t.Fatalf("ingest=%v %v", added, err)
	}
	parent.End()
	spans := exporter.GetSpans()
	if len(spans) != 2 {
		t.Fatalf("spans=%d", len(spans))
	}
	if spans[0].Name != "postgres.transaction" || spans[0].Parent.SpanID() != spans[1].SpanContext.SpanID() || spans[0].SpanContext.TraceID() != spans[1].SpanContext.TraceID() {
		t.Fatal("database span lost consumer parent")
	}
}
