CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         CITEXT      NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE app_status AS ENUM ('SAVED', 'APPLIED', 'OA', 'INTERVIEW', 'OFFER', 'REJECTED');

CREATE TABLE applications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company    TEXT        NOT NULL CHECK (length(company) BETWEEN 1 AND 200),
    role       TEXT        NOT NULL CHECK (length(role) BETWEEN 1 AND 200),
    url        TEXT,
    status     app_status  NOT NULL DEFAULT 'SAVED',
    applied_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX applications_user_status_idx ON applications (user_id, status, updated_at DESC);
