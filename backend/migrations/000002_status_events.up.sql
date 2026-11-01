-- Audit trail of status changes (powers the application timeline).
CREATE TABLE status_events (
    id             BIGSERIAL PRIMARY KEY,
    application_id BIGINT      NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    status         app_status  NOT NULL,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX status_events_app_idx ON status_events (application_id, at);
