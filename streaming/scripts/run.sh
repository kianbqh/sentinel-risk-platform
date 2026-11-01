#!/usr/bin/env bash
# Run a fraudstream command on the JVM directly (faster than `sbt run` for repeated runs).
# Usage: scripts/run.sh <prepare|batch-reference|produce|stream|report> [--opts]
set -euo pipefail
cd "$(dirname "$0")/.."
CP_FILE=target/runtime-classpath.txt
classes_dir=
if [[ -s $CP_FILE ]]; then
  IFS=: read -r classes_dir _ < "$CP_FILE"
fi
if [[ ! -f "$classes_dir/fraudstream/Main.class" || build.sbt -nt $CP_FILE || project/build.properties -nt $CP_FILE || -n $(find src/main -type f -newer "$CP_FILE" -print -quit 2>/dev/null) ]]; then
  mkdir -p target
  sbt -batch -error writeRuntimeClasspath
fi
exec java -Xmx${JVM_HEAP:-4g} \
  --add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.lang.invoke=ALL-UNNAMED \
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED --add-opens=java.base/java.io=ALL-UNNAMED \
  --add-opens=java.base/java.net=ALL-UNNAMED --add-opens=java.base/java.nio=ALL-UNNAMED \
  --add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.util.concurrent=ALL-UNNAMED \
  --add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED --add-opens=java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens=java.base/sun.nio.cs=ALL-UNNAMED --add-opens=java.base/sun.security.action=ALL-UNNAMED \
  --add-opens=java.base/sun.util.calendar=ALL-UNNAMED -Djdk.reflect.useDirectMethodHandle=false \
  -Dio.netty.tryReflectionSetAccessible=true \
  -cp "$(cat $CP_FILE)" fraudstream.Main "$@"
