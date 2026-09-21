#!/usr/bin/env bash
# V3 Docker acceptance: multimodal → hybrid generate → edit → develop → versions →
# neural fake → export → restart → reopen → seeded reproduce.
#
# Opt-in (slow): requires Docker. Fake LLM / audio / neural only — no API credits,
# no GPU, no weight downloads.
#
#   RUN_DOCKER_ACCEPTANCE=1 ./scripts/v3_docker_acceptance.sh
#
# Env:
#   COMPOSE_PROJECT_NAME  — default mukit-v3-accept
#   KEEP_VOLUME=1         — keep named volume after run (default: remove)
#   BACKEND_URL / FRONTEND_URL — defaults 8888 / 3000

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [[ "${RUN_DOCKER_ACCEPTANCE:-}" != "1" ]]; then
  echo "SKIP: set RUN_DOCKER_ACCEPTANCE=1 to run V3 Docker acceptance"
  exit 0
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mukit-v3-accept}"
export LLM_FAKE_MODE=1
export AUDIO_FAKE_MODE=1
export NEURAL_AUDIO_FAKE_MODE=1
export DEFAULT_LLM_PROVIDER=fake
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8888}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
COMPOSE=(docker compose -f docker-compose.yml)
MIDI_FIXTURE="${ROOT}/backend/tests/fixtures/import/multitrack.mid"
SEED=42
PIPELINE="hybrid_plan_symbolic"
PHASE_TS=$(date +%s)

log() { echo "[v3-docker-acceptance] $*"; }
phase() {
  local name="$1"
  local now
  now=$(date +%s)
  log "PHASE ${name} (+$(( now - PHASE_TS ))s)"
  PHASE_TS=${now}
}
fail() {
  echo "[v3-docker-acceptance] ERROR: $*" >&2
  "${COMPOSE[@]}" logs --tail=120 backend frontend 2>/dev/null || true
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

phase "compose-up"
START_TS=$(date +%s)
log "Building and starting stack (fake modes, project=${COMPOSE_PROJECT_NAME})"
"${COMPOSE[@]}" up --build -d

wait_http() {
  local url="$1"
  local name="$2"
  local attempts="${3:-90}"
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
  "${COMPOSE[@]}" ps || true
  fail "${name} did not become healthy: ${url}"
}

wait_http "${BACKEND_URL}/health" "backend"
wait_http "${BACKEND_URL}/ready" "backend-ready"
wait_http "${FRONTEND_URL}/" "frontend"

phase "project-create"
CREATE=$(curl -fsS -X POST "${BACKEND_URL}/projects" \
  -H "Content-Type: application/json" \
  -d '{"name":"V3 Docker Accept"}')
PROJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${CREATE}")
log "Created project_id=${PROJECT_ID}"

phase "midi-import"
IMPORT=$(curl -fsS -X POST "${BACKEND_URL}/imports/midi" \
  -F "file=@${MIDI_FIXTURE};type=audio/midi")
IMPORT_SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["composition"]["schema_version"])' <<<"${IMPORT}")
[[ "${IMPORT_SCHEMA}" == "composition.v2" ]] || fail "MIDI import must return composition.v2"
IMPORT_COMP=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["composition"]))' <<<"${IMPORT}")
curl -fsS -X PATCH "${BACKEND_URL}/projects/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${IMPORT_COMP}, \"clear_generation\": true}" >/dev/null
log "Imported multitrack MIDI into project"

phase "audio-transcription-optional"
TMP_WAV="$(mktemp /tmp/mukit-v3-XXXXXX.wav)"
python3 - <<'PY' "${TMP_WAV}"
import math, struct, sys, wave
path = sys.argv[1]
rate = 16000
duration = 0.35
freq = 523.25  # C5
n = int(rate * duration)
with wave.open(path, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    for i in range(n):
        sample = int(12000 * math.sin(2 * math.pi * freq * i / rate))
        w.writeframes(struct.pack("<h", sample))
PY
TX=$(curl -fsS -X POST "${BACKEND_URL}/transcription/audio" \
  -F "file=@${TMP_WAV};type=audio/wav" || true)
rm -f "${TMP_WAV}"
if [[ -n "${TX}" ]]; then
  TX_SCHEMA=$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("schema_version") or d.get("preview",{}).get("schema_version") or "")' <<<"${TX}" 2>/dev/null || true)
  log "Audio transcription responded (schema=${TX_SCHEMA:-unknown}); preview not applied to V2 notes"
else
  log "Audio transcription skipped or unavailable (non-fatal)"
fi

phase "analysis"
OPENED=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
COMP_JSON=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["composition"]))' <<<"${OPENED}")
ANALYSIS=$(curl -fsS -X POST "${BACKEND_URL}/analysis/composition" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${COMP_JSON}}")
ANALYSIS_SV=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("schema_version",""))' <<<"${ANALYSIS}")
[[ "${ANALYSIS_SV}" == "composition.analysis.v1" ]] || fail "Analysis must return composition.analysis.v1"
log "Analysis ok"

phase "hybrid-generate"
GEN=$(curl -fsS -X POST "${BACKEND_URL}/llm/generate-music-json" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":{\"genre\":\"classical\",\"mood\":\"calm\",\"key\":\"C major\",\"time_signature\":\"4/4\",\"tempo_min\":100,\"tempo_max\":140,\"duration_bars\":8,\"instruments\":[\"piano\",\"bass\"],\"complexity\":\"simple\"},\"selection\":{\"provider\":\"fake\",\"model\":\"fake-v1\"},\"options\":{\"pipeline\":\"${PIPELINE}\",\"seed\":${SEED},\"max_retries\":0}}")
GEN_PIPELINE=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("pipeline_id") or "")' <<<"${GEN}")
GEN_SEED=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("seed"))' <<<"${GEN}")
[[ "${GEN_PIPELINE}" == "${PIPELINE}" ]] || fail "Expected pipeline ${PIPELINE}, got ${GEN_PIPELINE}"
[[ "${GEN_SEED}" == "${SEED}" ]] || fail "Expected seed ${SEED}, got ${GEN_SEED}"
GEN_PARAMS=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("generation_parameters") or {}))' <<<"${GEN}")
python3 -c 'import json,sys; p=json.load(sys.stdin); assert p.get("provenance_schema")=="generation.provenance.v1", p' <<<"${GEN_PARAMS}"
FP1=$(python3 -c 'import json,sys,hashlib; m=json.load(sys.stdin)["music"]; print(hashlib.sha256(json.dumps(m,sort_keys=True,separators=(",",":")).encode()).hexdigest())' <<<"${GEN}")
MUSIC=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["music"]))' <<<"${GEN}")
log "Hybrid generate fingerprint_prefix=${FP1:0:16} seed=${SEED}"

phase "edit-note"
EDITED=$(python3 - <<'PY' "${MUSIC}"
import json, sys
music = json.loads(sys.argv[1])
track = music["tracks"][0]
events = track.get("events") or []
if not events:
    raise SystemExit("no events to edit")
ev = events[0]
# Deterministic pitch nudge for edit evidence (still valid V2).
pitch = ev.get("pitch") or "C4"
ev["pitch"] = "D4" if pitch != "D4" else "E4"
print(json.dumps(music))
PY
)
curl -fsS -X PATCH "${BACKEND_URL}/projects/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${EDITED}}" >/dev/null
log "Patched first-note pitch edit"

phase "development-preview-apply"
DEV=$(curl -fsS -X POST "${BACKEND_URL}/composition/development/preview" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${EDITED}, \"operation\": \"continue\", \"output_bars\": 4, \"variation_strength\": \"balanced\", \"development_intent\": \"continue\", \"candidate_count\": 1, \"selection\": {\"provider\": \"fake\", \"model\": \"fake-deterministic\"}, \"options\": {\"max_repairs\": 1}}")
DEV_CAND=$(python3 -c 'import json,sys; d=json.load(sys.stdin); c=(d.get("candidates") or [None])[0]; print(json.dumps(c["composition"] if c else None))' <<<"${DEV}")
[[ "${DEV_CAND}" != "null" ]] || fail "Development preview returned no candidates"
curl -fsS -X PATCH "${BACKEND_URL}/projects/${PROJECT_ID}" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${DEV_CAND}}" >/dev/null
WORKING=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
log "Development continue applied"

phase "revision-commit"
BRANCH_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["active_branch_id"])' <<<"${WORKING}")
WORKING_VERSION=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["working_version"])' <<<"${WORKING}")
HEAD_REV=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["current_revision_id"])' <<<"${WORKING}")
FP_WORK=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["working_fingerprint"])' <<<"${WORKING}")
COMP_NOW=$(python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["composition"]))' <<<"${WORKING}")
COMMIT_BODY=$(python3 - <<'PY' "${BRANCH_ID}" "${WORKING_VERSION}" "${HEAD_REV}" "${FP_WORK}" "${COMP_NOW}" "${GEN_PARAMS}" "${SEED}"
import json, sys
branch_id, working_version, head_rev, fp_work = sys.argv[1:5]
comp = json.loads(sys.argv[5])
gen_params = json.loads(sys.argv[6])
seed = int(sys.argv[7])
body = {
    "branch_id": branch_id,
    "expected_active_branch_id": branch_id,
    "expected_working_version": int(working_version),
    "expected_head_revision_id": head_rev,
    "expected_source_fingerprint": fp_work,
    "composition": comp,
    "operation_type": "generate-apply",
    "name": f"V3 hybrid seed {seed}",
    "ai": {
        "provider": "fake",
        "model": "fake-v1",
        "model_id": "fake:fake-v1",
        "generation_parameters": gen_params,
    },
}
print(json.dumps(body))
PY
)
COMMIT=$(curl -fsS -X POST "${BACKEND_URL}/projects/${PROJECT_ID}/revisions" \
  -H "Content-Type: application/json" \
  -d "${COMMIT_BODY}")
REV_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["current_revision_id"])' <<<"${COMMIT}")
log "Committed revision_id=${REV_ID}"

phase "neural-audio-fake"
NEURAL=$(curl -fsS -X POST "${BACKEND_URL}/neural-audio/renders" \
  -H "Content-Type: application/json" \
  -d "{\"composition\": ${COMP_NOW}, \"instructions\": \"chamber pad\", \"model_id\": \"fake:neural-audio\", \"seed\": 1, \"project_id\": \"${PROJECT_ID}\"}")
NEURAL_STATUS=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status"))' <<<"${NEURAL}")
[[ "${NEURAL_STATUS}" == "complete" ]] || fail "Neural fake render must complete (got ${NEURAL_STATUS})"
RENDER_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${NEURAL}")
curl -fsS -o /tmp/mukit-v3-neural.wav "${BACKEND_URL}/neural-audio/renders/${RENDER_ID}/audio"
[[ -s /tmp/mukit-v3-neural.wav ]] || fail "Neural audio download empty"
python3 -c 'import pathlib; b=pathlib.Path("/tmp/mukit-v3-neural.wav").read_bytes(); assert b[:4]==b"RIFF"'
log "Neural fake render downloaded"

phase "export-midi-musicxml"
MIDI_HEADERS=$(curl -fsS -D - -o /tmp/mukit-v3-export.mid -X POST "${BACKEND_URL}/export/midi" \
  -H "Content-Type: application/json" \
  -d "${COMP_NOW}")
echo "${MIDI_HEADERS}" | grep -qi 'x-mukit-projection-status' || fail "Missing MIDI projection status header"
[[ -s /tmp/mukit-v3-export.mid ]] || fail "MIDI export empty"
# Multi-track SMF Type 1 smoke: file larger than a trivial Type-0 single track stub
MIDI_SIZE=$(wc -c </tmp/mukit-v3-export.mid)
[[ "${MIDI_SIZE}" -gt 64 ]] || fail "MIDI export too small (${MIDI_SIZE})"
# Prefer mido when host venv has it; otherwise rely on projection issue codes when sections exist.
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  "${ROOT}/.venv/bin/python" - <<'PY' || fail "MIDI multi-track/marker smoke failed"
from pathlib import Path
import mido
mid = mido.MidiFile("/tmp/mukit-v3-export.mid")
assert mid.type == 1, mid.type
assert len(mid.tracks) >= 2, len(mid.tracks)
names = []
markers = []
for tr in mid.tracks:
    for msg in tr:
        if msg.type == "track_name":
            names.append(msg.name)
        if msg.type == "marker":
            markers.append(msg.text)
print(f"tracks={len(mid.tracks)} names={len(names)} markers={len(markers)}")
PY
else
  log "Host .venv mido unavailable; size+headers smoke only"
fi
XML_HEADERS=$(curl -fsS -D - -o /tmp/mukit-v3-export.musicxml -X POST "${BACKEND_URL}/export/musicxml" \
  -H "Content-Type: application/json" \
  -d "${COMP_NOW}")
echo "${XML_HEADERS}" | grep -qi 'x-mukit-projection-status' || fail "Missing MusicXML projection status header"
[[ -s /tmp/mukit-v3-export.musicxml ]] || fail "MusicXML export empty"
log "Export MIDI (${MIDI_SIZE} bytes) + MusicXML ok"

phase "compose-restart"
"${COMPOSE[@]}" restart
sleep 3
wait_http "${BACKEND_URL}/health" "backend-after-restart" 45
wait_http "${BACKEND_URL}/ready" "backend-ready-after-restart" 45

phase "reopen-provenance"
REOPEN=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}")
REOPEN_SCHEMA=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["composition"]["schema_version"])' <<<"${REOPEN}")
[[ "${REOPEN_SCHEMA}" == "composition.v2" ]] || fail "Reopened composition must be composition.v2"
REV_DETAIL=$(curl -fsS "${BACKEND_URL}/projects/${PROJECT_ID}/revisions/${REV_ID}")
python3 - <<'PY' "${REV_DETAIL}" "${SEED}" "${PIPELINE}"
import json, sys
detail = json.loads(sys.argv[1])
seed = int(sys.argv[2])
pipeline = sys.argv[3]
rev = detail.get("revision") or {}
summary = rev.get("summary") or {}
params = summary.get("generation_parameters") or {}
if params.get("provenance_schema") != "generation.provenance.v1":
    raise SystemExit(f"missing provenance_schema in revision.summary: {summary.keys()}")
if params.get("pipeline_id") != pipeline:
    raise SystemExit(f"pipeline mismatch: {params.get('pipeline_id')}")
if params.get("seed") != seed:
    raise SystemExit(f"seed mismatch: {params.get('seed')}")
print("reopen-provenance-ok")
PY
log "Reopened project + revision provenance present"

phase "seeded-reproduce"
GEN2=$(curl -fsS -X POST "${BACKEND_URL}/llm/generate-music-json" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":{\"genre\":\"classical\",\"mood\":\"calm\",\"key\":\"C major\",\"time_signature\":\"4/4\",\"tempo_min\":100,\"tempo_max\":140,\"duration_bars\":8,\"instruments\":[\"piano\",\"bass\"],\"complexity\":\"simple\"},\"selection\":{\"provider\":\"fake\",\"model\":\"fake-v1\"},\"options\":{\"pipeline\":\"${PIPELINE}\",\"seed\":${SEED},\"max_retries\":0}}")
FP2=$(python3 -c 'import json,sys,hashlib; m=json.load(sys.stdin)["music"]; print(hashlib.sha256(json.dumps(m,sort_keys=True,separators=(",",":")).encode()).hexdigest())' <<<"${GEN2}")
log "Reproduce fingerprint_prefix=${FP2:0:16}"
[[ "${FP1}" == "${FP2}" ]] || fail "Seeded reproduce mismatch ${FP1:0:16} vs ${FP2:0:16}"
log "PASS: V3 docker acceptance complete (restart + seeded reproduce)"
