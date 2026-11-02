package alerts

import (
	"context"
	"fmt"
	"github.com/segmentio/kafka-go"
	"log"
	"strconv"
	"strings"
	"time"

	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/telemetry"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

type Sink interface {
	Ingest(context.Context, Envelope) (bool, error)
}

type messageReader interface {
	FetchMessage(context.Context) (kafka.Message, error)
	CommitMessages(context.Context, ...kafka.Message) error
}

// Consume never advances an offset before the database transaction commits.
// A malformed envelope stops the consumer and surfaces a service failure.
func Consume(ctx context.Context, bootstrap, topic, group string, sink Sink) error {
	reader := kafka.NewReader(kafka.ReaderConfig{Brokers: strings.Split(bootstrap, ","), Topic: topic, GroupID: group, MinBytes: 1, MaxBytes: 10 << 20, StartOffset: kafka.FirstOffset, CommitInterval: 0})
	defer reader.Close()
	monitorCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go monitorOffsets(monitorCtx, bootstrap, topic, group)
	return consume(ctx, reader, sink)
}

func consume(ctx context.Context, reader messageReader, sink Sink) error {
	for {
		msg, err := reader.FetchMessage(ctx)
		if err != nil {
			return err
		}
		telemetry.Messages.Inc()
		event, err := Decode(msg.Value)
		if err != nil {
			return fmt.Errorf("invalid alert partition=%d offset=%d: %w", msg.Partition, msg.Offset, err)
		}
		if string(msg.Key) != event.AlertID {
			return fmt.Errorf("alert key does not match id at offset %d", msg.Offset)
		}
		if err := persistMessage(ctx, reader, sink, msg, event); err != nil {
			return err
		}
	}
}

func persistMessage(ctx context.Context, reader messageReader, sink Sink, msg kafka.Message, event Envelope) error {
	carrier := propagation.MapCarrier{}
	for _, header := range msg.Headers {
		carrier[strings.ToLower(header.Key)] = string(header.Value)
	}
	parent := propagation.TraceContext{}.Extract(ctx, carrier)
	ctx, span := otel.Tracer("sentinel.consumer").Start(parent, "kafka.consume", trace.WithSpanKind(trace.SpanKindConsumer),
		trace.WithAttributes(attribute.String("messaging.system", "kafka"), attribute.String("messaging.destination.name", msg.Topic), attribute.String("sentinel.correlation_id", event.TraceID)))
	defer span.End()
	for {
		added, ingestErr := sink.Ingest(ctx, event)
		if ingestErr == nil {
			if added {
				log.Printf("ingested alert=%s source=%s", event.AlertID, event.Source)
			}
			if !added {
				telemetry.Duplicates.Inc()
			}
			break
		}
		telemetry.PersistenceErrors.Inc()
		log.Printf("alert persistence failed; offset retained: %v", ingestErr)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Second):
		}
	}
	if err := reader.CommitMessages(ctx, msg); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "Kafka commit failed")
		return err
	}
	telemetry.CommittedOffset.WithLabelValues(msg.Topic, strconv.Itoa(msg.Partition)).Set(float64(msg.Offset + 1))
	return nil
}
