package fraudstream

import com.fasterxml.jackson.databind.ObjectMapper
import org.scalatest.funsuite.AnyFunSuite

class AlertContractSpec extends AnyFunSuite {
  test("canonical producer uses the shared envelope and excludes evaluation labels") {
    val s = Scored(42L, 7200.0, 100.0, 8L, 1.2, 0.3, 4.0, 0.98, true, 1, 1700000000000L, 1700000000100L)
    val json = new ObjectMapper().readTree(AlertContract.json(s, "fixture-model", 0.9))
    assert(json.get("schema_version").asText() == "1.0")
    assert(json.get("alert_id").asText() == AlertContract.key(s))
    assert(json.get("source").asText() == "transaction-stream")
    assert(json.get("entity_type").asText() == "transaction")
    assert(json.get("risk_score").asDouble() == 0.98)
    assert(json.get("threshold").asDouble() == 0.9)
    assert(json.get("event_time").asText() == "2023-11-14T22:13:20Z")
    assert(json.get("model").get("version").asText() == "fixture-model")
    assert(!json.has("label"))
    assert(!json.get("evidence").has("label"))
    assert(json.get("evidence").get("global_txn_count_1h").asLong() == 8L)
  }
}
