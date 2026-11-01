package alerts

import (
	"encoding/json"
	"os"
	"testing"
)

func fixture(t *testing.T) map[string]any {
	t.Helper()
	b, err := os.ReadFile("../../../../contracts/examples/alert-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var e map[string]any
	if err = json.Unmarshal(b, &e); err != nil {
		t.Fatal(err)
	}
	return e
}
func TestSharedProducerFixture(t *testing.T) {
	b, _ := json.Marshal(fixture(t))
	if _, err := Decode(b); err != nil {
		t.Fatal(err)
	}
}
func TestConsumerContractRejectsInvalidEvents(t *testing.T) {
	for name, change := range map[string]func(map[string]any){
		"version":   func(e map[string]any) { delete(e, "schema_version") },
		"entity":    func(e map[string]any) { e["entity_type"] = "transaction" },
		"synthetic": func(e map[string]any) { e["evidence"].(map[string]any)["synthetic"] = false },
		"score":     func(e map[string]any) { e["risk_score"] = nil },
		"unknown":   func(e map[string]any) { e["unexpected"] = 1 },
	} {
		t.Run(name, func(t *testing.T) {
			e := fixture(t)
			change(e)
			b, _ := json.Marshal(e)
			if _, err := Decode(b); err == nil {
				t.Fatal("invalid event accepted")
			}
		})
	}
}
func TestTextLabelWithoutProbability(t *testing.T) {
	e := fixture(t)
	e["source"] = "text-risk"
	e["entity_type"] = "message"
	e["risk_score"] = nil
	e["threshold"] = nil
	e["evidence"] = map[string]any{"score_source": "none", "decision_rule": "classifier label = scam"}
	b, _ := json.Marshal(e)
	if _, err := Decode(b); err != nil {
		t.Fatal(err)
	}
	e["evidence"].(map[string]any)["score_source"] = "tfidf_lr"
	b, _ = json.Marshal(e)
	if _, err := Decode(b); err == nil {
		t.Fatal("invented probability provenance accepted")
	}
}
func TestTrailingJSONRejected(t *testing.T) {
	b, _ := json.Marshal(fixture(t))
	if _, err := Decode(append(b, []byte(" {}")...)); err == nil {
		t.Fatal("trailing object accepted")
	}
}
