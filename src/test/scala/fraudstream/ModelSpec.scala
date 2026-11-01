package fraudstream

import scala.io.Source

import org.scalatest.funsuite.AnyFunSuite

class ModelSpec extends AnyFunSuite {
  private val model = LrModel.load("model/lr_model.json")

  test("model JSON has the 34 stripe-fraud-ml features in training order") {
    assert(model.features.size == 34)
    assert(model.features.take(2) == Vector("V1", "V2"))
    assert(model.features.takeRight(3) == Vector("txn_count_1h", "amount_ratio_1h", "amount_z_1h"))
  }

  test("Scala scorer matches sklearn predict_proba on 474 real rows") {
    val src = Source.fromFile("model/sklearn_parity.csv")
    try {
      val lines = src.getLines().toVector
      val header = lines.head.split(",").toVector
      assert(header.init == model.features)
      var maxDiff = 0.0
      var decisionMismatch = 0
      lines.tail.foreach { l =>
        val cells = l.split(",").map(_.toDouble)
        val p = model.proba(cells.init)
        maxDiff = math.max(maxDiff, math.abs(p - cells.last))
        if (model.isAlert(p) != (cells.last >= model.threshold)) decisionMismatch += 1
      }
      assert(lines.size - 1 == 474)
      assert(maxDiff < 1e-12, s"max |p_scala - p_sklearn| = $maxDiff")
      assert(decisionMismatch == 0)
    } finally src.close()
  }

  test("sigmoid is stable at extreme logits") {
    assert(LrModel.sigmoid(800) == 1.0)
    assert(LrModel.sigmoid(-800) == 0.0)
    assert(math.abs(LrModel.sigmoid(0) - 0.5) < 1e-15)
  }
}
