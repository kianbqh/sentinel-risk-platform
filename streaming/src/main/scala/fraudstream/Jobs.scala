package fraudstream

import java.nio.file.{Files, Paths, StandardOpenOption}
import java.util.Properties
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.locks.LockSupport

import org.apache.kafka.clients.producer.{Callback, KafkaProducer, ProducerConfig, ProducerRecord, RecordMetadata}
import org.apache.kafka.common.serialization.StringSerializer
import org.apache.spark.sql.{DataFrame, Dataset, Encoders, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.streaming.{GroupState, GroupStateTimeout, OutputMode, StreamingQueryListener, Trigger}
import org.apache.spark.sql.types._

object Spark {
  def session(app: String): SparkSession = {
    val s = SparkSession.builder()
      .appName(app)
      .master(sys.env.getOrElse("SPARK_MASTER", "local[4]"))
      .config("spark.sql.shuffle.partitions", "1") // one global key (dataset has no card id); see README
      .config("spark.ui.enabled", "false")
      .config("spark.sql.streaming.stateStore.providerClass",
        "org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider")
      .getOrCreate()
    s.sparkContext.setLogLevel("WARN")
    s
  }
}

final case class Topics(runId: String) {
  val in = s"transactions-$runId"
  val scores = s"scores-$runId"
  val alerts = s"fraud-alerts-$runId"
}

object Prepare {
  /** Raw OpenML parquet -> deduplicated, ordered replay file with a stable txn_id. */
  def run(spark: SparkSession, raw: String, out: String): Long = {
    val df = spark.read.parquet(raw)
    val vCols = (1 to 28).map(i => s"V$i")
    val dedup = df.dropDuplicates()
    val ordered = dedup.withColumn(
      "txn_id",
      row_number().over(Window.orderBy((Seq("Time") ++ vCols ++ Seq("Amount")).map(col): _*)) - 1
    )
    val replay = ordered.select(
      col("txn_id").cast(LongType),
      col("Time").cast(DoubleType).as("time"),
      col("Amount").cast(DoubleType).as("amount"),
      array(vCols.map(c => col(c).cast(DoubleType)): _*).as("v"),
      col("Class").cast(IntegerType).as("label")
    ).orderBy("txn_id")
    replay.coalesce(1).write.mode("overwrite").parquet(out)
    spark.read.parquet(out).count()
  }
}

object BatchReference {
  /** The batch PySpark feature definition from stripe-fraud-ml, ported to Scala. */
  def withWindowFeatures(replay: DataFrame): DataFrame = {
    val w = Window.orderBy("time").rangeBetween(-FeatureLogic.WindowSeconds.toLong, 0)
    replay
      .withColumn("w_count", count(lit(1)).over(w))
      .withColumn("w_mean", avg("amount").over(w))
      .withColumn("w_std", stddev_pop("amount").over(w))
  }

  def score(spark: SparkSession, replay: DataFrame, model: LrModel): Dataset[Scored] = {
    import spark.implicits._
    withWindowFeatures(replay)
      .select($"txn_id", $"time", $"amount", $"v", $"label", $"w_count", $"w_mean", $"w_std")
      .as[(Long, Double, Double, Array[Double], Int, Long, Double, Double)]
      .map { case (id, t, a, v, l, n, m, s) =>
        FeatureLogic.score(Txn(id, t, a, v, l, 0L), WindowStats(n, m, s), model, 0L)
      }
  }

  def run(spark: SparkSession, replayPath: String, model: LrModel, out: String): Long = {
    score(spark, spark.read.parquet(replayPath), model).coalesce(1).write.mode("overwrite").parquet(out)
    spark.read.parquet(out).count()
  }
}

object Producer {
  def json(t: Txn): String = {
    val sb = new java.lang.StringBuilder(640)
    sb.append("{\"txn_id\":").append(t.txnId).append(",\"time\":").append(t.time)
      .append(",\"amount\":").append(t.amount).append(",\"label\":").append(t.label)
      .append(",\"produce_ts_ms\":").append(t.produceTsMs).append(",\"v\":[")
    var i = 0
    while (i < t.v.length) { if (i > 0) sb.append(','); sb.append(t.v(i)); i += 1 }
    sb.append("]}").toString
  }

  /** Replays the transactions in time order at `rate` events/s (0 = as fast as possible). */
  def run(spark: SparkSession, replayPath: String, bootstrap: String, topic: String, rate: Double, limit: Int): String = {
    import spark.implicits._
    val rows = spark.read.parquet(replayPath).orderBy("txn_id")
      .select($"txn_id", $"time", $"amount", $"v", $"label").as[(Long, Double, Double, Array[Double], Int)]
      .collect()
    val n = if (limit > 0) math.min(limit, rows.length) else rows.length
    val props = new Properties()
    props.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap)
    props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, classOf[StringSerializer].getName)
    props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, classOf[StringSerializer].getName)
    props.put(ProducerConfig.ACKS_CONFIG, "1")
    props.put(ProducerConfig.LINGER_MS_CONFIG, "1")
    props.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "lz4")
    val producer = new KafkaProducer[String, String](props)
    val failures = new AtomicLong(0)
    val firstFailure = new java.util.concurrent.atomic.AtomicReference[Exception]()
    val callback = new Callback {
      override def onCompletion(metadata: RecordMetadata, error: Exception): Unit =
        if (error != null) { failures.incrementAndGet(); firstFailure.compareAndSet(null, error); () }
    }
    val t0 = System.nanoTime()
    var i = 0
    while (i < n) {
      if (rate > 0) {
        val due = t0 + (i / rate * 1e9).toLong
        var now = System.nanoTime()
        while (now < due) { LockSupport.parkNanos(math.min(due - now, 200000L)); now = System.nanoTime() }
      }
      val (id, t, a, v, l) = rows(i)
      producer.send(new ProducerRecord[String, String](topic, null, json(Txn(id, t, a, v, l, System.currentTimeMillis()))), callback)
      i += 1
    }
    producer.send(new ProducerRecord[String, String](topic, null,
      json(Txn(FeatureLogic.FlushMarkerId, Double.MaxValue, 0.0, Array.fill(28)(0.0), 0, System.currentTimeMillis()))), callback)
    producer.flush()
    producer.close()
    if (failures.get() > 0) throw new IllegalStateException(s"Kafka failed to deliver ${failures.get()} records", firstFailure.get())
    val secs = (System.nanoTime() - t0) / 1e9
    f"""{"events": $n, "seconds": $secs%.2f, "events_per_second": ${n / secs}%.0f, "target_rate": $rate}"""
  }
}

object StreamJob {
  val txnSchema: StructType = new StructType()
    .add("txn_id", LongType).add("time", DoubleType).add("amount", DoubleType)
    .add("label", IntegerType).add("produce_ts_ms", LongType).add("v", ArrayType(DoubleType))

  def score(spark: SparkSession, txns: Dataset[Txn], model: LrModel): Dataset[Scored] = {
    import spark.implicits._
    txns
      .groupByKey(_ => 0) // single global stream: the dataset has no card/merchant id to partition by
      .flatMapGroupsWithState[StreamState, Scored](OutputMode.Append(), GroupStateTimeout.NoTimeout()) {
        (_: Int, rows: Iterator[Txn], state: GroupState[StreamState]) =>
          val (next, out) = FeatureLogic.process(state.getOption.getOrElse(StreamState.empty), rows, model)
          state.update(next)
          out.iterator
      }
  }

  def parseKafka(spark: SparkSession, raw: DataFrame): Dataset[Txn] = {
    import spark.implicits._
    raw.select(from_json($"value".cast("string"), txnSchema).as("j"))
      .select($"j.txn_id".as("txnId"), $"j.time", $"j.amount", $"j.v", $"j.label", $"j.produce_ts_ms".as("produceTsMs"))
      .as[Txn]
  }

  def run(spark: SparkSession, model: LrModel, bootstrap: String, topics: Topics, checkpoint: String,
          startingOffsets: String, maxOffsetsPerTrigger: Long, expected: Long, progressLog: String,
          idleTimeoutSec: Int): String = {
    val raw = {
      val r = spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", topics.in)
        .option("startingOffsets", startingOffsets)
        .option("failOnDataLoss", "false")
      (if (maxOffsetsPerTrigger > 0) r.option("maxOffsetsPerTrigger", maxOffsetsPerTrigger) else r).load()
    }
    val scored = score(spark, parseKafka(spark, raw), model)
    val total = new AtomicLong(0)
    val alerts = new AtomicLong(0)

    def writeBatch(ds: Dataset[Scored], batchId: Long): Unit = {
      ds.persist()
      val asKafka = (d: DataFrame) =>
        d.select(col("txnId").cast("string").as("key"), to_json(struct(d.columns.map(col).toIndexedSeq: _*)).as("value"))
      asKafka(ds.toDF()).write.format("kafka").option("kafka.bootstrap.servers", bootstrap)
        .option("topic", topics.scores).save()
      val a = ds.filter(col("isAlert"))
      asKafka(a.toDF()).write.format("kafka").option("kafka.bootstrap.servers", bootstrap)
        .option("topic", topics.alerts).save()
      alerts.addAndGet(a.count())
      total.addAndGet(ds.count()) // last: the driver loop stops once this reaches `expected`
      ds.unpersist()
    }

    Files.createDirectories(Paths.get(progressLog).getParent)
    Files.deleteIfExists(Paths.get(progressLog))
    val recordedInputs = new AtomicLong(0)
    spark.streams.addListener(new StreamingQueryListener {
      override def onQueryStarted(e: StreamingQueryListener.QueryStartedEvent): Unit = ()
      override def onQueryTerminated(e: StreamingQueryListener.QueryTerminatedEvent): Unit = ()
      override def onQueryProgress(e: StreamingQueryListener.QueryProgressEvent): Unit = {
        Files.write(Paths.get(progressLog), (e.progress.json.replace("\n", "") + "\n").getBytes("UTF-8"),
          StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        recordedInputs.addAndGet(e.progress.numInputRows)
      }
    })

    val q = scored.writeStream
      .queryName(s"fraud-stream-${topics.runId}")
      .outputMode("append")
      .option("checkpointLocation", checkpoint)
      .trigger(Trigger.ProcessingTime(0L))
      .foreachBatch(writeBatch _)
      .start()

    val t0 = System.nanoTime()
    var lastCount = -1L
    var lastChange = System.nanoTime()
    while (q.isActive && (expected <= 0 || total.get() < expected) &&
           (System.nanoTime() - lastChange) / 1e9 < idleTimeoutSec) {
      Thread.sleep(200)
      val c = total.get()
      if (c != lastCount) { lastCount = c; lastChange = System.nanoTime() }
    }
    q.exception.foreach(e => throw e)
    // foreachBatch finishes before its asynchronous progress callback. Record the final
    // input batch (including the flush marker) before stopping the query.
    if (expected > 0 && total.get() >= expected) {
      val deadline = System.nanoTime() + 60L * 1000000000L
      while (q.isActive && recordedInputs.get() < expected + 1 && System.nanoTime() < deadline) Thread.sleep(20)
    }
    q.exception.foreach(e => throw e)
    q.stop()
    require(expected <= 0 || total.get() == expected, s"Incomplete stream: scored ${total.get()} of $expected rows")
    require(expected <= 0 || recordedInputs.get() >= expected + 1,
      s"Incomplete progress record: ${recordedInputs.get()} of ${expected + 1} input rows")
    val secs = (System.nanoTime() - t0) / 1e9
    f"""{"scored": ${total.get()}, "alerts": ${alerts.get()}, "wall_seconds": $secs%.1f}"""
  }
}

object Report {
  private def pct(sorted: Array[Long], p: Double): Long =
    if (sorted.isEmpty) 0L else sorted(math.min(sorted.length - 1, math.ceil(p / 100.0 * sorted.length).toInt - 1).max(0))

  def run(spark: SparkSession, bootstrap: String, topics: Topics, batchPath: String, out: String, extra: Map[String, String]): String = {
    import spark.implicits._
    val schema = Encoders.product[Scored].schema
    val kafka = spark.read.format("kafka").option("kafka.bootstrap.servers", bootstrap)
      .option("subscribe", topics.scores).option("startingOffsets", "earliest").option("endingOffsets", "latest").load()
    val s = kafka.select(unix_millis($"timestamp").as("sinkTsMs"), from_json($"value".cast("string"), schema).as("j"))
      .select($"sinkTsMs", $"j.*").cache()
    val rows = s.count()
    val distinct = s.select("txnId").distinct().count()
    val lat = s.select(($"sinkTsMs" - $"produceTsMs").as("l")).as[Long].collect().sorted
    val proc = s.select(($"scoredTsMs" - $"produceTsMs").as("l")).as[Long].collect().sorted
    val r = s.agg(min("sinkTsMs"), max("sinkTsMs"), min("produceTsMs"), max("produceTsMs")).as[(Long, Long, Long, Long)].head()
    val sinkSpan = (r._2 - r._1) / 1000.0
    val produceSpan = (r._4 - r._3) / 1000.0

    val alertRows = spark.read.format("kafka").option("kafka.bootstrap.servers", bootstrap)
      .option("subscribe", topics.alerts).option("startingOffsets", "earliest").option("endingOffsets", "latest").load()
      .select(from_json($"value".cast("string"), schema).as("j")).select($"j.*").cache()
    val alertsInTopic = alertRows.count()
    val scoreAlertIds = s.filter($"isAlert").select("txnId")
    val topicAlertIds = alertRows.select("txnId")
    val alertTopicIdMismatches = scoreAlertIds.exceptAll(topicAlertIds).count() + topicAlertIds.exceptAll(scoreAlertIds).count()
    val b = spark.read.parquet(batchPath)
    val j = s.dropDuplicates("txnId").as("s").join(b.as("b"), $"s.txnId" === $"b.txnId", "full_outer")
    val p = j.agg(
      sum(when($"s.txnId".isNull, 1).otherwise(0)).as("missing_in_stream"),
      sum(when($"b.txnId".isNull, 1).otherwise(0)).as("extra_in_stream"),
      max(abs($"s.proba" - $"b.proba")).as("max_abs_proba_diff"),
      max(abs($"s.logit" - $"b.logit")).as("max_abs_logit_diff"),
      max(abs($"s.amountZ1h" - $"b.amountZ1h")).as("max_abs_amount_z_diff"),
      max(abs($"s.amountRatio1h" - $"b.amountRatio1h")).as("max_abs_amount_ratio_diff"),
      sum(when($"s.txnCount1h" =!= $"b.txnCount1h", 1).otherwise(0)).as("txn_count_mismatches"),
      sum(when($"s.isAlert" =!= $"b.isAlert", 1).otherwise(0)).as("alert_decision_mismatches"),
      sum(when($"s.isAlert", 1).otherwise(0)).as("stream_alerts"),
      sum(when($"s.isAlert" && $"s.label" === 1, 1).otherwise(0)).as("stream_alerts_true_fraud")
    ).head()

    val expectedRows = b.count()
    require(rows == expectedRows && distinct == expectedRows,
      s"Score coverage failed: rows=$rows distinct=$distinct batch=$expectedRows")
    Seq("missing_in_stream", "extra_in_stream", "txn_count_mismatches", "alert_decision_mismatches").foreach { name =>
      require(p.getAs[Long](name) == 0L, s"Parity failure: $name=${p.getAs[Long](name)}")
    }
    Seq("max_abs_proba_diff" -> 1e-12, "max_abs_logit_diff" -> 1e-9,
      "max_abs_amount_z_diff" -> 1e-9, "max_abs_amount_ratio_diff" -> 1e-9).foreach { case (name, tolerance) =>
      val value = p.getAs[java.lang.Number](name)
      require(value != null && java.lang.Double.isFinite(value.doubleValue()) && value.doubleValue() <= tolerance,
        s"Nonfinite or mismatched $name=$value (tolerance=$tolerance)")
    }
    require(alertsInTopic == p.getAs[Long]("stream_alerts") && alertTopicIdMismatches == 0L,
      s"Alert topic disagrees with scored decisions: rows=$alertsInTopic id_mismatches=$alertTopicIdMismatches")
    require(alertRows.filter(!coalesce($"isAlert", lit(false))).count() == 0L, "Alert topic contains a non-alert record")

    def f(d: Any): String = d match {
      case null => "null"
      case x: Double => f"$x%.3e"
      case x => x.toString
    }
    val fields = Seq(
      "run_id" -> s"\"${topics.runId}\"",
      "rows_in_scores_topic" -> rows.toString,
      "distinct_txn_ids" -> distinct.toString,
      "duplicates" -> (rows - distinct).toString,
      "e2e_latency_ms_p50" -> pct(lat, 50).toString,
      "e2e_latency_ms_p95" -> pct(lat, 95).toString,
      "e2e_latency_ms_p99" -> pct(lat, 99).toString,
      "e2e_latency_ms_max" -> lat.lastOption.getOrElse(0L).toString,
      "produce_to_scored_ms_p50" -> pct(proc, 50).toString,
      "produce_to_scored_ms_p95" -> pct(proc, 95).toString,
      "produce_span_s" -> f"$produceSpan%.2f",
      "sink_span_s" -> f"$sinkSpan%.2f",
      "throughput_events_per_s" -> f"${rows / math.max(sinkSpan, 1e-3)}%.0f",
      "records_in_alerts_topic" -> alertsInTopic.toString,
      "alert_topic_id_mismatches" -> alertTopicIdMismatches.toString
    ) ++ p.schema.fieldNames.indices.map(i => p.schema.fieldNames(i) -> f(p.get(i))) ++ extra.toSeq
    val json = fields.map { case (k, v) => s"  \"$k\": $v" }.mkString("{\n", ",\n", "\n}\n")
    Files.createDirectories(Paths.get(out).getParent)
    Files.write(Paths.get(out), json.getBytes("UTF-8"))
    json
  }
}
