from riskclf.baseline import build
from riskclf.data import split


def test_split_is_deduplicated_stratified_and_disjoint():
    tr, te = split()
    assert len(set(tr.text) & set(te.text)) == 0
    assert abs(tr.y.mean() - te.y.mean()) < 0.01
    assert len(tr) + len(te) == 5171


def test_tfidf_baseline_learns_a_tiny_problem():
    xs = ["win free cash now", "claim your prize", "free entry txt win", "see you soon", "lunch at noon?", "call me later"]
    ys = [1, 1, 1, 0, 0, 0]
    m = build().set_params(featureunion__word__min_df=1, featureunion__char__min_df=1).fit(xs * 3, ys * 3)
    assert m.predict(["free cash prize"])[0] == 1 and m.predict(["see you at lunch"])[0] == 0
