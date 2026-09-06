# AI Music Composer (Mukit AI)

## Overview

Full-stack LLM music composer that generates and edits canonical playable `composition.v1` JSON. A FastAPI backend orchestrates multi-stage LangChain/LangGraph composition, validation, MusicXML/MIDI/WAV export, and SQLite project persistence. A React/Vite frontend provides prompt controls, piano-roll and JSON editing, OSMD notation, and Tone.js playback of the same canonical note events.

## Core Features

- Multi-stage LLM generation of `composition.v1` (form → harmony → melody → bass → accompaniment → assemble → validate/repair)
- Partial region editing via LLM patch (`replace_region`) without regenerating the full score
- Local project CRUD with SQLite persistence and debounced autosave
- Piano-roll and JSON editors sharing the same `editedMusicJson` Zustand state
- Notation preview (MusicXML) and browser playback (Tone.js) from `tracks[].events[]`
- Deterministic export: MusicXML, MIDI, and server-side FluidSynth WAV

## Tech Stack

- **Programming language:** Python 3.14+ (backend), JavaScript (frontend)
- **Framework:** FastAPI + Uvicorn; React 18 + Vite
- **Database:** SQLite (`PROJECT_DB_PATH`) with numbered SQL migrations
- **ORM:** None — raw SQL via `sqlite3` helpers in `backend/app/db/` and `project_store`
- **LLM:** LangChain / LangGraph with OpenAI-compatible providers (OpenAI, DeepSeek) plus optional `LLM_FAKE_MODE` deterministic fixture provider for demos/E2E
- **Music processing:** music21 (MusicXML), mido (MIDI), FluidSynth + SoundFont (WAV)
- **Frontend libraries:** Zustand, styled-components, Tone.js, OpenSheetMusicDisplay, axios
- **Integrations:** Docker Compose production-local stack (`backend` + nginx `frontend`), optional LLM API keys via `.env` / Compose (backend-only)

## Architecture Notes

- Monolith: one API service + one SPA; logical modules (projects, composition/LLM, rendering/export) live inside `backend/app/` and `frontend/src/`
- Dependency direction: HTTP handlers → services → DB / external I/O; Pydantic schemas are shared contracts
- `composition.v1` is the single source of truth for playable notes; `harmony` is metadata only and must not invent audible events

## Architecture

See `.ai-factory/ARCHITECTURE.md` for detailed architecture guidelines.
**Pattern:** Structured Modules (Technical Layer)

## Non-Functional Requirements

- **Logging:** Configurable via `LOG_LEVEL`; structured extras for stage/provider/model; never log API keys, full prompts, or raw MusicXML/MIDI payloads
- **Error handling:** Domain errors mapped to HTTP 4xx/5xx with sanitized detail; oversized LLM prompts → 422; missing LLM/WAV deps → 503
- **Security:** Secrets only via environment; CORS for local frontend; no credentials in repo
- **Testing:** Backend pytest + httpx; frontend Node test runner for utils/store/api
