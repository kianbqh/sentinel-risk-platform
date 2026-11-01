// Package store is the PostgreSQL data layer (pgx v5 connection pool, golang-migrate migrations).
package store

import (
	"context"
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

	"github.com/kianbqh/fullstack-go-graphql/backend/graph/model"
	"github.com/kianbqh/fullstack-go-graphql/backend/migrations"
)

var (
	ErrNotFound  = errors.New("not found")
	ErrDuplicate = errors.New("already exists")
)

type Store struct{ DB *pgxpool.Pool }

type UserRow struct {
	ID           int64
	Email        string
	PasswordHash string
	CreatedAt    time.Time
}

func (u UserRow) Model() *model.User {
	return &model.User{ID: strconv.FormatInt(u.ID, 10), Email: u.Email, CreatedAt: u.CreatedAt}
}

// Migrate applies all pending up-migrations.
func Migrate(databaseURL string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return err
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, "pgx5://"+strings.SplitN(databaseURL, "://", 2)[1])
	if err != nil {
		return err
	}
	defer m.Close()
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return err
	}
	return nil
}

func New(ctx context.Context, databaseURL string, maxConns int32) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, err
	}
	if maxConns > 0 {
		cfg.MaxConns = maxConns
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return &Store{DB: pool}, pool.Ping(ctx)
}

func (s *Store) CreateUser(ctx context.Context, email, hash string) (UserRow, error) {
	var u UserRow
	err := s.DB.QueryRow(ctx,
		`INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id, email, password_hash, created_at`,
		email, hash).Scan(&u.ID, &u.Email, &u.PasswordHash, &u.CreatedAt)
	var pg *pgconn.PgError
	if errors.As(err, &pg) && pg.Code == "23505" {
		return u, ErrDuplicate
	}
	return u, err
}

func (s *Store) UserByEmail(ctx context.Context, email string) (UserRow, error) {
	return s.user(ctx, `WHERE email = $1`, email)
}

func (s *Store) UserByID(ctx context.Context, id int64) (UserRow, error) {
	return s.user(ctx, `WHERE id = $1`, id)
}

func (s *Store) user(ctx context.Context, where string, arg any) (UserRow, error) {
	var u UserRow
	err := s.DB.QueryRow(ctx, `SELECT id, email, password_hash, created_at FROM users `+where, arg).
		Scan(&u.ID, &u.Email, &u.PasswordHash, &u.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return u, ErrNotFound
	}
	return u, err
}

const appCols = `id, company, role, url, status, applied_at, updated_at`

func scanApp(row pgx.Row) (*model.Application, error) {
	var a model.Application
	var id int64
	var st string
	if err := row.Scan(&id, &a.Company, &a.Role, &a.URL, &st, &a.AppliedAt, &a.UpdatedAt); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	a.ID, a.Status = strconv.FormatInt(id, 10), model.Status(st)
	return &a, nil
}

// CreateApplication inserts the application and its first status event in one transaction.
func (s *Store) CreateApplication(ctx context.Context, userID int64, in model.NewApplication) (*model.Application, error) {
	st := model.StatusSaved
	if in.Status != nil {
		st = *in.Status
	}
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	a, err := scanApp(tx.QueryRow(ctx, `INSERT INTO applications (user_id, company, role, url, status, applied_at)
		VALUES ($1, $2, $3, $4, $5::app_status, CASE WHEN $5::app_status <> 'SAVED' THEN now() END) RETURNING `+appCols,
		userID, in.Company, in.Role, in.URL, string(st)))
	if err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO status_events (application_id, status) VALUES ($1, $2)`, a.ID, string(st)); err != nil {
		return nil, err
	}
	return a, tx.Commit(ctx)
}

func (s *Store) UpdateStatus(ctx context.Context, userID int64, id string, st model.Status) (*model.Application, error) {
	tx, err := s.DB.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	a, err := scanApp(tx.QueryRow(ctx, `UPDATE applications SET status = $3::app_status, updated_at = now(),
		applied_at = COALESCE(applied_at, CASE WHEN $3::app_status <> 'SAVED' THEN now() END)
		WHERE id = $1 AND user_id = $2 RETURNING `+appCols, id, userID, string(st)))
	if err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO status_events (application_id, status) VALUES ($1, $2)`, id, string(st)); err != nil {
		return nil, err
	}
	return a, tx.Commit(ctx)
}

func (s *Store) DeleteApplication(ctx context.Context, userID int64, id string) (bool, error) {
	tag, err := s.DB.Exec(ctx, `DELETE FROM applications WHERE id = $1 AND user_id = $2`, id, userID)
	return tag.RowsAffected() == 1, err
}

func (s *Store) Application(ctx context.Context, userID int64, id string) (*model.Application, error) {
	return scanApp(s.DB.QueryRow(ctx, `SELECT `+appCols+` FROM applications WHERE id = $1 AND user_id = $2`, id, userID))
}

func (s *Store) Applications(ctx context.Context, userID int64, st *model.Status, search *string, limit, offset int) ([]*model.Application, error) {
	q := `SELECT ` + appCols + ` FROM applications WHERE user_id = $1`
	args := []any{userID}
	if st != nil {
		args = append(args, string(*st))
		q += fmt.Sprintf(" AND status = $%d", len(args))
	}
	if search != nil && *search != "" {
		args = append(args, "%"+*search+"%")
		q += fmt.Sprintf(" AND (company ILIKE $%d OR role ILIKE $%d)", len(args), len(args))
	}
	args = append(args, limit, offset)
	q += fmt.Sprintf(" ORDER BY updated_at DESC, id DESC LIMIT $%d OFFSET $%d", len(args)-1, len(args))
	rows, err := s.DB.Query(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*model.Application{}
	for rows.Next() {
		a, err := scanApp(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func (s *Store) Events(ctx context.Context, appID string) ([]*model.StatusEvent, error) {
	rows, err := s.DB.Query(ctx, `SELECT status, at FROM status_events WHERE application_id = $1 ORDER BY at, id`, appID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*model.StatusEvent{}
	for rows.Next() {
		var e model.StatusEvent
		var st string
		if err := rows.Scan(&st, &e.At); err != nil {
			return nil, err
		}
		e.Status = model.Status(st)
		out = append(out, &e)
	}
	return out, rows.Err()
}

func (s *Store) Stats(ctx context.Context, userID int64) (*model.Stats, error) {
	rows, err := s.DB.Query(ctx, `SELECT status, count(*) FROM applications WHERE user_id = $1 GROUP BY status ORDER BY status`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	st := &model.Stats{ByStatus: []*model.StatusCount{}}
	for rows.Next() {
		var c model.StatusCount
		var s string
		if err := rows.Scan(&s, &c.Count); err != nil {
			return nil, err
		}
		c.Status = model.Status(s)
		st.Total += c.Count
		st.ByStatus = append(st.ByStatus, &c)
	}
	return st, rows.Err()
}

// EventsByApplication batch-loads status events for many applications in one query (used by the dataloader).
func (s *Store) EventsByApplication(ctx context.Context, appIDs []string) ([][]*model.StatusEvent, []error) {
	idx := make(map[string]int, len(appIDs))
	ids := make([]int64, len(appIDs))
	for i, id := range appIDs {
		idx[id] = i
		ids[i], _ = strconv.ParseInt(id, 10, 64)
	}
	out := make([][]*model.StatusEvent, len(appIDs))
	for i := range out {
		out[i] = []*model.StatusEvent{}
	}
	rows, err := s.DB.Query(ctx, `SELECT application_id, status, at FROM status_events
		WHERE application_id = ANY($1) ORDER BY application_id, at, id`, ids)
	if err != nil {
		return nil, []error{err}
	}
	defer rows.Close()
	for rows.Next() {
		var app int64
		var e model.StatusEvent
		var st string
		if err := rows.Scan(&app, &st, &e.At); err != nil {
			return nil, []error{err}
		}
		e.Status = model.Status(st)
		i := idx[strconv.FormatInt(app, 10)]
		out[i] = append(out[i], &e)
	}
	if err := rows.Err(); err != nil {
		return nil, []error{err}
	}
	return out, nil
}
