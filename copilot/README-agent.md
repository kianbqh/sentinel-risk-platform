# Local analyst drafts

I use a small local Qwen model to choose read-only case tools and draft a short
triage note. The tools read the same case database as the analyst console.
Only an analyst can label, assign or resolve a case. The copilot saves a
validated draft event with actor `copilot` through a restricted writer role.

The graph, protocol and replay code are implemented here. Real-model quality,
HTTP/database integration and latency results are **not run** until recorded
under a new `copilot/results/` run directory. Unit tests use clearly synthetic
models and are not model-quality measurements.

## Setup

Use the isolated runtime described in [README-tools.md](README-tools.md), with
[requirements-agent.candidate.txt](requirements-agent.candidate.txt). These are
direct candidates; the coordinated runtime lock records resolved dependencies.
Keep caches, weights and temporary files on G or its WSL disk. Do not install
into a shared ML environment.

The model is the existing Qwen2.5-1.5B-Instruct Q4_K_M GGUF, SHA-256
`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`.
The local llama.cpp binary and model paths are in the
[real evaluation template](eval/configs/real-eval.template.json). Verify both
hashes before a run, reserve the GPU, and start that binary with `--host
127.0.0.1`, `-c 8192`, `-np 1`, `--jinja`, `-ngl 99` and the alias
`sentinel-copilot-qwen15`. Record the exact command and startup log. The older
text-risk launch script fixes context at 4096; use a direct launch for this
protocol. No hosted LLM or API key is used.

## Agent

[agent/workflow.py](agent/workflow.py) compiles an actual LangGraph StateGraph:
initial read and memory load → model decision → schema validation → tool loop →
guard → controlled draft persistence. The mandatory initial `get_case` is
reported separately. Every later tool choice and its arguments come from the
model's structured JSON decision over llama.cpp's OpenAI-compatible HTTP API.
This is a JSON tool planner, rather than native OpenAI `tool_calls` decoding.

The default hard budget is five requested tool invocations including the
initial read; a budget refusal never executes another tool. Model calls and
schema retries have independent limits. The allow-list has exactly the five
MCP tools, case arguments must match the current case, repeated identical calls
are rejected, and re-scoring must copy the complete current transaction vector.
All case reads and scoring use `ToolClient.call`; the agent has no gateway or
case-table connection. `DraftStore` is a controlled orchestration dependency,
not a model-callable tool.

Regex and known-field redaction run before every prompt and before memory
storage. Prompts treat notes, retrieved text and prior memory as untrusted data.
Exact explanation prompts project the five largest absolute log-odds terms,
copying values without rounding; full tool evidence remains in the recording.
No raw graph checkpoint is stored. Bounded redacted conversation turns use the
case-qualified PostgreSQL memory functions. External LangSmith tracing is
explicitly disabled in the graph context.

The final [closed JSON Schema](agent/schemas.py) is validated with `jsonschema`.
Invalid output gets a bounded retry and an explicit handoff fallback. Low
confidence, PII, unsupported numeric/identifier claims, unavailable evidence or
tool failures also hand off. Raw malformed outputs and all completed model
turns are kept in evaluation evidence, including when later persistence fails.

For a small authenticated endpoint, copy
[agent/configs/api.template.json](agent/configs/api.template.json) to a private
configuration, generate separate API and scoped MCP token files, and set its
writer DSN environment variable. Then run:

```bash
COPILOT_AGENT_CONFIG=/absolute/private/api.json \
  uvicorn copilot.agent.api:create_app --factory --host 127.0.0.1 --port 18071
```

`POST /v1/copilot/cases/<positive_integer>/drafts` accepts only
`{"task":"review request"}` with the API bearer token. It returns a draft,
controlled persistence receipt and `applied:false`. It rejects concurrent
inference while busy. The existing console remains the human action path.

## Fixed evaluation

The [source protocol builder](eval/build_protocol.py) selects real labeled rows
from the bundled OpenML card fixture and explicitly synthetic ATO windows from
the existing generator. It fixes source hashes, case IDs, original facts,
labels, expected model tool names/arguments and abstention expectations before
LLM evaluation. Labels and expected plans never enter case evidence, embeddings
or prompts. Constructed case envelopes and adversarial perturbations are
labeled as such; a real card feature row does not make its injected privacy or
instruction probe real user data.

There are two freezes. First build the source protocol; then explicitly import
it into the opt-in database shared by the Go console, MCP reader and draft
writer. Index complete eligible cases. Finally
[freeze_runtime.py](eval/freeze_runtime.py) reads the fixed expected calls
through actual MCP, verifies the import and freezes event IDs, exact score
terms and neighbor values before any model evaluation. The real runner rejects
source drift. Missing vectors remain missing; do not replace tool errors with
invented evidence.

Run from the repository root with copied configs, actual hashes and new output
paths. Every output directory must be fresh:

```bash
python -m copilot.eval.build_protocol --config copilot/eval/configs/source-protocol.json
python -m copilot.scripts.import_eval_cases --config /absolute/private/import-eval.json
python -m copilot.retrieval.index_cases --config /absolute/private/index-eval.json
python -m copilot.eval.freeze_runtime --config /absolute/private/freeze-runtime.json
python -m copilot.eval.run --config /absolute/private/real-eval.json
python -m copilot.eval.export_cassette --config /absolute/private/export-cassette.json
python -m copilot.eval.replay --config /absolute/private/replay.json
```

The real run requires committed reviewed source, verified GGUF/binary hashes,
empty case memory, concurrency one and a single measured pass. Two warmup cases
run without persistence. Timing includes memory load, mandatory read, all
LLM/MCP calls, validation/retries and committed draft storage. Startup, hashing,
warmups and a separate post-run MCP check of protected human fields are outside
the timer. Raw case latencies, token usage and retry counts are retained.

Tool exact match compares the ordered model-selected name/argument list;
set match compares unique name/argument pairs, ignoring order and duplicates.
Both exclude the mandatory initial read. Rejected model selections remain
visible. Token usage comes from the server; missing usage stays null.

The [lexical checker](eval/faithfulness.py) checks every numeric and identifier
token in the summary and evidence text against frozen source/tool facts. It
normalizes decimal formatting, grouping commas, leading zeroes/plus and Unicode
minus without rounding. Scientific notation is compared exactly. Percent and
currency markers remain distinct: `0.2` does not authorize `20%`; `100` alone
does not authorize `$100`. UUIDs, hashes and mixed alphanumeric identifiers are
atomic and case-sensitive. `case_id` is separately bound; model confidence is a
self-assessment and is excluded from factual numeric membership.

Reports distinguish raw decision/draft schema validity, pre/post-guard PII,
pre/post-guard lexical faithfulness, abstention, expected handoff agreement,
tool accuracy, usage and latency. Failed cases stay in quality denominators.
A fallback that passes final schema/PII checks does not erase the raw failure.

Only a completed actual local-model/MCP/PostgreSQL run can export the per-case
gzip replay cassette. CI replays those actual responses with exact prompt,
schema, tool, draft and memory checks, plus floors copied from the observed
baseline. Replay timing is not model latency. Synthetic fault fixtures under
`tests/agent/` remain separate from that cassette.

## Checks

```bash
python -m unittest discover -s copilot/tests/agent -p 'test_*.py' -v
# If using pytest on a WSL-mounted Windows path:
pytest --capture=sys copilot/tests/agent
```

These checks cover schema failures, numeric precision, privacy patterns, case
binding, budgets, model-selected calls in actual LangGraph, redacted memory,
authentication handling, preserved failures and exact recording boundaries.
Real HTTP, PostgreSQL role/transaction invariants, actual Qwen output quality,
same-database console actions and real-model cassette generation still require
the local integration run and independently reviewed evidence.

## Limitations

The small local model can make poor choices or abstain frequently. The fixed
evaluation comes from demonstration fixtures, with no production users. The
privacy detector covers patterns and known keys, not general entity recognition.
The faithfulness checker proves lexical membership, not correct reasoning,
causal explanation or the relationship between a field and a quoted value.
Atomic identifier matching does not identify arbitrary alphabetic names.
Exact LR terms require complete compatible transaction evidence; ATO and
incomplete stream evidence do not get a substitute explanation. A selected
explanation projection is not the complete coefficient table. This endpoint is
a local draft service with provisioned tokens, not a production OAuth system.

Sources: [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[tracing context](https://docs.langchain.com/langsmith/trace-without-env-vars),
[llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server),
[Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct),
[OpenML 1597](https://www.openml.org/d/1597), and the bundled
[fixture attribution](../gateway/fixtures/manifest.json).
