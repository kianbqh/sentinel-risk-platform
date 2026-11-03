"""Pattern redaction at every model and memory boundary.

This deliberately does not claim general entity recognition. Values at known
email/card/phone/IP keys are removed even when they do not match a pattern.
Decimals and scientific notation are protected from phone/card false positives.
"""
import ipaddress
import re

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
CARD = re.compile(r"(?<![\w.])(?:\d[ -]?){12,18}\d(?![\w.]|[eE][+-]?\d)")
PHONE = re.compile(r"(?<![\w.])\+?(?:\d[ ()-]*){9,11}\d(?![\w.]|[eE][+-]?\d)")
SENSITIVE_KEYS = {"email", "email_address", "phone", "phone_number", "mobile", "card", "card_number", "pan", "ip", "ip_address", "client_ip", "home_ip"}


def _valid_ip(match):
    try:
        ipaddress.ip_address(match.group())
        return True
    except ValueError:
        return False


def findings(text):
    """Nonoverlapping pattern hits; never returns the sensitive strings."""
    text = str(text)
    result = []
    occupied = []
    for kind, pattern in (("email", EMAIL), ("ipv4", IPV4), ("ipv6", IPV6), ("card", CARD), ("phone", PHONE)):
        for match in pattern.finditer(text):
            if kind.startswith("ipv") and not _valid_ip(match):
                continue
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            occupied.append(match.span())
            result.append({"kind": kind, "start": match.start(), "end": match.end()})
    return result


def redact_text(text):
    text = str(text)
    for hit in sorted(findings(text), key=lambda hit: hit["start"], reverse=True):
        text = text[:hit["start"]] + "[REDACTED_" + hit["kind"].upper() + "]" + text[hit["end"]:]
    return text


def redact(value):
    """Recursively redact keys and values without mutating a tool response."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(key)): "[REDACTED_FIELD]" if str(key).casefold() in SENSITIVE_KEYS else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
