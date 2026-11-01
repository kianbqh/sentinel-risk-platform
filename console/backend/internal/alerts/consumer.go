package alerts

import (
	"context"
	"fmt"
	"github.com/segmentio/kafka-go"
	"log"
	"strings"
	"time"
)

type Sink interface {
	Ingest(context.Context, Envelope) (bool, error)
}

// Consume never advances an offset before the database transaction commits.
// A malformed envelope stops the consumer and surfaces a service failure.
func Consume(ctx context.Context, bootstrap, topic, group string, sink Sink) error {
	reader := kafka.NewReader(kafka.ReaderConfig{Brokers: strings.Split(bootstrap, ","), Topic: topic, GroupID: group, MinBytes: 1, MaxBytes: 10 << 20, StartOffset: kafka.FirstOffset, CommitInterval: 0})
	defer reader.Close()
	for {
		msg, err := reader.FetchMessage(ctx)
		if err != nil {
			return err
		}
		event, err := Decode(msg.Value)
		if err != nil {
			return fmt.Errorf("invalid alert partition=%d offset=%d: %w", msg.Partition, msg.Offset, err)
		}
		if string(msg.Key) != event.AlertID {
			return fmt.Errorf("alert key does not match id at offset %d", msg.Offset)
		}
		for {
			added, ingestErr := sink.Ingest(ctx, event)
			if ingestErr == nil {
				if added {
					log.Printf("ingested alert=%s source=%s", event.AlertID, event.Source)
				}
				break
			}
			log.Printf("alert persistence failed; offset retained: %v", ingestErr)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
		}
		if err = reader.CommitMessages(ctx, msg); err != nil {
			return err
		}
	}
}
