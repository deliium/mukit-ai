#!/usr/bin/env bash
# V5 Docker acceptance: fake-mode studio reopen fingerprint with continuous flags.
#
# Opt-in. Fake LLM / recovery / neural / mix / rights / continuous only.
# No API credits, no GPU, no weight downloads, no live Ardour.
#
#   RUN_DOCKER_ACCEPTANCE=1 ./scripts/v5_docker_acceptance.sh
#
# Env:
#   COMPOSE_PROJECT_NAME  — default mukit-v5-accept
#   KEEP_VOLUME=1         — keep named volume after run (default: remove)
#
# Not invoked by scripts/run_tests.sh.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [[ "${RUN_DOCKER_ACCEPTANCE:-}" != "1" ]]; then
  echo "SKIP: set RUN_DOCKER_ACCEPTANCE=1 to run V5 Docker acceptance"
  exit 0
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mukit-v5-accept}"
export LLM_FAKE_MODE=1
export AUDIO_FAKE_MODE=1
export AUDIO_RECOVERY_FAKE_MODE=1
export NEURAL_AUDIO_FAKE_MODE=1
export MIX_ANALYSIS_FAKE_MODE=1
export MIX_PLAN_FAKE_MODE=1
export DEFAULT_LLM_PROVIDER=fake
export ADAPTIVE_CONTINUOUS_ENABLED=1
export RIGHTS_GOVERNANCE_ENABLED=1
export MODEL_LAB_ENABLED=0
export ENSEMBLE_ARBITRATION_ENABLED=0
export ARDOUR_COMPANION_ENABLED=0
export ARDOUR_EXCHANGE_ENABLED=0
unset OPENAI_API_KEY DEEPSEEK_API_KEY LOCAL_LLM_API_KEY RUN_LLM_SMOKE || true
export OPENAI_API_KEY=
export DEEPSEEK_API_KEY=

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8888}"
COMPOSE=(docker compose -f docker-compose.yml)

log() { echo "[v5-docker-acceptance] $*"; }
phase() { log "PHASE $1"; }
fail() {
  echo "[v5-docker-acceptance] ERROR: $*" >&2
  "${COMPOSE[@]}" logs --tail=80 backend 2>/dev/null || true
  exit 1
}

cleanup() {
  log "Tearing down compose project ${COMPOSE_PROJECT_NAME}"
  if [[ "${KEEP_VOLUME:-0}" == "1" ]]; then
    "${COMPOSE[@]}" down --remove-orphans || true
  else
    "${COMPOSE[@]}" down -v --remove-orphans || true
  fi
}
trap cleanup EXIT

wait_http() {
  local url="$1"
  local name="$2"
  local attempts="${3:-90}"
  local i=0
  while (( i < attempts )); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      log "${name} healthy"
      return 0
    fi
    sleep 2
    i=$((i + 1))
  done
  fail "${name} did not become healthy"
}

fingerprint() {
  python3 -c 'import hashlib,json,sys
comp=json.load(sys.stdin)["composition"]
rows=[]
for track in comp.get("tracks") or []:
    for event in track.get("events") or []:
        rows.append([track.get("id"), event.get("pitch"), event.get("start_tick"), event.get("duration_ticks"), event.get("velocity")])
print(hashlib.sha256(json.dumps(rows).encode()).hexdigest())'
}

phase "compose-up"
"${COMPOSE[@]}" up --build -d
wait_http "${BACKEND_URL}/health" "backend"
wait_http "${BACKEND_URL}/ready" "backend-ready"

phase "project-create"
CREATE=$(curl -fsS -X POST "${BACKEND_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"V5 Docker Accept"}')
PROJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${CREATE}")
log "project_id=${PROJECT_ID}"

phase "autonomous-run"
RUN=$(curl -fsS -X POST "${BACKEND_URL}/ai/agents/autonomous/runs" \
  -H "Content-Type: application/json" \
  -d '{"brief":{"schema_version":"creative.brief.v1","duration_seconds":150,"narrative":[{"intent":"sparse_opening","text":"opening"},{"intent":"establish_theme","text":"theme"},{"intent":"build","text":"build"},{"intent":"climax","text":"climax"},{"intent":"resolve","text":"ending"}],"instrumentation":["piano","cello","strings"],"forbidden_instrument_families":["drums"],"opening_key":"F# minor","final_section_key":"F# major"},"project_id":"'"${PROJECT_ID}"'","include_rendering":false,"autonomy_mode":"autonomous"}')
STATUS=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' <<<"${RUN}")
[[ "${STATUS}" == "completed" ]] || fail "autonomous run status ${STATUS}"

phase "schema-guard"
OPENED=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
python3 -c 'import json,sys
body=json.load(sys.stdin)
comp=body["composition"]
assert comp.get("schema_version")=="composition.v2", comp.get("schema_version")
blob=json.dumps(body)
assert "composition.v5" not in blob
' <<<"${OPENED}"

phase "fingerprint"
FP=$(fingerprint <<<"${OPENED}")
log "fingerprint=${FP}"

phase "backend-restart"
"${COMPOSE[@]}" restart backend
wait_http "${BACKEND_URL}/health" "backend-after-restart" 45
wait_http "${BACKEND_URL}/ready" "backend-ready-after-restart" 45

phase "reopen"
REOPENED=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
FP2=$(fingerprint <<<"${REOPENED}")
[[ "${FP}" == "${FP2}" ]] || fail "event fingerprint changed after restart"
SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["composition"]["schema_version"])' <<<"${REOPENED}")
[[ "${SCHEMA}" == "composition.v2" ]] || fail "schema became ${SCHEMA}"
log "PASS project_id=${PROJECT_ID}"
