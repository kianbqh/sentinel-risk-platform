#!/usr/bin/env bash
# Prepared only: no download, no automatic resume, no implicit smoke rerun.
set -euo pipefail
if [[ "${M1A_TRAINING_WINDOW:-}" != reserved ]]; then
  printf '%s\n' 'Root must release the formal training/evaluation window.' >&2
  exit 2
fi
if [[ ! "${M1A_EXPECTED_SHA:-}" =~ ^[0-9a-f]{40}$ || -z "${M1A_SMOKE_RUN:-}" ]]; then
  printf '%s\n' 'Supply the reviewed checkpoint and the already accepted smoke run.' >&2
  exit 2
fi
M1A_RUNTIME=/opt/codex-resume/all-modules-v2/m1a
REPO=/mnt/g/ResumeProject/platforms/sentinel-risk-platform
HELPERS=/mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/training
PY="$M1A_RUNTIME/train-venv/bin/python"
export FINETUNE_GIT="$M1A_RUNTIME/tools/git-native/usr/bin/git"
export PATH="/usr/lib/wsl/lib:$PATH"
export GIT_OPTIONAL_LOCKS=0
export HF_HOME="$M1A_RUNTIME/cache/huggingface"
export PIP_CACHE_DIR="$M1A_RUNTIME/cache/pip"
export XDG_CACHE_HOME="$M1A_RUNTIME/cache/xdg"
export TORCH_HOME="$M1A_RUNTIME/cache/torch"
export CUDA_CACHE_PATH="$M1A_RUNTIME/cache/cuda"
export TMPDIR="$M1A_RUNTIME/tmp"
export HF_HUB_OFFLINE=1
export CUBLAS_WORKSPACE_CONFIG=:4096:8
export TOKENIZERS_PARALLELISM=false
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=42
test -x "$PY"
test -x "$FINETUNE_GIT"
cd "$REPO"
ACTUAL_SHA="$("$FINETUNE_GIT" --no-optional-locks -C "$REPO" rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$M1A_EXPECTED_SHA" ]]; then
  printf 'Checkpoint mismatch: expected %s, actual %s\n' "$M1A_EXPECTED_SHA" "$ACTUAL_SHA" >&2
  exit 3
fi
CFG=text-risk/finetune/configs/qwen15_nf4.json
"$PY" - <<'PY'
import hashlib, importlib.metadata as md, json, os, pathlib
smoke=pathlib.Path(os.environ['M1A_SMOKE_RUN']).resolve()
runtime=pathlib.Path('/opt/codex-resume/all-modules-v2/m1a/runs').resolve()
assert smoke.is_relative_to(runtime) and smoke.name=='smoke'
summary=json.loads((smoke/'summary.json').read_text())
assert summary['status']=='smoke_passed'
proof=summary['provenance']
assert proof['git_sha']==os.environ['M1A_EXPECTED_SHA'], 'Fresh accepted smoke required at this exact HEAD'
assert proof['config_sha256']==hashlib.sha256(pathlib.Path('text-risk/finetune/configs/qwen15_nf4.json').read_bytes()).hexdigest()
assert proof['protocol_sha256']==hashlib.sha256(pathlib.Path('text-risk/finetune/protocol/sms_split.json').read_bytes()).hexdigest()
for name,version in proof['versions'].items():
    assert md.version(name)==version, (name,'environment changed since smoke')
print('Accepted smoke checkpoint/config/protocol/package versions match; each action will also enforce full current-source identity.')
PY
GPU_APPS="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"
if [[ -n "$GPU_APPS" ]]; then
  printf 'Other GPU compute processes are present; root must inspect before formal timing:\n%s\n' "$GPU_APPS" >&2
  exit 4
fi
mkdir -p "$M1A_RUNTIME/runs" "$CUDA_CACHE_PATH"
GROUP="$M1A_RUNTIME/runs/continuation-$(date -u +%Y%m%dT%H%M%SZ)-$("$PY" -c 'import uuid; print(uuid.uuid4().hex[:8])')"
mkdir -m 700 "$GROUP"
mkdir "$GROUP/logs"
printf '%s\n' "$GROUP" | tee "$GROUP/run-group.txt"
export M1A_CONTINUATION_GROUP="$GROUP"
"$PY" - <<'PY'
import datetime as dt, hashlib, json, os, pathlib
group=pathlib.Path(os.environ['M1A_CONTINUATION_GROUP'])
smoke=pathlib.Path(os.environ['M1A_SMOKE_RUN'])
helper=pathlib.Path('/mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/training')
files=[pathlib.Path('text-risk/finetune/configs/qwen15_nf4.json'),pathlib.Path('text-risk/finetune/requirements-lock.txt'),
       helper/'continue_training.sh',helper/'run_continuation_stage.py',smoke/'summary.json']
result={'started_at':dt.datetime.now(dt.timezone.utc).isoformat(), 'expected_git_sha':os.environ['M1A_EXPECTED_SHA'],
        'accepted_smoke_run':str(smoke),'scope':'three training seeds then matched model evaluations and paired comparison; no download',
        'files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
        'environment':{k:os.environ.get(k) for k in ['FINETUNE_GIT','HF_HOME','PIP_CACHE_DIR','XDG_CACHE_HOME','TORCH_HOME','CUDA_CACHE_PATH','TMPDIR','HF_HUB_OFFLINE','CUBLAS_WORKSPACE_CONFIG']}}
with (group/'launcher.json').open('x') as f:
    json.dump(result,f,indent=2); f.write('\n')
PY
run_stage() {
  local NAME="$1"
  shift
  "$PY" "$HELPERS/run_continuation_stage.py" --group "$GROUP" --stage "$NAME" -- "$@"
}
run_stage pip-check "$PY" -m pip check
run_stage gpu-before nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,memory.used --format=csv
for SEED in 42 43 44; do
  export PYTHONHASHSEED="$SEED"
  run_stage "train-$SEED" "$PY" -u text-risk/finetune/run.py train --config "$CFG" --seed "$SEED" \
    --smoke-run "$M1A_SMOKE_RUN" --run-dir "$GROUP/train-$SEED"
done
export PYTHONHASHSEED=42
run_stage prompt-only "$PY" -u text-risk/finetune/run.py evaluate --config "$CFG" \
  --smoke-run "$M1A_SMOKE_RUN" --run-dir "$GROUP/prompt-only"
for SEED in 42 43 44; do
  run_stage "eval-$SEED" "$PY" -u text-risk/finetune/run.py evaluate --config "$CFG" \
    --smoke-run "$M1A_SMOKE_RUN" --adapter-run "$GROUP/train-$SEED" --run-dir "$GROUP/eval-$SEED"
done
run_stage comparison "$PY" -u text-risk/finetune/run.py compare --config "$CFG" --run-dir "$GROUP/comparison" \
  --prompt-run "$GROUP/prompt-only" --seed-run "$GROUP/eval-42" --seed-run "$GROUP/eval-43" --seed-run "$GROUP/eval-44"
run_stage gpu-after nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,memory.used --format=csv
"$PY" - <<'PY'
import datetime as dt, hashlib, json, os, pathlib
group=pathlib.Path(os.environ['M1A_CONTINUATION_GROUP'])
summary=json.loads((group/'comparison/summary.json').read_text())
assert summary['status']=='compared'
assert summary['provenance']['git_sha']==os.environ['M1A_EXPECTED_SHA']
files=[p for p in sorted(group.rglob('*')) if p.is_file() and 'adapter' not in p.relative_to(group).parts and 'checkpoints' not in p.relative_to(group).parts]
result={'status':'completed','finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'expected_git_sha':os.environ['M1A_EXPECTED_SHA'],'accepted_smoke_run':os.environ['M1A_SMOKE_RUN'],
        'files':[{'path':p.relative_to(group).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
        'large_artifact_note':'Adapters/checkpoint hashes remain in each training summary; weights never copied into Git.'}
with (group/'CONTINUATION_COMPLETE.json').open('x') as f:
    json.dump(result,f,indent=2); f.write('\n')
print(json.dumps({'status':result['status'],'group':str(group),'summary':str(group/'comparison/summary.json')}))
PY
