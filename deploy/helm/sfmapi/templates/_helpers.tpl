{{/*
Common name + label helpers, mirroring Bitnami / official chart conventions.
*/}}

{{- define "sfmapi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sfmapi.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "sfmapi.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sfmapi.labels" -}}
helm.sh/chart: {{ include "sfmapi.chart" . }}
{{ include "sfmapi.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "sfmapi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sfmapi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "sfmapi.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "sfmapi.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Key inside postgresql.auth.existingSecret that holds the app-user
password. Mirrors the Bitnami subchart's auth.secretKeys.userPasswordKey
so one secret serves both the subchart and the sfmapi pods.
*/}}
{{- define "sfmapi.dbPasswordKey" -}}
{{- default "password" (((.Values.postgresql.auth).secretKeys).userPasswordKey) -}}
{{- end -}}

{{/*
Compute the SCENEAPI_DB_URL value. Prefer the bundled Postgres subchart
when enabled; otherwise the operator must set `env.extraEnv.SCENEAPI_DB_URL`
(or ship it via `env.existingSecret`). When postgresql.auth.existingSecret
is set, the password segment is the Kubernetes dependent-env reference
$(SCENEAPI_DB_PASSWORD) — expanded by the kubelet from the secretKeyRef env
var emitted just before SCENEAPI_DB_URL in "sfmapi.commonEnv" — so no
plaintext password lands in the pod spec. Without an existingSecret, a
plaintext postgresql.auth.password is required (render fails otherwise).
*/}}
{{- define "sfmapi.dbUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- if .Values.postgresql.auth.existingSecret -}}
postgresql+psycopg://{{ .Values.postgresql.auth.username }}:$(SCENEAPI_DB_PASSWORD)@{{ .Release.Name }}-postgresql:5432/{{ .Values.postgresql.auth.database }}
{{- else -}}
postgresql+psycopg://{{ .Values.postgresql.auth.username }}:{{ required "postgresql.enabled=true needs a database password: set postgresql.auth.existingSecret (recommended) or postgresql.auth.password" .Values.postgresql.auth.password }}@{{ .Release.Name }}-postgresql:5432/{{ .Values.postgresql.auth.database }}
{{- end -}}
{{- else -}}
{{ default "" (index .Values.env.extraEnv "SCENEAPI_DB_URL") }}
{{- end -}}
{{- end -}}

{{- define "sfmapi.redisUrl" -}}
{{- if .Values.redis.enabled -}}
redis://{{ .Release.Name }}-redis-master:6379/0
{{- else -}}
{{ default "" (index .Values.env.extraEnv "SCENEAPI_REDIS_URL") }}
{{- end -}}
{{- end -}}

{{- define "sfmapi.image" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.image.repository -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- if $reg -}}
{{- printf "%s/%s:%s" $reg $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "sfmapi.workerImage" -}}
{{- $reg := .Values.worker.image.registry -}}
{{- $repo := .Values.worker.image.repository -}}
{{- $tag := default .Chart.AppVersion .Values.worker.image.tag -}}
{{- if $reg -}}
{{- printf "%s/%s:%s" $reg $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/*
Common environment block injected into both web and worker pods.
SCENEAPI_DB_PASSWORD must precede SCENEAPI_DB_URL: Kubernetes expands the
$(SCENEAPI_DB_PASSWORD) reference only from env vars defined earlier in
the list. SCENEAPI_DB_URL / SCENEAPI_REDIS_URL are omitted (not emitted
empty) when unset here, so values supplied via env.existingSecret
(envFrom) are not shadowed by explicit empty entries.
*/}}
{{- define "sfmapi.commonEnv" -}}
{{- if and .Values.postgresql.enabled .Values.postgresql.auth.existingSecret }}
- name: SCENEAPI_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgresql.auth.existingSecret | quote }}
      key: {{ include "sfmapi.dbPasswordKey" . | quote }}
{{- end }}
{{- with include "sfmapi.dbUrl" . }}
- name: SCENEAPI_DB_URL
  value: {{ . | quote }}
{{- end }}
{{- with include "sfmapi.redisUrl" . }}
- name: SCENEAPI_REDIS_URL
  value: {{ . | quote }}
{{- end }}
- name: SCENEAPI_AUTH_MODE
  value: {{ .Values.env.authMode | quote }}
- name: SCENEAPI_LOG_LEVEL
  value: {{ .Values.env.logLevel | quote }}
- name: SCENEAPI_INLINE_TASKS
  value: {{ .Values.env.inlineTasks | quote }}
- name: SCENEAPI_WORKSPACE_ROOT
  value: "/workspaces"
- name: SCENEAPI_BLOB_ROOT
  value: "/workspaces/_blobs"
- name: SCENEAPI_S3_CACHE_ROOT
  value: "/workspaces/_cache/s3"
{{- range $k, $v := .Values.env.extraEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
