// Package health keeps dependency readiness separate from process liveness.
package health

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/telemetry"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Check func(context.Context) error
type Handler struct{ Checks map[string]Check }

func (h Handler) probe(ctx context.Context) (map[string]bool, bool) {
	type outcome struct {
		name  string
		ready bool
	}
	results := make(chan outcome, len(h.Checks))
	for name, check := range h.Checks {
		go func(name string, check Check) {
			probeCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
			defer cancel()
			results <- outcome{name: name, ready: check(probeCtx) == nil}
		}(name, check)
	}
	checks, ready := map[string]bool{}, true
	// Probes honor their context. Only this goroutine mutates the response map.
	for range h.Checks {
		result := <-results
		checks[result.name] = result.ready
		value := 0.0
		if result.ready {
			value = 1
		} else {
			ready = false
		}
		telemetry.DependencyReady.WithLabelValues(result.name).Set(value)
	}
	value := 0.0
	if ready {
		value = 1
	}
	telemetry.ServiceReady.Set(value)
	return checks, ready
}

func (h Handler) Live(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(`{"status":"alive"}`))
}
func (h Handler) Ready(w http.ResponseWriter, r *http.Request) {
	checks, ready := h.probe(r.Context())
	w.Header().Set("Content-Type", "application/json")
	status := "ready"
	if !ready {
		status = "not ready"
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"status": status, "checks": checks})
}
func (h Handler) Metrics(w http.ResponseWriter, r *http.Request) {
	h.probe(r.Context())
	promhttp.Handler().ServeHTTP(w, r)
}
