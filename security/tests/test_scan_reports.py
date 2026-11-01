"""A failed scanner or an unscanned target must never be summarized as zero findings."""
import json

import pytest

from scan.run_dast import validate_report


def reports(tmp_path, sites=None):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"site": sites if sites is not None else [
        {"@name": "http://127.0.0.1:5001", "alerts": []}
    ]}))
    html = tmp_path / "report.html"
    html.write_text("<html><body>Scanner report</body></html>")
    return path, html


@pytest.mark.parametrize("code", [0, 1, 2])
def test_completed_scanner_codes_require_real_reports(tmp_path, code):
    path, html = reports(tmp_path)
    assert validate_report(path, html, "http://127.0.0.1:5001", code)["json_parsed"]


def test_container_start_failure_is_not_zero_findings(tmp_path):
    path, html = reports(tmp_path)
    with pytest.raises(RuntimeError, match="infrastructure"):
        validate_report(path, html, "http://127.0.0.1:5001", 125)


def test_missing_html_is_not_a_completed_scan(tmp_path):
    path, html = reports(tmp_path)
    html.unlink()
    with pytest.raises(RuntimeError, match="HTML"):
        validate_report(path, html, "http://127.0.0.1:5001", 0)


def test_empty_site_list_is_not_zero_findings(tmp_path):
    path, html = reports(tmp_path, [])
    with pytest.raises(RuntimeError, match="no scanned site"):
        validate_report(path, html, "http://127.0.0.1:5001", 0)


def test_unowned_origin_is_rejected_without_network_access(tmp_path):
    path, html = reports(tmp_path, [{"@name": "https://example.invalid", "alerts": []}])
    with pytest.raises(RuntimeError, match="origin"):
        validate_report(path, html, "http://127.0.0.1:5001", 0)
