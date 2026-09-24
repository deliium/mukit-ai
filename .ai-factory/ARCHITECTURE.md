# Architecture: Structured Modules (Technical Layer)

## Overview

Mukit AI is a full-stack LLM music composer: a FastAPI backend produces and transforms canonical `composition.v2` JSON (generate, import, analyze, edit, validate, render, export, persist), and a React/Vite frontend edits that composition on a piano roll / JSON surface, shows notation and scoped analysis, and plays note events in the browser. `composition.v1` remains accepted migration input.

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
│   │   ├── composition_schemas.py  # composition.v1 / composition.v2 document contracts
│   │   ├── arrangement_schemas.py  # Arrangement preview / catalog DTOs
│   │   ├── analysis_schemas.py     # composition.analysis.v1 sidecar DTOs / warning codes
│   │   ├── schemas.py              # LLM request/response models + re-exports
│   │   ├── project_schemas.py      # Project CRUD API models
│   │   ├── import_schemas.py       # Import response/report/issue DTOs
│   │   ├── import_settings.py      # IMPORT_* limits and conversion policy
│   │   ├── audio_transcription_schemas.py  # transcription.preview.v1 (V3 mono)
│   │   ├── audio_transcription_settings.py # AUDIO_* mono limits
│   │   ├── audio_format_policy.py  # Shared audio sniff / signature helpers
│   │   ├── audio_recovery_schemas.py       # audio.recovery.preview/result/bind.v1
│   │   ├── audio_recovery_settings.py      # AUDIO_RECOVERY_* limits / asset root
│   │   ├── audio_alignment_schemas.py      # audio.alignment.v1 + roundtrip.provenance.v1 + bound discovery
│   │   ├── neural_audio_schemas.py # neural_audio_render.job.v1 + stem_set/stem.v1 (egress)
│   │   ├── neural_audio_settings.py# NEURAL_AUDIO_* render root / quotas
│   │   ├── dataset/                # Offline symbolic corpus pipeline (CLI; DATASET_ROOT)
│   │   ├── embeddings/             # Handcrafted symbolic features (cache/index; no torch)
│   │   ├── dataset/                # Offline symbolic corpus (DATASET_ROOT only)
│   │   ├── tokenizer/              # Composition V2 ↔ tokens (CLI; train+inference codec)
│   │   ├── music_transformer/      # PyTorch LM: experiment train/eval/listen/compare (CLI; optional torch)
│   │   ├── llm_settings.py         # Provider config from environment
│   │   ├── local_llm_settings.py   # Optional LOCAL_* OpenAI-compatible sidecar settings
│   │   ├── ai_runtime/             # Capability registry, routing, typed model adapters (provider boundary)
│   │   │   ├── local_health.py     # Bounded local sidecar probe (no weight download)
│   │   │   └── runtimes/           # openai_compatible_chat, fake, stub, local_openai_compatible
│   │   ├── ai_agents/              # V4 multi-agent layer above ai_runtime (typed artifacts; no DB writes)
│   │   │   ├── registry.py         # AgentRegistry + bootstrap
│   │   │   ├── workflow.py         # Spine + delegates revision modes to revision_loop
│   │   │   ├── revision_loop.py    # Bounded critique → revise → re-critique (session preview only)
│   │   │   ├── revision_loop_schemas.py  # Modes, stop reasons, pass records (non-playable)
│   │   │   ├── revision_plan_builder.py  # Finding → RevisionPlan targeting
│   │   │   ├── revision_stop_policy.py   # Multi-condition stop + score digests
│   │   │   └── progressive_realize.py  # working_draft trust boundary
│   │   ├── revision_loop_settings.py  # REVISION_LOOP_* thresholds / budgets
│   │   ├── routers/
│   │   │   ├── projects.py         # Projects module HTTP routes
│   │   │   ├── imports.py          # MIDI / MusicXML multipart import
│   │   │   ├── transcription.py    # POST /transcription/audio (V3 mono)
│   │   │   ├── audio_recovery.py   # /audio-recovery/jobs (+ bind/assets/bound; ingress + alignment)
│   │   │   ├── neural_audio.py     # /neural-audio/renders + /stem-sets (egress only)
│   │   │   ├── analysis.py         # POST /analysis/composition
│   │   │   ├── arrangement.py      # GET/POST /composition/arrangement/*
│   │   │   ├── composition_development.py
│   │   │   ├── harmony.py
│   │   │   ├── motifs.py
│   │   │   ├── critique.py         # POST /critique/evaluate (session-only)
│   │   │   ├── ai_models.py        # GET /ai/models discovery
│   │   │   └── ai_agents.py        # GET/POST /ai/agents* (+ workflow preview revision modes)
│   │   ├── services/               # Application services (orchestration + domain helpers)
│   │   │   ├── llm_music_generator.py
│   │   │   ├── llm_composition_editor.py
│   │   │   ├── llm_composition_arrangement.py
│   │   │   ├── composition_arrangement_context.py
│   │   │   ├── composition_arrangement_patch.py
│   │   │   ├── composition_arrangement_validation.py
│   │   │   ├── instrument_catalog.py    # Curated arrangement GM catalog
│   │   │   ├── fake_llm.py              # LLM_FAKE_MODE deterministic generate/edit/arrange
│   │   │   ├── fixture_compositions.py  # Load packaged Composition V1 fixtures
│   │   │   ├── composition_planner.py
│   │   │   ├── generation_constraints.py # Immutable request hard/soft constraints + conformance
│   │   │   ├── composition_tonality.py   # Deterministic tonal-center analysis
│   │   │   ├── composition_analysis.py  # Analysis orchestrator + bounded LLM projection
│   │   │   ├── composition_fingerprint.py
│   │   │   ├── composition_analysis_context.py
│   │   │   ├── composition_harmony_analysis.py
│   │   │   ├── composition_melody_analysis.py
│   │   │   ├── composition_density_analysis.py
│   │   │   ├── composition_role_analysis.py
│   │   │   ├── composition_repetition_analysis.py
│   │   │   ├── composition_tension_analysis.py
│   │   │   ├── composition_analysis_warnings.py
│   │   │   ├── composition_validator.py
│   │   │   ├── composition_normalizer.py
│   │   │   ├── composition_migration.py   # V1→V2 migration
│   │   │   ├── composition_import.py      # Shared import → V2 canonicalization
│   │   │   ├── composition_midi_import.py
│   │   │   ├── composition_musicxml_import.py
│   │   │   ├── import_instruments.py      # GM map + role inference
│   │   │   ├── composition_projection.py  # Export projection report
│   │   │   ├── composition_timeline.py    # Variable tempo/meter compiler (or timing.py)
│   │   │   ├── composition_region_patch.py
│   │   │   ├── music_json_renderer.py   # MusicXML
│   │   │   ├── composition_midi.py
│   │   │   ├── composition_wav.py
│   │   │   ├── project_store.py         # SQLite persistence
│   │   │   ├── project_history_store.py # Snapshots, revisions, branches, bootstrap
│   │   │   ├── composition_snapshot_encoding.py  # composition.snapshot.v1 zlib encoding
│   │   │   ├── composition_revision_preserve.py  # Outside-target event fingerprints for revise
│   │   │   ├── agent_artifact_workspace.py       # Immutable typed artifact INSERT/promote/GC
│   │   │   ├── audio_recovery_store.py           # Job/asset FS + SQLite meta; kind=alignment_json; project-delete GC
│   │   │   ├── audio_recovery/                   # Separation, scaffolding, transcribe, pipeline (run_inline Bind→alignment)
│   │   │   ├── audio_alignment.py                # Parametric ticks↔source_seconds map + quality
│   │   │   ├── audio_roundtrip_provenance.py     # audio.roundtrip.provenance.v1 fragment builder
│   │   │   ├── neural_audio_render_store.py      # Mix egress jobs/assets under NEURAL_AUDIO_RENDER_ROOT
│   │   │   ├── neural_audio_stem_store.py        # Stem-set / stem member meta + WAV paths
│   │   │   ├── neural_audio_stem_partition.py    # Track→role heuristic + composition slice helpers
│   │   │   ├── neural_audio_stems.py             # Stem-set enqueue/run/rerender (never mutates V2)
│   │   │   └── project_composition.py   # Project ↔ composition mapping
│   │   ├── fixtures/               # composition.v1 + composition_v2_expressive + arrangement_instruments.v1.json
│   │   └── db/                     # Shared infrastructure: connection + Alembic
│   │       ├── connection.py
│   │       └── alembic/            # env.py + versions (incl. alignment_json CHECK / alignment_asset_id)
│   ├── alembic.ini
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── e2e/                        # Playwright V1/V2/import/analysis/arrangement/editor/midi-live-input/audio-recovery/audio-alignment-roundtrip acceptance
│   └── src/
│       ├── api/                    # HTTP clients (outbound adapters)
│       │   ├── musicApi.js
│       │   └── projectApi.js
│       ├── store/
│       │   └── musicStore.js       # Zustand — shared UI/application state (incl. analysis/arrangement/MIDI/audio-recovery/alignment session)
│       ├── components/             # Feature UI (… AudioRecoveryPanel, AudioAlignmentWaveform, NeuralAudioRenderPanel, piano-roll source playhead, …)
│       ├── utils/                  # Client helpers incl. audioAlignment*, audioWaveformPeaks, compositionSnapshotFingerprint, audioRecovery*
│       ├── App.jsx
│       └── main.jsx
├── docs/                           # … audio-recovery, audio-symbolic-alignment, neural-audio-rendering, …
├── models/llm/                     # Optional host GGUF/weights for Compose local-ai profiles (gitignored)
├── models/neural-audio/            # Optional MusicGen-shaped weights (gitignored; Compose neural-audio)
├── models/audio-recovery/          # Optional Demucs-shaped separation weights (gitignored; Compose audio-recovery)
├── docker-compose.yml              # Default stack: backend + frontend only (no GPU / no local AI pull)
├── compose.dev.yml                 # Hot-reload override; optional notes for local-ai compose
├── compose.local-ai.yml            # Optional profiles: local-ai (llama.cpp), local-ai-vllm, training stub
├── compose.neural-audio.yml        # Optional --profile neural-audio (MusicGen sidecar; egress)
├── compose.audio-recovery.yml      # Optional --profile audio-recovery (Demucs-shaped separation; ingress)
├── .env.example
└── README.md
```
### Logical modules (within the flat trees)

| Module | Backend home | Frontend home |
|--------|--------------|---------------|
| **Projects** | `routers/projects.py`, `project_schemas.py`, `services/project_*` | `ProjectBrowser`, `projectApi.js`, project slice of `musicStore` |
| **Import** | `routers/imports.py`, `import_schemas.py`, `import_settings.py`, `composition_*_import.py`, `composition_import.py` | `ImportControls`, `importMidi` / `importMusicXml` in `musicApi.js`, import slice of `musicStore` |
| **Audio transcription** | `routers/transcription.py`, `audio_transcription_schemas.py`, `audio_transcription_settings.py`, `services/audio_transcription/` | `AudioInputPanel`, `transcribeAudio` in `musicApi.js`, audio session slice of `musicStore`, `audioTranscriptionApply.js` |
| **Audio recovery (V4 mixed ingress)** | `routers/audio_recovery.py`, `audio_recovery_schemas.py`, `audio_recovery_settings.py`, `services/audio_recovery/` + `audio_recovery_store.py` — optional separation → scaffolding → confidence-gated notes; Apply→Bind durable source + `audio.recovery.result.v1` overlay (+ sibling `alignment_json` via alignment services); never confidence on V2 events; never `DATASET_ROOT`; `run_inline` jobs | `AudioRecoveryPanel`, recovery APIs in `musicApi.js`, `audioRecovery{Apply,EnsureTracks,Gates,Overlay,PhaseGuards}.js`, recovery session slice of `musicStore` — HTMLAudio source audition (not `playbackSource` / Tone) |
| **Audio↔symbolic alignment** | `audio_alignment_schemas.py` (`audio.alignment.v1`, `audio.roundtrip.provenance.v1`, bound discovery DTOs); `services/audio_alignment.py` + `audio_roundtrip_provenance.py`; Bind writes `kind=alignment_json` + `alignment_asset_id`; `GET /audio-recovery/projects/{id}/bound` hydrate; never nests into `AudioRecoveryResultV1`; never mutates source WAV; never `composition.v4` / `DATASET_ROOT` | `AudioAlignmentWaveform`, `audioAlignment.js` / `audioWaveformPeaks.js` / `compositionSnapshotFingerprint.js`, alignment sync clock + `sourcePlayheadTick` in `musicStore`, piano-roll source playhead (dual clock vs Tone); soft-stale neural UI via snapshot fingerprint |
| **Neural audio rendering** | `routers/neural_audio.py`, `neural_audio_schemas.py` (mix `job.v1` + `stem_set`/`stem.v1`), `neural_audio_settings.py`, `services/neural_audio_{render,stem_*}.py`, `ai_runtime` `AUDIO_RENDER` adapters (`fake:neural-audio` + `direct_stems`, `sidecar:musicgen`, `local:midi-ddsp`) — egress only; stem sets under `{project}/{set_id}/{stem_id}.wav`; selective rerender via `supersedes_stem_id`; explicit FluidSynth stems only (never silent generative fallback); never mutates V2; pins `composition.snapshot.v1` fingerprint for soft-stale; recovery `stem_bindings` are **not** these WAVs | `NeuralAudioRenderPanel` (mix + Stems), neural APIs in `musicApi.js`, `neuralAudioRenderUi.js` + `neuralAudioStemUi.js` (sync honesty + soft-stale) |
| **Datasets (offline)** | `app/dataset/` (`cli`, schemas, store, ingest/normalize/segment/dedup/split/stats); `DATASET_ROOT` filesystem only — never `PROJECT_DB_PATH` | CLI / docs only (no SPA) |
| **Embeddings** | `app/embeddings/` (schemas/features/vector/cache/index); `routers/embeddings.py`; `services/composition_embedding.py` + style conditioning / invalidation; ready `local:symbolic-features-v1` — affinity ≠ quality; never artist≡style; never silent `DATASET_ROOT` ingest | Develop reference picker + similar sections; Motifs related; `compositionEmbeddingReference.js` |
| **Composer profiles** | `composer_profile_schemas.py`, `composer_profile_settings.py`, `routers/composer_profiles.py`, `services/composer_profile_{store,derive,resolve,merge}.py` — durable soft prefs in `PROJECT_DB_PATH`; additive generate fragment only; never melodies / `DATASET_ROOT` | `ComposerProfilesPanel`, `composerProfileApi.js`, MusicGenerator profile/strength selectors, `llmGenerateRequest.js` |
| **Reference features** | `reference_feature_schemas.py`, `reference_feature_settings.py`, `reference_conditioning_schemas.py`, `routers/reference_features.py`, `services/reference_feature_{analyze,condition,affinity}.py`, `services/reference_conditioning_policy.py` — derived `reference.features.v1` masks + request-scoped `reference.conditioning.policy.v1` (preserve/borrow/regenerate, per-dim strength, multi-ref); soft generate/develop/edit fragments; affinity only with `compare_to`; never mutates reference V2; never melodies / `DATASET_ROOT` | `ReferenceFeaturesControls`, `referenceFeatures.js`, `referenceConditioningPolicy.js`, MusicGenerator + Develop + AI Edit policy/multi-ref UI, `compositionEmbeddingReference.js` dimensions |
| **Tokenizer (offline)** | `app/tokenizer/` (encode/decode/vocab/repair/stats/viz/manifest/cli); Composition V2 ↔ `tokenizer.v1` tokens — never `PROJECT_DB_PATH` / FastAPI / weight load | CLI / docs only (no SPA) |
| **Music Transformer (offline + optional API)** | `app/music_transformer/` (model, experiment store, train/resume, metrics, eval, listening, compare, checkpoint, inference, cli); optional `routers/music_transformer.py` generate-only behind env flag; torch via extras — never `PROJECT_DB_PATH` / GGUF in FastAPI; symbolic metrics ≠ musical quality | CLI default; HTTP opt-in generate only |
| **Analysis** | `routers/analysis.py`, `analysis_schemas.py`, `composition_analysis.py` + analyzer cluster / fingerprint / warnings | `CompositionAnalysisPanel`, `analyzeComposition` in `musicApi.js`, `compositionAnalysis.js`, analysis slice of `musicStore` |
| **Arrangement** | `routers/arrangement.py`, `arrangement_schemas.py`, `instrument_catalog.py`, `composition_arrangement_*`, `llm_composition_arrangement.py` | `ArrangementPanel`, arrangement APIs in `musicApi.js`, `compositionArrangementCandidates.js`, arrangement slice of `musicStore` |
| **Harmony / reharmonize** | `routers/harmony.py`, `harmony_schemas.py`, `composition_harmony_*`, `composition_reharmonization.py`, `llm_reharmonizer.py` | `HarmonyTimelinePanel`, `previewReharmonization` in `musicApi.js`, `compositionHarmony*.js`, reharmonize slice of `musicStore` |
| **Composition / LLM** | `main.py` LLM routes, `schemas.py`, `llm_*`, `composition_*` (plan/validate/normalize/patch); bounded analysis advisory via `build_llm_analysis_context` | `MusicGenerator`, `PromptJsonEditor`, `AiRegionEditPanel`, `musicApi.js` |
| **AI runtime** | `ai_runtime/` (capability registry, operation routing, typed protocols/adapters including `local_openai_compatible`); `local_llm_settings.py` + `local_health.py` for optional OpenAI-compatible sidecars; discovery via `/ai/models` (compat `/llm/models`); FluidSynth stays outside; app never loads GGUF/safetensors | `musicApi.js` model catalog clients; global selector remains `/llm/models` (includes ready `local:*` when enabled) |
| **AI agents (V4)** | `ai_agents/` (registry, spine `workflow`, `revision_loop` controller, typed artifact schemas, progressive realize); `revision_loop_settings.py`; `routers/ai_agents.py` preview/run; Critic uses Evaluation Engine read-only. Session `revision_history` + sibling `pass_candidates` (audition) — never auto-Apply / never embed playable scores in pass records. `ai_agents/` must not import workspace or SQLite; promote stays in `agent_artifact_workspace` on Apply CAS | `MultiAgentPanel`, `previewMultiAgentWorkflow` in `musicApi.js`, `revisionLoopModes.js`, multi-agent + audition slice of `musicStore` / `playbackSource` |
| **Critique** | `routers/critique.py`, `critique_schemas.py`, `critique_settings.py`, `services/composition_critique.py` — session evaluate only; does not mutate V2 | Critique UI; cross-links to multi-agent revision loops |
| **Rendering / Export** | `music_json_renderer`, `composition_midi`, `composition_wav` | `NotationViewer`, `ExportControls`, playback components + `utils/playback*` / `tonePlaybackEngine` |
| **MIDI live input (browser)** | None (no backend MIDI stream / WebSocket bridge) | `MidiInputPanel`, session slice in `musicStore`, `utils/midiInput*` / `midiPerformanceCapture` / `midiTakeApply` / `midiMetronome` / `computerKeyboardMidi` — Web MIDI or QWERTY → one V2 take commit; never required at startup; not file import |
| **Shared infrastructure** | `db/`, `llm_settings.py`, CORS/lifespan in `main.py` | `api/*`, shared store fields, `utils/downloadFile.js` |

Prefer growing these boundaries (new routers under `routers/`, cohesive service clusters, schema files per module) over dumping unrelated logic into `main.py` or a single god service.

## Dependency Rules

Backend flow is strict downward: **HTTP handlers → services → persistence / external I/O**. Models (Pydantic schemas) are shared data contracts, not a layer that imports services. Multi-agent code sits above the runtime and must not touch SQLite.

```text
routers / main.py handlers
        ↓
   services/  (orchestration + composition rules)
        ↓
   ai_agents/  (typed agents + revision_loop; session preview; no SQLite)
        ↓
   ai_runtime/  (model registry + typed adapters; not FluidSynth)
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
- ✅ Multi-agent workflow preview may run `ai_agents/revision_loop` then return session candidates; durable mutation is only client Apply via `multi-agent-apply` CAS
- ✅ Frontend components call Zustand actions and API modules; playback/notation utils stay free of React components
- ✅ Cross-module use goes through service functions or shared schemas (`composition.v2` operational, `composition.v1` migration input), not private helpers inside another module’s files when avoidable
- ❌ Services must not import FastAPI routers or request objects
- ❌ `ai_agents/` must not import `agent_artifact_workspace`, `db/`, or project stores
- ❌ `db/` / store implementations must not import route handlers
- ❌ Frontend `utils/` must not import React components or the Zustand store (keep pure functions testable)
- ❌ Do not skip the service layer from routers to raw SQL / LLM clients for new features
- ❌ Critic / Evaluation Engine must not mutate `composition.v2`; revision loops must not auto-commit project revisions

## Layer/Module Communication

- **HTTP boundary:** FastAPI routers and `main.py` endpoints validate with Pydantic, map domain/service errors to HTTP status codes, and return DTOs — no composition business rules in handlers beyond thin orchestration.
- **Canonical contract:** `composition.v2` (see `docs/composition-v2.md` and `composition_schemas.py`) is the operational shared language between generate, edit, persist, render, export, and the frontend editors/playback. `composition.v1` remains migration/parser input (`docs/composition-v1.md`). Derived `composition.analysis.v1` is advisory only (`docs/composition-analysis.md`) and must not become a second source of truth.
- **Projects module:** `project_store` owns SQLite; `project_composition` normalizes stored JSON to the canonical model before API responses.
- **Composition pipeline:** LLM generate/edit services produce or patch JSON; validator/normalizer/timing services enforce and shape the model; render/export services consume validated compositions only. Analysis may feed a bounded advisory projection into edit/repair prompts without mutating events.
- **Frontend state:** Zustand `musicStore` holds API status, models, project browser/save status, edited composition, piano-roll and playback transport state, a single derived analysis report, and ephemeral session slices (analysis/arrangement/development/multi-agent revision passes/MIDI live input/mono transcription/audio recovery/**alignment**). Feature components subscribe to slices; they do not own parallel sources of truth for the same composition. MIDI session fields (device ids, active notes, raw takes) and recovery preview/job temps must never enter project autosave / revision payloads. Multi-agent `pass_candidates` are session audition sources only — Apply remains explicit. Recovery confidence overlays and `audio.alignment.v1` are related assets (or session state), never fields on V2 note events. Source audition uses HTMLAudio + `sourcePlayheadTick` — not a Tone `playbackSource`.
- **Client ↔ server:** `musicApi.js` / `projectApi.js` are the only HTTP clients; components and store actions go through them. Browser Web MIDI / QWERTY performance capture stays frontend-only and commits into validated `composition.v2` via store transactions — it is not the file MIDI import path.

## Key Principles

1. **Module boundaries by convention:** Treat Projects, Import, Analysis, Composition/LLM, and Rendering/Export as modules even while files live in shared `services/` / `components/` folders. Prefer new files named and clustered by module.
2. **Thin HTTP, fat services:** Keep `main.py` / routers focused on transport. Put generation, validation, patching, persistence, and export logic in `services/`.
3. **Canonical composition first:** Any path that mutates or exports music should go through validated `composition.v2` (or explicit legacy/V1 migration), not ad-hoc JSON shapes. Neural audio mix jobs and stem sets are egress-only and must never write into compositions or snapshots. Audio recovery Apply writes V2 notes/metadata only (confidence lives on the related overlay asset after Bind); recovery must not invent playable notes from estimated harmony alone. Alignment (`audio.alignment.v1`) is a non-playable Bind sibling (`kind=alignment_json`) — never on note events, never `composition.v4`.
4. **Application services orchestrate:** Services coordinate LLM calls, validation, and I/O. Push invariants into schema validation and dedicated composition helpers rather than scattering rules across handlers and React components.
5. **Frontend purity where it matters:** Keep event math, validation mirrors, and Tone.js engine code in `utils/` with unit tests; keep UI in `components/`. Mirror alignment map math and `composition.snapshot.v1` fingerprints in FE utils for seek/stale checks.
6. **Infrastructure stays small and shared:** `db/`, env-based `llm_settings`, Docker, and CORS belong to shared infrastructure — not copied per feature.
7. **AI provider boundary:** Orchestrators resolve models via `ai_runtime` (capability + operation), not by constructing LangChain clients inline. Remote and optional local chat must use `llm_chat_client` / OpenAI-compatible HTTP only (`LocalLanguageModel` for `runtime=local_openai_compatible`); never import llama.cpp/vLLM/MusicGen/Demucs weights in FastAPI. FluidSynth WAV export is not an AI runtime. Optional local sidecars live under Compose profiles in `compose.local-ai.yml` / `compose.neural-audio.yml` / `compose.audio-recovery.yml` — default `docker compose up` must not require GPU or multi-GB inference images.
8. **Multi-agent preview vs Apply:** `ai_agents/revision_loop` is a bounded session controller (modes Fast/Balanced/Thorough, stop reasons, last_valid rollback, optional `pass_candidates` for audition). Critic approve never persists; only `multi-agent-apply` CAS commits. Typed pass records stay non-playable; do not invent `composition.v4`.
9. **Dual audition clocks:** Composition audition stays Tone Transport / `playbackSource`. Source audition after recovery Bind is HTMLAudio (+ waveform) driven by alignment — never register a new `playbackSource` for the source WAV; never mutate bound `source_audio` bytes when re-rendering.

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
- ❌ Putting alignment / confidence / stem asset ids on `CompositionV2NoteEvent`, inventing `composition.v4`, or driving source WAV from Tone `playbackSource`
- ❌ Confusing recovery/Demucs `stem_bindings` (role→`track_id`) with durable neural stem-set WAVs under `/neural-audio/stem-sets`
- ❌ Silently swapping generative stem failure to FluidSynth, or labeling per-stem `render` as `direct_stems`
- ❌ Rewriting bound `source_audio` assets when enqueueing neural renders or marking jobs soft-stale
