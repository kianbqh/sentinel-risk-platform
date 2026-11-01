| set | system | n | accuracy | precision | recall | F1 | p50 ms | p95 ms | elapsed s per 1k |
|---|---|---|---|---|---|---|---|---|---|
| full test | tfidf_lr | 1035 | 0.9894 | 0.9688 | 0.9466 | 0.9575 |  |  |  |
| full test | rules | 1035 | 0.9536 | 0.9882 | 0.6412 | 0.7778 |  |  |  |
| subsample | tfidf_lr | 400 | 0.985 | 0.9787 | 0.902 | 0.9388 | 0.9326 | 1.1614 | 0.955 |
| subsample | rules | 400 | 0.955 | 0.9714 | 0.6667 | 0.7907 | 0.0104 | 0.0259 | 0.0131 |
| subsample | llm:qwen2.5-0.5b-q4 | 400 | 0.6975 | 0.2308 | 0.5882 | 0.3315 | 73.0529 | 86.1212 | 74.0264 |
| subsample | hybrid:qwen2.5-0.5b-q4 | 400 | 0.9525 | 0.7963 | 0.8431 | 0.819 | 0.9552 | 74.7921 | 9.324 |

Hybrid rows replay paired predictions; their latencies are derived, not a separate routed measurement.
