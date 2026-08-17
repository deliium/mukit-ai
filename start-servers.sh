#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Starting AI Music Composer Servers..."

# Function to check if port is in use
check_port() {
    if ss -ltn "sport = :$1" | grep -q ":$1"; then
        echo "⚠️  Port $1 is already in use"
        return 1
    elif lsof -i :$1 > /dev/null 2>&1; then
        echo "⚠️  Port $1 is already in use"
        return 1
    else
        return 0
    fi
}

wait_for_process() {
    local pid=$1
    local name=$2

    sleep 3
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "❌ $name failed to start"
        exit 1
    fi
}

find_python() {
    local candidates=()

    [ -x "$ROOT_DIR/.venv/bin/python" ] && candidates+=("$ROOT_DIR/.venv/bin/python")
    [ -x "$ROOT_DIR/backend/venv/bin/python" ] && candidates+=("$ROOT_DIR/backend/venv/bin/python")
    command -v python3 >/dev/null 2>&1 && candidates+=("$(command -v python3)")

    for candidate in "${candidates[@]}"; do
        if "$candidate" - <<'PY' >/dev/null 2>&1
import fastapi
import music21
import uvicorn
PY
        then
            echo "$candidate"
            return 0
        fi
    done

    echo ""
}

# Kill any existing servers
echo "🧹 Cleaning up existing servers..."
pkill -f "uvicorn" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 2

# Start backend
echo "🔧 Starting FastAPI backend..."
cd "$ROOT_DIR/backend" || exit 1
if check_port 8888; then
    PYTHON_BIN="$(find_python)"
    if [ -z "$PYTHON_BIN" ]; then
        echo "❌ Backend dependencies are missing. Create a Python 3.14 virtualenv and run: pip install -r backend/requirements.txt"
        exit 1
    fi

    "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload &
    BACKEND_PID=$!
    wait_for_process "$BACKEND_PID" "Backend"
    echo "✅ Backend started (PID: $BACKEND_PID)"
else
    echo "❌ Backend port 8888 is busy"
    exit 1
fi

# Start frontend
echo "🎨 Starting Vite frontend..."
cd "$ROOT_DIR/frontend" || exit 1
if check_port 3000; then
    if [ ! -f "node_modules/vite/bin/vite.js" ]; then
        echo "❌ Frontend dependencies are missing or corrupt. Run: cd frontend && npm install"
        exit 1
    fi

    node node_modules/vite/bin/vite.js --host 0.0.0.0 &
    FRONTEND_PID=$!
    wait_for_process "$FRONTEND_PID" "Frontend"
    echo "✅ Frontend started (PID: $FRONTEND_PID)"
else
    echo "❌ Frontend port 3000 is busy"
    exit 1
fi

echo ""
echo "🎉 AI Music Composer is running!"
echo ""
echo "🌐 Frontend: http://localhost:3000"
echo "🔧 Backend:  http://localhost:8888"
echo "📊 Health:   http://localhost:8888/health"
echo ""
echo "🎵 Your AI Music Composer is ready to create beautiful music!"
echo ""
echo "To stop servers: pkill -f 'uvicorn' && pkill -f 'vite'"
