"""Deterministic fallback used when the LLM times out, errors, or returns invalid JSON."""
import re

from .schema import LlmVerdict

_PATTERNS = [
    (re.compile(r"\b(won|winner|win|prize|award|cash|£\d+|\$\d+|claim)\b", re.I), 2.0, "prize_or_lottery"),
    (re.compile(r"\b(free|txt|text|reply|stop|subscri\w*|ringtone|per msg|150p|\d+p\b)", re.I), 1.0,
     "premium_rate_or_subscription"),
    (re.compile(r"\b(urgent|verify|account|password|bank|suspend\w*|security|login)\b", re.I), 1.5, "phishing_or_account"),
    (re.compile(r"(https?://|www\.|\.com\b|\.co\.uk\b)", re.I), 1.5, "phishing_or_account"),
    (re.compile(r"\b0?8\d{9}\b|\b\d{5}\b"), 1.5, "premium_rate_or_subscription"),  # premium/short codes
    (re.compile(r"\b(offer|discount|voucher|mobile|camera|latest|guaranteed)\b", re.I), 1.0, "marketing"),
]
THRESHOLD = 3.0


def score(text: str) -> tuple[float, str]:
    total, hits = 0.0, {}
    for rx, w, cat in _PATTERNS:
        n = len(rx.findall(text))
        if n:
            total += w * min(n, 2)
            hits[cat] = hits.get(cat, 0.0) + w * min(n, 2)
    return total, (max(hits, key=hits.get) if hits else "personal")


def classify(text: str) -> LlmVerdict:
    s, cat = score(text)
    if s >= THRESHOLD:
        return LlmVerdict(label="scam", category=cat, reason=f"rule score {s:.1f} >= {THRESHOLD}")
    return LlmVerdict(label="legit", category="personal" if s == 0 else "other", reason=f"rule score {s:.1f} < {THRESHOLD}")
