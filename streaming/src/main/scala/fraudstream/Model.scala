package fraudstream

import java.nio.file.{Files, Paths}

import com.fasterxml.jackson.databind.{JsonNode, ObjectMapper}

/** StandardScaler + LogisticRegression exported from stripe-fraud-ml (see scripts/export_model.py).
  *
  * Scoring is done in-process on the JVM: z = b + sum_i w_i * (x_i - mu_i) / sigma_i, p = sigmoid(z).
  * The arithmetic follows sklearn's order (scale first, then dot product) so probabilities match
  * predict_proba to ~1e-15; the parity test checks this on 474 real rows.
  */
final case class LrModel(
    features: Vector[String],
    mean: Array[Double],
    scale: Array[Double],
    coef: Array[Double],
    intercept: Double,
    threshold: Double
) {
  require(Seq(mean.length, scale.length, coef.length).forall(_ == features.length), "model arrays disagree")

  def logit(x: Array[Double]): Double = {
    require(x.length == features.length, s"expected ${features.length} features, got ${x.length}")
    var z = 0.0
    var i = 0
    while (i < x.length) {
      z += coef(i) * ((x(i) - mean(i)) / scale(i))
      i += 1
    }
    z + intercept
  }

  def proba(x: Array[Double]): Double = LrModel.sigmoid(logit(x))

  def isAlert(p: Double): Boolean = p >= threshold
}

object LrModel {
  def sigmoid(z: Double): Double =
    if (z >= 0) 1.0 / (1.0 + math.exp(-z)) else { val e = math.exp(z); e / (1.0 + e) }

  private def doubles(n: JsonNode): Array[Double] = {
    val out = new Array[Double](n.size())
    var i = 0
    while (i < out.length) { out(i) = n.get(i).asDouble(); i += 1 }
    out
  }

  def fromJson(json: String): LrModel = {
    val root = new ObjectMapper().readTree(json)
    val feats = (0 until root.get("features").size()).map(i => root.get("features").get(i).asText()).toVector
    LrModel(
      feats,
      doubles(root.get("mean")),
      doubles(root.get("scale")),
      doubles(root.get("coef")),
      root.get("intercept").asDouble(),
      root.get("threshold").asDouble()
    )
  }

  def load(path: String): LrModel = fromJson(new String(Files.readAllBytes(Paths.get(path)), "UTF-8"))
}
