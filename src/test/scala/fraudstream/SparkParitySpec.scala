package fraudstream

import org.apache.spark.sql.{Encoders, SparkSession}
import org.apache.spark.sql.execution.streaming.runtime.MemoryStream
import org.scalatest.BeforeAndAfterAll
import org.scalatest.funsuite.AnyFunSuite

class SparkParitySpec extends AnyFunSuite with BeforeAndAfterAll {
  import Fixtures._
  private lazy val spark = SparkSession.builder().master("local[2]").appName("test")
    .config("spark.sql.shuffle.partitions", "1").config("spark.ui.enabled", "false").getOrCreate()

  override def afterAll(): Unit = spark.stop()

  private def replayDf(rows: Vector[Txn]) = {
    import spark.implicits._
    rows.map(t => (t.txnId, t.time, t.amount, t.v, t.label)).toDF("txn_id", "time", "amount", "v", "label")
  }

  test("Spark batch window features (rangeBetween) equal the brute-force definition") {
    val rows = stream(600, seed = 11)
    val ref = bruteForce(rows)
    val batch = BatchReference.score(spark, replayDf(rows), model).collect()
    assert(batch.length == rows.size)
    batch.foreach(s => assert(s.txnCount1h == ref(s.txnId).count))
  }

  test("Structured Streaming job (flatMapGroupsWithState) reproduces batch scores exactly") {
    implicit val sqlCtx = spark.sqlContext
    implicit val enc = Encoders.product[Txn]
    val rows = stream(900, seed = 12)
    val batch = BatchReference.score(spark, replayDf(rows), model).collect().map(s => s.txnId -> s).toMap

    val input = MemoryStream[Txn]
    val q = StreamJob.score(spark, input.toDS(), model).writeStream
      .format("memory").queryName("scored").outputMode("append").start()
    try {
      Seq(rows.take(250), rows.slice(250, 251), rows.slice(251, 700), rows.drop(700) :+ marker).foreach { chunk =>
        input.addData(chunk)
        q.processAllAvailable()
      }
      val streamed = spark.table("scored").as(Encoders.product[Scored]).collect()
      assert(streamed.length == rows.size)
      streamed.foreach { s =>
        val b = batch(s.txnId)
        assert(s.txnCount1h == b.txnCount1h)
        assert(math.abs(s.proba - b.proba) < 1e-12, s"txn ${s.txnId}")
        assert(s.isAlert == b.isAlert)
      }
    } finally q.stop()
  }

  test("producer JSON round-trips through the Kafka value parser") {
    import spark.implicits._
    val t = Txn(42L, 12.5, 0.1 + 0.2, Array.tabulate(28)(i => i * 1e-17 - 3.3), 1, 1700000000123L)
    val parsed = StreamJob.parseKafka(spark, Seq(Producer.json(t)).toDF("value")).collect().head
    assert(parsed.txnId == 42L && parsed.amount == t.amount && parsed.produceTsMs == t.produceTsMs)
    assert(parsed.v.toVector == t.v.toVector)
  }
}
