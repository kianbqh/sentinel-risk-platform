# Short-message risk classification

I compared two small local Qwen models with a supervised TF-IDF/logistic-regression baseline on UCI's SMS Spam Collection. The baseline won this comparison, so it is the default text route in the shared gateway.

The source has5,574 messages and5,171 unique texts. A stratified80/20 split with seed42 yields4,136 training and1,035 test messages. Spam is mapped to the API label `scam`; the task includes unsolicited marketing as well as prize, premium-rate and phishing messages.

The retained October31 local evaluation uses the exact same stratified400-message subset (51 spam messages) for every row below. Both Qwen models ran with llama.cpp on the RTX3080; hybrid rows replay the confidence gate over the paired predictions.

| System | F1 |
|---|---:|
| TF-IDF + logistic regression |0.9388|
| Rules |0.7907|
| Qwen2.5-0.5B Q4 |0.3315|
| Qwen2.5-1.5B Q4 |0.5217|
| Hybrid→0.5B |0.8190|
| Hybrid→1.5B |0.8491|

Neither hybrid improved F1 over TF-IDF. The full1,035-message baseline evaluation has F1.9575; that is a different sample and is kept separate from the paired400 comparison. Raw predictions, complete reports, model hashes and the original timing environment are under `results/`. These are retained source evaluations, not a new LLM measurement during integration.

`riskclf.Classifier` retains validated JSON output, TTL/LRU caching, a timeout/error rule fallback and the hybrid0.05≤p≤0.95 gate. The gateway exposes it at `POST /v1/text/classify`. LLM-only decisions have no probability. A hybrid response's probability comes from TF-IDF even when the LLM decides its label; alert evidence records that distinction.

Sixteen unit tests cover the classifier, schema, routing, cache, fallbacks and data split. New shared-gateway contract tests and the real Compose SMS→Kafka→case check live at the repository root. See `../results/tests/` and `../results/integration/`.

Data: [UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection), [CC BY4.0](https://creativecommons.org/licenses/by/4.0/).
