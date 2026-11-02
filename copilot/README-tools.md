# Read-only case tools and similar evidence

I use five small tools to keep the analyst copilot close to evidence already in
Sentinel. The tool server can read a case, read its audit history, score a complete
transaction vector, explain a supported transaction score and find similar case
evidence. Assignments, labels and resolutions remain analyst actions.

The transport is the [official MCP Python SDK v2](https://github.com/modelcontextprotocol/python-sdk)
over Streamable HTTP. A separate local opaque service token carries per-tool
scopes; the console's analyst JWT is not accepted. This demonstrates a local
resource server with a token verifier. It does not deploy an external OAuth
provider or a public identity system.

## Setup

Run commands from the Sentinel repository root in WSL2. Keep the private runtime,
caches and weights on the existing G-backed WSL disk. These are opt-in steps;
the original Compose stack and original migrations remain usable on plain PG17.

```bash
M1A_RUNTIME=/opt/codex-resume/all-modules-v2/m1a
export HF_HOME="$M1A_RUNTIME/cache/huggingface"
export PIP_CACHE_DIR="$M1A_RUNTIME/cache/pip"
export XDG_CACHE_HOME="$M1A_RUNTIME/cache/xdg"
export TORCH_HOME="$M1A_RUNTIME/cache/torch"
export TMPDIR="$M1A_RUNTIME/tmp"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR"
python3 -m venv "$M1A_RUNTIME/agent-venv"
"$M1A_RUNTIME/agent-venv/bin/python" -m pip install -r copilot/requirements-tools.txt
"$M1A_RUNTIME/agent-venv/bin/python" -m pip check
```

The direct dependency file is a pinned candidate list, not a resolved lock or
evidence of compatibility. Save the installed package freeze with actual run
evidence after testing. The embedding dependency may install Torch; coordinate
it with the training owner and keep shared environments read-only.

Generate fresh short-lived credentials into a private, ignored G: directory.
The generator prints paths only. Never commit the token, registry, database
passwords, DSNs, `.env` files or expanded shell environment.

```bash
python -m copilot.scripts.create_credentials \
  --output /mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01
python -m copilot.scripts.write_stack_env \
  --credentials /mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01 \
  --secrets-bind /run/desktop/mnt/host/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01
set -a
. /mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01/stack.env
set +a
# Let the unchanged Go backend initialize its own original migration first.
docker compose -f copilot/compose.copilot.yml up -d postgres backend
# Wait for http://127.0.0.1:18074/readyz before adding the copilot migration.
```

The opt-in Sentinel stack uses one shared PG17+pgvector database and named volume
for its Go backend, MCP reader, indexer and controlled draft writer. It also
runs the actual gateway and console. Its database port is15449, backend18074,
gateway18079 and console13049. Existing named data volumes are not reused.
Do not point an Alpine PostgreSQL volume at this image. Record
the pulled image digest in runtime evidence; it can be pinned through
`COPILOT_PGVECTOR_IMAGE`. Use the daemon and bind mapping verified for the local
machine. No global Docker setting is required by this module.

Populate environment variables `COPILOT_ADMIN_DSN`, `COPILOT_READER_DSN`,
`COPILOT_WRITER_DSN` and `COPILOT_INDEXER_DSN` from the private password files.
Use distinct login users `copilot_admin`, `copilot_local_reader`,
`copilot_local_writer` and `copilot_local_indexer` on database `sentinel_copilot`.
The DSNs are never config-file fields or measurement output.

```bash
python -m copilot.db.migrate --repo-root .
python -m copilot.scripts.provision_roles \
  --credentials /mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01
python -m copilot.scripts.import_cases --config copilot/configs/import-cases.json
docker compose -f copilot/compose.copilot.yml up -d --wait gateway console
export COPILOT_TOKEN_REGISTRY=/mnt/g/ResumeProject/.runtime/all-modules-v2/m1a/credentials/run-01/registry.json
python -m copilot.mcp_server.server --config copilot/configs/tools.json
```

Normally the unchanged Go backend applies its original migration, then the
Python migrator verifies a clean version1 ledger and adds copilot tables.
`--bootstrap-demo` supports an empty test DB by atomically reading the exact
original base SQL and recording golang-migrate's clean version1 ledger, so later
Go startup does not rerun the original CREATE TABLE/TYPE statements. Existing
base tables without a clean known ledger are refused. The new migration is
under `copilot/db/migrations`, outside Go's embedded wildcard. An applied SQL hash is recorded and a changed
already-applied migration is refused. Credentials are provisioned once; existing
ones are preserved rather than silently rotated.

## Tools and exact explanation

| Tool | Scope | Input |
| --- | --- | --- |
| `get_case` | `cases:read` | positive integer `case_id` |
| `list_case_events` | `events:read` | positive integer `case_id` |
| `score_transaction` | `transactions:score` | exactly 34 finite named `features` |
| `explain_score` | `scores:explain` | positive integer `case_id` |
| `search_similar_cases` | `retrieval:read` | positive integer `case_id`, integer `k` in 1..20 |

Unknown tools, extra arguments, Boolean numbers and incomplete feature vectors
are rejected. The scoring adapter always sends `publish:false` to the fixed
configured gateway origin, so the tool cannot publish alerts. No caller-supplied
URL or arbitrary SQL is accepted. Tool results preserve source scores for the
agent's summary; the agent redacts all evidence before prompts or persistence.

For a supported transaction, feature j contributes
`coef[j] * (value[j] - mean[j]) / scale[j]` to **log-odds**. Adding the intercept
reconstructs log-odds; sigmoid reconstructs probability. This is the exact
standardized logistic-regression decomposition, not SHAP or additive probability
attribution. The export and joblib hashes are checked, the case's model name and
full SHA-256 version must match (API hashes joblib; Spark hashes its coefficient
JSON and names the model `windowed-logistic-regression`), and reconstructed probability must match
its stored score within 1e-10. Existing stream alerts omit the full feature
vector and return `features_unavailable`. ATO and SMS use other models and return
`unsupported_source`. Missing/mismatching evidence never gets an invented explanation.

The MCP login has SELECT on three narrow views only, without users, password
hashes or analyst labels. Draft persistence is a separate orchestration adapter,
not an MCP tool: `PostgresDraftStore.save_turn` rejects unredacted text, stores
case-qualified memory and writes a `COPILOT_DRAFT` event with actor `copilot`.
Its database login can execute only two functions. Those functions fix their
search path, revoke PUBLIC execution, serialize same-case turns and enforce
case/run-id idempotency. Case status, assignee, labels and updated_at are not
changed. The agent owner validates the draft schema before calling this adapter.

## Retrieval data and protocol

The card source is the bundled real [OpenML fixture](../gateway/fixtures/README.md),
with its actual binary `label`. The login source is the existing explicitly
synthetic [ATO generator](../ato/datagen.py), whose ground truth is aggregated by
IP/hour. Classifier flags are never used as gold. The protocol builder fixes a
seed and balances both query and gallery across domain × gold label, fails on
insufficient independent entities and records source hashes and all IDs.

```bash
python -m copilot.retrieval.build_protocol --config copilot/configs/retrieval-protocol.json
python -m copilot.scripts.download_embedding \
  --reference copilot/configs/bge-reference.json \
  --cache "$HF_HOME/hub" --receipt "$M1A_RUNTIME/runs/bge-download-01.json"
# Copy/edit the template: pin protocol SHA, downloaded snapshot and a NEW output_dir.
python -m copilot.retrieval.evaluate --config /path/to/new-retrieval-config.json
# Use a separately edited index-cases template to populate live-case vectors.
python -m copilot.retrieval.index_cases --config /path/to/new-index-config.json
```

Embeddings use [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)
(MIT), pinned to revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.
Each downloaded file is checked against the pinned SHA-256 or Git blob identity.
Only the safetensors weights and needed configuration/tokenizer/pooling files
are downloaded. The embedding dimension is 384, max input 512 tokens, and
normalized CPU embeddings are the default. No retrieval model is trained here.

Embedding text is an allow-list of raw numeric transaction or login-window
features. It excludes labels, campaign kind, predictions, scores, severity,
summaries, model names and all case/alert/entity identifiers. All methods exclude
self, shared entities and exact duplicate evidence. TF-IDF is fitted only on
the gallery. The runner inserts a new namespaced dataset and performs actual
PostgreSQL pgvector exact cosine SELECTs; it does not substitute a NumPy search.
It saves per-query neighbors and same-label precision@5 for BGE, fixed random
and TF-IDF, plus overall and per-domain averages and class counts. Timing is a
database query diagnostic, including its round trip, and excludes embedding or
indexing; it is not a serving throughput benchmark.

`copilot.retrieval.index_cases` separately indexes complete raw live case
evidence with the indexer login. It preserves existing vectors and records
unsupported/incomplete cases as skipped. The original mostly-positive demo
queue is not the balanced retrieval benchmark.

The agent owner's source-frozen120-case protocol is imported with
`copilot.scripts.import_eval_cases` and the matching config template. This is
explicit admin fixture setup in the same Go-console database. It accepts only
the reserved case ID interval, inserts case/evidence/system events, rejects
collisions and advances the case sequence. Gold labels, expected calls and tasks
stay in the offline protocol. Agent evidence access still goes only through MCP.

## Checks and results

```bash
python -m unittest discover -s copilot/tests/tools -p 'test_unit.py' -v
RUN_COPILOT_HTTP=1 python -m unittest copilot.tests.tools.test_http -v
RUN_COPILOT_DB=1 python -m unittest copilot.tests.tools.test_database -v
python -m copilot.scripts.verify_model --config copilot/configs/model-parity.json
# Formal combined checks save their own provenance and log to a fresh directory.
python -m copilot.scripts.run_checks --config copilot/configs/checks.json
python -m copilot.scripts.verify_human_review --config /path/to/new-human-review-config.json
# If using pytest on /mnt/g with project TMPDIR: always add --capture=sys.
```

HTTP tests launch a real localhost TCP server and exercise missing/invalid/
expired/wrong-resource token rejection, per-tool scopes, the exact allow-list
and raw extra-argument rejection. In-memory MCP clients bypass HTTP auth and are
not accepted as auth evidence. The database tests use three distinct logins,
attempt forbidden reads/writes and prove that a repeated draft creates one
copilot event while human decision fields and labels remain unchanged.

The same-case workflow check creates a new source-backed integration case, saves
a draft through the restricted writer and reads it through both MCP and the
actual console GraphQL reverse proxy. It then explicitly labels and resolves
the case with a newly registered analyst JWT. The draft leaves human decisions
untouched and is preserved after that review. The check also scores the complete
vector using this stack's real gateway. Fixed evaluation cases are not changed.
The optional gateway is configured for inference-only scoring; MCP always forces
publish=false.

Runtime measurements, model parity against actual joblib inference, PostgreSQL
integration and the real HTTP suite must be run before reporting results. This
preparation contains no claimed retrieval accuracy or serving latency. Every
runner requires a config and a fresh output directory and records source/config
identity, packages, environment, hardware, exact invocation and wall time.

Git provenance is mandatory: `COPILOT_GIT` selects an explicit executable,
otherwise `FINETUNE_GIT`, the task-private Linux Git or Git on PATH is used.
Each command supplies a per-invocation safe.directory and disables optional
index locks; no global config is changed. Failure, missing commit identity or
HEAD changing during collection stops the run. Records separate selected source
status/hashes from the whole worktree status, retain exact read-only command
arguments and relevant non-secret environment values, and identify the actual
runner file/interpreter/import paths. Formal configs can require integrated,
committed selected source. Database DSNs and tokens are never environment output.

Every JSON written by RunRecord.write includes a reserved `run_context` with
commit/config identity, packages, environment/device information, invocation,
timestamp and elapsed run wall time. Dictionaries retain their existing fact
fields; lists/scalars and explicitly raw external responses are preserved under
`value`. Existing upstream contexts are retained as context history. The exact
input `config.json` is a byte-for-byte input snapshot, not a measurement result;
its hash is carried by every result context. Raw observations and partial-case
files are evidence, and a successful completed measurement additionally requires
result.json plus an unchanged end HEAD and selected source bytes/index state.
Unrelated worktree outputs do not fail that source check.

## Limitations

This is local, small-model decision support on demo evidence. A numeric-feature
embedding may perform worse than TF-IDF or random; the benchmark reports that
outcome without changing the served risk model. Balanced gallery precision is
not production prevalence or analyst usefulness. ATO remains synthetic, and
the card fixture is a bounded prefix of an existing dataset. Unsupported cases
are skipped or handed back explicitly. The local token registry is not a
production identity provider. PII detection uses patterns and can have false
positives or miss unusual formats, so agent redaction remains a separate tested
boundary. No tool applies an analyst decision.
