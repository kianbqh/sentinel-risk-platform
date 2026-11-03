"""Config-driven entrypoint; all actions require an exclusive private run path."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Supports running directly from the flagship root without installing a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finetune.artifacts import OwnedRun, load_config, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "smoke", "train", "evaluate", "compare"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, choices=(42, 43, 44))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--smoke-run", type=Path)
    parser.add_argument("--adapter-run", type=Path)
    parser.add_argument("--prompt-run", type=Path)
    parser.add_argument("--seed-run", action="append", type=Path, default=[])
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    if args.resume and args.action != "train":
        parser.error("resume applies only to an owned training run")
    if args.action == "train" and (args.seed is None or args.smoke_run is None):
        parser.error("training requires --seed and an actual passed --smoke-run")
    if args.action == "evaluate" and args.smoke_run is None:
        parser.error("evaluation requires an actual passed --smoke-run")
    if args.action == "compare" and (args.prompt_run is None or len(args.seed_run) != 3):
        parser.error("comparison requires --prompt-run and exactly three --seed-run arguments")
    repo = args.repo.resolve()
    # PYTHONHASHSEED is fixed before Python starts; setting it inside the process
    # would not change this interpreter's already-initialized hash randomization.
    expected_seed = args.seed if args.action == "train" else config["evaluation"]["generation_seed"]
    if args.action in ("smoke", "train", "evaluate") and os.environ.get("PYTHONHASHSEED") != str(expected_seed):
        raise RuntimeError(f"launch Python with PYTHONHASHSEED={expected_seed}")
    torch = None
    if args.action in ("smoke", "train", "evaluate"):
        import torch
    proof = provenance(repo, args.config.resolve(), config, torch)
    run = OwnedRun(args.run_dir, config, proof, args.action, args.seed, args.resume)
    error = None
    try:
        if args.action == "compare":
            from finetune.comparison import compare
            compare(repo, config, run, args.prompt_run, args.seed_run)
        else:
            from finetune import experiment
            if args.action == "download":
                experiment.download_model(repo, config, run)
            elif args.action == "smoke":
                experiment.smoke(repo, config, run)
            elif args.action == "train":
                experiment.train(repo, config, run, args.seed, args.smoke_run, args.resume)
            elif args.action == "evaluate":
                experiment.evaluate(repo, config, run, args.adapter_run, args.smoke_run)
    except BaseException as caught:
        error = caught
        raise
    finally:
        run.close(error)


if __name__ == "__main__":
    main()
