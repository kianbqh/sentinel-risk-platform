"""Fixed identities and a train-only validation partition; no ML dependencies."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def text_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def order_hash(values: list) -> str:
    # Matches the original evaluator/preflight's json.dumps encoding exactly.
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def load_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    ids = value["identifiers"]
    full, paired = ids["full1035_ids"], ids["paired400_ids"]
    if len(full) != 1035 or len(paired) != 400 or len(set(full)) != len(full):
        raise ValueError("unexpected frozen test identities")
    if len(set(paired)) != 400 or not set(paired) <= set(full):
        raise ValueError("paired IDs must be unique held-out IDs")
    for name, seq in (("full1035", full), ("paired400", paired)):
        labels = ids[f"{name}_labels"]
        if len(labels) != len(seq) or any(type(x) is not int or x not in (0, 1) for x in labels):
            raise ValueError("invalid frozen labels")
    return value


def load_corpus(repo: Path, config: dict) -> tuple[list[dict], dict]:
    manifest = load_manifest(repo / config["data"]["manifest"])
    raw_path = repo / config["data"]["raw_path"]
    expected = manifest["dataset"]
    if sha256_file(raw_path) != expected["dataset_file_sha256"]:
        raise ValueError("raw SMS hash differs from frozen protocol")
    rows, seen, raw_n = [], set(), 0
    with raw_path.open(encoding="utf-8", newline="") as stream:
        for row in csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE):
            raw_n += 1
            if len(row) != 2 or row[0] not in ("ham", "spam"):
                raise ValueError("unexpected SMS row")
            raw_label, text = row
            if text in seen:
                continue
            seen.add(text)
            rows.append({"id": text_id(text), "text": text, "y": int(raw_label == "spam")})
    if raw_n != expected["raw_rows"] or len(rows) != expected["unique_rows"]:
        raise ValueError("unexpected corpus count")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("message identity collision")
    by_id = {r["id"]: r for r in rows}
    for name in ("full1035", "paired400"):
        entries = [by_id[i] for i in manifest["identifiers"][f"{name}_ids"]]
        if [r["y"] for r in entries] != manifest["identifiers"][f"{name}_labels"]:
            raise ValueError("frozen message labels differ")
        if order_hash([r["text"] for r in entries]) != manifest["identifiers"][f"{name}_text_order_sha256"]:
            raise ValueError("frozen message text/order differs")
    return rows, manifest


def partition_training(rows: list[dict], heldout_ids: set[str], validation_n: int, seed: int):
    """Largest-remainder stratification, seeded SHA rank; never uses held-out text."""
    available = [r for r in rows if r["id"] not in heldout_ids]
    if not 0 < validation_n < len(available):
        raise ValueError("invalid train-only validation size")
    groups = {y: [r for r in available if r["y"] == y] for y in (0, 1)}
    expected = {y: validation_n * len(g) / len(available) for y, g in groups.items()}
    counts = {y: math.floor(v) for y, v in expected.items()}
    for y in sorted(groups, key=lambda y: (-(expected[y] - counts[y]), y))[:validation_n - sum(counts.values())]:
        counts[y] += 1
    selected = set()
    for y, group in groups.items():
        ranked = sorted(group, key=lambda r: (text_id(f"{seed}:{r['id']}"), r["id"]))
        selected.update(r["id"] for r in ranked[:counts[y]])
    train = [r for r in available if r["id"] not in selected]
    validation = [r for r in available if r["id"] in selected]
    if ({r["id"] for r in train} | selected) & heldout_ids:
        raise AssertionError("held-out leakage")
    return train, validation


def training_data(repo: Path, config: dict):
    rows, manifest = load_corpus(repo, config)
    heldout = set(manifest["identifiers"]["full1035_ids"])
    train, val = partition_training(rows, heldout, config["data"]["validation_rows"], config["data"]["validation_seed"])
    if len(train) + len(val) != 4136 or sum(r["y"] for r in train + val) != 522:
        raise ValueError("training membership differs from original seed-42 split")
    # Held-out messages are never returned to the fitting/selection code.
    return train, val, manifest


def evaluation_data(repo: Path, config: dict):
    rows, manifest = load_corpus(repo, config)
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in manifest["identifiers"]["full1035_ids"]], manifest


def partition_record(train: list[dict], validation: list[dict], config: dict) -> dict:
    return {"method": "train-only largest-remainder stratification; SHA256(seed:message_id) rank",
            "validation_seed": config["data"]["validation_seed"],
            "training_source_order": "original deduplicated corpus order; original split membership",
            "train_ids": [r["id"] for r in train], "validation_ids": [r["id"] for r in validation],
            "train_rows": len(train), "validation_rows": len(validation),
            "train_scam": sum(r["y"] for r in train), "validation_scam": sum(r["y"] for r in validation)}
