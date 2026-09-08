#!/usr/bin/env bash
# V2 Docker acceptance: healthchecks, V1→V2 migration, expressive generate, persistence after restart.
#
# Opt-in (slow): requires Docker. Fake LLM only — no API credits.
#
#   RUN_DOCKER_ACCEPTANCE=1 ./scripts/v2_docker_acceptance.sh
#
# Env:
#   COMPOSE_PROJECT_NAME  — default mukit-v2-accept
#   KEEP_VOLUME=1         — keep named volume after run (default: remove)
#   LLM_FAKE_MODE         — forced to 1 for this script

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [[ "${RUN_DOCKER_ACCEPTANCE:-}" != "1" ]]; then
  echo "SKIP: set RUN_DOCKER_ACCEPTANCE=1 to run Docker persistence acceptance"
  exit 0
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mukit-v2-accept}"
export LLM_FAKE_MODE=1
export DEFAULT_LLM_PROVIDER=fake
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8888}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
COMPOSE=(docker compose -f docker-compose.yml)
V1_FIXTURE="${ROOT}/backend/tests/fixtures/composition_v1_minimal.json"

log() { echo "[v2-docker-acceptance] $*"; }
fail() { echo "[v2-docker-acceptance] ERROR: $*" >&2; exit 1; }

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

log "Fake-generating native V2 expressive composition (4 bars)"
GEN=$(curl -fsS -X POST "${BACKEND_URL}/llm/generate-music-json" \
  -H "Content-Type: application/json" \
  -d '{"prompt":{"genre":"pop","mood":"bright","duration_bars":4,"time_signature":"4/4","instruments":["piano","bass"]},"selection":{"provider":"fake"}}')
SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["music"]["schema_version"])' <<<"${GEN}")
[[ "${SCHEMA}" == "composition.v2" ]] || fail "Fake 4-bar generate must return composition.v2 (got ${SCHEMA})"
TEMPO_CHANGES=$(python3 -c 'import json,sys; print(len(json.load(sys.stdin)["music"].get("tempo_changes") or []))' <<<"${GEN}")
[[ "${TEMPO_CHANGES}" -ge 1 ]] || fail "Expressive fixture must include tempo_changes"
EXPRESSIVE=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["music"]))' <<<"${GEN}")

log "Creating project and saving expressive V2 composition"
CREATE=$(curl -fsS -X POST "${BACKEND_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"V2 Docker Accept"}')
PROJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${CREATE}")

curl -fsS -X PATCH "${BACKEND_URL}/projects/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${EXPRESSIVE}}" >/dev/null

log "Seeding raw V1 JSON for migrate-on-open acceptance"
V1_PROJECT=$(curl -fsS -X POST "${BACKEND_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"V1 Migrate Accept"}')
V1_PROJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${V1_PROJECT}")
V1_JSON=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "${V1_FIXTURE}")

"${COMPOSE[@]}" exec -T -e V1_JSON="${V1_JSON}" backend python - <<PY
import json
import os
from app.services import project_store as store

project_id = "${V1_PROJECT_ID}"
payload = json.loads(os.environ["V1_JSON"])
store.update_project(project_id, composition=json.dumps(payload, separators=(",", ":")))
PY

V1_OPEN=$(curl -fsS "${BACKEND_URL}/projects/${V1_PROJECT_ID}")
python3 - <<'PY' "${V1_OPEN}" "${V1_FIXTURE}"
import json, sys
body = json.loads(sys.argv[1])
fixture = json.loads(open(sys.argv[2]).read())
opened = body["composition"]
if opened["schema_version"] != "composition.v2":
    raise SystemExit(f"expected composition.v2 after open, got {opened['schema_version']}")
if not body.get("composition_migrated"):
    raise SystemExit("expected composition_migrated=true for raw V1 seed")
for t_fix, t_open in zip(fixture["tracks"], opened["tracks"], strict=True):
    if t_fix["id"] != t_open["id"]:
        raise SystemExit(f"track id mismatch: {t_fix['id']} vs {t_open['id']}")
    for e_fix, e_open in zip(t_fix["events"], t_open["events"], strict=True):
        keys = ("id", "pitch", "start_tick", "duration_ticks", "velocity", "staff", "voice")
        if any(e_fix.get(k) != e_open.get(k) for k in keys):
            raise SystemExit(f"note mismatch on track {t_fix['id']}")
print("v1-migrate-ok")
PY

log "Export MusicXML preview returns projection headers for expressive composition"
PREVIEW=$(curl -fsS -D - -o /dev/null -X POST "${BACKEND_URL}/export/musicxml/preview" \
  -H "Content-Type: application/json" \
  -d "${EXPRESSIVE}")
echo "${PREVIEW}" | grep -qi 'x-mukit-projection-status' || fail "Missing X-Mukit-Projection-Status header"
echo "${PREVIEW}" | grep -qi 'automation_omitted_from_notation' || fail "Expected automation_omitted_from_notation in projection issues"

log "Export MIDI returns projection headers for expressive composition"
MIDI_HEADERS=$(curl -fsS -D - -o /dev/null -X POST "${BACKEND_URL}/export/midi" \
  -H "Content-Type: application/json" \
  -d "${EXPRESSIVE}")
echo "${MIDI_HEADERS}" | grep -qi 'x-mukit-projection-status' || fail "Missing MIDI X-Mukit-Projection-Status header"
echo "${MIDI_HEADERS}" | grep -qi 'x-mukit-projection-issues: .\+' || fail "Expected non-empty MIDI projection issues for expressive fixture"

BEFORE=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
BEFORE_HASH=$(python3 -c 'import json,sys,hashlib; c=json.load(sys.stdin)["composition"]; print(hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())' <<<"${BEFORE}")
log "Pre-restart composition sha256=${BEFORE_HASH}"

log "Restarting containers (named volume retained)"
"${COMPOSE[@]}" restart
sleep 3
wait_http "${BACKEND_URL}/health" "backend-after-restart" 45
wait_http "${BACKEND_URL}/ready" "backend-ready-after-restart" 45

AFTER=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
AFTER_HASH=$(python3 -c 'import json,sys,hashlib; c=json.load(sys.stdin)["composition"]; print(hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())' <<<"${AFTER}")
log "Post-restart composition sha256=${AFTER_HASH}"

[[ "${BEFORE_HASH}" == "${AFTER_HASH}" ]] || fail "Composition changed after restart (volume not persisted?)"
AFTER_SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["composition"]["schema_version"])' <<<"${AFTER}")
[[ "${AFTER_SCHEMA}" == "composition.v2" ]] || fail "Persisted composition must remain composition.v2"
log "PASS: V2 docker acceptance complete"
