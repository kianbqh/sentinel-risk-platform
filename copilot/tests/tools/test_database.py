"""Real PG17+pgvector roles, atomic draft persistence and decision immutability."""
import json
import os
import unittest
import uuid

from copilot.db.persistence import PostgresDraftStore
from copilot.db.reader import PostgresCaseReader
from copilot.retrieval.embedding import MODEL,REVISION


@unittest.skipUnless(os.environ.get("RUN_COPILOT_DB")=="1","set RUN_COPILOT_DB=1 and three distinct DSNs for isolated real PostgreSQL acceptance")
class RealDatabaseTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.psycopg=psycopg
        self.admin_dsn=os.environ["COPILOT_ADMIN_DSN"]
        self.reader_dsn=os.environ["COPILOT_READER_DSN"]
        self.writer_dsn=os.environ["COPILOT_WRITER_DSN"]
        self.suffix=uuid.uuid4().hex
        self.alert_id="copilot-role-test:"+self.suffix
        with psycopg.connect(self.admin_dsn) as conn:
            self.assertEqual(conn.execute("SELECT version,dirty FROM public.schema_migrations").fetchall(),[(1,False)])
            self.assertIn("PostgreSQL 17",conn.execute("SELECT version()").fetchone()[0])
            self.assertIsNotNone(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone())
            self.user_id=conn.execute("INSERT INTO users(email,password_hash) VALUES(%s,'unused-test-hash') RETURNING id",("test-"+self.suffix+"@invalid.example",)).fetchone()[0]
            conn.execute("""INSERT INTO alerts(id,event_time,source,entity_type,entity_id,risk_score,threshold,severity,summary,
                model_name,model_version,evidence,trace_id) VALUES(%s,now(),'transaction-api','transaction',%s,.9,.8,'high',
                'Isolated copilot role test','test','test','{}',%s)""",(self.alert_id,self.suffix,self.suffix))
            self.case_id=conn.execute("INSERT INTO cases(alert_id,status,assignee_id) VALUES(%s,'INVESTIGATING',%s) RETURNING id",(self.alert_id,self.user_id)).fetchone()[0]
            conn.execute("INSERT INTO case_labels(case_id,actor_id,value,note) VALUES(%s,%s,'BENIGN','Human fixture label')",(self.case_id,self.user_id))

    def tearDown(self):
        with self.psycopg.connect(self.admin_dsn) as conn:
            conn.execute("DELETE FROM cases WHERE id=%s",(self.case_id,))
            conn.execute("DELETE FROM alerts WHERE id=%s",(self.alert_id,))
            conn.execute("DELETE FROM users WHERE id=%s",(self.user_id,))

    def snapshot(self):
        with self.psycopg.connect(self.admin_dsn) as conn:
            case=conn.execute("SELECT status,assignee_id,updated_at FROM cases WHERE id=%s",(self.case_id,)).fetchone()
            labels=conn.execute("SELECT actor_id,value,note,at FROM case_labels WHERE case_id=%s ORDER BY actor_id",(self.case_id,)).fetchall()
        return case,labels

    def assert_denied(self,dsn,query,params=None):
        with self.psycopg.connect(dsn) as conn:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):conn.execute(query,params)

    def test_real_roles_are_distinct_and_cannot_mutate_human_decisions(self):
        identities=[]
        for dsn in (self.admin_dsn,self.reader_dsn,self.writer_dsn):
            with self.psycopg.connect(dsn) as conn:
                identities.append(conn.execute("SELECT current_user").fetchone()[0])
        self.assertEqual(len(set(identities)),3)
        reader=PostgresCaseReader(self.reader_dsn,embedding_model=MODEL,embedding_revision=REVISION)
        self.assertEqual(reader.get_case(self.case_id)["case_id"],self.case_id)
        self.assert_denied(self.reader_dsn,"SELECT password_hash FROM users")
        self.assert_denied(self.reader_dsn,"SELECT * FROM case_labels")
        self.assert_denied(self.reader_dsn,"UPDATE cases SET status='RESOLVED' WHERE id=%s",(self.case_id,))
        self.assert_denied(self.reader_dsn,"SELECT copilot.save_turn(%s,'forbidden','[]','{}')",(self.case_id,))
        self.assert_denied(self.writer_dsn,"UPDATE cases SET status='RESOLVED' WHERE id=%s",(self.case_id,))
        self.assert_denied(self.writer_dsn,"INSERT INTO case_events(case_id,action) VALUES(%s,'RESOLVED')",(self.case_id,))
        self.assert_denied(self.writer_dsn,"SELECT * FROM users")

    def test_atomic_idempotent_copilot_event_and_unchanged_decisions(self):
        before=self.snapshot()
        store=PostgresDraftStore(self.writer_dsn)
        memory=[{"role":"assistant","content":"[EMAIL] redacted; human review required"}]
        draft={"risk_level":"high","confidence":.8,"key_evidence":["Stored score 0.9"],"recommended_next_step":"Review evidence"}
        self.assertEqual(store.load_memory(self.case_id),[])
        first=store.save_turn(self.case_id,run_id="run-1",redacted_memory=memory,draft=draft)
        second=store.save_turn(self.case_id,run_id="run-1",redacted_memory=memory,draft=draft)
        self.assertEqual(first,second)
        self.assertEqual(store.load_memory(self.case_id),memory)
        self.assertEqual(self.snapshot(),before)
        with self.assertRaises(self.psycopg.Error):
            store.save_turn(self.case_id,run_id="run-1",redacted_memory=memory,draft={"changed":True})
        with self.psycopg.connect(self.admin_dsn) as conn:
            events=conn.execute("SELECT actor,actor_id,action,note FROM case_events WHERE case_id=%s",(self.case_id,)).fetchall()
            self.assertEqual(len(events),1)
            self.assertEqual(events[0][:3],("copilot",None,"COPILOT_DRAFT"))
            self.assertEqual(json.loads(events[0][3]),draft)
            functions=conn.execute("SELECT proname,proconfig,proacl::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='copilot' ORDER BY proname").fetchall()
            for name,settings,acl in functions:
                self.assertIn("search_path=pg_catalog, pg_temp",settings)
                self.assertNotRegex(acl,r"(?:\{|,)=X/")
