package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/graph"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/alerts"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/auth"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/store"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

var ts *httptest.Server
var st *store.Store
var eventQueries, labelQueries atomic.Int64

type queryCounter struct{}

func (queryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.Contains(d.SQL, "FROM case_events") {
		eventQueries.Add(1)
	}
	if strings.Contains(d.SQL, "FROM case_labels") {
		labelQueries.Add(1)
	}
	return ctx
}
func (queryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func TestMain(m *testing.M) {
	ctx := context.Background()
	admin := os.Getenv("TEST_DATABASE_URL")
	if admin == "" {
		fmt.Fprintln(os.Stderr, "TEST_DATABASE_URL is required (real PostgreSQL)")
		os.Exit(1)
	}
	conn, err := pgx.Connect(ctx, admin)
	if err != nil {
		panic(err)
	}
	name := fmt.Sprintf("sentinel_test_%d", time.Now().UnixNano())
	if _, err = conn.Exec(ctx, "CREATE DATABASE "+name); err != nil {
		panic(err)
	}
	parsed, err := url.Parse(admin)
	if err != nil {
		panic(err)
	}
	parsed.Path = "/" + name
	cfg, err := pgxpool.ParseConfig(parsed.String())
	if err != nil {
		panic(err)
	}
	cfg.ConnConfig.Tracer = queryCounter{}
	if err = store.Migrate(parsed.String()); err != nil {
		panic(err)
	}
	db, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		panic(err)
	}
	st = &store.Store{DB: db}
	ts = httptest.NewServer(New(&graph.Resolver{Store: st, Auth: auth.Manager{Secret: []byte(strings.Repeat("t", 32)), TTL: time.Hour, Cost: 4}}, "http://localhost"))
	code := m.Run()
	ts.Close()
	db.Close()
	conn.Exec(ctx, "DROP DATABASE "+name+" WITH (FORCE)")
	conn.Close(ctx)
	os.Exit(code)
}

type gqlResponse struct {
	Data   map[string]json.RawMessage `json:"data"`
	Errors []struct{ Message string } `json:"errors"`
}

func gql(t *testing.T, token, query string, variables map[string]any) gqlResponse {
	t.Helper()
	b, _ := json.Marshal(map[string]any{"query": query, "variables": variables})
	req, _ := http.NewRequest("POST", ts.URL+"/graphql", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var out gqlResponse
	if err = json.NewDecoder(res.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}
func checked(t *testing.T, r gqlResponse) gqlResponse {
	t.Helper()
	if len(r.Errors) > 0 || r.Data == nil {
		t.Fatalf("GraphQL failed: %+v", r)
	}
	return r
}
func register(t *testing.T, email string) (string, string) {
	t.Helper()
	r := checked(t, gql(t, "", `mutation($e:String!){register(email:$e,password:"password123"){token user{id email}}}`, map[string]any{"e": email}))
	var p struct {
		Token string
		User  struct{ ID string }
	}
	if err := json.Unmarshal(r.Data["register"], &p); err != nil {
		t.Fatal(err)
	}
	return p.Token, p.User.ID
}
func event(id string) alerts.Envelope {
	p := .97
	threshold := .5
	return alerts.Envelope{SchemaVersion: "1.0", AlertID: id, EventTime: time.Now().UTC(), Source: "ato", EntityType: "ip", EntityID: "192.0.2.1", RiskScore: &p, Threshold: &threshold, Severity: "high", Summary: "Synthetic login abuse", Model: alerts.Model{Name: "hgb", Version: "test"}, Evidence: map[string]any{"synthetic": true}, TraceID: id}
}
func ingest(t *testing.T, id string) {
	t.Helper()
	if ok, err := st.Ingest(context.Background(), event(id)); err != nil || !ok {
		t.Fatalf("ingest %s: %v %v", id, ok, err)
	}
}
func TestAuthenticationAndInputValidation(t *testing.T) {
	token, _ := register(t, "Alice@Example.com")
	checked(t, gql(t, token, `{me{email} analysts{id}}`, nil))
	for _, q := range []string{`mutation{register(email:"alice@example.com",password:"password123"){token}}`, `mutation{login(email:"alice@example.com",password:"wrongpass"){token}}`, `mutation{register(email:"bad",password:"password123"){token}}`, `mutation{register(email:"short@example.com",password:"123"){token}}`} {
		if len(gql(t, "", q, nil).Errors) == 0 {
			t.Fatalf("invalid auth accepted: %s", q)
		}
	}
	checked(t, gql(t, "", `mutation{login(email:"alice@example.com",password:"password123"){token}}`, nil))
	if len(gql(t, "", `{alerts{id}}`, nil).Errors) == 0 {
		t.Fatal("anonymous alerts exposed")
	}
	for _, q := range []string{`{alerts(limit:101){id}}`, `{case(id:"-1"){id}}`, `mutation{assignCase(id:"1",assigneeId:"bad"){id}}`} {
		if len(gql(t, token, q, nil).Errors) == 0 {
			t.Fatal("invalid input accepted")
		}
	}
}
func TestConcurrentIngestionIsIdempotent(t *testing.T) {
	var added atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ok, err := st.Ingest(context.Background(), event("duplicate"))
			if err != nil {
				t.Error(err)
			}
			if ok {
				added.Add(1)
			}
		}()
	}
	wg.Wait()
	if added.Load() != 1 {
		t.Fatalf("inserted %d times", added.Load())
	}
	var cases, events int
	st.DB.QueryRow(context.Background(), `SELECT count(*) FROM cases WHERE alert_id='duplicate'`).Scan(&cases)
	st.DB.QueryRow(context.Background(), `SELECT count(*) FROM case_events e JOIN cases c ON c.id=e.case_id WHERE c.alert_id='duplicate'`).Scan(&events)
	if cases != 1 || events != 1 {
		t.Fatalf("duplicate case/event: %d/%d", cases, events)
	}
}
func TestSharedAnalystLifecycleAndFilters(t *testing.T) {
	ingest(t, "lifecycle")
	token, uid := register(t, "reviewer@example.com")
	other, _ := register(t, "second@example.com")
	r := checked(t, gql(t, token, `{alerts(source:"ato",status:OPEN,severity:"high",search:"lifecycle"){id riskScore case{id status events{action}}}}`, nil))
	var list []struct {
		ID   string
		Case struct {
			ID, Status string
			Events     []struct{ Action string }
		}
	}
	json.Unmarshal(r.Data["alerts"], &list)
	if len(list) != 1 || list[0].Case.Status != "OPEN" || len(list[0].Case.Events) != 1 {
		t.Fatalf("bad ingest view: %s", r.Data["alerts"])
	}
	id := list[0].Case.ID
	checked(t, gql(t, token, `mutation($id:ID!,$u:ID!){assignCase(id:$id,assigneeId:$u){status assigneeId}}`, map[string]any{"id": id, "u": uid}))
	checked(t, gql(t, other, `mutation($id:ID!){labelCase(id:$id,value:FRAUD,note:"reviewed synthetic signal"){id labels{value actorId}}}`, map[string]any{"id": id}))
	checked(t, gql(t, other, `mutation($id:ID!){labelCase(id:$id,value:BENIGN,note:"corrected label"){id}}`, map[string]any{"id": id}))
	checked(t, gql(t, token, `mutation($id:ID!){resolveCase(id:$id,note:"closed"){status}}`, map[string]any{"id": id}))
	checked(t, gql(t, token, `mutation($id:ID!){resolveCase(id:$id,note:"repeat"){status}}`, map[string]any{"id": id}))
	r = checked(t, gql(t, other, `query($u:ID!){alerts(status:RESOLVED,assigneeId:$u,search:"lifecycle"){case{status assigneeId events{action} labels{value note}}}}`, map[string]any{"u": uid}))
	var rows []struct {
		Case struct {
			Status string
			Events []struct{ Action string }
			Labels []struct{ Value, Note string }
		}
	}
	json.Unmarshal(r.Data["alerts"], &rows)
	if len(rows) != 1 || rows[0].Case.Status != "RESOLVED" || len(rows[0].Case.Events) != 5 || len(rows[0].Case.Labels) != 1 || rows[0].Case.Labels[0].Value != "BENIGN" {
		t.Fatalf("bad lifecycle: %s", r.Data["alerts"])
	}
}
func TestDataloaderBatchesHistoryForEightCases(t *testing.T) {
	for i := 0; i < 8; i++ {
		ingest(t, fmt.Sprintf("batch-history-%d", i))
	}
	token, _ := register(t, "loader@example.com")
	eventQueries.Store(0)
	labelQueries.Store(0)
	r := checked(t, gql(t, token, `{alerts(search:"batch-history-"){id case{events{action} labels{value}}}}`, nil))
	var rows []any
	json.Unmarshal(r.Data["alerts"], &rows)
	if len(rows) != 8 || eventQueries.Load() != 1 || labelQueries.Load() != 1 {
		t.Fatalf("N+1 regression: rows=%d events=%d labels=%d", len(rows), eventQueries.Load(), labelQueries.Load())
	}
}
func TestNullableTextProbabilityPersists(t *testing.T) {
	e := event("label-only")
	e.Source = "text-risk"
	e.EntityType = "message"
	e.RiskScore = nil
	e.Threshold = nil
	e.Evidence = map[string]any{"score_source": "none"}
	if ok, err := st.Ingest(context.Background(), e); !ok || err != nil {
		t.Fatal(ok, err)
	}
	token, _ := register(t, "text@example.com")
	r := checked(t, gql(t, token, `{alerts(search:"label-only"){riskScore threshold}}`, nil))
	if !bytes.Contains(r.Data["alerts"], []byte(`"riskScore":null`)) {
		t.Fatalf("probability invented: %s", r.Data["alerts"])
	}
}
