package fraudstream

import scala.util.Random

import org.scalatest.funsuite.AnyFunSuite

object Fixtures {
  val model: LrModel = LrModel.load("model/lr_model.json")

  /** Random stream with many shared timestamps and gaps longer than the 1-hour window. */
  def stream(n: Int, seed: Int): Vector[Txn] = {
    val r = new Random(seed)
    var t = 0.0
    (0 until n).map { i =>
      t += (r.nextInt(10) match { case 0 => 4000.0; case k if k < 5 => 0.0; case _ => r.nextInt(900).toDouble })
      Txn(i.toLong, t, math.round(r.nextDouble() * 50000) / 100.0, Array.fill(28)(r.nextGaussian()), r.nextInt(2), 0L)
    }.toVector
  }

  /** O(n^2) reference: the batch definition applied literally. */
  def bruteForce(rows: Vector[Txn]): Map[Long, WindowStats] =
    rows.map { r =>
      val w = rows.filter(o => o.time >= r.time - FeatureLogic.WindowSeconds && o.time <= r.time).map(_.amount)
      val n = w.size
      val mean = w.sum / n
      r.txnId -> WindowStats(n.toLong, mean, math.sqrt(w.map(a => (a - mean) * (a - mean)).sum / n))
    }.toMap

  val marker: Txn = Txn(FeatureLogic.FlushMarkerId, Double.MaxValue, 0, Array.fill(28)(0.0), 0, 0L)
}

class FeatureLogicSpec extends AnyFunSuite {
  import Fixtures._

  private def runInBatches(rows: Vector[Txn], cuts: Seq[Int]): (StreamState, Vector[Scored]) = {
    val bounds = (0 +: cuts.filter(c => c > 0 && c < rows.size).sorted.distinct :+ rows.size).sliding(2).toVector
    bounds.foldLeft((StreamState.empty, Vector.empty[Scored])) { case ((st, acc), Seq(a, b)) =>
      val (n, out) = FeatureLogic.process(st, rows.slice(a, b).iterator, model)
      (n, acc ++ out)
    } match {
      case (st, acc) =>
        val (n, out) = FeatureLogic.process(st, Iterator(marker), model)
        (n, acc ++ out)
    }
  }

  test("window statistics match the brute-force batch definition (ties and gaps included)") {
    val rows = stream(1500, seed = 1)
    val ref = bruteForce(rows)
    val (_, out) = runInBatches(rows, Seq(1, 7, 300, 301, 999))
    assert(out.map(_.txnId).sorted == rows.map(_.txnId))
    out.foreach { s =>
      val e = ref(s.txnId)
      assert(s.txnCount1h == e.count)
      val zRef = if (e.stdPop > 0) (s.amount - e.mean) / e.stdPop else 0.0
      assert(math.abs(s.amountZ1h - zRef) < 1e-9)
      assert(math.abs(s.amountRatio1h - s.amount / (e.mean + 1)) < 1e-12)
    }
  }

  test("results do not depend on how the stream is cut into micro-batches") {
    val rows = stream(800, seed = 2)
    val r = new Random(3)
    val a = runInBatches(rows, Seq.empty)._2.sortBy(_.txnId)
    (1 to 5).foreach { _ =>
      val b = runInBatches(rows, Seq.fill(r.nextInt(40))(r.nextInt(rows.size)))._2.sortBy(_.txnId)
      assert(a.map(s => (s.txnId, s.proba, s.txnCount1h)) == b.map(s => (s.txnId, s.proba, s.txnCount1h)))
    }
  }

  test("rows of the newest timestamp are held until a later timestamp or the flush marker arrives") {
    val t = (id: Long, time: Double) => Txn(id, time, 10.0, Array.fill(28)(0.0), 0, 0L)
    val (s1, o1) = FeatureLogic.process(StreamState.empty, Iterator(t(0, 5), t(1, 5)), model)
    assert(o1.isEmpty && s1.pending.length == 2)
    val (s2, o2) = FeatureLogic.process(s1, Iterator(t(2, 5)), model)
    assert(o2.isEmpty && s2.pending.length == 3)
    val (s3, o3) = FeatureLogic.process(s2, Iterator(t(3, 6)), model)
    assert(o3.map(_.txnId) == Vector(0L, 1L, 2L) && o3.forall(_.txnCount1h == 3))
    val (s4, o4) = FeatureLogic.process(s3, Iterator(marker), model)
    assert(o4.map(s => (s.txnId, s.txnCount1h)) == Vector((3L, 4L)) && s4.pending.isEmpty)
  }

  test("entries older than one hour are evicted from state") {
    val rows = Vector(0.0, 100.0, 3700.0, 3701.0).zipWithIndex.map { case (tm, i) => Txn(i.toLong, tm, 1.0, Array.fill(28)(0.0), 0, 0L) }
    val (st, out) = FeatureLogic.process(StreamState.empty, (rows :+ marker).iterator, model)
    // t=3700 keeps 100 (>= 3700-3600) and drops 0; t=3701 needs >= 101, so 100 is dropped too
    assert(out.map(_.txnCount1h) == Vector(1L, 2L, 2L, 2L))
    assert(st.winTimes.toVector == Vector(3700.0, 3701.0))
  }

  test("featureVector maps names to the right values") {
    val tx = Txn(9, 90000.0, 99.0, Array.tabulate(28)(_.toDouble), 0, 0L)
    val x = FeatureLogic.featureVector(tx, WindowStats(4, 49.0, 10.0), model.features)
    val m = model.features.zip(x).toMap
    assert(m("V1") == 0.0 && m("V28") == 27.0 && m("Amount") == 99.0)
    assert(m("hour_of_day") == 1.0 && m("txn_count_1h") == 4.0)
    assert(m("amount_ratio_1h") == 99.0 / 50.0 && m("amount_z_1h") == 5.0)
    assert(math.abs(m("log_amount") - math.log(100.0)) < 1e-12)
  }
}
