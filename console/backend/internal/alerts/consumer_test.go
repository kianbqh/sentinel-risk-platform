package alerts

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/segmentio/kafka-go"
	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

type testReader struct {
	committed []kafka.Message
	mu        sync.Mutex
}

func (r *testReader) FetchMessage(context.Context) (kafka.Message, error) {
	return kafka.Message{}, errors.New("done")
}
func (r *testReader) CommitMessages(_ context.Context, messages ...kafka.Message) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.committed = append(r.committed, messages...)
	return nil
}

type testSink struct {
	calls  int
	fail   bool
	parent trace.SpanContext
}

func (s *testSink) Ingest(ctx context.Context, _ Envelope) (bool, error) {
	s.calls++
	s.parent = trace.SpanContextFromContext(ctx)
	if s.fail {
		return false, errors.New("database unavailable")
	}
	return false, nil
}

func TestFailedPersistenceNeverCommits(t *testing.T) {
	reader, sink := &testReader{}, &testSink{fail: true}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	err := persistMessage(ctx, reader, sink, kafka.Message{Topic: "alerts", Offset: 9}, Envelope{})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err=%v", err)
	}
	if len(reader.committed) != 0 || sink.calls != 1 {
		t.Fatal("failed transaction advanced offset")
	}
	sink.fail = false
	if err := persistMessage(context.Background(), reader, sink, kafka.Message{Topic: "alerts", Offset: 9}, Envelope{}); err != nil {
		t.Fatal(err)
	}
	if len(reader.committed) != 1 || reader.committed[0].Offset != 9 {
		t.Fatal("restored transaction not committed")
	}
}

func TestConsumerExtractsW3CParent(t *testing.T) {
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	defer otel.SetTracerProvider(previous)
	defer provider.Shutdown(context.Background())
	sink := &testSink{}
	message := kafka.Message{Topic: "alerts", Headers: []kafka.Header{{Key: "traceparent", Value: []byte("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")}}}
	if err := persistMessage(context.Background(), &testReader{}, sink, message, Envelope{TraceID: "unchanged-v1"}); err != nil {
		t.Fatal(err)
	}
	spans := exporter.GetSpans()
	if len(spans) != 1 || spans[0].Name != "kafka.consume" {
		t.Fatalf("spans=%v", spans)
	}
	if spans[0].Parent.SpanID().String() != "0123456789abcdef" || spans[0].SpanContext.TraceID().String() != "0123456789abcdef0123456789abcdef" {
		t.Fatal("lost W3C parent")
	}
	if sink.parent.SpanID() != spans[0].SpanContext.SpanID() {
		t.Fatal("DB sink did not inherit consumer span")
	}
}
