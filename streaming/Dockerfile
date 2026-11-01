FROM eclipse-temurin:17-jdk AS build
WORKDIR /src
ADD https://github.com/sbt/sbt/releases/download/v1.13.1/sbt-1.13.1.tgz /tmp/sbt.tgz
RUN echo '4a20e36c1642b6d5be34ef641d2ed6c8c0d464e6145283730d5566716e4083be  /tmp/sbt.tgz' | sha256sum -c - && tar -xzf /tmp/sbt.tgz -C /opt
ENV PATH=/opt/sbt/bin:$PATH JAVA_TOOL_OPTIONS=-XX:ActiveProcessorCount=4
COPY streaming/build.sbt ./
COPY streaming/project ./project
RUN sbt -batch update
COPY streaming/src ./src
COPY streaming/model ./model
RUN sbt -batch assembly

FROM eclipse-temurin:17-jre
RUN useradd --uid 10001 --create-home sentinel && mkdir -p /state /results && chown sentinel /state /results
WORKDIR /opt/stream
COPY --from=build /src/target/scala-2.13/*assembly*.jar app.jar
COPY streaming/model ./model
ENV SPARK_LOCAL_IP=127.0.0.1 SPARK_MASTER=local[2] JAVA_TOOL_OPTIONS=-XX:ActiveProcessorCount=4
USER 10001
ENTRYPOINT ["java", "-Xmx2g", "--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED", "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED", "--add-opens=java.base/java.io=ALL-UNNAMED", "--add-opens=java.base/java.net=ALL-UNNAMED", "--add-opens=java.base/java.nio=ALL-UNNAMED", "--add-opens=java.base/java.util=ALL-UNNAMED", "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED", "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED", "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED", "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED", "--add-opens=java.base/sun.security.action=ALL-UNNAMED", "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED", "-Djdk.reflect.useDirectMethodHandle=false", "-Dio.netty.tryReflectionSetAccessible=true", "-jar", "app.jar"]
