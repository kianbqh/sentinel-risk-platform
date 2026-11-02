"""Allow-listed raw measurements only: no outcomes, predictions, IDs or prose."""
import hashlib
import json
import math

from copilot.mcp_server.contracts import FEATURE_NAMES

ATO_FEATURES = ("n", "n_fail", "fail_ratio", "n_users", "users_per_attempt", "unknown_user_ratio", "n_success",
    "iat_median", "iat_cv", "n_ua", "ua_tool", "max_fail_one_user", "ip_hist_users")


def evidence_text(domain, evidence):
    names = FEATURE_NAMES if domain == "card" else ATO_FEATURES if domain == "ato" else None
    if names is None or any(key not in evidence for key in names):
        raise ValueError("Incomplete or unsupported raw evidence")
    values = {key: float(evidence[key]) for key in names}
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("Non-finite raw evidence")
    # Domain text is constant within a domain. Gold/predictions, entity IDs,
    # alert IDs, model names, summaries and synthetic campaign kind are omitted.
    return domain + " measurements: " + "; ".join(f"{key}={values[key]:.12g}" for key in names)


def record(document_id, domain, entity_key, evidence, label):
    if type(label) is not int or label not in (0, 1):
        raise ValueError("Ground truth must be binary")
    text = evidence_text(domain, evidence)
    return {"document_id": document_id, "domain": domain, "entity_key": entity_key,
        "evidence_text": text, "evidence_sha256": hashlib.sha256(text.encode()).hexdigest(), "gold_label": label}


def fixed_split(records, seed, query_per_cell, gallery_per_cell):
    import random
    by_cell = {(domain, label): [] for domain in ("card", "ato") for label in (0, 1)}
    seen_ids, seen_hashes = set(), set()
    for row in sorted(records, key=lambda v: v["document_id"]):
        if row["document_id"] in seen_ids or row["evidence_sha256"] in seen_hashes:
            continue
        seen_ids.add(row["document_id"])
        seen_hashes.add(row["evidence_sha256"])
        by_cell[(row["domain"], row["gold_label"])].append(row)
    result, used_entities = [], set()
    rng = random.Random(seed)
    for cell, choices in by_cell.items():
        rng.shuffle(choices)
        selected = []
        for row in choices:
            if row["entity_key"] not in used_entities:
                selected.append(row)
                used_entities.add(row["entity_key"])
            if len(selected) == query_per_cell+gallery_per_cell:
                break
        if len(selected) < query_per_cell+gallery_per_cell:
            raise ValueError(f"Insufficient independent ground-truth entities for {cell}")
        result.extend({**row, "split": "query" if i < query_per_cell else "gallery"} for i, row in enumerate(selected))
    return sorted(result, key=lambda v: v["document_id"])


def validate_protocol(rows):
    ids, entities, hashes = set(), set(), set()
    counts = {}
    for row in rows:
        if row["domain"] not in {"card","ato"} or type(row["gold_label"]) is not int or row["gold_label"] not in (0,1) or row["split"] not in {"query","gallery"}:
            raise ValueError("Unsupported protocol domain, split or ground truth")
        if row["document_id"] in ids or row["entity_key"] in entities or row["evidence_sha256"] in hashes:
            raise ValueError("Duplicate document, entity or evidence in fixed protocol")
        ids.add(row["document_id"]); entities.add(row["entity_key"]); hashes.add(row["evidence_sha256"])
        if hashlib.sha256(row["evidence_text"].encode()).hexdigest() != row["evidence_sha256"]:
            raise ValueError("Evidence hash mismatch")
        cell = (row["split"], row["domain"], row["gold_label"])
        counts[cell] = counts.get(cell, 0)+1
    for split in ("query", "gallery"):
        cells = [counts.get((split, domain, label), 0) for domain in ("card", "ato") for label in (0,1)]
        if min(cells) < 5 or len(set(cells)) != 1:
            raise ValueError("Protocol must be balanced across domain and label, with at least five per cell")
    return {f"{a}/{b}/{c}": n for (a,b,c),n in sorted(counts.items())}
