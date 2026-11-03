import hashlib
import io
import json
import math
import os
import tempfile
import time
from pathlib import Path
import unittest
from unittest import mock

from copilot.db.persistence import validate_turn
from copilot.db.reader import PostgresCaseReader,select_unique_neighbors
from copilot.db.vectors import vector_literal
from copilot.mcp_server.contracts import FEATURE_NAMES,TOOL_SCOPES,ToolRejected,validate_call,tool_schemas,validate_envelope
from copilot.mcp_server.explanation import ExactExplainer,MODEL_SHA256
from copilot.mcp_server.gateway import GatewayScorer
from copilot.mcp_server.service import ToolService
from copilot.mcp_server.token_registry import TokenRegistry
from copilot.retrieval.evidence import ATO_FEATURES,evidence_text,fixed_split,record,validate_protocol
from copilot.scripts.provenance import RunRecord,git_output,git_snapshot,verify_source_snapshot


class ContractsTest(unittest.TestCase):
    def test_exact_structured_envelope_rejects_null_wrapped_and_malformed(self):
        for value in ({"ok":True,"data":{}},{"ok":False,"error":{"code":"forbidden","message":"Scope missing"}}):
            self.assertEqual(validate_envelope(value),value)
        malformed=(None,{"result":{"ok":True,"data":{}}},{"ok":1,"data":{}},{"ok":True},
            {"ok":True,"data":[]},{"ok":True,"data":{},"error":{}},{"ok":False,"error":"bad"},
            {"ok":False,"error":{"code":"bad","message":"x","extra":True}},{"ok":False,"error":{"code":"","message":"x"}})
        for value in malformed:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_envelope(value)
    def test_exact_allowlist(self):
        self.assertEqual(set(TOOL_SCOPES),{"get_case","list_case_events","score_transaction","explain_score","search_similar_cases"})
        for name in ("label_case","resolve_case","assign_case","sql","fetch_url",""):
            with self.subTest(name=name),self.assertRaises(ToolRejected): validate_call(name,{"case_id":1})

    def test_strict_schema(self):
        for name in TOOL_SCOPES:
            self.assertFalse(tool_schemas()[name]["additionalProperties"])
        for args in ({"case_id":True},{"case_id":1.0},{"case_id":"1"},{"case_id":0},{"case_id":1,"url":"http://bad"}):
            with self.subTest(args=args),self.assertRaises(ToolRejected): validate_call("get_case",args)
        for k in (True,0,21,5.0):
            with self.subTest(k=k),self.assertRaises(ToolRejected): validate_call("search_similar_cases",{"case_id":1,"k":k})
        features={name:0 for name in FEATURE_NAMES}
        validate_call("score_transaction",{"features":features})
        for bad in ({**features,"V1":True},{**features,"V1":math.nan},{**features,"V1":math.inf},{**features,"label":1}):
            with self.assertRaises(ToolRejected):validate_call("score_transaction",{"features":bad})
        with self.assertRaises(ToolRejected):validate_call("score_transaction",{"features":features,"publish":True})

    def test_per_tool_scopes_and_no_auth_bypass(self):
        class Reader:
            def get_case(self,case_id):return {"case_id":case_id}
            def list_case_events(self,case_id):return []
        service=ToolService(Reader(),None,None)
        self.assertTrue(service.call("get_case",{"case_id":1},{"cases:read"})["ok"])
        for scopes in (None,set(),{"events:read"}):
            self.assertEqual(service.call("get_case",{"case_id":1},scopes)["error"]["code"],"forbidden")


class TokenTest(unittest.TestCase):
    def test_expiry_audience_notbefore_unknown(self):
        token="test-token-"+"a"*40
        record={"sha256":hashlib.sha256(token.encode()).hexdigest(),"client_id":"test",
            "scopes":["cases:read"],"resource":"http://127.0.0.1:1/mcp","expires_at":100,"not_before":10}
        registry=TokenRegistry([record],record["resource"])
        self.assertEqual(registry.lookup(token,50),record)
        for now in (9,100,101):self.assertIsNone(registry.lookup(token,now))
        self.assertIsNone(registry.lookup("unknown-"+"b"*40,50))
        self.assertIsNone(TokenRegistry([record],"http://wrong/mcp").lookup(token,50))


class GatewayTest(unittest.TestCase):
    def test_publish_false_and_fixed_origin(self):
        class Opener:
            def open(self,request,timeout):
                self.request=request
                return io.BytesIO(json.dumps({"fraud_score":.9,"flagged":True,"threshold":.8,"alert_published":False}).encode())
        opener=Opener()
        scorer=GatewayScorer("http://127.0.0.1:18040",opener=opener)
        self.assertFalse(scorer.score({name:0. for name in FEATURE_NAMES})["alert_published"])
        body=json.loads(opener.request.data)
        self.assertIs(body["publish"],False)
        self.assertEqual(opener.request.full_url,"http://127.0.0.1:18040/v1/transactions/score")
        for origin in ("file:///tmp/foo","http://user:pass@host","http://host/path","http://host/?url=bad"):
            with self.assertRaises(ValueError):GatewayScorer(origin)


class ExplanationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve()
        # Same test works in private staging and after root applies it in the repo.
        candidates=[parent for parent in root.parents if (parent/"streaming/model/lr_model.json").exists()]
        fallback=Path(os.environ.get("COPILOT_REPO_ROOT","G:/ResumeProject/platforms/sentinel-risk-platform"))
        cls.root=candidates[0] if candidates else fallback
        export=cls.root/"streaming/model/lr_model.json"
        cls.a=json.loads(export.read_text())
        cls.explainer=ExactExplainer(export,cls.root/"serving/model/model.joblib",
            expected_export_sha256="12c063adddb9d2e2a0ecd1e9190a45281411f9b83cc7de279d48c54be06cd4a1")

    def case(self):
        a=self.a
        return {"case_id":1,"source":"transaction-api","model_name":"logreg_balanced","model_version":MODEL_SHA256,
            "evidence":{"features":dict(zip(FEATURE_NAMES,a["mean"]))},"risk_score":1/(1+math.exp(-a["intercept"]))}

    def test_exact_logodds_and_sigmoid(self):
        case=self.case();result=self.explainer.explain(case)
        self.assertEqual(len(result["contributions"]),34)
        self.assertEqual(result["log_odds"],self.a["intercept"])
        self.assertEqual(result["probability"],case["risk_score"])
        values=case["evidence"]["features"]
        values["V1"]+=self.a["scale"][0]*2
        expected=self.a["intercept"]+2*self.a["coef"][0]
        case["risk_score"]=1/(1+math.exp(-expected))
        result=self.explainer.explain(case)
        self.assertAlmostEqual(result["log_odds"],expected,places=14)
        self.assertAlmostEqual(result["contributions"][0]["log_odds_contribution"],2*self.a["coef"][0],places=14)

    def test_missing_source_version_and_score_reject(self):
        variants=[("source","ato"),("model_version",MODEL_SHA256[:12]),("risk_score",.99),("evidence",{}),
            ("evidence",{"features":{"Amount":100}})]
        for key,value in variants:
            case=self.case();case[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ToolRejected):self.explainer.explain(case)

    def test_actual_stream_identity_with_missing_vector(self):
        case=self.case()
        case.update(source="transaction-stream",model_name="windowed-logistic-regression",
            model_version=self.explainer.export_sha256,evidence={"amount":100})
        with self.assertRaises(ToolRejected) as error:self.explainer.explain(case)
        self.assertEqual(error.exception.code,"features_unavailable")


class RetrievalTest(unittest.TestCase):
    @staticmethod
    def neighbor(case_id,entity,digest,similarity):
        return {"case_id":case_id,"alert_id":"case:"+str(case_id),"source":"transaction-api","summary":"Fixture",
            "similarity":similarity,"entity_key":entity,"evidence_sha256":digest}

    def test_unique_entities_and_evidence_after_nearest_first_selection(self):
        rows=[self.neighbor(1,"query","query-hash",1.),self.neighbor(1000001,"A","hash-A",.99),
            self.neighbor(1000121,"A","hash-A",.99),self.neighbor(2,"B","hash-A",.98),
            self.neighbor(3,"A","hash-C",.97),self.neighbor(4,"C","hash-C",.96),self.neighbor(5,"B","hash-B",.95)]
        result=select_unique_neighbors(rows,3,query_case_id=1,query_entity_key="query",query_evidence_sha256="query-hash")
        self.assertEqual([row["case_id"] for row in result],[1000001,4,5])
        self.assertTrue(all(set(row)=={"case_id","alert_id","source","summary","similarity"} for row in result))

    def test_reader_scans_beyond_duplicate_prefix_before_k_selection(self):
        rows=[self.neighbor(i,"A","hash-A",.99) for i in range(2,22)]
        rows.extend([self.neighbor(22,"B","hash-B",.8),self.neighbor(23,"C","hash-C",.7)])
        query={"embedding":"[1,0]","entity_key":"query","evidence_sha256":"query-hash"}
        first=type("Cursor",(),{"fetchone":lambda self:query})()
        second=type("Cursor",(),{"fetchall":lambda self:rows})()
        conn=mock.MagicMock();conn.__enter__.return_value=conn;conn.execute.side_effect=[first,second]
        reader=PostgresCaseReader("unused",embedding_model="fixed",embedding_revision="fixed")
        with mock.patch.object(reader,"_connect",return_value=conn):result=reader.search_similar_cases(1,3)
        self.assertEqual([row["case_id"] for row in result["neighbors"]],[2,22,23])
        sql=conn.execute.call_args_list[1].args[0]
        self.assertNotIn("LIMIT",sql.upper())
        self.assertIn("ORDER BY v.embedding <=> %s::vector, v.case_id",sql)

    def test_no_outcomes_ids_or_predictions_in_embedding(self):
        raw={name:float(i+1) for i,name in enumerate(ATO_FEATURES)}
        base=evidence_text("ato",raw)
        changed={**raw,"label":1,"gold_label":1,"risk_score":1.,"severity":"high","flagged":True,
            "model_name":"fraud","case_id":99,"ip":"1.2.3.4","summary":"FRAUD","kind":"stuffing"}
        self.assertEqual(base,evidence_text("ato",changed))
        for marker in ("label","risk_score","severity","flagged","case_id","1.2.3.4","FRAUD","stuffing"):
            self.assertNotIn(marker,base)

    def test_balanced_unique_fixed_split(self):
        rows=[]
        for domain,names in (("card",FEATURE_NAMES),("ato",ATO_FEATURES)):
            for label in (0,1):
                for i in range(15):
                    key=f"{domain}:{label}:{i}"
                    rows.append(record(key,domain,key,{name:float(i+label*100+j) for j,name in enumerate(names)},label))
        selected=fixed_split(rows,42,5,5)
        self.assertEqual(selected,fixed_split(rows,42,5,5))
        counts=validate_protocol(selected)
        self.assertEqual(set(counts.values()),{5})
        with self.assertRaises(ValueError):validate_protocol(selected+[selected[0]])

    def test_vector_rejection(self):
        self.assertTrue(vector_literal([1.]+[0.]*383).startswith("[1.0"))
        for values in ([0.]*384,[1.]*383,[math.nan]*384):
            with self.assertRaises(ValueError):vector_literal(values)


class PersistenceTest(unittest.TestCase):
    def test_redacted_memory_and_draft_only(self):
        validate_turn(1,"run-1",[{"content":"[EMAIL] reviewed"}],{"summary":"Amount 12.50"})
        validate_turn(1,"run-score",[],{"summary":"Stored probability 0.9999722811772073 and threshold 0.9999722811772073"})
        for pii in ("alice@example.com","4111 1111 1111 1111","192.168.1.2","+1 (415) 555-1234"):
            with self.subTest(pii=pii),self.assertRaises(ValueError):
                validate_turn(1,"run-1",[{"content":pii}],{})
        with self.assertRaises(ValueError):validate_turn(1,"run-1",[],{"summary":"alice@example.com"})
        with self.assertRaises(ValueError):validate_turn(1,"bad key",[],{})


class ProvenanceTest(unittest.TestCase):
    def test_each_result_preserves_facts_and_adds_context(self):
        with tempfile.TemporaryDirectory() as folder:
            run=RunRecord.__new__(RunRecord)
            run.output=Path(folder);run.started=time.perf_counter();run.config={"device":"cpu"}
            run.context={"git_sha":"a"*40,"config_sha256":"b"*64,"packages":{},"environment":"unit-test",
                "command":["unit-test"],"timestamp":"2026-01-01T00:00:00+00:00","cpu":"test CPU","gpu_driver":None,"cuda":None}
            run.write("case.json",{"case_id":123,"facts":[1,2]})
            run.write("raw.json",{"run_context":"external field","data":[1,2]},raw=True)
            run.write("samples.json",[1,2,3])
            case=json.loads((run.output/"case.json").read_text())
            self.assertEqual(case["case_id"],123);self.assertEqual(case["facts"],[1,2])
            for name in ("case.json","raw.json","samples.json"):
                value=json.loads((run.output/name).read_text());context=value["run_context"]
                for key in ("git_sha","config_sha256","packages","environment","device","command","timestamp","wall_seconds"):
                    self.assertIn(key,context)
            self.assertEqual(json.loads((run.output/"raw.json").read_text())["value"],{"run_context":"external field","data":[1,2]})
            self.assertEqual(json.loads((run.output/"samples.json").read_text())["value"],[1,2,3])

    def test_end_source_check_allows_unrelated_outputs_and_rejects_drift(self):
        initial={"git_sha":"a"*40,"selected_source_scope":["a.py"],"source_hashes":{"sha256":{"a.py":"b"*64},"missing_paths":[]},
            "selected_source_status":{"stdout":""}}
        def receipt(value):return {"stdout":value,"returncode":0}
        with mock.patch("copilot.scripts.provenance._git_record",side_effect=[receipt("a"*40),receipt(""),receipt("a"*40)]) as commands,mock.patch(
                "copilot.scripts.provenance.source_hashes",return_value=initial["source_hashes"]):
            self.assertEqual(verify_source_snapshot(Path(__file__).parent,initial)["status"],"unchanged")
            self.assertTrue(all("--" in call.args[1] for call in commands.call_args_list if "status" in call.args[1]))
        for status,hashes,head in ((" M a.py",initial["source_hashes"],"a"*40),("",{"sha256":{"a.py":"c"*64},"missing_paths":[]},"a"*40),
            ("",initial["source_hashes"],"c"*40)):
            with mock.patch("copilot.scripts.provenance._git_record",side_effect=[receipt(head),receipt(status),receipt(head)]),mock.patch(
                    "copilot.scripts.provenance.source_hashes",return_value=hashes):
                with self.assertRaises(RuntimeError):verify_source_snapshot(Path(__file__).parent,initial)

    def test_git_failure_raises_instead_of_null(self):
        backend=(Path(__file__).resolve(),str(Path(__file__).resolve().parent))
        process=type("Process",(),{"returncode":128,"stdout":"","stderr":"not a git repository"})()
        with mock.patch("copilot.scripts.provenance._git_backend",return_value=backend),mock.patch(
                "copilot.scripts.provenance.subprocess.run",return_value=process) as run:
            with self.assertRaises(RuntimeError):git_output(Path(__file__).parent,["rev-parse","HEAD"])
        command=run.call_args.args[0]
        self.assertIn("--no-optional-locks",command)
        self.assertIn("safe.directory="+backend[1],command)
        self.assertEqual(run.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"],"0")

    def test_snapshot_separates_selected_source_and_whole_tree(self):
        def receipt(value):return {"stdout":value,"returncode":0}
        backend=(Path(__file__).resolve(),str(Path(__file__).resolve().parent))
        with mock.patch("copilot.scripts.provenance._git_record",side_effect=[receipt("a"*40),receipt(""),receipt("?? unrelated.txt"),receipt("a"*40)]),mock.patch(
                "copilot.scripts.provenance._git_backend",return_value=backend),mock.patch(
                "copilot.scripts.provenance.source_hashes",return_value={"sha256":{"a.py":"b"*64},"missing_paths":[]}):
            snapshot=git_snapshot(Path(__file__).parent,["a.py"])
        self.assertEqual(snapshot["git_sha"],"a"*40)
        self.assertFalse(snapshot["selected_source_dirty"])
        self.assertTrue(snapshot["whole_worktree_dirty"])
        self.assertEqual(snapshot["source_hashes"]["sha256"],{"a.py":"b"*64})

    def test_snapshot_rejects_missing_sha_and_head_race(self):
        def receipt(value):return {"stdout":value,"returncode":0}
        for values in ([receipt("")],[receipt("a"*40),receipt(""),receipt(""),receipt("b"*40)]):
            with self.subTest(values=values),mock.patch("copilot.scripts.provenance._git_record",side_effect=values),mock.patch(
                    "copilot.scripts.provenance.source_hashes",return_value={"sha256":{},"missing_paths":[]}):
                with self.assertRaises(RuntimeError):git_snapshot(Path(__file__).parent,["a.py"])


if __name__=="__main__":unittest.main()
