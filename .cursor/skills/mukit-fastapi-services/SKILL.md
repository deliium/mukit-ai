---
name: mukit-fastapi-services
description: Mukit AI FastAPI backend conventions — routers vs main handlers, services, SQLite migrations, domain errors to HTTP, structured logging. Use when adding or changing backend routes, services, persistence, or API error mapping.
metadata:
  author: mukit-ai
  version: "1.0"
---

# Mukit FastAPI Services

## Overview

Backend layout under `backend/app/`: HTTP surface (`main.py`, `routers/`) → `services/` → `db/` / external I/O. Pydantic schemas in `schemas.py` / `project_schemas.py` are shared contracts, not a layer that imports services.

## Guidelines

### HTTP surface

- Prefer new resource APIs under `routers/` (see `routers/projects.py`)
- Keep composition/LLM/export handlers cohesive; avoid dumping unrelated logic into `main.py`
- Map domain exceptions to `HTTPException` with `raise ... from exc`
- Common statuses: 404 not found, 422 validation/composition errors, 502 bad LLM output, 503 missing provider/FluidSynth

### Services

- One concern per module (`project_store`, `composition_midi`, `llm_music_generator`, …)
- Domain error types live next to the service that owns them
- Pure transforms (normalize, timing, region patch) stay free of FastAPI imports

### Persistence

- SQLite via `PROJECT_DB_PATH`; connection helpers in `db/connection.py`
- Schema changes: numbered SQL migrations under `db/migrations/`
- Opening projects re-runs composition normalization before returning to clients

### Logging

```python
logger = logging.getLogger(__name__)
logger.info("Project open requested", extra={"project_id": project_id})
```

- Use structured `extra={...}` for ids, counts, stages, providers
- Never log secrets, full prompts, or raw binary/XML/MIDI payloads

## Examples

### Good: domain error → HTTP

```python
try:
    record = store.get(project_id)
except ProjectNotFoundError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc
```

### Bad: swallowing errors or logging secrets

```python
except Exception:
    logger.error("failed: %s", api_key)  # never
    return {"ok": False}
```

## Checklist

- [ ] No service → router upward imports
- [ ] New tables/columns have a migration
- [ ] Tests under `backend/tests/` for route + service behavior
- [ ] Error messages sanitized for clients
