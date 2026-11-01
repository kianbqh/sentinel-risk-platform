CREATE EXTENSION IF NOT EXISTS citext;
CREATE TYPE case_status AS ENUM ('OPEN', 'INVESTIGATING', 'RESOLVED');
CREATE TYPE label_value AS ENUM ('FRAUD', 'BENIGN', 'UNCERTAIN');
CREATE TABLE users (
 id BIGSERIAL PRIMARY KEY, email CITEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE alerts (
 id TEXT PRIMARY KEY, event_time TIMESTAMPTZ NOT NULL, source TEXT NOT NULL,
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 risk_score DOUBLE PRECISION CHECK (risk_score BETWEEN 0 AND 1),
 threshold DOUBLE PRECISION CHECK (threshold BETWEEN 0 AND 1),
 severity TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
 summary TEXT NOT NULL, model_name TEXT NOT NULL, model_version TEXT NOT NULL,
 evidence JSONB NOT NULL, trace_id TEXT NOT NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE cases (
 id BIGSERIAL PRIMARY KEY, alert_id TEXT NOT NULL UNIQUE REFERENCES alerts(id),
 status case_status NOT NULL DEFAULT 'OPEN', assignee_id BIGINT REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE case_events (
 id BIGSERIAL PRIMARY KEY, case_id BIGINT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
 actor_id BIGINT REFERENCES users(id), action TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE case_labels (
 case_id BIGINT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
 actor_id BIGINT NOT NULL REFERENCES users(id), value label_value NOT NULL,
 note TEXT NOT NULL DEFAULT '', at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(case_id,actor_id)
);
CREATE INDEX alerts_source_time ON alerts(source,event_time DESC);
CREATE INDEX cases_status_assignee ON cases(status,assignee_id);
CREATE INDEX events_case ON case_events(case_id,at,id);
