# AGENTS.md

> Keep this file factual and update it when the project structure changes significantly. Detailed product docs live under `docs/` and `.ai-factory/DESCRIPTION.md`.

## Project Overview

Full-stack LLM music composer: FastAPI generates/edits canonical `composition.v1` JSON; React/Vite edits on piano roll/JSON, shows OSMD notation, and plays note events with Tone.js. Projects persist in SQLite.

## Tech Stack

- **Programming language:** Python 3.14+ (backend), JavaScript (frontend)
- **Framework:** FastAPI + Uvicorn; React 18 + Vite
- **Database:** SQLite (`PROJECT_DB_PATH`) with numbered SQL migrations
- **ORM:** None (raw `sqlite3`)

## Project Structure

```
mukit-ai/
├── backend/                 # FastAPI app, tests, Dockerfile
│   ├── app/
│   │   ├── main.py          # Composition / LLM / export routes
│   │   ├── ready.py         # LOG_LEVEL, CORS parse, /ready helpers
│   │   ├── routers/         # Projects HTTP API
│   │   ├── services/        # Domain + orchestration services (+ fake_llm, fixture_compositions)
│   │   ├── fixtures/        # Canonical composition.v1 JSON for fake LLM / tests
│   │   ├── db/              # SQLite connection + migrations
│   │   └── schemas.py       # composition.v1 + LLM models
│   └── tests/
├── frontend/                # React + Vite SPA
│   ├── e2e/                 # Playwright V1 acceptance journeys
│   └── src/
│       ├── api/             # musicApi, projectApi
│       ├── components/      # Workspace, generator, piano roll, playback, …
│       ├── store/           # Zustand musicStore
│       └── utils/           # validation, playback, piano-roll helpers
├── scripts/                 # e.g. v1_docker_acceptance.sh
├── docs/                    # composition.v1, persistence, testing, codebase map
├── .ai-factory/             # DESCRIPTION, ARCHITECTURE, plans, config
├── docker-compose.yml
├── compose.dev.yml
├── .env.example
└── start-servers.sh
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app, LLM generate/edit, MusicXML/MIDI/WAV export |
| `backend/app/services/fake_llm.py` | Deterministic `LLM_FAKE_MODE` generate/edit (no API credits) |
| `backend/app/ready.py` | Logging/CORS helpers and readiness report |
| `backend/app/routers/projects.py` | Project CRUD + autosave APIs |
| `backend/run.py` / `uvicorn app.main:app` | Backend process entry |
| `frontend/src/main.jsx` | Frontend bootstrap |
| `frontend/src/store/musicStore.js` | Shared UI/application state |
| `docker-compose.yml` | Production-local backend + nginx frontend |
| `compose.dev.yml` | Optional hot-reload override |
| `.env.example` | Env template for LLM/settings |
| `.ai-factory/config.yaml` | AI Factory language/paths/git settings |

## Documentation

| Document | Path | Description |
|----------|------|-------------|
| README | `README.md` | Install, features, env vars, run instructions |
| Composition V1 | `docs/composition-v1.md` | Canonical JSON contract and fidelity rules |
| Project persistence | `docs/project-persistence.md` | SQLite projects and migrations |
| Testing | `docs/testing.md` | How to run backend/frontend tests |
| Codebase map | `docs/CODEBASE_MAP.md` | Broader navigation map |

## AI Context Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | Structural map for agents (this file) |
| `.ai-factory/DESCRIPTION.md` | Project specification and stack |
| `.ai-factory/ARCHITECTURE.md` | Architecture pattern and dependency rules |
| `.ai-factory/rules/base.md` | Detected coding conventions |
| `.ai-factory/config.yaml` | AI Factory configuration |

## Agent Rules

- Decompose shell command chains; do not combine unrelated git operations with `&&` when a failure mid-chain is confusing
  - Incorrect: `git checkout main && git pull`
  - Correct: First `git checkout main`, then `git pull origin main`
- Treat `composition.v1` `tracks[].events[]` as the only playable source; do not invent notes from `harmony`
- Prefer extending `routers/` + `services/` over growing unrelated logic in `main.py`
- Never log API keys, full prompts, or raw MusicXML/MIDI/WAV payloads
