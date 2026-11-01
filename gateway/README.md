# Shared risk gateway

One FastAPI process loads the credit-card model from `../serving/model/` and the SMS TF-IDF model from `../text-risk/models/`. The transaction model uses OpenML 1597; the text model uses UCI SMS Spam Collection.

- `POST /v1/transactions/score`: one engineered feature map.
- `POST /v1/transactions/score-batch`: up to 512 maps, with the same feature order and threshold.
- `POST /v1/text/classify`: SMS label and its actual score source.
- `GET /health/ready`, `GET /metrics`.

Requests publish flagged signals to `sentinel.alerts.v1` by default. A caller can set `publish:false` for inference only. A missing publisher or failed Kafka acknowledgement returns503; the response never reports a silently dropped alert as published. Caller-supplied transaction/message IDs make retries idempotent at the console.

An LLM-only result keeps `risk_score:null`; its label decides the alert. Hybrid results retain the TF-IDF score with explicit provenance, and a null probability threshold when the LLM/cache/rule route made the label decision. Default Compose uses the stronger TF-IDF baseline. The original hybrid, cache and timeout behavior remains in `riskclf`.

`python -m gateway.demo` is the Compose integration runner. It checks the real ordered credit-card prefix against the Python model, validates every producer's wire envelope, reconciles the console IDs, exercises the analyst workflow and retries one alert ID. Raw scores, wire records and the summary go to `../results/integration/`.
