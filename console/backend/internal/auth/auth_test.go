package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

var m = Manager{Secret: []byte("test"), TTL: time.Hour, Cost: 4}

func TestHashAndCheck(t *testing.T) {
	h, err := m.Hash("correct horse")
	if err != nil || !m.Check(h, "correct horse") || m.Check(h, "wrong pass") {
		t.Fatalf("hash/check failed: %v", err)
	}
	if _, err := m.Hash("short"); err == nil {
		t.Fatal("expected short-password error")
	}
}

func TestIssueParseRoundTrip(t *testing.T) {
	tok, _ := m.Issue(42)
	id, err := m.Parse(tok)
	if err != nil || id != 42 {
		t.Fatalf("got %d %v", id, err)
	}
}

func TestRejectsExpiredAndForeignTokens(t *testing.T) {
	exp := Manager{Secret: []byte("test"), TTL: -time.Minute}
	tok, _ := exp.Issue(1)
	if _, err := m.Parse(tok); err == nil {
		t.Fatal("expired token accepted")
	}
	other := Manager{Secret: []byte("other"), TTL: time.Hour}
	tok, _ = other.Issue(1)
	if _, err := m.Parse(tok); err == nil {
		t.Fatal("token signed with another secret accepted")
	}
}

func TestMiddleware(t *testing.T) {
	var got int64
	h := m.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { got, _ = UserID(r.Context()) }))
	tok, _ := m.Issue(7)
	req := httptest.NewRequest("POST", "/query", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	h.ServeHTTP(httptest.NewRecorder(), req)
	if got != 7 {
		t.Fatalf("user id = %d", got)
	}
	bad := httptest.NewRequest("POST", "/query", nil)
	bad.Header.Set("Authorization", "Bearer garbage")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, bad)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("code = %d", rec.Code)
	}
}
