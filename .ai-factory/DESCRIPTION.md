# AI Music Composer (Mukit AI)

## Overview

Full-stack LLM music composer that generates and edits canonical playable `composition.v2` JSON. A FastAPI backend orchestrates multi-stage LangChain/LangGraph composition, validation, secure MIDI/MusicXML import into V2, deterministic `composition.analysis.v1` musical analysis, harmony timeline editing and reharmonization preview, AI-assisted arrangement/orchestration preview, MusicXML/MIDI/WAV export, and SQLite project persistence. A React/Vite frontend provides prompt controls, import workflows, piano-roll and JSON editing, Analysis / Motifs / Harmony / Arrange / Develop tabs, OSMD notation, and Tone.js playback of the same canonical note events. `composition.v1` remains accepted migration/parser input.

## Core Features

- Multi-stage LLM generation of operational `composition.v2` (form → harmony → melody → bass → accompaniment → assemble → validate/repair)
- Secure MIDI / MusicXML / MXL import into the same V2 workspace with session import reports (no LLM required; no retained source files)
- Deterministic composition analysis (`composition.analysis.v1`) over current V2 scopes; frontend-derived cache only; optional bounded advisory context for LLM edit/repair
- Explicit harmony tick-span timeline with local add/replace/remove/move/resize; `POST /harmony/reharmonize/preview` for deterministic/AI candidates (apply is client-side, fingerprint-gated)
- AI-assisted arrangement / orchestration via `GET /composition/arrangement/instruments` and `POST /composition/arrangement/preview` (ephemeral candidates; Apply commits V2 only; curated catalog not persisted as catalog IDs/ranges)
- Partial region editing via LLM patch (`replace_region`) without regenerating the full score
- Local project CRUD with SQLite persistence and debounced autosave
- Piano-roll and JSON editors sharing the same `editedMusicJson` Zustand state
- Notation preview (MusicXML regenerated from V2) and browser playback (Tone.js) from `tracks[].events[]`
- Deterministic export: MusicXML, MIDI, and server-side FluidSynth WAV

## Tech Stack

- **Programming language:** Python 3.14+ (backend), JavaScript (frontend)
- **Framework:** FastAPI + Uvicorn; React 18 + Vite
- **Database:** SQLite (`PROJECT_DB_PATH`) with numbered SQL migrations
- **ORM:** None — raw SQL via `sqlite3` helpers in `backend/app/db/` and `project_store`
- **LLM:** LangChain / LangGraph with OpenAI-compatible providers (OpenAI, DeepSeek) plus optional `LLM_FAKE_MODE` deterministic fixture provider for demos/E2E
- **Music processing:** music21 (MusicXML render + import), mido (MIDI import/export), defusedxml (import preflight), FluidSynth + SoundFont (WAV)
- **Frontend libraries:** Zustand, styled-components, Tone.js, OpenSheetMusicDisplay, axios
- **Integrations:** Docker Compose production-local stack (`backend` + nginx `frontend`), optional LLM API keys via `.env` / Compose (backend-only); `IMPORT_*` byte/complexity limits

## Architecture Notes

- Monolith: one API service + one SPA; logical modules (projects, import, analysis, composition/LLM, rendering/export) live inside `backend/app/` and `frontend/src/`
- Dependency direction: HTTP handlers → services → DB / external I/O; Pydantic schemas are shared contracts
- `composition.v2` is the operational source of truth for playable notes; `composition.v1` is migration/parser input. External imports convert directly to V2. `harmony` is explicit tick-span metadata only and must not invent audible events; raw import leaves `harmony: []`. Analysis, arrangement, development, and reharmonize previews are non-persisted until the user explicitly applies a candidate.

## Architecture

See `.ai-factory/ARCHITECTURE.md` for detailed architecture guidelines.
**Pattern:** Structured Modules (Technical Layer)

## Non-Functional Requirements

- **Logging:** Configurable via `LOG_LEVEL` (backend) and `VITE_LOG_LEVEL` (frontend); structured extras for stage/provider/model/import/analysis/arrangement codes and fingerprint prefixes; never log API keys, full prompts, instructions, raw MusicXML/MIDI payloads, upload bytes, catalog override contents, or full analysis/composition reports
- **Error handling:** Domain errors mapped to HTTP 4xx/5xx with sanitized detail; oversized LLM prompts → 422; oversized/hostile imports → 413/415/422; invalid analysis composition/scope → 422; missing LLM/WAV/import deps → 503
- **Security:** Secrets only via environment; CORS for local frontend; no credentials in repo; import rejects DTD/entities, unsafe MXL, and unbounded expansion
- **Testing:** Backend pytest + httpx; frontend Node test runner for utils/store/api; Playwright import and analysis journeys; Docker acceptance includes multipart import
