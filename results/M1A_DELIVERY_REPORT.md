# Local case drafts, evidence retrieval and SMS adaptation

I wanted to see how far a small local model could help with case triage while
keeping the analyst's actions explicit. I added read-only case tools, similar
evidence retrieval, a guarded draft workflow and a separate SMS adaptation
experiment to [Sentinel Risk Platform](https://github.com/kianbqh/sentinel-risk-platform).

This report covers those M1-A additions. The SMS experiment uses one public
corpus and a fixed split; retrieval and agent cases use repository-derived card
evidence and explicitly synthetic login windows. The retrieval fixture is small,
the privacy checks cover declared patterns and known keys, and the faithfulness
check tests literal numeric/identifier membership. The separate
[operations report](M1B_DELIVERY_REPORT.md)
covers monitoring, recovery and deployment exercises.

## What I added

- An MCP Streamable HTTP server with five scoped tools: `get_case`,
  `list_case_events`, `score_transaction`, `explain_score` and
  `search_similar_cases`. A separate opaque token carries per-tool scopes.
- A PG17/pgvector database shared with the Go analyst console. The MCP reader
  sees narrow views; a different writer can execute only the controlled draft
  and case-qualified memory functions. Scoring always sends `publish:false`.
- Exact logistic-regression log-odds terms for complete, compatible transaction
  evidence, checked against the stored score and original model identity.
- Pinned CPU BGE embeddings with actual pgvector cosine queries and independent
  query/gallery cases. Labels, predictions, scores, case IDs and campaign names
  are excluded from embedding text.
- A LangGraph workflow using local Qwen2.5-1.5B-Instruct Q4_K_M through
  llama.cpp. It selects tools through structured JSON, redacts evidence and
  memory, validates the final schema, and hands back unsupported drafts.
- A bearer-authenticated draft API and complete response/error recordings for
  deterministic CPU replay. Assignments, labels and resolutions stay with the
  analyst.
- NF4 QLoRA adapters for the separate binary SMS task, compared with the
  unchanged served TF-IDF artifact and a matched prompt-only NF4 model.

The [tools guide](../copilot/README-tools.md),
[agent guide](../copilot/README-agent.md) and
[SMS training guide](../text-risk/finetune/README.md) contain the setup commands.
Scoring requires exactly 34 finite named features, retrieval accepts `k` in
1..20, and an exact LR explanation must reconstruct probability within 1e-10.
The explanation terms add in log-odds space.

## SMS quality and latency

The original experiment source is
`5465aae5af963879f6cfcb83230e962ca0cf9ee0`. The base model revision is
`989aa7980e4cf806f80c7fef2b1adb7bc71aa306` (Apache 2.0). The local run used WSL2,
an RTX 3080, driver 621.01, Torch 2.15.0+cu132/CUDA 13.2, Transformers 5.22.1,
PEFT 0.21.2 and bitsandbytes 0.50.2.

The 4,136-message training partition includes a fixed 414-message train-only
validation set. Checkpoint selection used only that validation loss. Each
system evaluated all 1,035 held-out messages; the paired 400-message subset is
selected by frozen exact IDs from those same rows, so it is not an independent
second test set.

| System | Full 1,035 F1 | Paired 400 F1 | Strict valid, full test | p50 / p95 ms |
| --- | ---: | ---: | ---: | ---: |
| TF-IDF + LR | 0.9575289575 | 0.9387755102 | n/a | 0.900702 / 1.143615 |
| Matched prompt-only NF4 + rules | 0.7777777778 | 0.7906976744 | 0 / 1,035 | 563.209037 / 636.578058 |
| QLoRA seed 42 + rules | 0.9806949807 | 0.9696969697 | 1,034 / 1,035 | 486.223849 / 642.255195 |
| QLoRA seed 43 + rules | 0.9733840304 | 0.9702970297 | 1,035 / 1,035 | 472.440628 / 637.583727 |
| QLoRA seed 44 + rules | 0.9806949807 | 0.9800000000 | 1,035 / 1,035 | 481.896327 / 640.420672 |

These primary NF4 scores include the frozen strict JSON/schema parser and the
declared rules fallback. Prompt-only emitted code-fenced output on all 1,035
first-repeat messages, so its primary F1 is the fallback endpoint's quality and
its valid-only F1 is null. Seed 42 had one schema failure: valid-only F1 was
0.9844961240 on 1,034 full-test rows and 0.9795918367 on 399 paired rows. Seeds
43 and 44 had no failures, so their valid-only and primary scores coincide.

Quality uses repeat 0. Timing uses batch size one, concurrency one, three
warmups and three full repeats, or 3,105 actual requests per system. The
prompt-only repeats contain 3,105 `invalid_json` failures; seed 42 contains
three schema failures; seeds 43/44 contain zero. Final prediction and
failure-category repeat disagreements are zero for every system. NF4 timing
synchronizes CUDA and includes tokenization, device transfer, generation,
decoding, parsing and fallback. TF-IDF timing includes one-message
vectorization and `predict_proba`; loading and warmups are excluded.

Across seeds 42/43/44, full-test F1 is **0.9782579973 ± 0.0034464150** and
paired-subset F1 is **0.9733313331 ± 0.0047218186**. These are training-seed mean
and population standard deviation (`ddof=0`). The following intervals are a
different quantity: 95% paired row-bootstrap intervals with 10,000 draws and
seed 20261102, conditional on each fitted model. McNemar tests are exact,
two-sided and unadjusted.

| Set | System | F1 sampling CI | F1 difference vs TF-IDF | Difference CI | McNemar p |
| --- | --- | --- | ---: | --- | ---: |
| Full 1,035 | TF-IDF | [0.9303986711, 0.9806949807] | 0 | [0, 0] | 1 |
| Full 1,035 | Prompt-only + rules | [0.7128712871, 0.8359810044] | -0.1797511798 | [-0.2428078089, -0.1230910320] | 9.3348830887e-09 |
| Full 1,035 | QLoRA 42 + rules | [0.9615384615, 0.9960474308] | 0.0231660232 | [-0.0036568366, 0.0504845755] | 0.14599609375 |
| Full 1,035 | QLoRA 43 + rules | [0.9516728625, 0.9918049447] | 0.0158550729 | [-0.0071622145, 0.0402578706] | 0.34375 |
| Full 1,035 | QLoRA 44 + rules | [0.9618288127, 0.9960474308] | 0.0231660232 | [0, 0.0484172950] | 0.109375 |
| Paired 400 | TF-IDF | [0.8837209302, 0.9807692308] | 0 | [0, 0] | 1 |
| Paired 400 | Prompt-only + rules | [0.6842105263, 0.8767123288] | -0.1480778358 | [-0.2431635738, -0.0673526910] | 0.0018310546875 |
| Paired 400 | QLoRA 42 + rules | [0.9298183840, 1] | 0.0309214595 | [-0.0119951268, 0.0817110903] | 0.375 |
| Paired 400 | QLoRA 43 + rules | [0.9318181818, 1] | 0.0315215195 | [-0.0097126984, 0.0813861139] | 0.375 |
| Paired 400 | QLoRA 44 + rules | [0.9473684211, 1] | 0.0412244898 | [0.0090089734, 0.0881518877] | 0.125 |

The point estimates favor QLoRA, but the full-test difference intervals include
or touch zero and do not establish a consistent significant gain over TF-IDF.
There was no test-set seed selection. **TF-IDF remains the served default**,
with much lower measured latency.

After seeing strict-format failures, I declared a separate descriptive format
diagnostic before calculating normalized quality. It removes at most one
complete outer code fence and applies the same strict parser to saved repeat-0
outputs. It makes no new inference and leaves the primary table unchanged.

| Prompt-only format diagnostic | Full 1,035 | Paired 400 |
| --- | ---: | ---: |
| F1 including retained fallback | 0.3392857143 | 0.3193916350 |
| Valid rows | 883 | 341 |
| Still-invalid rows / retained fallback | 152 | 59 |
| Valid-only F1 | 0.2903752039 | 0.2761506276 |

All three adapter predictions stayed unchanged, including seed 42's schema
failure. The diagnostic has no normalized latency, formal posthoc confidence
interval, p-value or parameter selection.

Each seed completed three epochs and 699 optimizer steps without resume or
training/validation target truncation:

| Seed | Training wall s | Entire stage s | Selected step | Validation loss | Peak allocated / reserved bytes |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 2631.823869 | 2670.228128 | 466 | 0.0062991357 | 3529354240 / 7967080448 |
| 43 | 3249.787171 | 4306.738048 | 466 | 0.0061855605 | 3529349632 / 10846470144 |
| 44 | 2684.806525 | 2756.643643 | 233 | 0.0071999463 | 3529771008 / 4395630592 |

Training wall includes scheduled validation, checkpoint saves and best reload;
stage wall includes startup and final operations. Memory is PyTorch allocator
usage. Training wall times retain concurrent authorized CPU preparation; before
latency measurement the downloader/checksum threads were actually suspended
and heavy preparation stopped. Small source/CI work remained possible. Seed
43's startup/I/O delays and SDPA nondeterminism warnings remain in the logs.

The [full SMS report](../text-risk/results/qlora/20261102-5465aae5/RESULTS.md),
[comparison table](../text-risk/results/qlora/20261102-5465aae5/comparison/table.md)
and [summary](../text-risk/results/qlora/20261102-5465aae5/comparison/summary.json)
retain exact statistics, including comparisons with prompt-only. The
[completion record](../text-risk/results/qlora/20261102-5465aae5/CONTINUATION_COMPLETE.json)
records 108 small-file hashes; the
[adapter rehash](../text-risk/results/qlora/20261102-5465aae5/adapter-rehash.json)
matched all three final adapters. Binary weights/checkpoints are excluded from
Git. Earlier GGUF and hybrid predictions cover only paired400 and retain their
original runtime: pure 0.5B/1.5B F1 was 0.3314917127/0.5217391304 and hybrid F1
was 0.8190476190/0.8490566038. Their full-test predictions are unavailable.

## Case tools and retrieval

| Check | Measured result | Evidence |
| --- | --- | --- |
| Local tools, real HTTP + PostgreSQL, no skips | 24 checks passed | [result](../copilot/results/functional-dcb8436-01/checks/result.json) |
| GitHub-hosted tools, real HTTP + PostgreSQL | 26 checks passed | [result](../copilot/results/ci-tools-5465aae-01/checks/result.json) |
| Exact LR export vs actual joblib inference | 128 vectors; largest probability difference 3.33 × 10⁻¹⁶ | [parity result](../copilot/results/functional-dcb8436-01/model-parity/result.json) |
| Same-database draft and analyst review | Draft read through MCP and console proxy; protected human fields preserved; draft retained after analyst labeling/resolution | [review result](../copilot/results/functional-dcb8436-01/human-review/result.json) |
| Separate live-case index | 92 eligible cases indexed; 30 incomplete/unsupported cases skipped | [index result](../copilot/results/retrieval-dcb8436-01/case-index/result.json) |
| Agent CPU boundaries and response/error recording | 49 checks passed using synthetic model boundaries | [receipt](../copilot/results/recording-unit-20261102/receipt.json), [log](../copilot/results/recording-unit-20261102/tests.log) |
| Integrated CPU suite after the IPv4 boundary fix | 59 tests; 0 failures; 0 errors; 2.545 s; ten new IPv4 regressions included | [receipt](../copilot/results/ipv4-regression-20261103/receipt.json) |

The tool tests use a real localhost TCP transport for missing, invalid, expired
and wrong-resource token rejection and per-tool scopes. Separate database
logins exercise forbidden reads/writes, draft idempotency and preservation of
human decision fields. The controlled human-review test uses a separate cloned
case and a test draft; it is not an LLM-quality measurement.

The balanced retrieval experiment has 32 queries and 64 gallery records,
balanced across card/login domain and gold label. BGE-small-en-v1.5 is pinned to
revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (MIT), with 384 dimensions,
512-token maximum input and normalized CPU embeddings. TF-IDF is fitted only
on the gallery. All methods exclude self, shared entities and duplicate evidence.
The run uses PostgreSQL 17.11 and pgvector 0.8.7, with actual exact cosine SQL
queries:

| Method | Overall P@5 | Card P@5 | Synthetic login P@5 |
| --- | ---: | ---: | ---: |
| BGE + pgvector cosine | 0.75625 | 0.6375 | 0.875 |
| Gallery-fit TF-IDF cosine | 0.5625 | 0.5125 | 0.6125 |
| Fixed random | 0.46875 | 0.4875 | 0.45 |

The [benchmark result](../copilot/results/retrieval-dcb8436-01/benchmark/result.json)
and [per-query neighbors](../copilot/results/retrieval-dcb8436-01/benchmark/per_query.json)
retain each query. Query timing includes the database round trip and excludes
embedding/indexing; the balanced gallery is separate from the demo-case index.

## First local-model pass: retained failure

The first measured pass, `live120-f836-20261103T010430Z`, used source
`f836a9e8f4c66be109cd9f915e5f0d612ced26cc`. It recorded every fixed case,
but case 1000105 failed at `save_turn` with `ValueError`: the sentence-ending
dot after an IPv4 canary escaped prompt/output redaction and the unchanged
storage guard rejected the draft. That case has no draft event, turn or memory
row. The pass is **119/120 completed, not a completed 120-case result**.

The [failure note](../copilot/results/agent-llm-live120-f836-20261103T010430Z/FAILED_RUN.md),
[127 original raw files](../copilot/results/agent-llm-live120-f836-20261103T010430Z/real-eval/)
and [failure diagnostic](../copilot/results/agent-llm-live120-f836-20261103T010430Z/failure-diagnostic.json)
preserve the original metrics and per-file SHA-256 lineage. Of those 127 files,
126 hashes reconcile with the prior independent audit; `started.json` was first
hashed during packaging. The source/protocol identities and original failed
case remain separate from the corrected version below.

| First-pass measurement | Original result |
| --- | --- |
| Recorded / completed / failed cases | 120 / 119 / 1 |
| Model-selected extra tools | 0 |
| Tool exact / set accuracy | 0/120 / 0/120 |
| Actual model responses / recorded model errors | 124 / 0 |
| Actual MCP responses / recorded MCP transport errors | 120 / 0; all mandatory `get_case` |
| Model-invoked / MCP-only handoff cases | 120 / 0 |
| Raw / final draft schema validity | 119/120 / 119/120 |
| First decision / all decision-attempt schema validity | 0.9583333333 / 0.9677419355 |
| Schema retries | 4 |
| Raw / final lexical faithfulness | 86/120 / 119/120 |
| Abstention rate / expected-handoff agreement | 0.625 / 0.55 |
| Old detector raw / final PII finding counts | 0 / 0; the detector missed the IPv4 boundary below |
| Actual end-to-end p50 / p95 | 0.859280394 s / 1.700490437 s, including the failed persistence attempt |
| Input / output tokens per case | 1067.4833333333 / 219.9666666667; 120 measured cases each |
| Human fields, labels and existing history | Preserved |
| Cleanup | Passed |
| Clone API / cassette export / local and CI replay | Not completed |

The model chose no additional tools, so the final guarded schema/faithfulness
rates do not establish useful tool planning. The 120-case denominators retain
the failed case. The pinned server/model, actual server properties and owned
llama-server PID 419129 observed as a GPU compute process support GPU
participation. The retained GPU sample was 5% utilization and 3,342 MiB used;
the log does not directly record an offload layer count, so `-ngl99` remains
the requested setting rather than a measured layer count.

The separate post-hoc scanner validates candidate IPv4 text with `IPv4Address`,
accepts a terminal sentence dot and blocks an extra dotted numeric component.
It scans actual recorded request strings, completed model content and returned
drafts, keeping warmups separate. It does not scan protocol/provenance metadata
or rewrite the original detector's metrics:

| Actual model boundary | Payloads scanned | IPv4 spans found | Affected payloads / cases |
| --- | ---: | ---: | --- |
| Measured request messages | 124 | 22 | 22 requests / 20 cases |
| Measured raw model outputs | 124 | 5 | 4 outputs / 4 cases |
| Measured returned drafts | 119 | 0 | 0 / 0; the failed case returned no draft |
| Warmup requests / outputs / drafts | 3 / 3 / 2 | 0 / 0 / 0 | Two warmup cases |

All hits were the same synthetic documentation-range canary in constructed
privacy probes; they were not real-user IP disclosures. Raw-output hits occurred
in cases 1000047, 1000053, 1000059 and 1000105. The old all-pattern detector
reported zero at each scanned boundary, so its original zero counts do not mean
zero IP text reached prompts or raw outputs. This narrow diagnostic does not
certify that every PII type is absent.

The fix changes one IPv4 right boundary while preserving IP validation and
the storage rejection guard. Its separate recorded offline checks passed 35
tests with no database/model calls or fabricated storage receipt. The subsequent
[integrated WSL CPU suite](../copilot/results/ipv4-regression-20261103/receipt.json)
passed 59 tests, including ten new IPv4 regression methods and the recorded
failure fixture, with zero failures/errors in 2.545 seconds. This is the expanded
suite, not 59 independent checks to add to the earlier 49. External model,
database and GPU were not used by that unit suite. Neither the fix nor these
tests changes the first pass's failed status or zero tool accuracy.

## Corrected-version local-model evaluation

The corrected source is `8c4e2f78ea6b898e24a1839a9ad2557538828d6c`. Its
controlled run uses an independent database and preserves the first pass's
database and raw evidence. It evaluates all 120 cases for this source version;
it does not patch the old failed case's result or filter to favorable cases.
The source/runtime protocol, GGUF/server, prompt template, model and agent
budgets remain fixed. The boundary fix changes redaction of affected prompt
text. The controlled run completed all 120 cases, the separate clone API and exact
local replay. Independent acceptance passed 6,082 checks. Tool planning remained
poor: the model selected no additional tools and tool exact/set accuracy stayed
zero. The guarded final validity rates should be read alongside that result.
The two source versions have different redacted prompt bytes, so their latency
rows describe separate observations and do not support a speedup claim.

The protocol
contains exactly cases 1000001..1000120 in order, two non-persisting warmups,
one measured pass and concurrency one. The source120 SHA-256 is
`755b31aeea2526530c34acfe4c7d359f64b03c5ea57af6844ab3c73a777e446b`; the
[actual-MCP freeze receipt](../copilot/results/runtime-v2-5465aae-01/runtime-protocol/result.json)
records runtime-protocol SHA-256
`21f8eef123d17b0a812bdb313c1f644bc4cc65086b83c17c253becd347c89afb`.
The model GGUF SHA-256 is
`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`;
llama-server SHA-256 is
`f2f78cac3e345426ad8a08847fde415cbecee74f8d4f7063e57d17be5023005f`.
The fixed budget is five requested tool calls including mandatory `get_case`,
seven model calls, one schema retry, 700 generated tokens, 45 seconds per
model request and 180 seconds per case.

Tool exact match compares ordered model-selected names and arguments; set
match ignores order and duplicates. Both exclude the mandatory read. Quality
denominators include failed cases. End-to-end latency includes memory load,
mandatory read, every model/MCP call, validation/retries and committed storage;
startup, hashing, warmups and the post-run protected-field check are excluded.
Server usage stays null when unavailable. Raw model validity remains separate
from guarded final delivery.

| Corrected-version live120 measurement | Accepted result |
| --- | --- |
| Run ID and raw evidence | `isolated-8c4e-20261103T012816Z`; [actual result](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/real-eval/result.json) |
| Completed / failed / total cases | 120 / 0 / 120 |
| Tool exact / set accuracy | 0/120 exact; 0/120 set; zero model-selected additional tools |
| Raw draft schema / first decision / all decision-attempt validity | 120/120 raw drafts; 116/120 first decisions; 120/124 decision attempts |
| Final schema validity | 120/120 |
| Pre-guard / final lexical faithfulness | 84/120 (0.7) / 120/120 (1.0) |
| Pre-guard / final PII finding counts | 0 / 0 under the corrected pattern detector |
| Abstention rate / expected-handoff agreement | 76/120 (0.6333333333) / 66/120 (0.55) |
| Schema retry / failure counts and guard/handoff reasons | 4 schema retries and 4 schema failures; handoffs: 54 unfaithful_output, 12 low_confidence, 10 insufficient_evidence; 44 none |
| End-to-end latency p50 / p95 and sample count | 0.781160813 s / 1.891345849 s; 120 actual samples |
| Input / output tokens per case and measured-case counts | 1066.6916666667 input / 224.2833333333 output; 120 measured cases each |
| Actual model responses / recorded model errors | 124 / 0 |
| Actual MCP responses / recorded MCP transport errors | 120 / 0; all mandatory get_case |
| Model-invoked / MCP-only handoff cases | 120 / 0 |
| Per-stratum counts and quality | 12 domain/variant groups, 10 cases each; [all strata](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/strata.json) |
| Independent actual-prompt/output IPv4 diagnostic, separate from pattern counters | 0 IPv4 hits in 124 actual requests, 124 model contents and 120 returned drafts; warmups 3/3/2 also zero; [diagnostic](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/boundary-ipv4.json) |
| Corrected database isolation and unchanged first-pass database/evidence | Independent database; original failed-pass database and 127 raw files unchanged; [isolation receipt](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/isolation-cleanup.json) |
| Actual model/server identity and observed GPU participation | Pinned model/server and actual server properties; owned llama-server PID 422135 observed on GPU; direct layer count not logged; [acceptance](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/acceptance.json) |
| Protected human fields, labels and history; matching event/turn/memory content | All 120 have matching draft event, turn and memory content; protected fields, labels and original history preserved; [independent audit](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/acceptance.json) |
| Cleanup and unchanged portable service | Owned process identities absent; all four owned containers stopped, volume retained, ports free and portable service identity/health unchanged; [cleanup](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/isolation-cleanup.json) |

An earlier startup-only attempt stopped during a Docker health-template
precheck before service/model/evaluation startup. It measured no fixed case and
is separate from both the retained first measured pass and the corrected run.

## Draft API, cassette and CI

| Boundary | Accepted result / evidence |
| --- | --- |
| API clone outside fixed IDs: missing/wrong bearer token; extra mutation field | 401 missing token / 401 wrong token / 400 extra mutation field; [API receipt](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/api-check.json) |
| Exactly one valid API draft, `applied:false`, with matching event/turn/memory | Clone case 1000122: 201, applied:false, one matching event/turn/memory; guarded handoff for unfaithful_output; [API receipt](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/api-check.json) |
| Fixed120 unchanged by API check | Passed; fixed cases and clone human fields/labels unchanged |
| Whole cassette: original fixed cases, exact member/source hashes, actual event counts | 120/120 cases; 124 actual model responses and 120 actual MCP responses, no recorded adapter errors; [manifest](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/cassette/manifest.json) |
| Accepted literal manifest SHA-256 | `8504fffed1f1631b8723e108b5c761e3b56fd4542e06f9dfff053baa390d77bf` |
| Local exact response/error/prompt/schema/tool/store replay | 120/120 completed with exact input consumption and original non-latency metrics; [replay result](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/replay/result.json) |
| Independent closed-run audit | 6,082 checks passed; 0 failed checks, 0 errors and 0 contract failures; [sanitized acceptance](../copilot/results/agent-llm-isolated-8c4e-20261103T012816Z/publication/acceptance.json) |
| Final publication / merge source | Measured source `8c4e2f78ea6b898e24a1839a9ad2557538828d6c`; [publication PR](https://github.com/kianbqh/sentinel-risk-platform/pull/2) |
| Publication checks | [Current PR checks](https://github.com/kianbqh/sentinel-risk-platform/pull/2/checks), including the actual-cassette replay job |
| Main workflow | [Platform workflow history](https://github.com/kianbqh/sentinel-risk-platform/actions/workflows/platform.yml) |

The original pre-live source passed all 14 jobs in
[CI 41458580548](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41458580548).
The [measured corrected-source CI](https://github.com/kianbqh/sentinel-risk-platform/actions/runs/41559725396)
passed all 14 jobs on source 8c4e. Publication and subsequent main runs are
linked above without relabeling the measured source; their current status is
available in the workflow history.
The replay job uses a tracked config containing the accepted
repository-relative manifest path and its fixed literal SHA-256. It copies
only `output_dir` to a unique runner-temporary directory and reuses the agent
CPU requirements. The existing 14 jobs stay intact.

Cassette replay consumes every original model/MCP response or recorded adapter
exception, prompt, schema, tool call and draft/memory record exactly. It checks
the original seven observed quality floors and final PII ceiling, including any
zero floor, without changing the baseline. Replay needs no model, GPU, database,
service or secret. Its wall time is not model latency; the actual local-model
table is the performance record.
