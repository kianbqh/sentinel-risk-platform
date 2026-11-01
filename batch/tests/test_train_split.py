import pandas as pd
import pytest

from fraudml import train


def test_validation_keeps_equal_timestamps_together(monkeypatch):
    source = pd.DataFrame({"Time": [7, 0, 8, 7, 6, 1, 2, 3, 4, 5], "label": [0] * 10})
    held_out = pd.DataFrame({"Time": [11, 10], "label": [0, 1]})
    monkeypatch.setattr(
        train.pd, "read_parquet",
        lambda path: source.copy() if path.name == "train" else held_out.copy(),
    )

    fitting, validation, test = train.load_splits()

    assert fitting["Time"].max() < validation["Time"].min() < test["Time"].min()
    assert fitting["Time"].eq(7).sum() == 2
    assert validation["Time"].tolist() == [8]
    assert len(fitting) + len(validation) == len(source)
    assert test["Time"].tolist() == [10, 11]


def test_validation_requires_a_later_timestamp(monkeypatch):
    source = pd.DataFrame({"Time": [7, 7, 7], "label": [0, 1, 0]})
    monkeypatch.setattr(train.pd, "read_parquet", lambda path: source.copy())

    with pytest.raises(ValueError, match="distinct timestamps"):
        train.load_splits()
