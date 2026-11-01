"""Record the local runtime and timing protocol after a full replay suite."""
import argparse
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--broker-container", required=True)
args = parser.parse_args()
broker = json.loads(subprocess.check_output(["docker", "inspect", args.broker_container], text=True))[0]
image = json.loads(subprocess.check_output(["docker", "image", "inspect", broker["Image"]], text=True))[0]
java = subprocess.run(["java", "-version"], capture_output=True, text=True, check=True)
java_version = "\n".join(line for line in (java.stdout + java.stderr).splitlines() if not line.startswith("Picked up "))
cpu = platform.processor() or platform.machine()
if Path("/proc/cpuinfo").exists():
    cpu = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
               if line.startswith("model name"))
memory_kib = None
if Path("/proc/meminfo").exists():
    memory_kib = int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1])
classpath = (root / "target/runtime-classpath.txt").read_text()
spark_version = re.search(r"spark-sql_2\.13-([\d.]+)\.jar", classpath).group(1)
scala_version = re.search(r"scala-library-([\d.]+)\.jar", classpath).group(1)
processor_flag = re.search(r"-XX:ActiveProcessorCount=(\d+)", os.environ.get("JAVA_TOOL_OPTIONS", ""))
broker_command = " ".join(broker["Config"].get("Cmd", []))
broker_smp = re.search(r"--smp(?:=|\s+)(\d+)", broker_command)
broker_memory = re.search(r"--memory(?:=|\s+)(\S+)", broker_command)
report = {
    "run_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
    "cpu": cpu, "visible_cpu_threads": os.cpu_count(), "wsl_memory_kib": memory_kib,
    "java": java_version, "spark_runtime_jar_version": spark_version, "scala_runtime_jar_version": scala_version,
    "sbt_project_version": (root / "project/build.properties").read_text().strip().split("=", 1)[1],
    "python": platform.python_version(),
    "python_packages": {p: importlib.metadata.version(p) for p in ("numpy", "pandas", "pyarrow", "scikit-learn", "joblib")},
    "spark_master": os.environ.get("SPARK_MASTER", "local[4]"),
    "java_active_processor_count": int(processor_flag.group(1)) if processor_flag else None,
    "stream_max_heap": os.environ.get("JVM_HEAP", "4g"), "producer_max_heap": "2g", "state_store": "RocksDB",
    "redpanda": {"image": broker["Config"]["Image"], "image_id": broker["Image"],
                 "repo_digests": image.get("RepoDigests", []), "network_mode": broker["HostConfig"]["NetworkMode"],
                 "smp": int(broker_smp.group(1)) if broker_smp else None,
                 "process_memory": broker_memory.group(1) if broker_memory else None, "brokers": 1, "topic_partitions": 1},
    "protocol": {
        "transactions_per_run": 283726, "input_rows_with_flush_marker": 283727,
        "live_rates": [1000, 5000, 10000], "backlog_offset_caps": [20000, 0],
        "live_start": "producer starts after initial query progress",
        "e2e_latency": "producer event-creation timestamp to score Kafka record timestamp (default CreateTime)",
        "spark_throughput": "input rows including flush marker / summed durations of nonempty micro-batches",
        "backlog_latency": "includes preloaded queueing and is omitted from comparison tables",
        "coordination": "formal CPU timing serialized with the other workspace projects",
    },
}
(root / "results/environment.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
