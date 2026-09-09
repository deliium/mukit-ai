# AGENTS.md

> Keep this file factual and update it when the project structure changes significantly. Detailed product docs live under `docs/` and `.ai-factory/DESCRIPTION.md`.

## Project Overview

Full-stack LLM music composer: FastAPI generates/edits canonical `composition.v2` JSON; MIDI/MusicXML import converts uploads into the same V2; deterministic `composition.analysis.v1` analyzes current V2 without mutating it; React/Vite edits on piano roll/JSON, shows OSMD notation, Analysis tab, and plays note events with Tone.js. Projects persist in SQLite. V1 remains migration/parser input.

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
│   │   ├── routers/         # Projects + imports + analysis + motifs + harmony HTTP API
│   │   ├── services/        # Domain + orchestration (incl. import, analysis, motifs, theme, harmony, reharmonization, fake_llm)
│   │   ├── composition_schemas.py  # composition.v1 / composition.v2 contracts (incl. optional motifs)
│   │   ├── analysis_schemas.py     # composition.analysis.v1 DTOs / warning codes
│   │   ├── motif_schemas.py        # Motif apply DTOs
│   │   ├── harmony_schemas.py      # Harmony timeline + reharmonize preview DTOs
│   │   ├── import_schemas.py       # Import DTOs, issue/error codes
│   │   ├── import_settings.py      # IMPORT_* limits and conversion policy
│   │   ├── fixtures/        # Canonical composition JSON (V1 + V2 expressive for fake LLM / tests)
│   │   ├── db/              # SQLite connection + migrations
│   │   └── schemas.py       # LLM models + composition re-exports
│   └── tests/
├── frontend/                # React + Vite SPA
│   ├── e2e/                 # Playwright V1/V2/import/analysis/motif acceptance journeys
│   └── src/
│       ├── api/             # musicApi, projectApi
│       ├── components/      # Workspace, generator, import, analysis, motifs, piano roll, playback, …
│       ├── store/           # Zustand musicStore
│       └── utils/           # validation, playback, piano-roll, analysis, motif, harmony helpers
├── scripts/                 # e.g. v1/v2_docker_acceptance.sh
├── docs/                    # composition.v2/v1, analysis, import, persistence, testing, codebase map
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
| `backend/app/composition_schemas.py` | Strict V1/V2 document models and timeline helpers |
| `backend/app/analysis_schemas.py` | `composition.analysis.v1` DTOs, scopes, warning codes |
| `backend/app/motif_schemas.py` | Motif apply request/response DTOs |
| `backend/app/routers/analysis.py` | `POST /analysis/composition` |
| `backend/app/routers/motifs.py` | `POST /motifs/apply` |
| `backend/app/routers/harmony.py` | `POST /harmony/reharmonize/preview` |
| `backend/app/harmony_schemas.py` | Harmony timeline + reharmonize preview DTOs |
| `backend/app/services/composition_reharmonization.py` | Deterministic reharmonize preview engine |
| `backend/app/services/composition_analysis.py` | Analysis orchestrator + bounded LLM advisory projection |
| `backend/app/services/composition_motif_editor.py` | Canonical motif apply + destination replacement |
| `backend/app/services/composition_theme.py` | Structured theme plan + generation recurrence |
| `backend/app/routers/imports.py` | `POST /imports/midi` and `/imports/musicxml` |
| `backend/app/services/composition_import.py` | Shared source → V2 canonicalization |
| `backend/app/services/composition_midi_import.py` | Deterministic MIDI parse |
| `backend/app/services/composition_musicxml_import.py` | Hardened MusicXML/MXL parse |
| `backend/app/services/composition_migration.py` | V1→V2 migration and fidelity gate |
| `backend/app/services/composition_projection.py` | Shared export projection report + issue codes |
| `backend/app/services/fake_llm.py` | Deterministic `LLM_FAKE_MODE` generate/edit (incl. V2 expressive fixture) |
| `backend/app/ready.py` | Logging/CORS helpers and readiness report |
| `backend/app/routers/projects.py` | Project CRUD + autosave APIs |
| `backend/run.py` / `uvicorn app.main:app` | Backend process entry |
| `frontend/src/main.jsx` | Frontend bootstrap |
| `frontend/src/store/musicStore.js` | Shared UI/application state (incl. import + analysis + harmony + development preview) |
| `frontend/src/components/ImportControls.jsx` | Import / replace UX |
| `frontend/src/components/CompositionAnalysisPanel.jsx` | Analysis tab UI |
| `frontend/src/components/CompositionDevelopmentPanel.jsx` | Develop tab candidate workflow |
| `frontend/src/components/MotifPanel.jsx` | Motifs tab authoring / apply UI |
| `frontend/src/components/HarmonyTimelinePanel.jsx` | Harmony tab timeline + reharmonize preview/apply |
| `backend/app/routers/composition_development.py` | `POST /composition/development/preview` |
| `backend/app/composition_development_schemas.py` | Development preview request/candidate DTOs |
| `backend/app/services/llm_composition_development.py` | Multi-candidate development orchestration |
| `backend/app/services/composition_development_patch.py` | Append/variation draft realization |
| `docker-compose.yml` | Production-local backend + nginx frontend |
| `compose.dev.yml` | Optional hot-reload override |
| `.env.example` | Env template for LLM/import settings |
| `.ai-factory/config.yaml` | AI Factory language/paths/git settings |

## Documentation

| Document | Path | Description |
|----------|------|-------------|
| README | `README.md` | Install, features, env vars, run instructions |
| Composition V2 | `docs/composition-v2.md` | Operational canonical contract and export fidelity |
| Composition Analysis | `docs/composition-analysis.md` | Deterministic sidecar, scopes, warnings, Analysis tab |
| Composition Development | `docs/composition-development.md` | Continue / add section / vary; multi-candidate preview |
| MIDI / MusicXML import | `docs/import.md` | Ingestion mappings, limits, issue codes |
| Composition V1 | `docs/composition-v1.md` | V1 compatibility, staged generation, region editing |
| Project persistence | `docs/project-persistence.md` | SQLite projects and migrations |
| Testing | `docs/testing.md` | How to run backend/frontend tests |
| Codebase map | `docs/CODEBASE_MAP.md` | Broader navigation map |

## AI Context Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | Structural map for agents (this file) |
| `.ai-factory/DESCRIPTION.md` | Project specification and stack |
| `.ai-factory/ARCHITECTURE.md` | Architecture pattern and dependency rules |
| `.ai-factory/RULES.md` | Project axioms for agents and quality gates |
| `.ai-factory/rules/base.md` | Detected coding conventions |
| `.ai-factory/config.yaml` | AI Factory configuration |

## Agent Rules

- Decompose shell command chains; do not combine unrelated git operations with `&&` when a failure mid-chain is confusing
  - Incorrect: `git checkout main && git pull`
  - Correct: First `git checkout main`, then `git pull origin main`
- Treat `composition.v2` `tracks[].events[]` as the only playable source; do not invent notes from `harmony`. V1 is migration input only. Raw MIDI/MusicXML import sets `harmony: []` and does not run musical analysis. `composition.analysis.v1` is a derived sidecar only — never persist it as composition data.
- Prefer extending `routers/` + `services/` over growing unrelated logic in `main.py`
- Never log API keys, full prompts, raw MusicXML/MIDI/WAV payloads, uploaded import source bytes, full analysis reports, or event arrays
