"""Pinned local safetensors model, deterministic CPU embedding by default."""
import hashlib
from pathlib import Path

MODEL = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
WEIGHT_SHA256 = "3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad"


def verify_snapshot(snapshot, reference):
    snapshot = Path(snapshot)
    if reference["model"] != MODEL or reference["revision"] != REVISION:
        raise ValueError("Unsupported embedding reference")
    verified = {}
    for entry in reference["files"]:
        name = entry["name"]
        if name.endswith(".bin") or name.startswith("onnx/") or name in {".gitattributes", "README.md"}:
            continue
        raw = (snapshot/name).read_bytes()
        if len(raw) != entry["bytes"]:
            raise ValueError("Embedding file size mismatch: " + name)
        digest = hashlib.sha256(raw).hexdigest()
        if entry["lfs_sha256"]:
            if digest != entry["lfs_sha256"]:
                raise ValueError("Embedding SHA-256 mismatch: " + name)
        elif hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != entry["git_blob_sha1"]:
            raise ValueError("Embedding Git blob mismatch: " + name)
        verified[name] = digest
    if verified.get("model.safetensors") != WEIGHT_SHA256:
        raise ValueError("Pinned embedding weights unavailable")
    return verified


class LocalEmbedder:
    def __init__(self, snapshot, reference, device="cpu", seed=42):
        self.hashes = verify_snapshot(snapshot, reference)
        import random
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
        random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.use_deterministic_algorithms(True)
        self.model = SentenceTransformer(str(snapshot), device=device, local_files_only=True,
            model_kwargs={"use_safetensors": True})
        self.model.max_seq_length = 512
        if self.model.get_sentence_embedding_dimension() != 384:
            raise ValueError("Unexpected embedding dimension")

    def encode(self, texts, batch_size=16):
        return self.model.encode(texts, batch_size=batch_size, show_progress_bar=False,
            normalize_embeddings=True, convert_to_numpy=True).astype("float32")
