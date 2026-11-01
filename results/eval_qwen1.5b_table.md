| set | system | n | accuracy | precision | recall | F1 | p50 ms | p95 ms | elapsed s per 1k |
|---|---|---|---|---|---|---|---|---|---|
| full test | tfidf_lr | 1035 | 0.9894 | 0.9688 | 0.9466 | 0.9575 |  |  |  |
| full test | rules | 1035 | 0.9536 | 0.9882 | 0.6412 | 0.7778 |  |  |  |
| subsample | tfidf_lr | 400 | 0.985 | 0.9787 | 0.902 | 0.9388 | 0.9011 | 1.0895 | 0.9207 |
| subsample | rules | 400 | 0.955 | 0.9714 | 0.6667 | 0.7907 | 0.0109 | 0.0271 | 0.0137 |
| subsample | llm:qwen2.5-1.5b-q4 | 400 | 0.8075 | 0.3818 | 0.8235 | 0.5217 | 97.8644 | 125.1869 | 102.3438 |
| subsample | hybrid:qwen2.5-1.5b-q4 | 400 | 0.96 | 0.8182 | 0.8824 | 0.8491 | 0.9142 | 105.7636 | 12.8455 |

Hybrid rows replay paired predictions; their latencies are derived, not a separate routed measurement.
