"""Gate fixable HIGH/CRITICAL vulnerabilities and unreviewed secret findings."""
import hashlib
import json
from pathlib import Path


# A single reviewed, immutable PyJWT 2.15.1 package-description example.
# This is not a path/rule allowlist: every source byte and scanner field is bound.
# See security/TRIVY_PUBLIC_EXAMPLE.md for the original finding and review.
DOC_EXAMPLE_TARGET = (
    "copilot/results/ci-tools-5465aae-01/environment/pip-inspect.json"
)
DOC_EXAMPLE_FILE_SHA256 = (
    "10f7bda7eab3d1d36165b6aadce1b91288cfc4b9ac6159e255dfd8e072540a48"
)
DOC_EXAMPLE_FINDING_SHA256 = (
    "67aff153a18be9ea3016ce83032da419dc798a93f3c11b8ed9eaa57b79dbef52"
)
DOC_EXAMPLE_TOKEN_SHA256 = (
    "bf145d9380bbe05bdc1a72525796b224f8a8c93ae0e9e102eafbc255ac1af9e8"
)
DOC_EXAMPLE_OFFSET = 281365
DOC_EXAMPLE_LENGTH = 105
DOC_EXAMPLE_LINE = 1496
DOC_EXAMPLE_REASON = (
    "Reviewed PyJWT 2.15.1 package-description HS256 example with payload "
    "{'some': 'payload'} and the documented example signing key; exact "
    "source bytes, token span and scanner descriptor verified. "
    "See security/TRIVY_PUBLIC_EXAMPLE.md."
)


def reviewed_public_example(root, target, item):
    """Recognize exactly one reviewed finding; any changed binding fails closed."""
    if target.get("Target") != DOC_EXAMPLE_TARGET or target.get("Class") != "secret":
        return False
    if (
        item.get("RuleID") != "jwt-token"
        or item.get("StartLine") != DOC_EXAMPLE_LINE
        or item.get("EndLine") != DOC_EXAMPLE_LINE
        or item.get("Offset") != DOC_EXAMPLE_OFFSET
    ):
        return False
    descriptor = json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    if hashlib.sha256(descriptor).hexdigest() != DOC_EXAMPLE_FINDING_SHA256:
        return False
    try:
        source = (root / DOC_EXAMPLE_TARGET).read_bytes()
    except OSError:
        return False
    token = source[DOC_EXAMPLE_OFFSET:DOC_EXAMPLE_OFFSET + DOC_EXAMPLE_LENGTH]
    return (
        hashlib.sha256(source).hexdigest() == DOC_EXAMPLE_FILE_SHA256
        and hashlib.sha256(token).hexdigest() == DOC_EXAMPLE_TOKEN_SHA256
        and source[:DOC_EXAMPLE_OFFSET].count(b"\n") + 1 == DOC_EXAMPLE_LINE
    )


def build_summary(report, root):
    if not isinstance(report.get("Results"), list):
        raise RuntimeError("Trivy report has no scan results")
    findings, blocked, secrets, blocking_secrets = [], [], [], []
    example_used = False
    for target in report["Results"]:
        for item in target.get("Vulnerabilities", []):
            finding = {
                "target": target["Target"], "id": item["VulnerabilityID"],
                "package": item["PkgName"], "severity": item["Severity"],
                "installed": item.get("InstalledVersion"),
                "fixed": item.get("FixedVersion", ""),
            }
            findings.append(finding)
            if finding["severity"] in ("HIGH", "CRITICAL") and finding["fixed"]:
                blocked.append(finding)
        for item in target.get("Secrets", []):
            finding = {
                "target": target["Target"], "rule": item["RuleID"],
                "severity": item["Severity"],
                "start_line": item.get("StartLine"),
                "end_line": item.get("EndLine"), "offset": item.get("Offset"),
                "disposition": "blocking",
            }
            if not example_used and reviewed_public_example(root, target, item):
                example_used = True
                finding.update({
                    "disposition": "reviewed_public_example",
                    "reason": DOC_EXAMPLE_REASON,
                    "source_sha256": DOC_EXAMPLE_FILE_SHA256,
                    "token_sha256": DOC_EXAMPLE_TOKEN_SHA256,
                    "finding_sha256": DOC_EXAMPLE_FINDING_SHA256,
                })
            else:
                blocking_secrets.append(finding)
            secrets.append(finding)
    return {
        "scanned_targets": len(report["Results"]), "vulnerabilities": findings,
        "fixable_high_critical": blocked, "secrets": secrets,
        "blocking_secrets": blocking_secrets,
        "policy_passed": not blocked and not blocking_secrets,
    }


def main(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    report = json.loads((root / "results/security/trivy.json").read_bytes())
    summary = build_summary(report, root)
    (root / "results/security/trivy-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if not summary["policy_passed"]:
        raise SystemExit("Trivy policy failed; complete raw findings retained")


if __name__ == "__main__":
    main()
