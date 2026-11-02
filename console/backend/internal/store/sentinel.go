// Package store persists alerts and the shared analyst case workflow.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/graph/model"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/alerts"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/telemetry"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/migrations"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

var ErrNotFound = errors.New("not found")
var ErrDuplicate = errors.New("already exists")

type Store struct{ DB *pgxpool.Pool }
type UserRow struct {
	ID                  int64
	Email, PasswordHash string
	CreatedAt           time.Time
}

func (u UserRow) Model() *model.User {
	return &model.User{ID: strconv.FormatInt(u.ID, 10), Email: u.Email, CreatedAt: u.CreatedAt}
}
func Migrate(url string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return err
	}
	parts := strings.SplitN(url, "://", 2)
	if len(parts) != 2 {
		return errors.New("invalid database URL")
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, "pgx5://"+parts[1])
	if err != nil {
		return err
	}
	defer m.Close()
	err = m.Up()
	if errors.Is(err, migrate.ErrNoChange) {
		return nil
	}
	return err
}
func New(ctx context.Context, url string, max int32) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, err
	}
	if max > 0 {
		cfg.MaxConns = max
	}
	db, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err = db.Ping(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{DB: db}, nil
}
func (s *Store) CreateUser(ctx context.Context, email, hash string) (UserRow, error) {
	var u UserRow
	err := s.DB.QueryRow(ctx, `INSERT INTO users(email,password_hash) VALUES($1,$2) RETURNING id,email,password_hash,created_at`, email, hash).Scan(&u.ID, &u.Email, &u.PasswordHash, &u.CreatedAt)
	var pe *pgconn.PgError
	if errors.As(err, &pe) && pe.Code == "23505" {
		err = ErrDuplicate
	}
	return u, err
}
func (s *Store) user(ctx context.Context, where string, arg any) (UserRow, error) {
	var u UserRow
	err := s.DB.QueryRow(ctx, `SELECT id,email,password_hash,created_at FROM users `+where, arg).Scan(&u.ID, &u.Email, &u.PasswordHash, &u.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	return u, err
}
func (s *Store) UserByEmail(ctx context.Context, email string) (UserRow, error) {
	return s.user(ctx, "WHERE email=$1", email)
}
func (s *Store) UserByID(ctx context.Context, id int64) (UserRow, error) {
	return s.user(ctx, "WHERE id=$1", id)
}
func (s *Store) Analysts(ctx context.Context) ([]*model.User, error) {
	rows, err := s.DB.Query(ctx, `SELECT id,email,created_at FROM users ORDER BY email`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*model.User{}
	for rows.Next() {
		var u model.User
		var id int64
		if err = rows.Scan(&id, &u.Email, &u.CreatedAt); err != nil {
			return nil, err
		}
		u.ID = strconv.FormatInt(id, 10)
		out = append(out, &u)
	}
	return out, rows.Err()
}

// Ingest is atomic and idempotent: Kafka offsets are committed only after this transaction.
func (s *Store) Ingest(ctx context.Context, e alerts.Envelope) (added bool, resultErr error) {
	started := time.Now()
	ctx, span := otel.Tracer("sentinel.store").Start(ctx, "postgres.transaction", trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attribute.String("db.system.name", "postgresql"), attribute.String("db.operation.name", "ingest")))
	defer func() {
		outcome := "success"
		if resultErr != nil {
			outcome = "error"
			span.RecordError(resultErr)
			span.SetStatus(codes.Error, "PostgreSQL transaction failed")
		}
		telemetry.DBTransactions.WithLabelValues("ingest", outcome).Observe(time.Since(started).Seconds())
		span.End()
	}()
	if err := e.Validate(); err != nil {
		return false, err
	}
	evidence, err := json.Marshal(e.Evidence)
	if err != nil {
		return false, err
	}
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `INSERT INTO alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,model_name,model_version,evidence,trace_id)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT(id) DO NOTHING`, e.AlertID, e.EventTime, e.Source, e.EntityType, e.EntityID, e.RiskScore, e.Threshold, e.Severity, e.Summary, e.Model.Name, e.Model.Version, evidence, e.TraceID)
	if err != nil {
		return false, err
	}
	if tag.RowsAffected() == 0 {
		return false, tx.Commit(ctx)
	}
	var id int64
	if err = tx.QueryRow(ctx, `INSERT INTO cases(alert_id) VALUES($1) RETURNING id`, e.AlertID).Scan(&id); err != nil {
		return false, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO case_events(case_id,action,note) VALUES($1,'INGESTED',$2)`, id, e.Source); err != nil {
		return false, err
	}
	return true, tx.Commit(ctx)
}

const caseCols = `id,alert_id,status,assignee_id,created_at,updated_at`

func scanCase(row pgx.Row) (*model.Case, error) {
	var c model.Case
	var id int64
	var assignee *int64
	var status string
	err := row.Scan(&id, &c.AlertID, &status, &assignee, &c.CreatedAt, &c.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		err = ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	c.ID = strconv.FormatInt(id, 10)
	c.Status = model.CaseStatus(status)
	if assignee != nil {
		v := strconv.FormatInt(*assignee, 10)
		c.AssigneeID = &v
	}
	return &c, nil
}
func (s *Store) Case(ctx context.Context, id string) (*model.Case, error) {
	return scanCase(s.DB.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1`, id))
}

type Filter struct {
	Source, Severity, Search, AssigneeID *string
	Status                               *model.CaseStatus
	Limit, Offset                        int
}

func (s *Store) Alerts(ctx context.Context, f Filter) ([]*model.Alert, error) {
	q := `SELECT a.id,a.event_time,a.source,a.entity_type,a.entity_id,a.risk_score,a.threshold,a.severity,a.summary,a.model_name,a.model_version,a.evidence,a.trace_id,c.id,c.status,c.assignee_id,c.created_at,c.updated_at FROM alerts a JOIN cases c ON c.alert_id=a.id WHERE true`
	args := []any{}
	add := func(clause string, v any) { args = append(args, v); q += fmt.Sprintf(clause, len(args)) }
	if f.Source != nil {
		add(" AND a.source=$%d", *f.Source)
	}
	if f.Status != nil {
		add(" AND c.status=$%d", string(*f.Status))
	}
	if f.Severity != nil {
		add(" AND a.severity=$%d", *f.Severity)
	}
	if f.AssigneeID != nil {
		add(" AND c.assignee_id=$%d", *f.AssigneeID)
	}
	if f.Search != nil && *f.Search != "" {
		add(" AND (a.summary||' '||a.entity_id||' '||a.id) ILIKE $%d", "%"+*f.Search+"%")
	}
	args = append(args, f.Limit, f.Offset)
	q += fmt.Sprintf(" ORDER BY a.event_time DESC,a.id LIMIT $%d OFFSET $%d", len(args)-1, len(args))
	rows, err := s.DB.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*model.Alert{}
	for rows.Next() {
		var a model.Alert
		var c model.Case
		var cid int64
		var status string
		var assignee *int64
		var ev []byte
		if err = rows.Scan(&a.ID, &a.EventTime, &a.Source, &a.EntityType, &a.EntityID, &a.RiskScore, &a.Threshold, &a.Severity, &a.Summary, &a.ModelName, &a.ModelVersion, &ev, &a.TraceID, &cid, &status, &assignee, &c.CreatedAt, &c.UpdatedAt); err != nil {
			return nil, err
		}
		a.Evidence = string(ev)
		c.ID = strconv.FormatInt(cid, 10)
		c.AlertID = a.ID
		c.Status = model.CaseStatus(status)
		if assignee != nil {
			v := strconv.FormatInt(*assignee, 10)
			c.AssigneeID = &v
		}
		a.Case = &c
		out = append(out, &a)
	}
	return out, rows.Err()
}
func (s *Store) Assign(ctx context.Context, id string, actor int64, assignee string) (*model.Case, error) {
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	c, err := scanCase(tx.QueryRow(ctx, `UPDATE cases SET assignee_id=$2,status=CASE WHEN status='RESOLVED' THEN status ELSE 'INVESTIGATING' END,updated_at=now() WHERE id=$1 RETURNING `+caseCols, id, assignee))
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO case_events(case_id,actor_id,action,note) VALUES($1,$2,'ASSIGNED',$3)`, id, actor, assignee)
	if err != nil {
		return nil, err
	}
	return c, tx.Commit(ctx)
}
func (s *Store) Resolve(ctx context.Context, id string, actor int64, note string) (*model.Case, error) {
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	c, err := scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 FOR UPDATE`, id))
	if err != nil {
		return nil, err
	}
	if c.Status == model.CaseStatusResolved {
		return c, tx.Commit(ctx)
	}
	c, err = scanCase(tx.QueryRow(ctx, `UPDATE cases SET status='RESOLVED',updated_at=now() WHERE id=$1 RETURNING `+caseCols, id))
	if err != nil {
		return nil, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO case_events(case_id,actor_id,action,note) VALUES($1,$2,'RESOLVED',$3)`, id, actor, note); err != nil {
		return nil, err
	}
	return c, tx.Commit(ctx)
}
func (s *Store) Label(ctx context.Context, id string, actor int64, value model.LabelValue, note string) (*model.Case, error) {
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	c, err := scanCase(tx.QueryRow(ctx, `SELECT `+caseCols+` FROM cases WHERE id=$1 FOR UPDATE`, id))
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO case_labels(case_id,actor_id,value,note) VALUES($1,$2,$3,$4) ON CONFLICT(case_id,actor_id) DO UPDATE SET value=EXCLUDED.value,note=EXCLUDED.note,at=now()`, id, actor, string(value), note)
	if err != nil {
		return nil, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO case_events(case_id,actor_id,action,note) VALUES($1,$2,'LABELED',$3)`, id, actor, string(value)+": "+note); err != nil {
		return nil, err
	}
	c, err = scanCase(tx.QueryRow(ctx, `UPDATE cases SET updated_at=now() WHERE id=$1 RETURNING `+caseCols, id))
	if err != nil {
		return nil, err
	}
	return c, tx.Commit(ctx)
}
func (s *Store) Stats(ctx context.Context) (*model.Stats, error) {
	var v model.Stats
	err := s.DB.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE status='OPEN'),count(*) FILTER(WHERE status='INVESTIGATING'),count(*) FILTER(WHERE status='RESOLVED'),count(*) FILTER(WHERE assignee_id IS NOT NULL) FROM cases`).Scan(&v.Total, &v.Open, &v.Investigating, &v.Resolved, &v.Assigned)
	return &v, err
}
func caseIDs(keys []string) ([]int64, map[string]int) {
	ids := make([]int64, len(keys))
	index := map[string]int{}
	for i, k := range keys {
		ids[i], _ = strconv.ParseInt(k, 10, 64)
		index[k] = i
	}
	return ids, index
}
func (s *Store) EventsByCase(ctx context.Context, keys []string) ([][]*model.CaseEvent, []error) {
	ids, index := caseIDs(keys)
	out := make([][]*model.CaseEvent, len(keys))
	for i := range out {
		out[i] = []*model.CaseEvent{}
	}
	rows, err := s.DB.Query(ctx, `SELECT case_id,actor_id,action,note,at FROM case_events WHERE case_id=ANY($1) ORDER BY case_id,at,id`, ids)
	if err != nil {
		return nil, []error{err}
	}
	defer rows.Close()
	for rows.Next() {
		var id int64
		var actor *int64
		var e model.CaseEvent
		if err = rows.Scan(&id, &actor, &e.Action, &e.Note, &e.At); err != nil {
			return nil, []error{err}
		}
		if actor != nil {
			v := strconv.FormatInt(*actor, 10)
			e.ActorID = &v
		}
		i := index[strconv.FormatInt(id, 10)]
		out[i] = append(out[i], &e)
	}
	if err = rows.Err(); err != nil {
		return nil, []error{err}
	}
	return out, nil
}
func (s *Store) LabelsByCase(ctx context.Context, keys []string) ([][]*model.CaseLabel, []error) {
	ids, index := caseIDs(keys)
	out := make([][]*model.CaseLabel, len(keys))
	for i := range out {
		out[i] = []*model.CaseLabel{}
	}
	rows, err := s.DB.Query(ctx, `SELECT case_id,actor_id,value,note,at FROM case_labels WHERE case_id=ANY($1) ORDER BY case_id,at,actor_id`, ids)
	if err != nil {
		return nil, []error{err}
	}
	defer rows.Close()
	for rows.Next() {
		var id, actor int64
		var v string
		var l model.CaseLabel
		if err = rows.Scan(&id, &actor, &v, &l.Note, &l.At); err != nil {
			return nil, []error{err}
		}
		l.ActorID = strconv.FormatInt(actor, 10)
		l.Value = model.LabelValue(v)
		i := index[strconv.FormatInt(id, 10)]
		out[i] = append(out[i], &l)
	}
	if err = rows.Err(); err != nil {
		return nil, []error{err}
	}
	return out, nil
}
