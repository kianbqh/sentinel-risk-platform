package fraudstream

/** Entry point: sbt "run <command> [--key value ...]". Commands: prepare, batch-reference, produce, stream, report. */
object Main {
  def parse(args: Seq[String]): Map[String, String] =
    args.grouped(2).collect { case Seq(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap

  def main(argv: Array[String]): Unit = {
    val cmd = argv.headOption.getOrElse(sys.error("usage: <prepare|batch-reference|produce|stream|report> [--opts]"))
    val o = parse(argv.toSeq.drop(1))
    def opt(k: String, d: String) = o.getOrElse(k, d)
    val bootstrap = opt("bootstrap", sys.env.getOrElse("KAFKA_BOOTSTRAP", "127.0.0.1:19092"))
    val runId = opt("run-id", "demo")
    val spark = Spark.session(s"fraudstream-$cmd")
    val model = LrModel.load(opt("model", "model/lr_model.json"))
    val res = cmd match {
      case "prepare" =>
        s"""{"replay_rows": ${Prepare.run(spark, opt("raw", "data/creditcard.parquet"), opt("out", "data/replay.parquet"))}}"""
      case "batch-reference" =>
        s"""{"batch_rows": ${BatchReference.run(spark, opt("replay", "data/replay.parquet"), model, opt("out", "data/batch_scores.parquet"))}}"""
      case "produce" =>
        Producer.run(spark, opt("replay", "data/replay.parquet"), bootstrap, Topics(runId).in, opt("rate", "0").toDouble, opt("limit", "0").toInt)
      case "stream" =>
        StreamJob.run(spark, model, bootstrap, Topics(runId), opt("checkpoint", s"checkpoints/$runId"),
          opt("starting-offsets", "earliest"), opt("max-offsets-per-trigger", "0").toLong, opt("expected", "0").toLong,
          opt("progress-log", s"results/${runId}_progress.jsonl"), opt("idle-timeout-s", "60").toInt)
      case "report" =>
        Report.run(spark, bootstrap, Topics(runId), opt("batch", "data/batch_scores.parquet"), opt("out", s"results/$runId.json"),
          o.filter(_._1.startsWith("note-")).map { case (k, v) => k.drop(5) -> s"\"$v\"" })
      case other => sys.error(s"unknown command $other")
    }
    println(res)
    spark.stop()
  }
}
