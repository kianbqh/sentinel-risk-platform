package fraudstream

import java.net.{HttpURLConnection, URI}
import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}
import java.security.MessageDigest
import java.time.OffsetDateTime
import java.util.UUID

import scala.jdk.CollectionConverters._

import com.fasterxml.jackson.databind.{DeserializationFeature, ObjectMapper}
import com.fasterxml.jackson.module.scala.DefaultScalaModule
import org.apache.spark.sql.{Encoders, SparkSession}
import org.apache.spark.sql.execution.streaming.runtime.MemoryStream
import org.apache.spark.sql.streaming.{StreamingQueryListener, StreamingQueryProgress}
import org.scalatest.BeforeAndAfterAll
import org.scalatest.funsuite.AnyFunSuite

class StreamMetricsSpec extends AnyFunSuite with BeforeAndAfterAll {
  private lazy val spark = SparkSession.builder().master("local[2]").appName("stream-metrics-test")
    .config("spark.sql.shuffle.partitions", "1").config("spark.ui.enabled", "false").getOrCreate()

  override def afterAll(): Unit = { if (SparkSession.getActiveSession.nonEmpty) spark.stop() }

  private def request(port: Int, path: String = "/metrics", method: String = "GET"): (Int, String, String) = {
    val c = URI.create(s"http://127.0.0.1:$port$path").toURL.openConnection().asInstanceOf[HttpURLConnection]
    c.setRequestMethod(method)
    c.setConnectTimeout(2000)
    c.setReadTimeout(2000)
    try {
      val status = c.getResponseCode
      val stream = if (status >= 400) c.getErrorStream else c.getInputStream
      val body = try new String(stream.readAllBytes(), StandardCharsets.UTF_8) finally stream.close()
      (status, c.getContentType, body)
    } finally c.disconnect()
  }

  private def scalar(body: String, name: String): Double =
    body.linesIterator.find(_.startsWith(name + " ")).getOrElse(fail(s"missing metric $name")).split(" ").last.toDouble

  private def awaitScalar(port: Int, name: String, expected: Double, timeoutSeconds: Int): String = {
    val deadline = System.nanoTime() + timeoutSeconds.toLong * 1000000000L
    var body = request(port)._3
    while (scalar(body, name) != expected && System.nanoTime() < deadline) {
      Thread.sleep(20)
      body = request(port)._3
    }
    assert(scalar(body, name) == expected, body)
    body
  }

  test("metrics are opt-in and environment configuration is validated") {
    assert(StreamMetricsConfig.fromEnv(Map.empty).isEmpty)
    assert(StreamMetricsConfig.fromEnv(Map("STREAM_METRICS_ENABLED" -> "false", "STREAM_METRICS_PORT" -> "invalid")).isEmpty)
    assert(StreamMetricsConfig.fromEnv(Map("STREAM_METRICS_ENABLED" -> "true")).contains(StreamMetricsConfig("0.0.0.0", 9108)))
    intercept[IllegalArgumentException](StreamMetricsConfig.fromEnv(Map("STREAM_METRICS_ENABLED" -> "yes")))
    intercept[IllegalArgumentException](StreamMetricsConfig.fromEnv(Map("STREAM_METRICS_ENABLED" -> "true", "STREAM_METRICS_PORT" -> "0")))
    intercept[IllegalArgumentException](StreamMetricsConfig.fromEnv(Map("STREAM_METRICS_ENABLED" -> "true", "STREAM_METRICS_HOST" -> "")))
  }

  test("executor latency accumulator preserves cumulative buckets across copy, merge and reset") {
    val a = new ScoringLatencyAccumulator
    assert(a.isZero)
    a.add(1000L)
    a.add(7000L)
    val copy = a.copy()
    a.add(2000000000L)
    assert(copy.value.count == 2L && copy.value.totalNanos == 8000L)
    assert(copy.value.buckets.take(3) == Vector(1L, 1L, 2L))
    copy.merge(a)
    assert(copy.value.count == 5L && copy.value.buckets.last == 4L)
    assert(copy.value.buckets.sliding(2).forall(pair => pair(0) <= pair(1)))
    copy.reset()
    assert(copy.isZero && copy.value == ScoringLatencyAccumulator.Empty)
    intercept[IllegalArgumentException](a.add(-1L))
  }

  test("timing leaves feature, probability, threshold, state and timestamp semantics unchanged") {
    val rows = Fixtures.stream(80, seed = 90) :+ Fixtures.marker
    val baseline = FeatureLogic.process(StreamState.empty, rows.iterator, Fixtures.model, () => 123L)
    val timer = new ScoringLatencyAccumulator
    val measured = FeatureLogic.process(StreamState.empty, rows.iterator, Fixtures.model, () => 123L, Some(timer))
    assert(measured._2 == baseline._2)
    assert(measured._1.lastEmittedTime == baseline._1.lastEmittedTime)
    assert(measured._1.winTimes.toVector == baseline._1.winTimes.toVector)
    assert(measured._1.winAmounts.toVector == baseline._1.winAmounts.toVector)
    assert(timer.value.count == 80L && timer.value.totalNanos > 0L)
  }

  test("HTTP endpoint serves Prometheus text with bounded labels and rejects other requests") {
    val endpoint = StreamMetricsEndpoint.start(StreamMetricsConfig("127.0.0.1", 0), new StreamMetricsRegistry)
    try {
      val (status, contentType, body) = request(endpoint.port)
      assert(status == 200 && contentType == "text/plain; version=0.0.4; charset=utf-8")
      assert(body.endsWith("\n"))
      assert(scalar(body, "sentinel_spark_query_active") == 0)
      assert(scalar(body, "sentinel_spark_scoring_duration_seconds_count") == 0)
      assert(body.linesIterator.filter(_.contains("{")).forall(_.matches(".*_bucket\\{le=\"[^\"]+\"\\} [0-9]+")))
      assert(request(endpoint.port, "/metrics/unexpected")._1 == 404)
      assert(request(endpoint.port, method = "POST")._1 == 405)
    } finally endpoint.close()
  }

  test("start, idle and repeated progress refresh activity without changing distinct progress or accepting other runs") {
    var clock = 100000L
    val registry = new StreamMetricsRegistry(() => clock)
    val listener = new StreamMetricsListener("activity-test", registry, new ScoringLatencyAccumulator)
    val id = UUID.fromString("00000000-0000-0000-0000-000000000001")
    val run = UUID.fromString("00000000-0000-0000-0000-000000000002")
    val otherRun = UUID.fromString("00000000-0000-0000-0000-000000000003")
    def metric(name: String): Double = scalar(registry.render, "sentinel_spark_" + name)
    def started(name: String, r: UUID): Unit = listener.onQueryStarted(
      new StreamingQueryListener.QueryStartedEvent(id, r, name, "2026-01-01T00:00:00Z"))
    val mapper = new ObjectMapper().registerModule(DefaultScalaModule)
      .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
    def progress(r: UUID, batch: Long): StreamingQueryProgress = mapper.readValue(
      s"""{"id":"$id","runId":"$r","name":"activity-test","timestamp":"2026-01-01T00:00:00Z",
         |"batchId":$batch,"batchDuration":50,"durationMs":{"triggerExecution":50},"eventTime":{},
         |"stateOperators":[],"sources":[{"description":"fixture","startOffset":"0","endOffset":"1",
         |"latestOffset":"1","numInputRows":3,"inputRowsPerSecond":0.0,"processedRowsPerSecond":0.0}],
         |"sink":{"description":"fixture","numOutputRows":3},"observedMetrics":{}}""".stripMargin,
      classOf[StreamingQueryProgress])

    started("unmonitored", otherRun)
    assert(metric("query_active") == 0 && metric("last_activity_timestamp_seconds") == 0)
    started("activity-test", run)
    assert(metric("query_active") == 1 && metric("last_activity_timestamp_seconds") == 100)
    assert(metric("last_progress_timestamp_seconds") == 0 && metric("batches_total") == 0)
    clock = 110000L
    listener.recordIdle(otherRun)
    assert(metric("last_activity_timestamp_seconds") == 100)
    listener.recordIdle(run)
    assert(metric("last_activity_timestamp_seconds") == 110)
    assert(metric("last_progress_timestamp_seconds") == 0 && metric("batches_total") == 0)
    clock = 120000L
    val batch = progress(run, 7L)
    listener.recordProgress(batch)
    assert(metric("last_activity_timestamp_seconds") == 120 && metric("last_progress_timestamp_seconds") == 120)
    assert(metric("batches_total") == 1 && metric("input_rows_total") == 3)
    clock = 130000L
    listener.recordProgress(batch)
    assert(metric("last_activity_timestamp_seconds") == 130 && metric("last_progress_timestamp_seconds") == 120)
    assert(metric("batches_total") == 1 && metric("input_rows_total") == 3)
    val beforeWrongRun = registry.render
    clock = 140000L
    listener.recordProgress(progress(otherRun, 8L))
    listener.recordIdle(otherRun)
    assert(registry.render == beforeWrongRun)
  }

  test("real Structured Streaming progress and executor scoring measurements reach the pull endpoint") {
    implicit val sqlCtx = spark.sqlContext
    implicit val enc = Encoders.product[Txn]
    val configBytes = Files.readAllBytes(Paths.get("metrics-smoke.json"))
    val config = new ObjectMapper().readTree(configBytes)
    val rowCount = config.get("rows").asInt()
    val split = config.get("first_batch_rows").asInt()
    val timeout = config.get("listener_timeout_seconds").asInt()
    val rows = Fixtures.stream(rowCount, config.get("seed").asInt())
    val baseline = FeatureLogic.process(StreamState.empty, (rows :+ Fixtures.marker).iterator, Fixtures.model)._2.map(s => s.txnId -> s).toMap
    val input = MemoryStream[Txn]
    val queryName = "scored_metrics_smoke"
    val metrics = StreamMetrics.start(spark, queryName, StreamMetricsConfig("127.0.0.1", 0))
    val started = System.nanoTime()
    val query = StreamJob.score(spark, input.toDS(), Fixtures.model, Some(metrics.scoring)).writeStream
      .format("memory").queryName(queryName).outputMode("append").start()
    try {
      assert(scalar(request(metrics.endpoint.port)._3, "sentinel_spark_query_active") == 1)
      assert(scalar(request(metrics.endpoint.port)._3, "sentinel_spark_last_activity_timestamp_seconds") > 0)
      input.addData(rows.take(split))
      query.processAllAvailable()
      input.addData(rows.drop(split) :+ Fixtures.marker)
      query.processAllAvailable()
      val body = awaitScalar(metrics.endpoint.port, "sentinel_spark_input_rows_total", rowCount + 1, timeout)
      val observed = spark.table(queryName).as(Encoders.product[Scored]).collect()
      assert(observed.length == rowCount)
      observed.foreach { s =>
        val ref = baseline(s.txnId)
        assert(s.proba == ref.proba && s.logit == ref.logit && s.isAlert == ref.isAlert)
        assert(s.txnCount1h == ref.txnCount1h && s.amountZ1h == ref.amountZ1h && s.amountRatio1h == ref.amountRatio1h)
      }
      assert(scalar(body, "sentinel_spark_scoring_duration_seconds_count") == rowCount)
      assert(scalar(body, "sentinel_spark_scoring_duration_seconds_sum") > 0)
      assert(scalar(body, "sentinel_spark_batch_duration_seconds_sum") > 0)
      val allProgress = query.recentProgress.toVector
      // recentProgress also retains idle snapshots (which can precede a real batch
      // with the same ID). onQueryProgress receives the actual batch events. Both
      // input chunks in this fixture are nonempty, so select those real batches.
      val progress = allProgress.filter(_.numInputRows > 0).groupBy(_.batchId).toVector.sortBy(_._1).map(_._2.head)
      assert(scalar(body, "sentinel_spark_batch_duration_seconds_count") == progress.length)
      assert(scalar(body, "sentinel_spark_batch_input_rows") == progress.last.numInputRows)
      val batchSum = progress.map(_.durationMs.get("triggerExecution").longValue()).sum.toDouble / 1e3
      assert(math.abs(scalar(body, "sentinel_spark_batch_duration_seconds_sum") - batchSum) < 1e-9,
        s"exported metrics:\n$body\nraw progress:\n${allProgress.map(_.json).mkString("\n")}")

      // Listener delivery is asynchronous. Re-delivery must not count a batch twice.
      metrics.listener.recordProgress(progress.last)
      val afterRepeat = request(metrics.endpoint.port)._3
      def withoutActivity(value: String): Vector[String] = value.linesIterator
        .filterNot(_.startsWith("sentinel_spark_last_activity_timestamp_seconds ")).toVector
      assert(withoutActivity(afterRepeat) == withoutActivity(body))
      assert(scalar(afterRepeat, "sentinel_spark_last_activity_timestamp_seconds") >=
        scalar(body, "sentinel_spark_last_activity_timestamp_seconds"))
      query.stop()
      val stopped = awaitScalar(metrics.endpoint.port, "sentinel_spark_query_active", 0, timeout)
      sys.env.get("STREAM_METRICS_EVIDENCE_DIR").foreach { destination =>
        val dir = Paths.get(destination)
        Files.createDirectories(dir)
        Files.write(dir.resolve("scala-metrics.prom"), stopped.getBytes(StandardCharsets.UTF_8))
        Files.write(dir.resolve("scala-progress.jsonl"), (allProgress.map(_.json.replace("\n", "")).mkString("\n") + "\n").getBytes(StandardCharsets.UTF_8))
        val mapper = new ObjectMapper()
        val result = mapper.createObjectNode()
        result.put("timestamp", OffsetDateTime.now().toString)
        result.put("environment", sys.env.getOrElse("STREAM_METRICS_ENVIRONMENT", "not recorded"))
        result.put("command", sys.env.getOrElse("STREAM_METRICS_TEST_COMMAND", "sbt -batch test"))
        result.put("git_sha", sys.env.getOrElse("STREAM_METRICS_GIT_SHA", "not recorded"))
        result.put("source_state", sys.env.getOrElse("STREAM_METRICS_SOURCE_STATE", "working tree; source_sha256 identifies the measured files"))
        result.put("config_sha256", MessageDigest.getInstance("SHA-256").digest(configBytes).map(b => f"${b & 0xff}%02x").mkString)
        result.set[com.fasterxml.jackson.databind.JsonNode]("config", config)
        val sourceHashes = result.putObject("source_sha256")
        val sourceFiles = Files.walk(Paths.get("src"))
        try {
          (sourceFiles.iterator().asScala.filter(p => Files.isRegularFile(p) && p.toString.endsWith(".scala")).toVector ++
            Vector(Paths.get("build.sbt"), Paths.get("metrics-smoke.json"), Paths.get("scripts/test_metrics.sh"))).sortBy(_.toString).foreach { p =>
            sourceHashes.put(p.toString.replace('\\', '/'), MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p)).map(b => f"${b & 0xff}%02x").mkString)
          }
        } finally sourceFiles.close()
        result.put("spark_version", spark.version)
        result.put("scala_version", util.Properties.versionNumberString)
        result.put("java_version", System.getProperty("java.version"))
        result.put("os", System.getProperty("os.name") + " " + System.getProperty("os.version"))
        result.put("cpu_model", sys.env.getOrElse("STREAM_METRICS_CPU_MODEL", "not recorded"))
        result.put("logical_cpus_available", Runtime.getRuntime.availableProcessors())
        result.put("gpu", "not used; CPU-only test")
        result.put("driver", "not applicable; CPU-only test")
        result.put("cuda", "not applicable; CPU-only test")
        result.put("input_rows_including_flush", rowCount + 1)
        result.put("scored_rows", observed.length)
        result.put("probability_mismatches", 0)
        result.put("threshold_mismatches", 0)
        result.put("metrics_http_status", 200)
        result.put("metric_families", stopped.linesIterator.count(_.startsWith("# TYPE ")))
        result.put("metric_samples", stopped.linesIterator.count(line => line.nonEmpty && !line.startsWith("#")))
        result.put("scoring_observations", scalar(stopped, "sentinel_spark_scoring_duration_seconds_count").toLong)
        result.put("scoring_seconds_sum", scalar(stopped, "sentinel_spark_scoring_duration_seconds_sum"))
        result.put("completed_batches", scalar(stopped, "sentinel_spark_batches_total").toLong)
        result.put("raw_progress_events_including_idle", allProgress.length)
        result.put("batch_progress_protocol", "onQueryProgress batch events; recentProgress also retains idle snapshots, excluded from this fixture's two nonempty batch comparison")
        result.put("batch_seconds_sum", scalar(stopped, "sentinel_spark_batch_duration_seconds_sum"))
        result.put("wall_seconds", (System.nanoTime() - started).toDouble / 1e9)
        result.put("timer_includes", "query startup, two synthetic microbatches, listener waits, HTTP scrapes, score parity check, query stop and evidence collection up to the summary write")
        Files.write(dir.resolve("scala-metrics-smoke.json"), (mapper.writerWithDefaultPrettyPrinter().writeValueAsString(result) + "\n").getBytes(StandardCharsets.UTF_8))
      }
    } finally { query.stop(); metrics.close() }
  }
}
