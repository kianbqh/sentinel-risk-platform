# First local-model pass: retained failure

This is the original `live120-f836-20261103T010430Z` run at source
`f836a9e8f4c66be109cd9f915e5f0d612ced26cc`. It recorded all 120 fixed cases,
completed 119 controlled draft writes and failed case 1000105 at `save_turn`
with `ValueError`. That case wrote no draft event, turn or memory row. The
original [127 raw files](real-eval/) and their metrics remain unchanged; this
version is **not a completed 120-case result**.

The model returned 124 actual responses. The 120 actual MCP calls were mandatory
`get_case` reads; the model selected **zero additional tools**, and ordered/set
tool accuracy were both **0/120**. Protected human fields, labels and existing
history stayed unchanged. Cleanup passed. The independent clone API check,
cassette export and local/CI replay were not completed.

The old detector reported raw/final PII counts 0. A separate post-hoc scan of
actual model boundaries found a sentence-ending-dot IPv4 gap: **22 spans in 22
requests across 20 cases**, and **5 spans in 4 raw outputs/cases** (1000047,
1000053, 1000059, 1000105). It found 0 spans in 119 returned drafts. All hits were
the same synthetic documentation-range canary from constructed privacy probes;
they are not real-user IP disclosures. Warmups are separate: 3 requests, 3
outputs and 2 drafts, all with 0 hits. The old zero count therefore does not mean
zero IP text reached prompts or raw outputs. The post-hoc diagnostic preserves
the original metrics and does not guarantee absence of every PII type.

The raw/final draft schema rates were 119/120 each; raw lexical faithfulness
was 86/120 and final lexical faithfulness 119/120. Final results include guarded
handoffs. Actual case p50/p95 were 0.859280394 s / 1.700490437 s, including the failed
persistence attempt. These are local-model observations, not replay latency.

The pinned Qwen model/server, actual server properties and exact owned
llama-server PID 419129 observed on the GPU support GPU participation. The
retained log does not directly record an offload layer count; `-ngl99` is the
requested setting.

The mechanical fix changes one IPv4 right boundary, preserving IP validation
and the storage rejection guard, with replay-derived regression coverage.
Recorded offline checks passed 35 tests without DB/model calls or a fabricated
storage receipt. That separate source fix does not turn this failed pass into
a success; the zero tool accuracy and other negative quality results remain.

[Failure diagnostic](failure-diagnostic.json) records scanner semantics,
case/stage/spans, the fixed source/protocol identities and SHA256 lineage for
all 127 original files. It contains no credentials and sends no new request.
