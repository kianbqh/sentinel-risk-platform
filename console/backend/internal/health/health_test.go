package health

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDependencyTransitionsDoNotAffectLiveness(t *testing.T) {
	available := map[string]bool{"database": true, "kafka": true}
	h := Handler{Checks: map[string]Check{}}
	for name := range available {
		dependency := name
		h.Checks[name] = func(context.Context) error {
			if !available[dependency] {
				return errors.New("offline")
			}
			return nil
		}
	}
	request := httptest.NewRequest("GET", "/readyz", nil)
	for _, state := range []bool{true, false, true} {
		for name := range available {
			available[name] = state
		}
		ready := httptest.NewRecorder()
		h.Ready(ready, request)
		want := 200
		if !state {
			want = 503
		}
		if ready.Code != want {
			t.Fatalf("ready=%d, want=%d", ready.Code, want)
		}
		live := httptest.NewRecorder()
		h.Live(live, request)
		if live.Code != 200 {
			t.Fatal("liveness depends on downstream")
		}
		metrics := httptest.NewRecorder()
		h.Metrics(metrics, request)
		value := "1"
		if !state {
			value = "0"
		}
		if !strings.Contains(metrics.Body.String(), "sentinel_service_ready "+value) {
			t.Fatal("readiness metric missing")
		}
	}
}

func TestSlowDependencyDoesNotEraseHealthyResult(t *testing.T) {
	h := Handler{Checks: map[string]Check{
		"database": func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() },
		"kafka":    func(ctx context.Context) error { return ctx.Err() },
	}}
	started := time.Now()
	checks, ready := h.probe(context.Background())
	if ready || checks["database"] || !checks["kafka"] {
		t.Fatalf("checks=%v ready=%v", checks, ready)
	}
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Fatalf("probe exceeded independent deadlines: %v", elapsed)
	}
}

func TestProbePropagatesRequestCancellation(t *testing.T) {
	h := Handler{Checks: map[string]Check{
		"database": func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() },
		"kafka":    func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() },
	}}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	started := time.Now()
	checks, ready := h.probe(ctx)
	if ready || checks["database"] || checks["kafka"] {
		t.Fatalf("cancelled checks=%v ready=%v", checks, ready)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("request cancellation ignored: %v", elapsed)
	}
}
