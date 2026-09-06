# Architecture: Structured Modules (Technical Layer)

## Overview

Mukit AI is a full-stack LLM music composer: a FastAPI backend produces and transforms canonical `composition.v1` JSON (generate, edit, validate, render, export, persist), and a React/Vite frontend edits that composition on a piano roll / JSON surface, shows notation, and plays note events in the browser.

This project uses **Structured Modules (Technical Layer)** as the guiding pattern — feature areas with clear service boundaries and downward dependencies — while **documenting the existing layout** rather than requiring an immediate module-folder refactor. New work should strengthen module boundaries inside the current trees (`backend/app/`, `frontend/src/`) instead of introducing hexagonal ceremony or microservices.

## Decision Rationale

- **Project type:** Full-stack AI music composition tool (monolith: FastAPI API + React SPA)
- **Tech stack:** Python 3 / FastAPI / Pydantic / LangChain·LangGraph / music21 / SQLite; React / Vite / Zustand / styled-components / Tone.js / OSMD
- **Key factor:** Medium domain complexity and a growing service surface, with a small team and a single deployable app — modular structure without Explicit Architecture overhead

## Folder Structure

Documented as the application exists today. Logical **modules** are named in comments; they are not separate top-level packages yet.

```text
mukit-ai/
├── backend/
│   ├── app/
│   │   ├── main.py                 # Composition / LLM / export HTTP handlers (composition module surface)
│   │   ├── ready.py                # LOG_LEVEL, CORS origins, readiness report helpers
│   │   ├── schemas.py              # composition.v1 + LLM request/response models
│   │   ├── project_schemas.py      # Project CRUD API models
│   │   ├── llm_settings.py         # Provider config from environment
│   │   ├── routers/
│   │   │   └── projects.py         # Projects module HTTP routes
│   │   ├── services/               # Application services (orchestration + domain helpers)
│   │   │   ├── llm_music_generator.py
│   │   │   ├── llm_composition_editor.py
│   │   │   ├── composition_planner.py
│   │   │   ├── composition_validator.py
│   │   │   ├── composition_normalizer.py
│   │   │   ├── composition_timing.py
│   │   │   ├── composition_region_patch.py
│   │   │   ├── music_json_renderer.py   # MusicXML
│   │   │   ├── composition_midi.py
│   │   │   ├── composition_wav.py
│   │   │   ├── project_store.py         # SQLite persistence
│   │   │   └── project_composition.py   # Project ↔ composition mapping
│   │   └── db/                     # Shared infrastructure: connection + SQL migrations
│   │       ├── connection.py
│   │       └── migrations/
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── api/                    # HTTP clients (outbound adapters)
│       │   ├── musicApi.js
│       │   └── projectApi.js
│       ├── store/
│       │   └── musicStore.js       # Zustand — shared UI/application state
│       ├── components/             # Feature UI (projects, generate, piano roll, playback, export)
│       ├── utils/                  # Client-side composition/playback helpers
│       ├── App.jsx
│       └── main.jsx
├── docs/                           # composition.v1, persistence, testing
├── docker-compose.yml
├── compose.dev.yml
├── .env.example
└── README.md
```

### Logical modules (within the flat trees)

| Module | Backend home | Frontend home |
|--------|--------------|---------------|
| **Projects** | `routers/projects.py`, `project_schemas.py`, `services/project_*` | `ProjectBrowser`, `projectApi.js`, project slice of `musicStore` |
| **Composition / LLM** | `main.py` LLM routes, `schemas.py`, `llm_*`, `composition_*` (plan/validate/normalize/patch) | `MusicGenerator`, `PromptJsonEditor`, `AiRegionEditPanel`, `musicApi.js` |
| **Rendering / Export** | `music_json_renderer`, `composition_midi`, `composition_wav` | `NotationViewer`, `ExportControls`, playback components + `utils/playback*` / `tonePlaybackEngine` |
| **Shared infrastructure** | `db/`, `llm_settings.py`, CORS/lifespan in `main.py` | `api/*`, shared store fields, `utils/downloadFile.js` |

Prefer growing these boundaries (new routers under `routers/`, cohesive service clusters, schema files per module) over dumping unrelated logic into `main.py` or a single god service.

## Dependency Rules

Backend flow is strict downward: **HTTP handlers → services → persistence / external I/O**. Models (Pydantic schemas) are shared data contracts, not a layer that imports services.

```text
routers / main.py handlers
        ↓
   services/  (orchestration + composition rules)
        ↓
   db/ + external (SQLite, LLM APIs, music21, FluidSynth)
```

Frontend flow: **components → store / utils → api → backend**.

```text
components/
     ↓
store/ + utils/
     ↓
api/
     ↓
FastAPI backend
```

- ✅ Route handlers call services; services call `db` / LLM / render libraries
- ✅ Frontend components call Zustand actions and API modules; playback/notation utils stay free of React components
- ✅ Cross-module use goes through service functions or shared schemas (`composition.v1`), not private helpers inside another module’s files when avoidable
- ❌ Services must not import FastAPI routers or request objects
- ❌ `db/` / store implementations must not import route handlers
- ❌ Frontend `utils/` must not import React components or the Zustand store (keep pure functions testable)
- ❌ Do not skip the service layer from routers to raw SQL / LLM clients for new features

## Layer/Module Communication

- **HTTP boundary:** FastAPI routers and `main.py` endpoints validate with Pydantic, map domain/service errors to HTTP status codes, and return DTOs — no composition business rules in handlers beyond thin orchestration.
- **Canonical contract:** `composition.v1` (see `docs/composition-v1.md` and `schemas.py`) is the shared language between generate, edit, persist, render, export, and the frontend editors/playback.
- **Projects module:** `project_store` owns SQLite; `project_composition` normalizes stored JSON to the canonical model before API responses.
- **Composition pipeline:** LLM generate/edit services produce or patch JSON; validator/normalizer/timing services enforce and shape the model; render/export services consume validated compositions only.
- **Frontend state:** Zustand `musicStore` holds API status, models, project browser/save status, edited composition, piano-roll and playback transport state. Feature components subscribe to slices; they do not own parallel sources of truth for the same composition.
- **Client ↔ server:** `musicApi.js` / `projectApi.js` are the only HTTP clients; components and store actions go through them.

## Key Principles

1. **Module boundaries by convention:** Treat Projects, Composition/LLM, and Rendering/Export as modules even while files live in shared `services/` / `components/` folders. Prefer new files named and clustered by module.
2. **Thin HTTP, fat services:** Keep `main.py` / routers focused on transport. Put generation, validation, patching, persistence, and export logic in `services/`.
3. **Canonical composition first:** Any path that mutates or exports music should go through validated `composition.v1` (or an explicit migration/normalize step), not ad-hoc JSON shapes.
4. **Application services orchestrate:** Services coordinate LLM calls, validation, and I/O. Push invariants into schema validation and dedicated composition helpers rather than scattering rules across handlers and React components.
5. **Frontend purity where it matters:** Keep event math, validation mirrors, and Tone.js engine code in `utils/` with unit tests; keep UI in `components/`.
6. **Infrastructure stays small and shared:** `db/`, env-based `llm_settings`, Docker, and CORS belong to shared infrastructure — not copied per feature.

## Code Organization Note

- **New Features:** All new code should follow the architecture defined in this document where practical.
- **Existing Code:** Document the current structure as-is. When modifying existing code, prefer following the architectural conventions in this document, but do not force a rewrite of unrelated code.
- **Interoperability:** When new code must call existing code, prefer clean interfaces but do not refactor purely for structural alignment.

## Code Examples

### Thin FastAPI handler calling a service

```python
# backend/app/routers/projects.py (pattern)
@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project_route(project_id: str) -> ProjectDetailResponse:
    try:
        record = get_project(project_id)  # service / store
        composition, migrated, path = normalize_project_composition(record.composition_json)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectDetailResponse(...)
```

### Service orchestration without HTTP types

```python
# backend/app/services/llm_music_generator.py (pattern)
def generate_music_json(request: LLMMusicGenerationRequest) -> Composition:
    settings = load_llm_settings()
    if not settings.available_providers:
        raise NoLLMProviderConfiguredError(...)
    raw = run_llm_graph(request, settings)  # external I/O
    composition = validate_and_normalize(raw)  # domain rules in services/schemas
    return composition
```

### Frontend: component → store → API

```javascript
// Prefer store actions that call api modules
const saveProject = useMusicStore((s) => s.saveProject);

// Inside the store action (musicStore.js):
// await projectApi.patchProject(id, { composition: editedMusicJson });
```

### Allowed vs forbidden dependency direction

```python
# ✅ Router → service
from ..services.project_store import get_project

# ❌ Service → router / FastAPI Request
# from ..routers.projects import router  # forbidden
```

## Anti-Patterns

- ❌ Growing `main.py` with new business logic instead of extracting a service and/or `routers/` module
- ❌ Handlers talking directly to SQLite or LLM clients, skipping `services/`
- ❌ Divergent JSON shapes for the same composition across persist, export, piano roll, and playback
- ❌ Duplicating composition rules in React components that already exist in backend validators/schemas
- ❌ Importing React components or the Zustand store from `frontend/src/utils/`
- ❌ Cross-importing unrelated feature internals (e.g. WAV renderer importing project router helpers) instead of shared schemas/services
- ❌ Anemic “pass-through” services that only forward kwargs with no validation or boundary — either add real orchestration or call the lower layer from the owning module consistently
