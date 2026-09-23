# AI Music Composer (Mukit AI)

## Overview

Full-stack LLM music composer that generates and edits canonical playable `composition.v2` JSON. A FastAPI backend orchestrates multi-stage LangChain/LangGraph composition, validation, secure MIDI/MusicXML import into V2, deterministic `composition.analysis.v1` musical analysis, harmony timeline editing and reharmonization preview, AI-assisted arrangement/orchestration preview, durable Composer Profiles (`composer.profile.v1`) for soft generate conditioning, selective reference feature dimensions (`reference.features.v1`) plus request-scoped conditioning policy (`reference.conditioning.policy.v1`) for generate/develop/edit soft masks, MusicXML/MIDI/WAV export, and SQLite project persistence with durable revision/branch history. A React/Vite frontend provides prompt controls, import workflows, piano-roll and JSON editing, Analysis / Motifs / Harmony / Arrange / Develop / Profiles / Versions tabs, OSMD notation, and Tone.js playback of the same canonical note events. Substantial AI results stay preview-first until Apply. `composition.v1` remains accepted migration/parser input.

## Core Features

- Multi-stage LLM generation of operational `composition.v2` (form → harmony → melody → bass → accompaniment → assemble → validate/repair); optional hybrid pipeline (`options.pipeline=hybrid_plan_symbolic`) where an LLM emits non-playable `composition.plan.v1` and a symbolic composer (`fake:symbolic-tiny` / Music Transformer) emits note events
- Versioned non-playable `composition.plan.v1` planning contract (form/harmony/themes/density only — never a second score)
- Secure MIDI / MusicXML / MXL import into the same V2 workspace with session import reports (no LLM required; no retained source files)
- Offline symbolic training dataset pipeline under `DATASET_ROOT` (CLI ingest/normalize/segment/split/stats; provenance-gated; never `PROJECT_DB_PATH`)
- Versioned Composition V2 tokenizer (`tokenizer.v1`) under `backend/app/tokenizer/` (CLI encode/decode/stats/viz; train+inference contract; never `PROJECT_DB_PATH` / weight load)
- Optional PyTorch Music Transformer (`music_transformer.v1`) under `backend/app/music_transformer/` (offline experiment train/eval/listen/compare over tokenizer; resume + metrics; optional generate API; never `PROJECT_DB_PATH` / GGUF in FastAPI; symbolic metrics ≠ musical quality)
- Deterministic composition analysis (`composition.analysis.v1`) over current V2 scopes; frontend-derived cache only; optional bounded advisory context for LLM edit/repair
- Durable Composer Profiles (`composer.profile.v1`) with explicit vs derived prefs, Off/Light/Normal/Strong soft generate conditioning; prompt/hard constraints always win; never copies melodies or writes `DATASET_ROOT`
- Selective reference features (`reference.features.v1`) — dimension masks (e.g. Rhythmic density / Orchestration texture) soft-condition generate and develop; derived reports only; never mutates the reference Composition; never copies melodies or writes `DATASET_ROOT`
- Reference conditioning policy (`reference.conditioning.policy.v1`) — explicit preserve / borrow / regenerate disposition with per-dimension strength and multi-reference assignment (e.g. texture of A + rhythm of B); wires generate, develop, and AI region edit; soft only; own-project motif reuse opt-in gated by `active_project_id`
- Explicit harmony tick-span timeline with local add/replace/remove/move/resize; `POST /harmony/reharmonize/preview` for deterministic/AI candidates (apply is client-side, fingerprint-gated)
- AI-assisted arrangement / orchestration via `GET /composition/arrangement/instruments` and `POST /composition/arrangement/preview` (ephemeral candidates; Apply commits V2 only; curated catalog not persisted as catalog IDs/ranges)
- Partial region editing via LLM patch (`replace_region`) without regenerating the full score
- Local project CRUD with SQLite persistence, debounced autosave, immutable compressed snapshots, named branches, and restore-as-child revisions
- Piano-roll and JSON editors sharing the same `editedMusicJson` Zustand state
- Browser Web MIDI / QWERTY test performance capture into a destination track (session take → one undoable V2 commit; optional quantize; never required at startup)
- Co-performance real-time layer: Transport-synced MIDI stream, shared playback engine accompaniment buffer, local degradation, optional `POST /live/accompaniment/predict`, explicit Commit (never per-note persist)
- AI Jam modes (`user_melody` / `user_chords`) on co-performance: live features + harmony belief, jam controls, multi-role local fill, multi-track Commit (+ optional harmony metadata)
- Monophonic audio transcription (mic/file → session `transcription.preview.v1` review → Apply into V2); confidence stays off V2 notes; audio never persisted
- Optional neural audio rendering (job-based generative/neural-instrument egress via `/neural-audio/renders`); never mutates V2; optional MusicGen Compose profile; distinct from FluidSynth Export WAV
- Notation preview (MusicXML regenerated from V2) and browser playback (Tone.js) from `tracks[].events[]`
- Deterministic export: MusicXML, MIDI (SMF Type 1 DAW handoff with section markers + drag/download UX), and server-side FluidSynth WAV
- Durable hybrid generation provenance (`generation.provenance.v1`) on AI revisions; V3 Docker acceptance (`scripts/v3_docker_acceptance.sh`) with seeded reproduce under fake modes
- V4 multi-agent music architecture: specialized in-process agents above `ai_runtime` (`GET /ai/agents`, workflow preview spine, typed artifacts, progressive realize); Apply only via `multi-agent-apply` CAS — never invents `composition.v4`

## Tech Stack

- **Programming language:** Python 3.14+ (backend), JavaScript (frontend)
- **Framework:** FastAPI + Uvicorn; React 18 + Vite
- **Database:** SQLite (`PROJECT_DB_PATH`) with Alembic schema migrations
- **ORM:** None — raw SQL via `sqlite3` helpers in `backend/app/db/` and `project_store`
- **LLM:** LangChain / LangGraph with OpenAI-compatible providers (OpenAI, DeepSeek) plus optional `LLM_FAKE_MODE` deterministic fixture provider for demos/E2E; optional local OpenAI-compatible sidecar (`LOCAL_LLM_*`, Compose `--profile local-ai` / `local-ai-vllm`) via `LocalLanguageModel` — app never loads weights; capability-aware `ai_runtime/` registry routes generate/edit/arrange/develop/reharmonize/motif by `AiOperation` (`GET /ai/models`, compat `/llm/models`); hybrid generation uncollapses `generate_planner` / `generate_composer` onto language vs `symbolic_composer` without silent LLM note fallback; V4 `ai_agents/` binds specialized roles to the same resolve path (`AI_AGENT_<ID>_MODEL`)
- **Music processing:** music21 (MusicXML render + import), mido (MIDI import/export), defusedxml (import preflight), FluidSynth + SoundFont (WAV)
- **Frontend libraries:** Zustand, styled-components, Tone.js, OpenSheetMusicDisplay, axios
- **Integrations:** Docker Compose production-local stack (`backend` + nginx `frontend`); optional `compose.local-ai.yml` profiles for local inference sidecars; optional `compose.neural-audio.yml` `--profile neural-audio` for MusicGen-shaped render sidecar; optional LLM API keys via `.env` / Compose (backend-only); `IMPORT_*` / `AUDIO_*` / `NEURAL_AUDIO_*` limits

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
