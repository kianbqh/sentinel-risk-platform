package fraudstream

import scala.collection.mutable

/** One replayed transaction. `label` travels with the event only so the report can evaluate alerts;
  * it is never read by the scorer.
  */
final case class Txn(txnId: Long, time: Double, amount: Double, v: Array[Double], label: Int, produceTsMs: Long)

final case class Scored(
    txnId: Long,
    time: Double,
    amount: Double,
    txnCount1h: Long,
    amountRatio1h: Double,
    amountZ1h: Double,
    logit: Double,
    proba: Double,
    isAlert: Boolean,
    label: Int,
    produceTsMs: Long,
    scoredTsMs: Long
)

/** Serializable streaming state: the trailing-hour window plus the rows of the newest timestamp,
  * which are held until that timestamp is complete (see FeatureLogic.process).
  */
final case class StreamState(
    winTimes: Array[Double],
    winAmounts: Array[Double],
    pending: Array[Txn],
    lastEmittedTime: Double,
    outOfOrder: Long
)

object StreamState {
  val empty: StreamState = StreamState(Array.empty, Array.empty, Array.empty, Double.NegativeInfinity, 0L)
}

/** Window statistics with the same semantics as the batch PySpark job:
  * Window.orderBy("Time").rangeBetween(-3600, 0) -> count, avg, stddev_pop over rows with
  * t - 3600 <= Time <= t, including every row that shares the current timestamp.
  */
final case class WindowStats(count: Long, mean: Double, stdPop: Double)

object FeatureLogic {
  val WindowSeconds = 3600.0
  /** txnId used by the producer's end-of-stream marker; it flushes held rows and is never scored. */
  val FlushMarkerId: Long = -1L

  /** avg = sum / n and population std from Welford updates, mirroring Spark's Average and CentralMomentAgg. */
  def stats(amounts: Iterator[Double]): WindowStats = {
    var n = 0L
    var sum = 0.0
    var avg = 0.0
    var m2 = 0.0
    amounts.foreach { x =>
      n += 1
      sum += x
      val delta = x - avg
      val deltaN = delta / n
      avg += deltaN
      m2 += delta * (delta - deltaN)
    }
    if (n == 0) WindowStats(0, 0.0, 0.0) else WindowStats(n, sum / n, math.sqrt(m2 / n))
  }

  def featureVector(t: Txn, s: WindowStats, order: Vector[String]): Array[Double] = {
    val ratio = t.amount / (s.mean + 1.0)
    val z = if (s.stdPop > 0) (t.amount - s.mean) / s.stdPop else 0.0
    order.map {
      case "Amount"          => t.amount
      case "log_amount"      => StrictMath.log1p(t.amount)
      case "hour_of_day"     => math.floor((t.time % 86400.0) / 3600.0)
      case "txn_count_1h"    => s.count.toDouble
      case "amount_ratio_1h" => ratio
      case "amount_z_1h"     => z
      case v if v.startsWith("V") => t.v(v.drop(1).toInt - 1)
      case other             => throw new IllegalArgumentException(s"unknown feature $other")
    }.toArray
  }

  def score(t: Txn, s: WindowStats, model: LrModel, nowMs: Long): Scored = {
    val x = featureVector(t, s, model.features)
    val z = model.logit(x)
    val p = LrModel.sigmoid(z)
    val ratio = t.amount / (s.mean + 1.0)
    val zAmt = if (s.stdPop > 0) (t.amount - s.mean) / s.stdPop else 0.0
    Scored(t.txnId, t.time, t.amount, s.count, ratio, zAmt, z, p, model.isAlert(p), t.label, t.produceTsMs, nowMs)
  }

  /** Advance the state with one micro-batch.
    *
    * Rows are grouped by timestamp. A timestamp is "complete" once a later timestamp has been seen
    * (or the flush marker arrives), because the batch window includes every row with the same Time.
    * Only complete timestamps are scored; the newest one stays in `pending` for the next batch.
    * This keeps streaming features identical to the batch ones at the cost of holding a row until the
    * next distinct timestamp arrives.
    */
  def process(state: StreamState, incoming: Iterator[Txn], model: LrModel, clock: () => Long = () => System.currentTimeMillis(),
              scoringLatency: Option[ScoringLatencyAccumulator] = None)
      : (StreamState, Vector[Scored]) = {
    val in = incoming.toVector
    val flush = in.exists(_.txnId == FlushMarkerId)
    val rows = (state.pending.toVector ++ in.filterNot(_.txnId == FlushMarkerId)).sortBy(t => (t.time, t.txnId))
    val late = rows.count(_.time <= state.lastEmittedTime)

    val window = mutable.ArrayDeque.empty[(Double, Double)]
    state.winTimes.indices.foreach(i => window.append((state.winTimes(i), state.winAmounts(i))))

    val groups = mutable.ArrayBuffer.empty[Vector[Txn]]
    var i = 0
    while (i < rows.length) {
      var j = i
      while (j < rows.length && rows(j).time == rows(i).time) j += 1
      groups += rows.slice(i, j)
      i = j
    }
    val (complete, held) = if (flush || groups.isEmpty) (groups.toVector, Vector.empty) else (groups.init.toVector, groups.last)

    val out = Vector.newBuilder[Scored]
    var lastTime = state.lastEmittedTime
    complete.foreach { g =>
      val t = g.head.time
      while (window.nonEmpty && window.head._1 < t - WindowSeconds) window.removeHead()
      g.foreach(r => window.append((r.time, r.amount)))
      val s = stats(window.iterator.map(_._2))
      val now = clock()
      g.foreach { r =>
        out += (scoringLatency match {
          case Some(timer) => timer.measure(score(r, s, model, now))
          case None => score(r, s, model, now)
        })
      }
      lastTime = t
    }
    val next = StreamState(
      window.iterator.map(_._1).toArray,
      window.iterator.map(_._2).toArray,
      held.toArray,
      lastTime,
      state.outOfOrder + late
    )
    (next, out.result())
  }
}
