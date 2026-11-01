import json
import pytest

from gateway.verification import completed_progress, verify_alert_values, verify_stream
from security.zap_seed import validate_seed


def test_wrong_stream_flag_is_rejected_against_python_reference():
    with pytest.raises(RuntimeError, match="alert flag"):
        verify_stream([{"txnId": 1, "proba": .9, "isAlert": False}], {1: .9}, .8)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.1, 1.1])
def test_nonfinite_or_out_of_range_stream_probability_is_rejected(value):
    with pytest.raises(RuntimeError, match="stream probability"):
        verify_stream([{"txnId": 1, "proba": value, "isAlert": True}], {1: .9}, .8)


def test_matching_flags_and_independent_probability_pass():
    assert verify_stream([{"txnId": 1, "proba": .9, "isAlert": True}], {1: .9}, .8) == (0.0, {"transaction-stream:1"})


def test_duplicate_after_the_first_complete_score_set_is_rejected():
    row = {"txnId": 1, "proba": .9, "isAlert": True}
    with pytest.raises(RuntimeError, match="duplicate stream score IDs"):
        verify_stream([row, row], {1: .9}, .8)


def test_progress_must_include_the_terminal_flush_input():
    first = {"batchId": 0, "numInputRows": 1, "sources": [{"endOffset": {"input": {"0": 1}}}]}
    final = {"batchId": 1, "numInputRows": 1, "sources": [{"endOffset": {"input": {"0": 2}}}]}
    assert completed_progress([first], "input", 2, 2) is None
    assert completed_progress([first, final], "input", 2, 2)["cumulative_input_rows"] == 2


@pytest.mark.parametrize("key", ["risk_score", "threshold"])
def test_corruption_shared_by_wire_and_console_is_rejected(key):
    reference = {"a": {"source": "ato", "risk_score": .9, "threshold": .8}}
    event = {"alert_id": "a", **reference["a"], key: .7}
    console = {"a": {"id": "a", "source": "ato", "riskScore": event["risk_score"], "threshold": event["threshold"]}}
    with pytest.raises(RuntimeError, match="producer reference"):
        verify_alert_values([event], console, reference)


def seed(body, status="200 OK"):
    return [{"responseHeader": "HTTP/1.1 " + status + "\r\n", "responseBody": json.dumps(body)}]


@pytest.mark.parametrize("response", [
    seed({"data": {"me": None}, "errors": [{"message": "unauthorized"}]}),
    seed({"data": {"me": {"id": "other"}}}),
    seed({"data": {"me": {"id": "42"}}}, "401 Unauthorized"),
])
def test_failed_zap_authentication_is_rejected(response):
    with pytest.raises(RuntimeError):
        validate_seed(response, "42")


def test_zap_seed_requires_the_registered_analyst():
    assert validate_seed(seed({"data": {"me": {"id": "42"}}}), "42") == "42"
