-- Explicit opt-in migration. Intentionally outside Go's embedded wildcard.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS copilot;
REVOKE ALL ON SCHEMA copilot FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='sentinel_copilot_reader') THEN
    CREATE ROLE sentinel_copilot_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='sentinel_copilot_writer') THEN
    CREATE ROLE sentinel_copilot_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='sentinel_copilot_indexer') THEN
    CREATE ROLE sentinel_copilot_indexer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END
$roles$;

ALTER TABLE public.case_events ADD COLUMN IF NOT EXISTS actor TEXT;
ALTER TABLE public.case_events ADD CONSTRAINT copilot_actor_action CHECK
 (actor IS NULL OR (actor='copilot' AND actor_id IS NULL AND action='COPILOT_DRAFT'));

CREATE VIEW copilot.case_evidence AS
 SELECT c.id AS case_id,c.alert_id,c.status::text,c.assignee_id,c.created_at,c.updated_at,
 a.event_time,a.source,a.entity_type,a.entity_id,a.risk_score,a.threshold,a.severity,
 a.summary,a.model_name,a.model_version,a.evidence,a.trace_id
 FROM public.cases c JOIN public.alerts a ON a.id=c.alert_id;
CREATE VIEW copilot.case_history AS
 SELECT e.id AS event_id,e.case_id,COALESCE(e.actor,CASE WHEN e.actor_id IS NULL THEN 'system' ELSE 'analyst' END) AS actor,
 e.action,e.note,e.at FROM public.case_events e;

CREATE TABLE copilot.case_vectors (
 case_id BIGINT PRIMARY KEY REFERENCES public.cases(id) ON DELETE CASCADE,
 entity_key TEXT NOT NULL, evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
 embedding_model TEXT NOT NULL, embedding_revision TEXT NOT NULL,
 embedding vector(384) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE VIEW copilot.case_vectors_read AS SELECT * FROM copilot.case_vectors;
-- Exact cosine scan is used for this bounded benchmark, without ANN recall loss.
CREATE TABLE copilot.retrieval_documents (
 dataset_id TEXT NOT NULL, document_id TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('query','gallery')),
 entity_key TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
 domain TEXT NOT NULL, gold_label INTEGER NOT NULL CHECK(gold_label IN (0,1)), evidence_text TEXT NOT NULL,
 embedding_model TEXT NOT NULL, embedding_revision TEXT NOT NULL, embedding vector(384) NOT NULL,
 PRIMARY KEY(dataset_id, document_id)
);
CREATE TABLE copilot.turns (
 case_id BIGINT NOT NULL REFERENCES public.cases(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL, redacted_memory JSONB NOT NULL CHECK(jsonb_typeof(redacted_memory)='array'),
 draft JSONB NOT NULL CHECK(jsonb_typeof(draft)='object'),
 event_id BIGINT NOT NULL UNIQUE REFERENCES public.case_events(id),
 at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(case_id,run_id)
);
CREATE TABLE copilot.memory (
 case_id BIGINT PRIMARY KEY REFERENCES public.cases(id) ON DELETE CASCADE,
 redacted_memory JSONB NOT NULL CHECK(jsonb_typeof(redacted_memory)='array'),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE FUNCTION copilot.load_memory(p_case_id BIGINT) RETURNS JSONB
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $function$
 SELECT COALESCE((SELECT m.redacted_memory FROM copilot.memory m WHERE m.case_id=p_case_id),'[]'::jsonb)
$function$;

CREATE FUNCTION copilot.save_turn(p_case_id BIGINT,p_run_id TEXT,p_memory JSONB,p_draft JSONB) RETURNS JSONB
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $function$
DECLARE previous copilot.turns%ROWTYPE; eid BIGINT;
BEGIN
 IF p_case_id<=0 OR p_run_id IS NULL OR p_run_id !~ '^[A-Za-z0-9_.:-]{1,120}$'
    OR jsonb_typeof(p_memory) IS DISTINCT FROM 'array' OR jsonb_typeof(p_draft) IS DISTINCT FROM 'object'
    OR octet_length(p_memory::text)+octet_length(p_draft::text)>131072 THEN
   RAISE EXCEPTION 'Invalid copilot turn';
 END IF;
 -- Serialize same-case writes while preserving every field in public.cases.
 PERFORM 1 FROM public.cases WHERE id=p_case_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Case not found'; END IF;
 SELECT * INTO previous FROM copilot.turns WHERE case_id=p_case_id AND run_id=p_run_id;
 IF FOUND THEN
   IF previous.redacted_memory<>p_memory OR previous.draft<>p_draft THEN
     RAISE EXCEPTION 'Idempotency key reused with different content';
   END IF;
   RETURN jsonb_build_object('case_id',p_case_id,'run_id',p_run_id,'event_id',previous.event_id,'actor','copilot');
 END IF;
 INSERT INTO public.case_events(case_id,actor_id,actor,action,note)
 VALUES(p_case_id,NULL,'copilot','COPILOT_DRAFT',p_draft::text) RETURNING id INTO eid;
 INSERT INTO copilot.turns(case_id,run_id,redacted_memory,draft,event_id) VALUES(p_case_id,p_run_id,p_memory,p_draft,eid);
 INSERT INTO copilot.memory(case_id,redacted_memory) VALUES(p_case_id,p_memory)
 ON CONFLICT(case_id) DO UPDATE SET redacted_memory=EXCLUDED.redacted_memory,updated_at=now();
 RETURN jsonb_build_object('case_id',p_case_id,'run_id',p_run_id,'event_id',eid,'actor','copilot');
END
$function$;

REVOKE ALL ON ALL TABLES IN SCHEMA copilot FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA copilot FROM PUBLIC;
GRANT USAGE ON SCHEMA copilot TO sentinel_copilot_reader,sentinel_copilot_writer,sentinel_copilot_indexer;
GRANT SELECT ON copilot.case_evidence,copilot.case_history,copilot.case_vectors_read TO sentinel_copilot_reader;
GRANT EXECUTE ON FUNCTION copilot.load_memory(BIGINT),copilot.save_turn(BIGINT,TEXT,JSONB,JSONB) TO sentinel_copilot_writer;
GRANT SELECT ON copilot.case_evidence TO sentinel_copilot_indexer;
GRANT SELECT,INSERT,UPDATE,DELETE ON copilot.case_vectors,copilot.retrieval_documents TO sentinel_copilot_indexer;
