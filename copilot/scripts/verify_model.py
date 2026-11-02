"""Actual joblib/export parity, independent inference and retained API evidence."""
import argparse
import json
import math

from copilot.mcp_server.contracts import FEATURE_NAMES,ToolRejected
from copilot.mcp_server.explanation import ExactExplainer,MODEL_SHA256
from copilot.scripts.provenance import RunRecord,sha256


def verify(config_path):
    run=RunRecord(config_path)
    try:
        import joblib
        import numpy as np
        config,root=run.config,run.root
        artifact=root/config["coefficient_export"]
        model_path=root/config["joblib_model"]
        explainer=ExactExplainer(artifact,model_path,expected_export_sha256=config["coefficient_export_sha256"])
        model=joblib.load(model_path)
        a=explainer.artifact
        scaler,classifier=model.steps[0][1],model.steps[1][1]
        checks={"mean":float(np.max(np.abs(scaler.mean_-np.array(a["mean"])))),
            "scale":float(np.max(np.abs(scaler.scale_-np.array(a["scale"])))),
            "coefficient":float(np.max(np.abs(classifier.coef_[0]-np.array(a["coef"])))),
            "intercept":abs(float(classifier.intercept_[0])-a["intercept"])}
        if any(value!=0 for value in checks.values()):raise ValueError("Export differs from frozen joblib pipeline")
        rng=np.random.default_rng(config["seed"])
        matrix=np.array(a["mean"])+rng.normal(size=(config["vectors"],34))*np.array(a["scale"])
        expected=model.predict_proba(matrix)[:,1]
        rows=[]
        for i,(vector,probability) in enumerate(zip(matrix,expected)):
            case={"case_id":i+1,"source":"transaction-api","model_name":"logreg_balanced","model_version":MODEL_SHA256,
                "risk_score":float(probability),"evidence":{"features":dict(zip(FEATURE_NAMES,map(float,vector)))}}
            result=explainer.explain(case)
            rows.append({"case_id":i+1,"joblib_probability":float(probability),"reconstructed_probability":result["probability"],
                "absolute_difference":abs(float(probability)-result["probability"])})
        source=root/config["retained_alerts"]
        if sha256(source)!=config["retained_alerts_sha256"]:raise ValueError("Retained alert source hash mismatch")
        retained=[]
        for i,line in enumerate(source.read_text().splitlines()):
            event=json.loads(line)
            case={"case_id":i+1,"source":event["source"],"model_name":event["model"]["name"],"model_version":event["model"]["version"],
                "risk_score":event["risk_score"],"evidence":event["evidence"]}
            try:
                result=explainer.explain(case)
                retained.append({"alert_id":event["alert_id"],"source":event["source"],"status":"explained", "absolute_difference":result["max_probability_difference"]})
            except ToolRejected as exc:
                retained.append({"alert_id":event["alert_id"],"source":event["source"],"status":"unavailable","reason":exc.code})
        run.write("parity_vectors.json",rows)
        run.write("retained_explanations.json",retained)
        run.finish({"status":"complete","artifact_sha256":sha256(artifact),"joblib_sha256":sha256(model_path),
            "retained_alerts_sha256":sha256(source),"parameter_max_absolute_differences":checks,
            "vectors":len(rows),"maximum_probability_absolute_difference":max(row["absolute_difference"] for row in rows),
            "retained_supported":sum(row["status"]=="explained" for row in retained),
            "retained_unavailable":sum(row["status"]=="unavailable" for row in retained)})
    except Exception as exc:
        run.fail(type(exc).__name__);raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);verify(parser.parse_args().config)
