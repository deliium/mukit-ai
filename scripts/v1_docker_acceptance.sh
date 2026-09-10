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
SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["music"]["schema_version"])' <<<"${GEN}")
[[ "${SCHEMA}" == "composition.v2" ]] || fail "Fake generate must return composition.v2 (got ${SCHEMA})"
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

log "Creating durable checkpoint + Darker harmony branch before restart"
BEFORE_FILE=$(mktemp)
printf '%s' "${BEFORE}" >"${BEFORE_FILE}"
HISTORY_STATE=$(
  BACKEND_URL="${BACKEND_URL}" PROJECT_ID="${PROJECT_ID}" PROJECT_JSON="${BEFORE_FILE}" python3 <<'PY'
import json, os, urllib.error, urllib.request

backend = os.environ["BACKEND_URL"].rstrip("/")
project_id = os.environ["PROJECT_ID"]
with open(os.environ["PROJECT_JSON"], encoding="utf-8") as handle:
    project = json.load(handle)

def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{backend}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} failed: {exc.code} {body[:800]}") from exc

composition = dict(project["composition"])
composition["tempo"] = int(composition.get("tempo") or 100) + 1
checkpoint = request(
    "POST",
    f"/projects/{project_id}/revisions",
    {
        "branch_id": project["active_branch_id"],
        "expected_active_branch_id": project["active_branch_id"],
        "expected_working_version": project["working_version"],
        "expected_head_revision_id": project["current_revision_id"],
        "expected_source_fingerprint": project["working_fingerprint"],
        "composition": composition,
        "operation_type": "manual-checkpoint",
        "name": "Docker checkpoint",
    },
)

alt = dict(composition)
alt["tempo"] = 90
branched = request(
    "POST",
    f"/projects/{project_id}/branches/apply-as-branch",
    {
        "name": "Darker harmony",
        "source_branch_id": checkpoint["active_branch_id"],
        "expected_active_branch_id": checkpoint["active_branch_id"],
        "expected_working_version": checkpoint["working_version"],
        "expected_head_revision_id": checkpoint["current_revision_id"],
        "expected_source_fingerprint": checkpoint["working_fingerprint"],
        "composition": alt,
        "operation_type": "development-apply",
    },
)

print(json.dumps({
    "root_revision_id": project["current_revision_id"],
    "active_branch_name": branched["active_branch_name"],
    "tempo": branched["composition"]["tempo"],
}))
PY
)
rm -f "${BEFORE_FILE}"
HISTORY_BRANCH=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["active_branch_name"])' <<<"${HISTORY_STATE}")
HISTORY_TEMPO=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["tempo"])' <<<"${HISTORY_STATE}")
ROOT_REV=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["root_revision_id"])' <<<"${HISTORY_STATE}")
[[ "${HISTORY_BRANCH}" == "Darker harmony" ]] || fail "Expected active branch Darker harmony, got ${HISTORY_BRANCH}"
[[ "${HISTORY_TEMPO}" == "90" ]] || fail "Expected branched tempo 90, got ${HISTORY_TEMPO}"

log "Restarting containers (named volume retained)"
"${COMPOSE[@]}" restart
# Give healthchecks time after restart
sleep 3
wait_http "${BACKEND_URL}/health" "backend-after-restart" 45
wait_http "${BACKEND_URL}/ready" "backend-ready-after-restart" 45

AFTER=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
AFTER_BRANCH=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["active_branch_name"])' <<<"${AFTER}")
AFTER_TEMPO=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["composition"]["tempo"])' <<<"${AFTER}")
[[ "${AFTER_BRANCH}" == "Darker harmony" ]] || fail "Active branch lost after restart (got ${AFTER_BRANCH})"
[[ "${AFTER_TEMPO}" == "90" ]] || fail "Branched composition lost after restart (tempo=${AFTER_TEMPO})"

log "Checking out Original and restoring root revision after restart"
AFTER_FILE=$(mktemp)
printf '%s' "${AFTER}" >"${AFTER_FILE}"
RESTORE_STATE=$(
  BACKEND_URL="${BACKEND_URL}" PROJECT_ID="${PROJECT_ID}" ROOT_REV="${ROOT_REV}" PROJECT_JSON="${AFTER_FILE}" python3 <<'PY'
import json, os, urllib.error, urllib.request

backend = os.environ["BACKEND_URL"].rstrip("/")
project_id = os.environ["PROJECT_ID"]
root_revision_id = os.environ["ROOT_REV"]
with open(os.environ["PROJECT_JSON"], encoding="utf-8") as handle:
    project = json.load(handle)

def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{backend}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} failed: {exc.code} {body[:800]}") from exc

branches = request("GET", f"/projects/{project_id}/branches")["branches"]
original = next(item for item in branches if item["name"] == "Original")
checked = request(
    "POST",
    f"/projects/{project_id}/branches/{original['id']}/checkout",
    {
        "expected_active_branch_id": project["active_branch_id"],
        "expected_working_version": project["working_version"],
        "expected_head_revision_id": project["current_revision_id"],
    },
)
restored = request(
    "POST",
    f"/projects/{project_id}/revisions/{root_revision_id}/restore",
    {
        "branch_id": checked["active_branch_id"],
        "expected_active_branch_id": checked["active_branch_id"],
        "expected_working_version": checked["working_version"],
        "expected_head_revision_id": checked["current_revision_id"],
    },
)
print(json.dumps({
    "checked_branch": checked["active_branch_name"],
    "restore_operation": restored["operation_type"],
}))
PY
)
rm -f "${AFTER_FILE}"
CHECKED_NAME=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["checked_branch"])' <<<"${RESTORE_STATE}")
RESTORE_OP=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["restore_operation"])' <<<"${RESTORE_STATE}")
[[ "${CHECKED_NAME}" == "Original" ]] || fail "Checkout Original failed (${CHECKED_NAME})"
[[ "${RESTORE_OP}" == "revision-restore" ]] || fail "Expected revision-restore, got ${RESTORE_OP}"

AFTER_HASH=$(python3 -c 'import json,sys,hashlib; c=json.load(sys.stdin)["composition"]; print(hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest())' <<<"${AFTER}")
log "Post-restart branched composition sha256=${AFTER_HASH}"
log "PASS: composition + multi-branch history persisted across container restart"
