"""Strict lexical number/identifier membership, with no arithmetic inference.

Decimal formatting, leading plus/zeroes, grouping commas and Unicode minus are
normalized. Percent-marked numbers remain distinct. Currency symbols are kept;
no rounding, percent conversion, fuzzy ID matching or scientific approximation
is allowed. UUIDs, long hex hashes and mixed alphanumeric IDs are atomic tokens.
Membership does not prove semantic correctness or attach a value to its field.
"""
from decimal import Decimal, InvalidOperation
import json
import re

ID = re.compile(r"\b(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?=[0-9a-fA-F]{16,64}\b)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]+|(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]*|(?=[A-Za-z0-9_-]*[A-Za-z])\d[A-Za-z0-9_-]+)\b")
NUMBER = re.compile(r"(?<![\w.])(?P<currency>[$€£]?)(?P<value>[+\-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+\-]?\d+)?)(?P<percent>%?)(?!\w|\.\d|,\d)")


def _decimal_key(number):
    # Decimal.normalize() rounds at the current decimal context precision.
    # Strip coefficient zeroes using its tuple to preserve *all* input digits.
    if not number: return "0"
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop(); exponent += 1
    return ("-" if sign else "")+"".join(map(str,digits))+"e"+str(exponent)


def tokens(text):
    text = str(text)
    result = []
    masked = list(text)
    for match in ID.finditer(text):
        if re.fullmatch(r"\d+[eE][+-]?\d+", match.group()):
            continue
        result.append("id:" + match.group())
        masked[match.start():match.end()] = " " * (match.end()-match.start())
    for match in NUMBER.finditer("".join(masked)):
        try:
            number = Decimal(match["value"].replace(",", "").replace("−", "-"))
            if not number.is_finite():
                continue
            # Decimal equality handles trailing zeroes and scientific notation
            # without a binary float conversion or arbitrary precision rounding.
            result.append("num:" + match["currency"] + _decimal_key(number) + match["percent"])
        except InvalidOperation:
            continue
    return result


def fact_tokens(value):
    if isinstance(value, dict):
        return set(tokens(" ".join(map(str, value.keys())))).union(*(fact_tokens(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(fact_tokens(item) for item in value))
    if value is None or type(value) is bool:
        return set()
    return set(tokens(str(value)))


def check_text(text, facts):
    observed = tokens(text)
    allowed = fact_tokens(facts)
    unknown = sorted(set(observed) - allowed)
    return {"faithful": not unknown, "checked_token_count": len(observed), "unsupported_tokens": unknown}


def draft_text(draft):
    return "\n".join([draft.get("summary", ""), *(item.get("fact", "") for item in draft.get("key_evidence", []) if isinstance(item, dict))])


def check_draft(draft, facts):
    # case_id is checked separately against the immutable requested case_id;
    # confidence is a model self-assessment, not a claimed observed fact.
    return check_text(draft_text(draft), facts)
