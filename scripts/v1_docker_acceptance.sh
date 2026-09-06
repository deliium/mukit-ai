#!/usr/bin/env bash
# V1 Docker acceptance: healthchecks + named-volume persistence after restart.
#
# Opt-in (slow): requires Docker. Fake LLM only — no API credits.
#
#   RUN_DOCKER_ACCEPTANCE=1 ./scripts/v1_docker_acceptance.sh
#
# Env:
#   COMPOSE_PROJECT_NAME  — default mukit-v1-accept
#   KEEP_VOLUME=1         — keep named volume after run (default: remove)
#   LLM_FAKE_MODE         — forced to 1 for this script

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [[ "${RUN_DOCKER_ACCEPTANCE:-}" != "1" ]]; then
  echo "SKIP: set RUN_DOCKER_ACCEPTANCE=1 to run Docker persistence acceptance"
  exit 0
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mukit-v1-accept}"
export LLM_FAKE_MODE=1
export DEFAULT_LLM_PROVIDER=fake
# Do not require real keys for this gate.
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8888}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
COMPOSE=(docker compose -f docker-compose.yml)

log() { echo "[v1-docker-acceptance] $*"; }
fail() { echo "[v1-docker-acceptance] ERROR: $*" >&2; exit 1; }

cleanup() {
  log "Tearing down compose project ${COMPOSE_PROJECT_NAME}"
  if [[ "${KEEP_VOLUME:-0}" == "1" ]]; then
    "${COMPOSE[@]}" down --remove-orphans || true
  else
    "${COMPOSE[@]}" down -v --remove-orphans || true
  fi
}
trap cleanup EXIT

log "Building and starting stack (LLM_FAKE_MODE=1, project=${COMPOSE_PROJECT_NAME})"
START_TS=$(date +%s)
"${COMPOSE[@]}" up --build -d

wait_http() {
  local url="$1"
  local name="$2"
  local attempts="${3:-60}"
  local i=0
  while (( i < attempts )); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      local elapsed=$(( $(date +%s) - START_TS ))
      log "${name} healthy after ${elapsed}s (${url})"
      return 0
    fi
    sleep 2
    i=$((i + 1))
  done
  log "Container status on failure:"
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail=80 backend frontend || true
  fail "${name} did not become healthy: ${url}"
}

wait_http "${BACKEND_URL}/health" "backend"
wait_http "${BACKEND_URL}/ready" "backend-ready"
wait_http "${FRONTEND_URL}/" "frontend"

log "Creating project via HTTP"
CREATE=$(curl -fsS -X POST "${BACKEND_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"V1 Docker Accept"}')
PROJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${CREATE}")
log "Created project id=${PROJECT_ID}"

log "Fake-generating composition"
GEN=$(curl -fsS -X POST "${BACKEND_URL}/llm/generate-music-json" \
  -H "Content-Type: application/json" \
  -d '{"prompt":{"genre":"pop","mood":"bright","duration_bars":16,"instruments":["piano","bass"]},"selection":{"provider":"fake"}}')
COMPOSITION=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["music"]))' <<<"${GEN}")
EVENT_COUNT=$(python3 -c 'import json,sys; m=json.load(sys.stdin); print(sum(len(t.get("events") or []) for t in m.get("tracks") or []))' <<<"${COMPOSITION}")
[[ "${EVENT_COUNT}" -gt 0 ]] || fail "Fake generate returned zero note events"

log "Saving composition to project (${EVENT_COUNT} events)"
curl -fsS -X PATCH "${BACKEND_URL}/projects/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${COMPOSITION}}" >/dev/null

BEFORE=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
BEFORE_HASH=$(python3 -c 'import json,sys,hashlib; c=json.load(sys.stdin)["composition"]; print(hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())' <<<"${BEFORE}")
log "Pre-restart composition sha256=${BEFORE_HASH}"

log "Restarting containers (named volume retained)"
"${COMPOSE[@]}" restart
# Give healthchecks time after restart
sleep 3
wait_http "${BACKEND_URL}/health" "backend-after-restart" 45
wait_http "${BACKEND_URL}/ready" "backend-ready-after-restart" 45

AFTER=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
AFTER_HASH=$(python3 -c 'import json,sys,hashlib; c=json.load(sys.stdin)["composition"]; print(hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())' <<<"${AFTER}")
log "Post-restart composition sha256=${AFTER_HASH}"

[[ "${BEFORE_HASH}" == "${AFTER_HASH}" ]] || fail "Composition changed after restart (volume not persisted?)"
log "PASS: composition persisted across container restart"
