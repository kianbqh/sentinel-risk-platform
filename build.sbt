ThisBuild / scalaVersion := "2.13.18"
ThisBuild / organization := "io.github.kianbqh"
ThisBuild / version := "0.1.0"

val sparkVersion = "4.3.0"

lazy val root = (project in file("."))
  .settings(
    name := "fraud-streaming-scala",
    libraryDependencies ++= Seq(
      "org.apache.spark" %% "spark-sql" % sparkVersion,
      "org.apache.spark" %% "spark-sql-kafka-0-10" % sparkVersion,
      "org.scalatest" %% "scalatest" % "3.2.19" % Test
    ),
    scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked"),
    // Spark needs its own JVM with these module flags on Java 17+.
    fork := true,
    Test / fork := true,
    Test / parallelExecution := false,
    run / connectInput := true,
    outputStrategy := Some(StdoutOutput),
    javaOptions ++= Seq(
      "-Xmx4g",
      "--add-opens=java.base/java.lang=ALL-UNNAMED",
      "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
      "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
      "--add-opens=java.base/java.io=ALL-UNNAMED",
      "--add-opens=java.base/java.net=ALL-UNNAMED",
      "--add-opens=java.base/java.nio=ALL-UNNAMED",
      "--add-opens=java.base/java.util=ALL-UNNAMED",
      "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
      "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
      "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
      "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED",
      "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
      "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED",
      "-Djdk.reflect.useDirectMethodHandle=false",
      "-Dio.netty.tryReflectionSetAccessible=true"
    ),
    assembly / mainClass := Some("fraudstream.Main"),
    assembly / assemblyMergeStrategy := {
      case PathList("META-INF", "services", _*) => MergeStrategy.concat
      case PathList("META-INF", _*)             => MergeStrategy.discard
      case "reference.conf"                     => MergeStrategy.concat
      case _                                    => MergeStrategy.first
    }
  )
