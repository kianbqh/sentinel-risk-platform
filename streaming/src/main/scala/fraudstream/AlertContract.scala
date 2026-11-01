package fraudstream

import com.fasterxml.jackson.databind.ObjectMapper
import java.nio.file.{Files, Paths}
import java.security.MessageDigest
import java.time.Instant

/** Canonical Kafka envelope. Ground-truth labels are never published as evidence. */
object AlertContract extends Serializable {
  @transient private lazy val mapper = new ObjectMapper()
  def modelVersion(path: String): String =
    MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(Paths.get(path))).map(b => f"${b & 0xff}%02x").mkString

  def key(s: Scored): String = s"transaction-stream:${s.txnId}"
  def json(s: Scored, version: String, threshold: Double): String = {
    val n = mapper.createObjectNode()
    n.put("schema_version", "1.0"); n.put("alert_id", key(s))
    n.put("event_time", Instant.ofEpochMilli(s.produceTsMs).toString)
    n.put("source", "transaction-stream"); n.put("entity_type", "transaction")
    n.put("entity_id", s.txnId.toString); n.put("risk_score", s.proba); n.put("threshold", threshold)
    n.put("severity", "high"); n.put("summary", "Streaming transaction model threshold exceeded")
    val model = n.putObject("model"); model.put("name", "windowed-logistic-regression"); model.put("version", version)
    val e = n.putObject("evidence")
    e.put("transaction_time_s", s.time); e.put("amount", s.amount)
    e.put("global_txn_count_1h", s.txnCount1h); e.put("amount_ratio_1h", s.amountRatio1h)
    e.put("amount_zscore_1h", s.amountZ1h); e.put("scored_ts_ms", s.scoredTsMs)
    n.put("trace_id", s.txnId.toString)
    mapper.writeValueAsString(n)
  }
}
