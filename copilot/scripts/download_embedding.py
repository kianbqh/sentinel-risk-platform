"""Explicit download into G-backed cache, followed by every pinned-file check."""
import argparse
import json
import os
from pathlib import Path

from copilot.retrieval.embedding import MODEL,REVISION,verify_snapshot


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--reference",required=True)
    parser.add_argument("--cache",required=True);parser.add_argument("--receipt",required=True)
    args=parser.parse_args()
    receipt=Path(args.receipt)
    if receipt.exists(): raise FileExistsError(receipt)
    reference=json.loads(Path(args.reference).read_text())
    names=[entry["name"] for entry in reference["files"] if not entry["name"].endswith(".bin")
        and not entry["name"].startswith("onnx/") and entry["name"] not in {".gitattributes","README.md"}]
    from huggingface_hub import snapshot_download
    snapshot=snapshot_download(MODEL,revision=REVISION,cache_dir=args.cache,allow_patterns=names)
    hashes=verify_snapshot(snapshot,reference)
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with receipt.open("x") as handle:
        json.dump({"model":MODEL,"revision":REVISION,"snapshot":snapshot,"file_sha256":hashes},handle,indent=2)
