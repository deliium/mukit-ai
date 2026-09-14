#!/usr/bin/env bash
# Local quality gate: frontend ESLint plus backend/frontend unit tests.
#
#   ./scripts/run_tests.sh
#   ./scripts/run_tests.sh --build
#   ./scripts/run_tests.sh --e2e
#   ./scripts/run_tests.sh --backend-only -- tests/test_arrangement_schemas.py
#
# Default does not start servers, spend API credits, or run Docker/FluidSynth smokes.
# Playwright (--e2e) still needs a running stack with LLM_FAKE_MODE=1.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

RUN_LINT=1
RUN_BACKEND=1
RUN_FRONTEND=1
RUN_BUILD=0
RUN_E2E=0
ONLY=""
PYTEST_ARGS=()

log() { echo "[run-tests] $*"; }
fail() { echo "[run-tests] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: ./scripts/run_tests.sh [options] [-- pytest-args...]

Default: frontend ESLint, backend pytest, frontend unit tests.

Options:
  --lint-only         Run ESLint only
  --backend-only      Run backend pytest only
  --frontend-only     Run frontend unit tests only
  --build             Also run frontend production build
  --e2e               Also run Playwright (requires running stack)
  --skip-lint         Skip ESLint
  --skip-backend      Skip pytest
  --skip-frontend     Skip frontend unit tests
  -h, --help          Show this help

Examples:
  ./scripts/run_tests.sh
  ./scripts/run_tests.sh --build
  ./scripts/run_tests.sh --backend-only -- -q tests/test_arrangement_schemas.py
  ./scripts/run_tests.sh --e2e
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --lint-only)
      ONLY="lint"
      shift
      ;;
    --backend-only)
      ONLY="backend"
      shift
      ;;
    --frontend-only)
      ONLY="frontend"
      shift
      ;;
    --build)
      RUN_BUILD=1
      shift
      ;;
    --e2e)
      RUN_E2E=1
      shift
      ;;
    --skip-lint)
      RUN_LINT=0
      shift
      ;;
    --skip-backend)
      RUN_BACKEND=0
      shift
      ;;
    --skip-frontend)
      RUN_FRONTEND=0
      shift
      ;;
    --)
      shift
      PYTEST_ARGS+=("$@")
      break
      ;;
    -*)
      fail "unknown option: $1 (see --help)"
      ;;
    *)
      PYTEST_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -n "${ONLY}" ]]; then
  RUN_LINT=0
  RUN_BACKEND=0
  RUN_FRONTEND=0
  RUN_BUILD=0
  RUN_E2E=0
  case "${ONLY}" in
    lint) RUN_LINT=1 ;;
    backend) RUN_BACKEND=1 ;;
    frontend) RUN_FRONTEND=1 ;;
  esac
fi

if [[ "${RUN_LINT}" -eq 0 && "${RUN_BACKEND}" -eq 0 && "${RUN_FRONTEND}" -eq 0 && "${RUN_BUILD}" -eq 0 && "${RUN_E2E}" -eq 0 ]]; then
  fail "nothing to run (all steps skipped)"
fi

if [[ "${#PYTEST_ARGS[@]}" -gt 0 && "${RUN_BACKEND}" -eq 0 ]]; then
  fail "pytest arguments require backend tests (omit --skip-backend / --lint-only / --frontend-only)"
fi

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    if [[ ! -x "${PYTHON}" ]] && ! command -v "${PYTHON}" >/dev/null 2>&1; then
      fail "PYTHON=${PYTHON} is not executable"
    fi
    echo "${PYTHON}"
    return 0
  fi

  local candidates=()
  [[ -x "${ROOT}/.venv/bin/python" ]] && candidates+=("${ROOT}/.venv/bin/python")
  [[ -x "${ROOT}/backend/venv/bin/python" ]] && candidates+=("${ROOT}/backend/venv/bin/python")
  command -v python3 >/dev/null 2>&1 && candidates+=("$(command -v python3)")

  local candidate
  for candidate in "${candidates[@]}"; do
    if "${candidate}" -c "import pytest" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done

  return 1
}

run_step() {
  local name="$1"
  shift
  local started elapsed
  started="$(date +%s)"
  log "START ${name}"
  "$@"
  elapsed=$(( $(date +%s) - started ))
  log "OK    ${name} (${elapsed}s)"
}

require_frontend_npm() {
  if [[ ! -f "${ROOT}/frontend/package.json" ]]; then
    fail "frontend/package.json is missing"
  fi
  if [[ ! -d "${ROOT}/frontend/node_modules" ]]; then
    fail "frontend dependencies are missing. Run: cd frontend && npm install"
  fi
}

GATE_STARTED="$(date +%s)"
log "Repository root ${ROOT}"

if [[ "${RUN_LINT}" -eq 1 ]]; then
  require_frontend_npm
  run_step "frontend lint (eslint)" npm --prefix "${ROOT}/frontend" run lint
fi

if [[ "${RUN_BACKEND}" -eq 1 ]]; then
  PYTHON_BIN="$(find_python)" || fail "pytest is not available. Create a venv and run: pip install -r backend/requirements.txt"
  log "Using Python ${PYTHON_BIN}"
  started="$(date +%s)"
  log "START backend pytest"
  (
    cd "${ROOT}/backend"
    if [[ "${#PYTEST_ARGS[@]}" -gt 0 ]]; then
      "${PYTHON_BIN}" -m pytest "${PYTEST_ARGS[@]}"
    else
      "${PYTHON_BIN}" -m pytest
    fi
  )
  elapsed=$(( $(date +%s) - started ))
  log "OK    backend pytest (${elapsed}s)"
fi

if [[ "${RUN_FRONTEND}" -eq 1 ]]; then
  require_frontend_npm
  run_step "frontend unit tests" npm --prefix "${ROOT}/frontend" test
fi

if [[ "${RUN_BUILD}" -eq 1 ]]; then
  require_frontend_npm
  run_step "frontend production build" npm --prefix "${ROOT}/frontend" run build
fi

if [[ "${RUN_E2E}" -eq 1 ]]; then
  require_frontend_npm
  log "Playwright requires a running stack (LLM_FAKE_MODE=1). See docs/testing.md."
  run_step "frontend Playwright e2e" npm --prefix "${ROOT}/frontend" run test:e2e
fi

GATE_ELAPSED=$(( $(date +%s) - GATE_STARTED ))
log "All selected checks passed (${GATE_ELAPSED}s)"
