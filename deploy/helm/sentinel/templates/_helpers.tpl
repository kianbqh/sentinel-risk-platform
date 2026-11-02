{{- define "sentinel.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 40 | trimSuffix "-" -}}
{{- end -}}
{{- define "sentinel.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "sentinel.name" .) | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- define "sentinel.labels" -}}
app.kubernetes.io/name: {{ include "sentinel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
{{- define "sentinel.kafka" -}}
{{- if .Values.kafka.enabled -}}
{{- printf "%s-kafka:9092" (include "sentinel.fullname" .) -}}
{{- else -}}
{{- required "kafka.bootstrapServers is required for external Kafka" .Values.kafka.bootstrapServers -}}
{{- end -}}
{{- end -}}
