"""Check the shipped serving artifacts before starting a fresh checkout."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def verify():
    manifest=json.loads((ROOT/"contracts/artifacts.json").read_text())
    for item in manifest["files"]:
        path=ROOT/item["path"]
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=item["sha256"]:raise RuntimeError(f"Artifact checksum mismatch: {item['path']}")
        print(f"verified {item['path']}")


if __name__=="__main__":verify()
