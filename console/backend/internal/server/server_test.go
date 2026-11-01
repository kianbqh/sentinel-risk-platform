package server

// Integration tests against a real PostgreSQL. Each run creates (and drops) a throwaway database.
// TEST_DATABASE_URL defaults to postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/kianbqh/fullstack-go-graphql/backend/graph"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/auth"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/store"
)

var (
	ts    *httptest.Server
	dbURL string
)

func TestMain(m *testing.M) {
	admin := os.Getenv("TEST_DATABASE_URL")
	if admin == "" {
		admin = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"
	}
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, admin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "PostgreSQL is required for integration tests:", err)
		os.Exit(1)
	}
	name := fmt.Sprintf("jobs_test_%d", time.Now().UnixNano())
	if _, err := conn.Exec(ctx, "CREATE DATABASE "+name); err != nil {
		panic(err)
	}
	url := strings.Replace(admin, "/postgres?", "/"+name+"?", 1)
	dbURL = url
	if err := store.Migrate(url); err != nil {
		panic(err)
	}
	st, err := store.New(ctx, url, 10)
	if err != nil {
		panic(err)
	}
	ts = httptest.NewServer(New(&graph.Resolver{Store: st, Auth: auth.Manager{Secret: []byte("t"), TTL: time.Hour, Cost: 4}}, "*"))
	code := m.Run()
	ts.Close()
	st.DB.Close()
	conn.Exec(ctx, "DROP DATABASE "+name+" WITH (FORCE)")
	os.Exit(code)
}

type gqlResp struct {
	Data   map[string]json.RawMessage `json:"data"`
	Errors []struct{ Message string } `json:"errors"`
}

func gql(t *testing.T, token, query string, vars map[string]any) gqlResp {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"query": query, "variables": vars})
	req, _ := http.NewRequest("POST", ts.URL+"/query", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var out gqlResp
	json.NewDecoder(res.Body).Decode(&out)
	return out
}

func register(t *testing.T, email string) string {
	r := gql(t, "", `mutation($e:String!,$p:String!){register(email:$e,password:$p){token user{email}}}`,
		map[string]any{"e": email, "p": "password123"})
	if len(r.Errors) > 0 {
		t.Fatalf("register: %v", r.Errors)
	}
	var p struct{ Token string }
	json.Unmarshal(r.Data["register"], &p)
	return p.Token
}

func TestRegisterLoginAndDuplicate(t *testing.T) {
	register(t, "Alice@Example.com")
	if r := gql(t, "", `mutation{register(email:"alice@example.com",password:"password123"){token}}`, nil); len(r.Errors) == 0 {
		t.Fatal("duplicate (case-insensitive) email accepted")
	}
	if r := gql(t, "", `mutation{login(email:"alice@example.com",password:"password123"){user{email}}}`, nil); len(r.Errors) > 0 {
		t.Fatalf("login: %v", r.Errors)
	}
	if r := gql(t, "", `mutation{login(email:"alice@example.com",password:"nope-nope"){token}}`, nil); len(r.Errors) == 0 {
		t.Fatal("bad password accepted")
	}
}

func TestValidation(t *testing.T) {
	if r := gql(t, "", `mutation{register(email:"not-an-email",password:"password123"){token}}`, nil); len(r.Errors) == 0 {
		t.Fatal("invalid email accepted")
	}
	if r := gql(t, "", `mutation{register(email:"short@x.io",password:"123"){token}}`, nil); len(r.Errors) == 0 {
		t.Fatal("short password accepted")
	}
	tok := register(t, "val@example.com")
	if r := gql(t, tok, `mutation{createApplication(input:{company:"  ",role:"SWE"}){id}}`, nil); len(r.Errors) == 0 {
		t.Fatal("blank company accepted")
	}
}

func TestRequiresAuth(t *testing.T) {
	r := gql(t, "", `{applications{id}}`, nil)
	if len(r.Errors) == 0 || r.Errors[0].Message != "unauthenticated" {
		t.Fatalf("expected unauthenticated, got %+v", r)
	}
}

func TestApplicationLifecycleAndStats(t *testing.T) {
	tok := register(t, "bob@example.com")
	r := gql(t, tok, `mutation{createApplication(input:{company:"Stripe",role:"ML Engineer Intern",status:APPLIED}){id status appliedAt}}`, nil)
	if len(r.Errors) > 0 {
		t.Fatal(r.Errors)
	}
	var a struct {
		ID, Status string
		AppliedAt  *string
	}
	json.Unmarshal(r.Data["createApplication"], &a)
	if a.Status != "APPLIED" || a.AppliedAt == nil {
		t.Fatalf("bad create: %+v", a)
	}
	gql(t, tok, `mutation{createApplication(input:{company:"NVIDIA",role:"Deep Learning Intern"}){id}}`, nil)
	r = gql(t, tok, `mutation($id:ID!){updateStatus(id:$id,status:OA){status events{status}}}`, map[string]any{"id": a.ID})
	var u struct {
		Status string
		Events []struct{ Status string }
	}
	json.Unmarshal(r.Data["updateStatus"], &u)
	if u.Status != "OA" || len(u.Events) != 2 || u.Events[1].Status != "OA" {
		t.Fatalf("bad update: %+v %v", u, r.Errors)
	}
	r = gql(t, tok, `{stats{total byStatus{status count}} applications(search:"strip"){company} oa: applications(status:OA){id}}`, nil)
	if !strings.Contains(string(r.Data["stats"]), `"total":2`) || !strings.Contains(string(r.Data["applications"]), "Stripe") ||
		strings.Contains(string(r.Data["applications"]), "NVIDIA") || !strings.Contains(string(r.Data["oa"]), a.ID) {
		t.Fatalf("bad stats/search: %s %s", r.Data["stats"], r.Data["applications"])
	}
	r = gql(t, tok, `mutation($id:ID!){deleteApplication(id:$id)}`, map[string]any{"id": a.ID})
	if string(r.Data["deleteApplication"]) != "true" {
		t.Fatalf("delete: %s", r.Data["deleteApplication"])
	}
}

func TestTenantIsolation(t *testing.T) {
	owner := register(t, "owner@example.com")
	intruder := register(t, "intruder@example.com")
	r := gql(t, owner, `mutation{createApplication(input:{company:"Secret Co",role:"Intern"}){id}}`, nil)
	var a struct{ ID string }
	json.Unmarshal(r.Data["createApplication"], &a)
	if r := gql(t, intruder, `query($id:ID!){application(id:$id){id}}`, map[string]any{"id": a.ID}); string(r.Data["application"]) != "null" {
		t.Fatal("intruder can read another user's application")
	}
	if r := gql(t, intruder, `mutation($id:ID!){updateStatus(id:$id,status:OFFER){id}}`, map[string]any{"id": a.ID}); len(r.Errors) == 0 {
		t.Fatal("intruder can update another user's application")
	}
	if r := gql(t, intruder, `mutation($id:ID!){deleteApplication(id:$id)}`, map[string]any{"id": a.ID}); string(r.Data["deleteApplication"]) != "false" {
		t.Fatal("intruder can delete another user's application")
	}
}

func TestPaginationAndHealth(t *testing.T) {
	tok := register(t, "pager@example.com")
	for i := 0; i < 5; i++ {
		gql(t, tok, fmt.Sprintf(`mutation{createApplication(input:{company:"C%d",role:"R"}){id}}`, i), nil)
	}
	r := gql(t, tok, `{p1: applications(limit:2){company} p3: applications(limit:2, offset:4){company}}`, nil)
	var p1, p3 []map[string]string
	json.Unmarshal(r.Data["p1"], &p1)
	json.Unmarshal(r.Data["p3"], &p3)
	if len(p1) != 2 || len(p3) != 1 || p1[0]["company"] != "C4" {
		t.Fatalf("pagination: %v %v", p1, p3)
	}
	res, _ := http.Get(ts.URL + "/healthz")
	if res.StatusCode != 200 {
		t.Fatalf("healthz %d", res.StatusCode)
	}
}

func TestMigrationsAreIdempotent(t *testing.T) {
	if err := store.Migrate(dbURL); err != nil { // second run on an up-to-date DB must be a no-op
		t.Fatal(err)
	}
}

func TestEventsBatchedPerApplication(t *testing.T) {
	tok := register(t, "loader@example.com")
	ids := []string{}
	for i := 0; i < 3; i++ {
		r := gql(t, tok, fmt.Sprintf(`mutation{createApplication(input:{company:"L%d",role:"R",status:APPLIED}){id}}`, i), nil)
		var a struct{ ID string }
		json.Unmarshal(r.Data["createApplication"], &a)
		ids = append(ids, a.ID)
	}
	gql(t, tok, `mutation($id:ID!){updateStatus(id:$id,status:INTERVIEW){id}}`, map[string]any{"id": ids[1]})
	r := gql(t, tok, `{applications{id company events{status}}}`, nil)
	var apps []struct {
		ID     string
		Events []struct{ Status string }
	}
	json.Unmarshal(r.Data["applications"], &apps)
	got := map[string]int{}
	for _, a := range apps {
		got[a.ID] = len(a.Events)
	}
	if got[ids[0]] != 1 || got[ids[1]] != 2 || got[ids[2]] != 1 {
		t.Fatalf("events not mapped to the right application: %v", got)
	}
}
