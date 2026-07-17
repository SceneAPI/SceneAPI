#!/usr/bin/env bash
# End-to-end smoke test for the deploy stack.
#
# Brings up `deploy/docker-compose.yml`, walks the public API
# (project -> chunked upload -> dataset -> image), then tears down.
#
# Usage:
#   bash scripts/smoke.sh                 # default ports, default tenant
#   bash scripts/smoke.sh --keep          # leave the stack running on success
#   SCENEAPI_WEB_PORT=18080 bash scripts/smoke.sh   # override web port
#
# Requirements:
#   - docker / docker compose
#   - curl
#   - python3 (for sha256 + json)
#
# Exit codes:
#   0 on success
#   non-zero on any step failure (also prints `web` container logs)

set -euo pipefail

KEEP=0
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-sfmapi-smoke}"
WEB_PORT="${SCENEAPI_WEB_PORT:-8080}"
PG_PORT="${SCENEAPI_PG_PORT:-55432}"
REDIS_PORT="${SCENEAPI_REDIS_PORT:-56379}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.yml"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

require() {
    command -v "$1" >/dev/null || { echo "missing required tool: $1" >&2; exit 2; }
}
require docker
require curl
require python3

BASE_URL="http://127.0.0.1:${WEB_PORT}"

cleanup() {
    if [[ $KEEP -eq 1 ]]; then
        echo
        echo "[--keep] leaving stack up. Tear down manually with:"
        echo "  docker compose -p $PROJECT_NAME -f $COMPOSE_FILE down -v"
        return
    fi
    echo
    echo "Tearing down..."
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans \
        >/dev/null 2>&1 || true
}

dump_logs_on_failure() {
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo
        echo "===== smoke FAILED (exit $rc) — last 80 lines of web logs ====="
        docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail=80 web 2>&1 || true
    fi
    cleanup
    exit $rc
}
trap dump_logs_on_failure EXIT

curl_json() {
    # curl_json METHOD PATH [JSON_BODY] [--header X:Y ...]
    local method="$1" path="$2"
    shift 2
    local body="${1:-}"
    if [[ -n "$body" ]]; then shift; fi
    if [[ -n "$body" ]]; then
        curl -fsS -X "$method" "${BASE_URL}${path}" \
            -H "Content-Type: application/json" \
            -d "$body" "$@"
    else
        curl -fsS -X "$method" "${BASE_URL}${path}" "$@"
    fi
}

jget() {
    # jget JSON_STRING DOTTED_KEY  -> prints the value
    python3 -c "import sys, json; obj=json.loads(sys.argv[1])
for k in sys.argv[2].split('.'):
    obj = obj[int(k)] if k.isdigit() else obj[k]
print(obj)" "$1" "$2"
}

echo "==> bringing up stack on web=$WEB_PORT pg=$PG_PORT redis=$REDIS_PORT"
SCENEAPI_WEB_PORT="$WEB_PORT" \
SCENEAPI_PG_PORT="$PG_PORT" \
SCENEAPI_REDIS_PORT="$REDIS_PORT" \
SCENEAPI_AUTH_MODE=none \
SCENEAPI_PG_USER=sfm SCENEAPI_PG_PASS=sfm SCENEAPI_PG_DB=sfmapi \
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --wait

echo "==> waiting for /healthz"
for i in $(seq 1 60); do
    if curl -fsS "${BASE_URL}/healthz" >/dev/null 2>&1; then
        echo "    healthz ok after ${i}s"
        break
    fi
    sleep 1
    [[ $i -eq 60 ]] && { echo "healthz never became 200"; exit 1; }
done

echo "==> /healthz"
curl_json GET /healthz | python3 -m json.tool

echo "==> /version"
VER_JSON="$(curl_json GET /version)"
echo "$VER_JSON" | python3 -m json.tool
SFM_VER="$(jget "$VER_JSON" sfmapi)"
[[ -n "$SFM_VER" ]] || { echo "no sfmapi version in /version"; exit 1; }

echo "==> /metrics surface check"
curl -fsS "${BASE_URL}/metrics" | grep -q "sfmapi_queue_depth" \
    || { echo "metrics surface missing"; exit 1; }
echo "    metrics ok"

echo "==> create project"
P_JSON="$(curl_json POST /v1/projects '{"name":"smoke-proj"}')"
PID="$(jget "$P_JSON" project_id)"
echo "    project_id=$PID"

echo "==> chunked upload"
PAYLOAD="$(python3 -c 'import os,sys; sys.stdout.buffer.write(b"\xff\xd8\xff\xe0" + os.urandom(2048))' \
    | python3 -c 'import sys; sys.stdout.write(sys.stdin.buffer.read().hex())')"
# decode the hex back to bytes via /dev/stdin to PATCH body
TMP_BIN="$(mktemp)"
python3 -c "import sys; open(sys.argv[1],'wb').write(bytes.fromhex(sys.argv[2]))" "$TMP_BIN" "$PAYLOAD"
SIZE="$(wc -c < "$TMP_BIN" | tr -d ' ')"
SHA="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TMP_BIN")"

INIT_JSON="$(curl_json POST /v1/uploads "{\"expected_size\":$SIZE,\"expected_sha\":\"$SHA\"}" \
    -H 'Idempotency-Key: smoke-1')"
UID_VAL="$(jget "$INIT_JSON" upload_id)"
echo "    upload_id=$UID_VAL size=$SIZE"

LAST=$((SIZE - 1))
curl -fsS -X PATCH "${BASE_URL}/v1/uploads/${UID_VAL}" \
    --data-binary "@${TMP_BIN}" \
    -H "Content-Range: bytes 0-${LAST}/${SIZE}" >/dev/null
FIN_JSON="$(curl_json POST "/v1/uploads/${UID_VAL}:finalize" '{}')"
BLOB_SHA="$(jget "$FIN_JSON" blob_sha)"
[[ "$BLOB_SHA" == "$SHA" ]] || { echo "sha mismatch: $BLOB_SHA != $SHA"; exit 1; }
rm -f "$TMP_BIN"

echo "==> create dataset (kind=upload)"
DS_BODY="$(python3 -c "import json,sys; print(json.dumps({'name':'ds-smoke','source':{'kind':'upload','entries':[{'name':'a.jpg','blob_sha':sys.argv[1]}]}}))" "$BLOB_SHA")"
DS_JSON="$(curl_json POST "/v1/projects/${PID}/datasets" "$DS_BODY")"
DID="$(jget "$DS_JSON" dataset_id)"
echo "    dataset_id=$DID"

echo "==> register image"
IMG_BODY="$(python3 -c "import json,sys; print(json.dumps({'name':'a.jpg','blob_sha':sys.argv[1]}))" "$BLOB_SHA")"
IMG_JSON="$(curl_json POST "/v1/datasets/${DID}/images" "$IMG_BODY")"
IID="$(jget "$IMG_JSON" image_id)"
echo "    image_id=$IID"

echo "==> list images"
LIST_JSON="$(curl -fsS "${BASE_URL}/v1/datasets/${DID}/images")"
COUNT="$(python3 -c "import json,sys; print(len(json.loads(sys.stdin.read())['items']))" <<<"$LIST_JSON")"
[[ "$COUNT" -ge 1 ]] || { echo "image listing returned $COUNT"; exit 1; }
echo "    listing ok ($COUNT images)"

echo "==> idempotent re-upload returns same upload_id"
INIT2_JSON="$(curl_json POST /v1/uploads "{\"expected_size\":$SIZE}" -H 'Idempotency-Key: smoke-1')"
UID2="$(jget "$INIT2_JSON" upload_id)"
[[ "$UID2" == "$UID_VAL" ]] || { echo "idempotency-key drift: $UID2 != $UID_VAL"; exit 1; }
echo "    idempotency ok"

echo
echo "==== SMOKE PASSED ===="
echo "    web=${BASE_URL}  pid=${PID}  did=${DID}  blob=${BLOB_SHA:0:12}..."
