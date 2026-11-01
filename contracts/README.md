# Module contracts

The gateway exposes `/v1/transactions/score`, `/v1/transactions/score-batch`, `/v1/text/classify`, `/health/ready` and `/metrics`. Transaction scoring accepts the trained model's engineered feature dictionary. Text labels describe the UCI SMS spam target.

Kafka raw ordered transactions enter `sentinel.transactions.v1`; the stateful Spark scorer writes `sentinel.scores.v1`. Its threshold alerts, gateway model alerts and synthetic ATO window alerts share `sentinel.alerts.v1` and the `alert-v1.schema.json` envelope. `alert_id` is the Kafka key and PostgreSQL idempotency key. The console commits its consumer offset after inserting the alert and its OPEN case in one database transaction.

`source` identifies `transaction-stream`, `transaction-api`, `ato` or `text-risk`. ATO evidence always includes `synthetic: true`. Event time is timezone-aware RFC3339. Numeric-model scores and thresholds must be finite and within [0,1]. Both fields are required on the wire, but can be null for text decisions. LLM-only labels have no fabricated probability; hybrid routes identify `evidence.score_source:tfidf_lr`, keep that model's score, and use a null decision threshold when the label came from the LLM/cache/rules. The console explicitly displays an unavailable probability.

Requests with `publish:true` require a working publisher. Missing publishers and failed acknowledgements return503. Use `publish:false` explicitly for inference only. Success responses include `alert_published`.

The authenticated console uses `/graphql`: analysts list/filter shared alerts, assign cases, resolve cases and attach `FRAUD`, `BENIGN` or `UNCERTAIN` labels. Case events are loaded through a request-scoped dataloader. Registration/login issue JWTs; analyst mutations record their actor.
