package alerts

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/telemetry"
	"github.com/segmentio/kafka-go"
)

func kafkaClient(bootstrap string) *kafka.Client {
	return &kafka.Client{Addr: kafka.TCP(strings.Split(bootstrap, ",")...), Timeout: 2 * time.Second}
}

// CheckKafka asks the broker for topic metadata; a cached socket is not readiness.
func CheckKafka(ctx context.Context, bootstrap, topic string) error {
	response, err := kafkaClient(bootstrap).Metadata(ctx, &kafka.MetadataRequest{Topics: []string{topic}})
	if err != nil {
		return err
	}
	if len(response.Topics) != 1 || len(response.Topics[0].Partitions) == 0 {
		return fmt.Errorf("Kafka alert topic unavailable")
	}
	for _, t := range response.Topics {
		if t.Error != nil {
			return t.Error
		}
		for _, p := range t.Partitions {
			if p.Error != nil {
				return p.Error
			}
			if p.Leader.ID < 0 || p.Leader.Host == "" || p.Leader.Port <= 0 {
				return fmt.Errorf("Kafka partition leader unavailable")
			}
		}
	}
	return nil
}

// Group readers deliberately return -1 for Reader.Lag(). Query broker offsets
// instead, including while a failed DB transaction is retaining an offset.
func pollOffsets(ctx context.Context, client *kafka.Client, topic, group string) error {
	metadata, err := client.Metadata(ctx, &kafka.MetadataRequest{Topics: []string{topic}})
	if err != nil {
		return err
	}
	partitions := []int{}
	requests := []kafka.OffsetRequest{}
	for _, t := range metadata.Topics {
		if t.Error != nil {
			return t.Error
		}
		for _, p := range t.Partitions {
			if p.Error != nil {
				return p.Error
			}
			partitions = append(partitions, p.ID)
			requests = append(requests, kafka.FirstOffsetOf(p.ID), kafka.LastOffsetOf(p.ID))
		}
	}
	if len(partitions) == 0 {
		return fmt.Errorf("no Kafka partitions")
	}
	committed, err := client.OffsetFetch(ctx, &kafka.OffsetFetchRequest{GroupID: group, Topics: map[string][]int{topic: partitions}})
	if err != nil {
		return err
	}
	if committed.Error != nil {
		return committed.Error
	}
	ends, err := client.ListOffsets(ctx, &kafka.ListOffsetsRequest{Topics: map[string][]kafka.OffsetRequest{topic: requests}})
	if err != nil {
		return err
	}
	if len(ends.Topics[topic]) != len(partitions) {
		return fmt.Errorf("incomplete Kafka end offsets")
	}
	byPartition := map[int]int64{}
	for _, p := range committed.Topics[topic] {
		if p.Error != nil {
			return p.Error
		}
		byPartition[p.Partition] = p.CommittedOffset
	}
	for _, p := range ends.Topics[topic] {
		if p.Error != nil {
			return p.Error
		}
		offset, ok := byPartition[p.Partition]
		if !ok {
			return fmt.Errorf("missing committed partition")
		}
		if offset < 0 {
			offset = p.FirstOffset
		}
		lag := p.LastOffset - offset
		if lag < 0 {
			lag = 0
		}
		partition := strconv.Itoa(p.Partition)
		telemetry.CommittedOffset.WithLabelValues(topic, partition).Set(float64(offset))
		telemetry.EndOffset.WithLabelValues(topic, partition).Set(float64(p.LastOffset))
		telemetry.ConsumerLag.WithLabelValues(topic, partition).Set(float64(lag))
	}
	telemetry.OffsetsObserved.SetToCurrentTime()
	return nil
}

func monitorOffsets(ctx context.Context, bootstrap, topic, group string) {
	client := kafkaClient(bootstrap)
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		probeCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
		err := pollOffsets(probeCtx, client, topic, group)
		cancel()
		if err != nil {
			telemetry.OffsetsUp.Set(0)
		} else {
			telemetry.OffsetsUp.Set(1)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
