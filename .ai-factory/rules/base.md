# Project Base Rules

> Auto-detected conventions from codebase analysis. Edit as needed.

## Naming Conventions

- Files: Python `snake_case.py`; React components `PascalCase.jsx`; frontend utils/api/store `camelCase.js`
- Variables: `snake_case` (Python), `camelCase` (JavaScript)
- Functions: `snake_case` (Python), `camelCase` (JavaScript)
- Classes / Pydantic models: `PascalCase` (e.g. `Composition`, `ProjectStoreError`)
- Tests: backend `test_*.py` under `backend/tests/`; frontend `*.test.js` colocated with utils/store/api

## Module Structure

- Backend: `backend/app/` with `main.py` + `routers/`, `services/`, `db/`, Pydantic schemas (`schemas.py`, `project_schemas.py`)
- Frontend: `frontend/src/` with `components/`, `api/`, `store/`, `utils/`
- Docs: `docs/` for composition.v2 (canonical), composition.v1 (compat), persistence, testing; AI Factory artifacts under `.ai-factory/` (axioms in `RULES.md`)
- Prefer new HTTP surface under `routers/` and cohesive service modules over growing unrelated logic in `main.py`

## Error Handling

- Domain exceptions in services (e.g. `ProjectNotFoundError`, `CompositionWavError`) mapped in routers/handlers to `HTTPException` with appropriate status codes
- Prefer `raise ... from exc` to preserve cause chains
- Sanitize user-facing `detail` (truncate long errors); use 404 / 422 / 502 / 503 consistently with existing routes

## Control Flow

- Prefer flat, readable control flow over deeply nested conditionals. Use guard clauses, early `return`/`continue`, small named helper methods, or explicit classification logic when they make the code easier to follow. Handle edge cases and irrelevant branches early so the main path stays visible.

## Logging

- Backend: `logging.getLogger(__name__)` with structured `extra={...}` for ids, counts, stages; respect `LOG_LEVEL`
- Frontend: prefixed `console.debug` / `console.warn` for playback, piano-roll, and API diagnostics
- Never log API keys, full freeform prompts, or raw MusicXML/MIDI/WAV payloads
