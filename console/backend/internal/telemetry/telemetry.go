// Package telemetry owns low-cardinality metrics and opt-in OTLP trace export.
package telemetry

import (
	"context"
	"os"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

var (
	Messages          = promauto.NewCounter(prometheus.CounterOpts{Name: "sentinel_consumer_messages_total", Help: "Kafka alert records fetched, including duplicate IDs"})
	Duplicates        = promauto.NewCounter(prometheus.CounterOpts{Name: "sentinel_consumer_duplicates_total", Help: "Persisted duplicate alert IDs suppressed"})
	PersistenceErrors = promauto.NewCounter(prometheus.CounterOpts{Name: "sentinel_consumer_persistence_errors_total", Help: "Failed persistence attempts; offsets retained"})
	DBTransactions    = promauto.NewHistogramVec(prometheus.HistogramOpts{Name: "sentinel_db_transaction_seconds", Help: "PostgreSQL transaction wall time including commit or rollback", Buckets: prometheus.DefBuckets}, []string{"operation", "outcome"})
	ConsumerLag       = promauto.NewGaugeVec(prometheus.GaugeOpts{Name: "sentinel_consumer_lag", Help: "Broker end offset minus consumer-group committed next offset"}, []string{"topic", "partition"})
	CommittedOffset   = promauto.NewGaugeVec(prometheus.GaugeOpts{Name: "sentinel_consumer_committed_offset", Help: "Broker consumer-group committed next offset"}, []string{"topic", "partition"})
	EndOffset         = promauto.NewGaugeVec(prometheus.GaugeOpts{Name: "sentinel_consumer_end_offset", Help: "Broker partition end next offset"}, []string{"topic", "partition"})
	OffsetsUp         = promauto.NewGauge(prometheus.GaugeOpts{Name: "sentinel_consumer_offsets_up", Help: "Last broker offset poll succeeded"})
	OffsetsObserved   = promauto.NewGauge(prometheus.GaugeOpts{Name: "sentinel_consumer_offsets_observed_timestamp_seconds", Help: "Unix timestamp of last successful broker offset poll"})
	ServiceReady      = promauto.NewGauge(prometheus.GaugeOpts{Name: "sentinel_service_ready", Help: "Last live dependency probe succeeded"})
	DependencyReady   = promauto.NewGaugeVec(prometheus.GaugeOpts{Name: "sentinel_dependency_ready", Help: "Last live dependency probe succeeded"}, []string{"dependency"})
)

// InitTracing leaves the default demo independent of an OTLP collector.
func InitTracing(ctx context.Context) (func(context.Context) error, error) {
	name := os.Getenv("OTEL_SERVICE_NAME")
	if name == "" {
		name = "sentinel-console"
	}
	options := []sdktrace.TracerProviderOption{sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", name)))}
	if os.Getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") != "" {
		exporter, err := otlptracehttp.New(ctx)
		if err != nil {
			return nil, err
		}
		options = append(options, sdktrace.WithBatcher(exporter))
	}
	provider := sdktrace.NewTracerProvider(options...)
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	return provider.Shutdown, nil
}
