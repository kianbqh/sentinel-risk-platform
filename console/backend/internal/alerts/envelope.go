// Package alerts owns the wire contract consumed from sentinel.alerts.v1.
package alerts

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"time"
)

type Model struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}
type Envelope struct {
	SchemaVersion string         `json:"schema_version"`
	AlertID       string         `json:"alert_id"`
	EventTime     time.Time      `json:"event_time"`
	Source        string         `json:"source"`
	EntityType    string         `json:"entity_type"`
	EntityID      string         `json:"entity_id"`
	RiskScore     *float64       `json:"risk_score"`
	Threshold     *float64       `json:"threshold"`
	Severity      string         `json:"severity"`
	Summary       string         `json:"summary"`
	Model         Model          `json:"model"`
	Evidence      map[string]any `json:"evidence"`
	TraceID       string         `json:"trace_id"`
}

func Decode(data []byte) (Envelope, error) {
	var e Envelope
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return e, err
	}
	for _, key := range []string{"schema_version", "alert_id", "event_time", "source", "entity_type", "entity_id", "risk_score", "threshold", "severity", "summary", "model", "evidence", "trace_id"} {
		if _, ok := fields[key]; !ok {
			return e, fmt.Errorf("missing field %s", key)
		}
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(&e); err != nil {
		return e, err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return e, errors.New("trailing JSON")
	}
	return e, e.Validate()
}
func (e Envelope) Validate() error {
	if e.SchemaVersion != "1.0" {
		return errors.New("unsupported alert schema")
	}
	for _, v := range []string{e.AlertID, e.EntityID, e.TraceID} {
		if len(v) == 0 || len(v) > 200 {
			return errors.New("invalid alert identifier")
		}
	}
	entities := map[string]string{"transaction-stream": "transaction", "transaction-api": "transaction", "ato": "ip", "text-risk": "message"}
	if entities[e.Source] == "" || entities[e.Source] != e.EntityType {
		return errors.New("source/entity mismatch")
	}
	if e.EventTime.IsZero() {
		return errors.New("missing event time")
	}
	for _, n := range []*float64{e.RiskScore, e.Threshold} {
		if n != nil && (math.IsNaN(*n) || math.IsInf(*n, 0) || *n < 0 || *n > 1) {
			return errors.New("invalid probability")
		}
	}
	if e.Source != "text-risk" && (e.RiskScore == nil || e.Threshold == nil) {
		return errors.New("numeric model requires score and threshold")
	}
	if e.Severity != "low" && e.Severity != "medium" && e.Severity != "high" {
		return errors.New("invalid severity")
	}
	if len(e.Summary) == 0 || len(e.Summary) > 500 || len(e.Model.Name) == 0 || len(e.Model.Name) > 100 || len(e.Model.Version) == 0 || len(e.Model.Version) > 100 || e.Evidence == nil {
		return errors.New("missing model/evidence/summary")
	}
	if e.Source == "ato" && e.Evidence["synthetic"] != true {
		return errors.New("ATO logs must be marked synthetic")
	}
	if e.Source == "text-risk" {
		source, _ := e.Evidence["score_source"].(string)
		if source != "tfidf_lr" && source != "none" {
			return errors.New("missing text score provenance")
		}
		if (e.RiskScore == nil) != (source == "none") {
			return fmt.Errorf("score provenance mismatch")
		}
	}
	return nil
}
