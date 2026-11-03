"""Actual bitsandbytes NF4 and assistant-only supervised LoRA training."""
from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path

from .protocol import sha256_file


def seed_everything(seed):
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    import numpy as np
    import torch
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True, warn_only=True)


def check_model_files(repo: Path, config: dict):
    manifest = json.loads((repo / "text-risk/finetune/protocol/qwen_reference.json").read_text(encoding="utf-8"))
    model_path = Path(config["model"]["local_path"])
    if model_path.resolve().is_relative_to(repo.resolve()):
        raise ValueError("model weights must remain outside the repository")
    records = []
    for item in manifest["files"]:
        if item["name"] not in manifest["download_files"]:
            continue
        path = model_path / item["name"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise ValueError(f"missing/wrong-size pinned model file: {item['name']}")
        digest = sha256_file(path)
        if item["lfs_sha256"] and digest != item["lfs_sha256"]:
            raise ValueError(f"model SHA256 mismatch: {item['name']}")
        if not item["lfs_sha256"]:
            import hashlib
            content = path.read_bytes()
            blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
            if blob != item["git_blob_sha1"]:
                raise ValueError(f"model Git blob mismatch: {item['name']}")
        records.append({"name": item["name"], "bytes": path.stat().st_size, "sha256": digest})
    if len(records) != len(manifest["download_files"]):
        raise ValueError("model reference manifest incomplete")
    return records


def load_nf4(config: dict, training=False, adapter=None):
    import torch
    import bitsandbytes as bnb
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    if not torch.cuda.is_available():
        raise RuntimeError("this protocol requires actual CUDA NF4; no CPU or unquantized fallback")
    dtype = getattr(torch, config["model"]["compute_dtype"])
    if dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported():
        raise RuntimeError("configured BF16 unsupported; revise config explicitly, never silently downgrade")
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                              bnb_4bit_use_double_quant=config["model"]["double_quant"], bnb_4bit_compute_dtype=dtype)
    location = config["model"]["local_path"]
    tokenizer = AutoTokenizer.from_pretrained(location, local_files_only=True, trust_remote_code=False)
    tokenizer.padding_side = "right"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(location, local_files_only=True, trust_remote_code=False,
             quantization_config=quant, device_map={"": 0}, dtype=dtype,
             attn_implementation=config["model"]["attn_implementation"])
    quant_layers = [m for m in model.modules() if isinstance(m, bnb.nn.Linear4bit)]
    if not getattr(model, "is_loaded_in_4bit", False) or not quant_layers:
        raise RuntimeError("model did not load real bitsandbytes Linear4bit layers")
    if any(getattr(m.weight, "quant_type", None) != "nf4" or m.weight.quant_state is None for m in quant_layers):
        raise RuntimeError("missing materialized NF4 quantization state")
    if any(parameter.device.type != "cuda" for parameter in model.parameters()):
        raise RuntimeError("unexpected offload; all model parameters must be on CUDA")
    # Match the k-bit preparation casts for prompt-only, training, and adapter
    # reload. In particular, PEFT casts non-quantized parameters to float32.
    model = prepare_model_for_kbit_training(model,
            use_gradient_checkpointing=training and config["training"]["gradient_checkpointing"],
            gradient_checkpointing_kwargs={"use_reentrant": False})
    if training:
        model.config.use_cache = False
        settings = config["training"]
        model = get_peft_model(model, LoraConfig(r=settings["lora_r"], lora_alpha=settings["lora_alpha"],
                lora_dropout=settings["lora_dropout"], target_modules=settings["target_modules"],
                task_type="CAUSAL_LM", bias="none"))
        trainable = [(name, p) for name, p in model.named_parameters() if p.requires_grad]
        if not trainable or any("lora_" not in name for name, _ in trainable):
            raise RuntimeError("training must update only LoRA adapters")
    elif adapter is not None:
        model = PeftModel.from_pretrained(model, str(adapter), local_files_only=True, is_trainable=False)
    model.config.use_cache = not training
    model.eval() if not training else model.train()
    return model, tokenizer, {"linear4bit_layers": len(quant_layers),
                            "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
                            "total_parameters_numel": sum(p.numel() for p in model.parameters()),
                            "quant_type": "nf4", "compute_dtype": config["model"]["compute_dtype"],
                            "device_map": {"": 0}}


def prompt_ids(tokenizer, text: str, config: dict):
    tokens = tokenizer.encode(text, add_special_tokens=False)
    truncated = len(tokens) > config["prompt"]["max_user_tokens"]
    if truncated:
        text = tokenizer.decode(tokens[:config["prompt"]["max_user_tokens"]], skip_special_tokens=False)
    messages = [{"role": "system", "content": config["prompt"]["system"]}, {"role": "user", "content": text}]
    # Transformers5 defaults return_dict=True; explicitly request the flat token
    # list used by masking/generation instead of slicing BatchEncoding keys.
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)
    require_flat_token_ids(ids)
    if len(ids) + config["evaluation"]["max_new_tokens"] > config["prompt"]["max_sequence_tokens"]:
        raise ValueError("prompt exceeds fixed sequence budget; revise protocol before fitting")
    return ids, messages, truncated


def require_flat_token_ids(ids):
    if not isinstance(ids, list) or not ids or any(type(token) is not int for token in ids):
        raise ValueError("chat template tokenize=True, return_dict=False must return a nonempty flat list[int]")


def encode_example(tokenizer, row, config):
    prefix, messages, truncated = prompt_ids(tokenizer, row["text"], config)
    target = json.dumps({"label": "scam" if row["y"] else "legit"}, separators=(",", ":"))
    full = tokenizer.apply_chat_template([*messages, {"role": "assistant", "content": target}],
                                         tokenize=True, add_generation_prompt=False, return_dict=False)
    require_flat_token_ids(full)
    if full[:len(prefix)] != prefix or len(full) <= len(prefix):
        raise ValueError("chat template does not preserve the masked prompt prefix")
    if len(full) > config["prompt"]["max_sequence_tokens"]:
        raise ValueError("training target would be truncated")
    return {"input_ids": full, "attention_mask": [1] * len(full),
            "labels": [-100] * len(prefix) + full[len(prefix):]}, truncated


class EncodedDataset:
    def __init__(self, tokenizer, rows, config):
        encoded = [encode_example(tokenizer, row, config) for row in rows]
        self.rows = [value for value, _ in encoded]
        self.truncated_messages = sum(truncated for _, truncated in encoded)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]


class AssistantCollator:
    def __init__(self, pad_token_id):
        self.pad = pad_token_id

    def __call__(self, rows):
        import torch
        n = max(len(row["input_ids"]) for row in rows)
        result = {}
        for key, fill in (("input_ids", self.pad), ("attention_mask", 0), ("labels", -100)):
            result[key] = torch.tensor([row[key] + [fill] * (n - len(row[key])) for row in rows], dtype=torch.long)
        return result


def parse_binary_json(raw: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        return None, "invalid_json"
    if not isinstance(value, dict) or set(value) != {"label"} or value["label"] not in ("scam", "legit"):
        return None, "schema"
    return int(value["label"] == "scam"), None
