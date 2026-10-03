# AGENTS.md

> Keep this file factual and update it when the project structure changes significantly. Detailed product docs live under `docs/` and `.ai-factory/DESCRIPTION.md`.

## Project Overview

Full-stack LLM music composer: FastAPI generates/edits canonical `composition.v2` JSON; MIDI/MusicXML import converts uploads into the same V2; deterministic `composition.analysis.v1` analyzes current V2 without mutating it; arrangement/development/harmony previews are session-only until Apply; durable Composer Profiles soft-condition generate without overriding prompt/hard constraints; React/Vite edits on piano roll/JSON, shows OSMD notation, Analysis/Arrange/Develop/Profiles/Performance tabs, and plays note events with Tone.js; AI Jam co-composition (`user_melody` / `user_chords`) rides the co-performance engine with multi-track Commit; recovery Bind adds `audio.alignment.v1` for bar↔source seek and soft-stale neural renders; mix analysis (`mix.analysis.v1`) measures completed neural stem/mix WAVs without mutating audio or V2. A project may store one immutable MP4/MOV as `video.asset.v1` plus a `video.scoring.v1` sync document; the Picture tab keeps the composition cursor on that picture without rewriting note events. An Agents-tab film-score preview returns non-playable `film.score.plan.v1` and writes `composition.v2` only on an explicit Commit. Projects persist in SQLite. A project may also store `adaptive.score.v1` graphs that reference V2 sections, bars, tracks, motifs, or revisions without copying note events. A project may store `performance.plan.v1` conductor plans that realize session `performance.realization.v1` streams without mutating pitch or harmony. A project group may share one `musical.universe.v1`; themes point at motif occurrences, and an explicit reuse writes transformed notes into a member `composition.v2`. Runtime intensity returns `adaptive.layer.intensity.v1` and does not rewrite those note events. A session playback clock returns `adaptive.playback.runtime.v1` without writing the score or the composition. A continuation session fills bars ahead of that clock into `adaptive.runtime.buffer.v1` and does not write the score or the composition. A musical-context session accepts a flat external sample, holds musical state across a hysteresis band, and emits existing playback commands only when that clock is already running. V1 remains migration/parser input.

## Tech Stack

- **Programming language:** Python 3.14+ (backend), JavaScript (frontend)
- **Framework:** FastAPI + Uvicorn; React 18 + Vite
- **Database:** SQLite (`PROJECT_DB_PATH`) with Alembic schema migrations
- **ORM:** None (raw `sqlite3`)

## Project Structure

```
mukit-ai/
├── backend/                 # FastAPI app, tests, Dockerfile
│   ├── app/
│   │   ├── main.py          # Composition / LLM / export routes
│   │   ├── ready.py         # LOG_LEVEL, CORS parse, /ready helpers
│   │   ├── ai_runtime/      # Capability registry, operation routing, typed model adapters
│   │   ├── ai_agents/       # V4 multi-agent layer (registry, spine workflow, progressive realize, typed artifact schemas, revision_loop)
│   │   ├── workflow_eval/   # Offline musical workflow benchmark CLI; not imported by ai_agents/
│   │   ├── plugin_sdk/      # Public plugin import surface (manifest, protocols, context)
│   │   ├── plugin_host/     # PLUGIN_PATHS discovery, import guard, catalog, host dispatch
│   │   ├── ai_runtime_schemas.py  # GET /ai/models DTOs
│   │   ├── execution_node_schemas.py  # execution.node.v1 + task DTOs (non-playable)
│   │   ├── execution_node_settings.py  # AI_EXECUTION_NODES_* flag / token / role
│   │   ├── scheduling_schemas.py  # scheduling.policy/job/candidate/decision.v1
│   │   ├── scheduling_settings.py  # AI_SCHEDULING_* flag / modes / local hints
│   │   ├── agent_artifact_settings.py  # AGENT_ARTIFACT_TEMP_* retention / inspect caps
│   │   ├── video_scoring_schemas.py   # video.asset.v1 + video.scoring.v1
│   │   ├── film_score_schemas.py      # film.score.plan.v1 + preview/commit DTOs
│   │   ├── film_score_adapt_schemas.py  # film.score.adaptation.v1 + preview/commit DTOs
│   │   ├── video_scoring_settings.py  # VIDEO_ASSET_ROOT / VIDEO_ASSET_MAX_UPLOAD_BYTES
│   │   ├── routers/         # Projects + imports + transcription + audio_recovery + neural_audio + mix_analysis + mix_plan + analysis + critique + motifs + harmony + arrangement + development + embeddings + composer_profiles + preferences + ai_scheduling + reference_features + live_performance + video_scoring + film_score + ai_models + ai_agents + plugins + collaboration + adaptive_scores + adaptive_engine + performance_plans HTTP API
│   │   ├── services/        # Domain + orchestration (incl. composition_critique, adaptive_score_*, adaptive_playback*, agent_artifact_workspace, import, audio_transcription, audio_recovery, neural_audio_render, neural_audio_stems, mix_analysis, mix_plan, video_scoring_store, video_container_probe, video_scoring_map, film_score_tempo, film_score_accents, film_score_workflow, film_score_adapt, analysis, motifs, theme, harmony, reharmonization, arrangement, embedding, fake_llm)
│   │   ├── llm_settings.py  # Env → LLM provider settings (bootstraps ai_runtime registry)
│   │   ├── composition_schemas.py  # composition.v1 / composition.v2 contracts (incl. optional motifs)
│   │   ├── analysis_schemas.py     # composition.analysis.v1 DTOs / warning codes
│   │   ├── critique_schemas.py     # CritiqueFindingV1 + critique scopes / errors
│   │   ├── critique_settings.py    # EvaluationEngine thresholds (climax deltas)
│   │   ├── arrangement_schemas.py  # Arrangement preview / catalog DTOs, error & warning codes
│   │   ├── motif_schemas.py        # Motif apply DTOs
│   │   ├── harmony_schemas.py      # Harmony timeline + reharmonize preview DTOs
│   │   ├── import_schemas.py       # Import DTOs, issue/error codes
│   │   ├── import_settings.py      # IMPORT_* limits and conversion policy
│   │   ├── audio_transcription_settings.py  # AUDIO_* limits / engine policy
│   │   ├── audio_transcription_schemas.py   # transcription.preview.v1 DTOs
│   │   ├── audio_upload.py         # Bounded upload reader shared by transcription and recovery
│   │   ├── storage_root_policy.py  # Refuse DATASET_ROOT and PROJECT_DB_PATH as writable roots
│   │   ├── audio_recovery_settings.py       # AUDIO_RECOVERY_* limits / asset root / engines
│   │   ├── audio_recovery_schemas.py        # audio.recovery.preview/result/bind.v1 DTOs
│   │   ├── audio_alignment_schemas.py       # audio.alignment.v1 + roundtrip provenance DTOs
│   │   ├── neural_audio_settings.py         # NEURAL_AUDIO_* render root / engine / quotas
│   │   ├── neural_audio_schemas.py          # neural_audio_render.job.v1 + stem_set/stem.v1 DTOs / fidelity / adapters
│   │   ├── mix_analysis_settings.py         # MIX_ANALYSIS_* report root / caps / fake mode
│   │   ├── mix_analysis_schemas.py          # mix.analysis.v1 DTOs / measurement vs observation vs interpretation
│   │   ├── mix_plan_settings.py             # MIX_PLAN_* revision root / caps / fake mode
│   │   ├── mix_plan_schemas.py              # mix.plan.v1 / intent / master-target DTOs (never PCM)
│   │   ├── embeddings/      # Handcrafted symbolic feature embeddings (cache/index; no torch; never DATASET_ROOT ingest)
│   │   ├── dataset/         # Offline symbolic corpus pipeline (CLI; DATASET_ROOT only)
│   │   ├── tokenizer/       # Composition V2 ↔ tokens codec (CLI; no PROJECT_DB_PATH)
│   │   ├── music_transformer/ # PyTorch decoder-only LM (CLI experiments/train/eval; optional torch; no PROJECT_DB_PATH)
│   │   ├── fixtures/        # Canonical composition JSON (V1 + V2 expressive) + arrangement_instruments.v1.json
│   │   ├── db/              # SQLite connection + Alembic (alembic/versions baseline)
│   │   └── schemas.py       # LLM models + composition re-exports
│   ├── examples/plugins/    # Shipped plugins; loaded only when their directory is on PLUGIN_PATHS
│   └── tests/
├── frontend/                # React + Vite SPA
│   ├── e2e/                 # Playwright V1/V2/import/analysis/motif/arrangement/editor/neural-audio acceptance journeys
│   └── src/
│       ├── api/             # musicApi, projectApi
│       ├── components/      # Workspace, generator, import, analysis, motifs, arrangement, piano-roll/, playback, MidiInputPanel, CoPerformancePanel (AI Jam), AudioInputPanel, AudioRecoveryPanel, VideoScoringPanel (Picture tab), FilmScorePanel (Agents tab), NeuralAudioRenderPanel (incl. Mix Analysis + Mix assist), …
│       ├── store/           # Zustand musicStore (composition transactions + session previews + MIDI/audio sessions)
│       └── utils/           # validation, editor, playback, analysis, motif, harmony, arrangement, midiInput*/midiCapture, midiExpressive/, audioCapture, mixAnalysisUi, mixPlanUi, browserModels/ helpers
│   └── public/browser-models/ # Public BrowserModel manifests/assets only (never private weight trees)
├── clients/                 # Adaptive music engine clients. They do not import backend/app or frontend/src
│   ├── fixtures/            # Shared public session, command, ack, error, and phase JSON
│   ├── python/              # Package mukit-adaptive, import mukit_adaptive
│   └── typescript/          # Private package @mukit/adaptive-music
├── scripts/                 # run_tests.sh, v1/v2/v3_docker_acceptance.sh, dataset_build.sh
├── datasets/                # DATASET_ROOT default (gitignored corpora; .gitkeep only)
├── docs/                    # composition.v2/v1, ai-runtime, browser-webgpu-inference, plugin-sdk, multi-agent, hybrid-generation, daw-interoperability, editor, midi-live-input, audio-transcription, audio-recovery, video-scoring, neural-audio-rendering, mix-analysis, analysis, arrangement, import, datasets, persistence, testing, codebase map
├── .ai-factory/             # DESCRIPTION, ARCHITECTURE, plans, config
├── docker-compose.yml
├── compose.dev.yml
├── compose.local-ai.yml   # Optional --profile local-ai / local-ai-vllm / training
├── compose.neural-audio.yml # Optional --profile neural-audio (MusicGen sidecar)
├── compose.audio-recovery.yml # Optional --profile audio-recovery (Demucs-shaped separation)
├── models/llm/            # Host GGUF/weights bind-mount (gitignored; .gitkeep only)
├── models/neural-audio/   # Host neural audio weights bind-mount (gitignored; .gitkeep only)
├── models/audio-recovery/ # Host recovery/separation weights bind-mount (gitignored; .gitkeep only)
├── .env.example
└── start-servers.sh
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app, LLM generate/edit, MusicXML/MIDI/WAV export |
| `frontend/src/components/MidiInputPanel.jsx` | Live MIDI / QWERTY record panel (sticky transport) |
| `frontend/src/components/CoPerformancePanel.jsx` | AI Jam / co-performance start/stop/cancel/commit + mode/controls + role→track Commit UX |
| `frontend/src/utils/liveJamContracts.js` | Jam modes, role partition, controls clamps, features/belief DTOs |
| `frontend/src/utils/liveJamEnsureTracks.js` | Pure `ensureJamRoleTracks` for multi-track Commit |
| `frontend/src/utils/liveTakeApply.js` | Single-track + multi-track AI Jam Commit into V2 |
| `frontend/src/utils/livePlaybackEngineAccess.js` | Shared PlaybackControls engine handle for live schedule |
| `frontend/src/utils/liveMidiStream.js` | Transport-synced MIDI stream ring (exclusive with midiPhase capture) |
| `backend/app/routers/live_performance.py` | `POST /live/accompaniment/predict` (cold-path fake fill) |
| `frontend/src/components/AudioInputPanel.jsx` | Monophonic mic/file transcription review → Apply |
| `frontend/src/components/AudioRecoveryPanel.jsx` | V4 mixed recovery upload/review → Apply→Bind + HTMLAudio source |
| `backend/app/routers/audio_recovery.py` | `POST/GET/DELETE /audio-recovery/jobs`, bind, assets |
| `backend/app/services/audio_recovery/` | Separation, scaffolding, transcription, pipeline |
| `backend/app/services/audio_recovery_store.py` | Job/asset FS + SQLite metadata; project-delete GC |
| `backend/app/routers/video_scoring.py` | Project picture upload, media, scoring CAS, and time map |
| `backend/app/routers/film_score.py` | Picture film-score preview and explicit commit; adaptation preview and commit; the only video reader on these paths |
| `backend/app/services/film_score_tempo.py` | Deterministic section, tempo, and dialogue compiler |
| `backend/app/services/film_score_workflow.py` | Existing-agent sequence and candidate; does not write the score |
| `backend/app/services/film_score_accents.py` | Accent repair for aligned sync cues |
| `backend/app/services/film_score_adapt.py` | Local picture-edit repair; does not import accents or video modules |
| `backend/app/services/video_scoring_store.py` | One immutable video file per project; replace-then-delete; project-delete GC |
| `backend/app/services/video_container_probe.py` | Read-only ISO-BMFF duration, frame rate, audio, and resolution |
| `backend/app/services/video_scoring_map.py` | Pure video time ↔ tick map and SMPTE timecode |
| `backend/app/services/video_spotting.py` | Pure cue landing check against note attacks |
| `backend/app/services/llm_video_spotting.py` | Explicit spotting suggestion preview; does not write cues |
| `frontend/src/components/VideoScoringPanel.jsx` | Picture tab: player, timecode, bar ruler, markers, hit points |
| `frontend/src/components/FilmScorePanel.jsx` | Agents tab: inspect `film.score.plan.v1` and commit |
| `frontend/src/components/FilmScoreAdaptPanel.jsx` | Agents tab: inspect `film.score.adaptation.v1` and commit |
| `frontend/src/api/filmScoreApi.js` | Film-score preview and commit HTTP client |
| `frontend/src/api/filmScoreAdaptApi.js` | Film-score adaptation preview and commit HTTP client |
| `frontend/src/api/videoScoringApi.js` | Picture asset and scoring HTTP client |
| `frontend/src/utils/videoScoringMap.js` | Frontend twin of the video ↔ tick map |
| `frontend/src/components/NeuralAudioRenderPanel.jsx` | Render with AI jobs / download (egress only) |
| `frontend/src/utils/browserModels/browserModelHost.js` | SPA BrowserModelHost: probe, manifest, ship-1 embed twin, HTTP fallback |
| `frontend/src/utils/browserModels/symbolicFeaturesV1.js` | JS twin of `symbolic.features.v1` (browser CPU; golden parity) |
| `frontend/src/utils/browserModels/webgpu/batchedCosine.js` | Optional WebGPU batched cosine WGSL for in-request batches |
| `frontend/public/browser-models/` | Public `browser.model.manifest.v1` assets only (no private weights) |
| `backend/app/ai_runtime/runtimes/browser_model.py` | Discovery-only `runtime=browser_model` registry descriptors |
| `docs/browser-webgpu-inference.md` | Benchmarks, asset policy, fallback, compatibility |
| `frontend/src/utils/midiInputAccess.js` | Lazy Web MIDI access + device registry |
| `frontend/src/utils/midiPerformanceCapture.js` | Session take buffer (expressive events → raw ticks + optional performance curves) |
| `frontend/src/utils/midiTakeApply.js` | Timeline extend + batch commit into V2 (+ optional `note_performances`) |
| `frontend/src/utils/midiExpressive/` | Capability probe, MIDI1/MPE/UMP adapters, velocity degrade, prune helpers |
| `frontend/src/utils/midiMetronome.js` | Ephemeral count-in / metronome clicks |
| `backend/app/dataset/cli.py` | Offline dataset CLI (`python -m app.dataset.cli`) |
| `backend/app/tokenizer/cli.py` | Offline tokenizer CLI (`python -m app.tokenizer.cli`) |
| `backend/app/embeddings/cli.py` | Offline embedding eval CLI (`python -m app.embeddings.cli`) |
| `backend/app/music_transformer/cli.py` | Offline Music Transformer train/generate/eval/listen/compare CLI (`python -m app.music_transformer.cli`) |
| `backend/app/workflow_eval/cli.py` | Offline V3/V4 musical workflow benchmark (`python -m app.workflow_eval.cli`) |
| `backend/app/workflow_eval_settings.py` | `EVAL_BENCHMARK_ROOT`; refuses `DATASET_ROOT` and `PROJECT_DB_PATH` |
| `backend/app/storage_root_policy.py` | Shared storage-root refusal for recovery, neural, mix, video assets, and workflow eval |
| `backend/app/audio_upload.py` | Chunked upload bound; recovery maps overflow to HTTP 413 |
| `backend/app/db/backup.py` | Offline `python -m app.db.backup` copy of `PROJECT_DB_PATH` |
| `scripts/v4_docker_acceptance.sh` | Opt-in fake autonomous run, backend restart, reopen |
| `backend/app/routers/embeddings.py` | `POST /embeddings/compute`, `/similarity`, `/related-motifs`, `/reference/resolve` |
| `backend/app/routers/composer_profiles.py` | Composer profile CRUD / derive / promote / preview / compare / export/import |
| `backend/app/routers/preferences.py` | Preference settings, inspect, reset, choice record, and rank. Does not write `composition.v2` |
| `backend/app/preference_settings.py` | `PREFERENCE_LEARNING_ENABLED` parser. Default off |
| `backend/app/execution_node_schemas.py` | `execution.node.v1` / task DTOs, address allowlist, HTTP error map |
| `backend/app/execution_node_settings.py` | `AI_EXECUTION_NODES_*` flag, token, role, TTL, fake |
| `backend/app/services/execution_node_auth.py` | Bearer compare for every peer when enabled |
| `backend/app/services/execution_node_store.py` | SQLite `execution_nodes` CAS; no Composition |
| `backend/app/services/execution_node_service.py` | Controller register/heartbeat/list/cancel |
| `backend/app/routers/execution_nodes.py` | `POST/GET/DELETE /ai/execution-nodes` |
| `backend/app/routers/execution_worker.py` | Worker `/execution/v1/*` typed inference |
| `backend/app/ai_runtime/runtimes/execution_node.py` | `runtime=execution_node` LanguageModel adapter |
| `backend/app/ai_runtime/invoke_text.py` | `ainvoke_text_for_resolved` generate/edit seam (bounded reschedule for schedule / execution_node) |
| `backend/app/scheduling_schemas.py` | `scheduling.policy/job/candidate/decision/attempt.v1` DTOs (non-playable) |
| `backend/app/scheduling_settings.py` | `AI_SCHEDULING_*` flag, modes, local resource hints. Default off |
| `backend/app/services/ai_job_scheduler.py` | Pure `schedule_ai_job` (no FastAPI/SQLite/LLM) |
| `backend/app/services/scheduling_candidates.py` | Registry + ExecutionNode → `SchedulingCandidateV1` |
| `backend/app/services/scheduling_policy_store.py` | SQLite singleton policy CAS. `ai_agents/` does not import it |
| `backend/app/services/ai_job_reschedule.py` | Bounded reschedule without trust escalation |
| `backend/app/routers/ai_scheduling.py` | `GET/PUT /ai/scheduling/policy`, `POST /ai/scheduling/preview` |
| `backend/app/services/preference_store.py` | Settings, pending ballots, choices, and the linear ranker. `ai_agents/` does not import it |
| `frontend/src/components/PreferenceLearningPanel.jsx` | Profiles-tab inspect, toggles, and reset. Opening the tab does not record a choice |
| `backend/app/routers/reference_features.py` | `POST /reference-features/analyze` (dimension-masked reference reports) |
| `backend/app/reference_conditioning_schemas.py` | `reference.conditioning.policy.v1` DTOs / partition validators |
| `backend/app/services/reference_conditioning_policy.py` | Preserve/borrow/regenerate soft assembly + policy digest |
| `backend/app/composer_profile_schemas.py` | `composer.profile.v1` + export envelope DTOs |
| `backend/app/services/composer_profile_store.py` | SQLite profile persistence + CAS |
| `backend/app/services/composer_profile_derive.py` | Multi-project abstract preference aggregate |
| `backend/app/services/composer_profile_resolve.py` | Explicit-over-derived + strength soft fragment |
| `backend/app/services/composer_profile_merge.py` | Additive generate merge + provenance (never mutates prompt) |
| `backend/app/personal_composer_schemas.py` | Personal training manifest, snapshot index, adapter config, job, and eval documents |
| `backend/app/personal_composer_settings.py` | `PERSONAL_COMPOSER_ROOT`, fake engine, project and step caps |
| `backend/app/routers/personal_composer.py` | Train, list, stop, resume, evaluate, and delete personal adapters |
| `backend/app/services/personal_composer_service.py` | Rights, snapshot, and job orchestration. Listing does not start training |
| `frontend/src/components/PersonalComposerPanel.jsx` | Profiles-tab personal adapter form. Opening the tab lists jobs |
| `scripts/run_tests.sh` | Local quality gate: ESLint + backend pytest + frontend unit tests |
| `backend/app/composition_plan_schemas.py` | Strict `composition.plan.v1` DTOs (non-playable) |
| `backend/app/services/composition_plan_constraints.py` | Plan ↔ hard `GenerationConstraints` conformance + digest |
| `backend/app/services/symbolic_composition_generate.py` | Symbolic composer adapter (MT + fake tiny) for hybrid pipelines |
| `backend/app/services/fake_symbolic_composer.py` | Deterministic CI symbolic note engine (`fake:symbolic-tiny`) |
| `backend/app/analysis_schemas.py` | `composition.analysis.v1` DTOs, scopes, warning codes |
| `backend/app/critique_schemas.py` | Critique findings, scopes, evaluate error codes |
| `backend/app/services/composition_critique.py` | Music Evaluation Engine orchestrator |
| `backend/app/ai_agents/revision_loop.py` | Bounded critique → revise → re-critique preview controller |
| `backend/app/ai_agents/revision_loop_schemas.py` | Revision modes, stop reasons, pass records, usage DTOs |
| `backend/app/ai_agents/revision_stop_policy.py` | Pure multi-condition stop evaluation |
| `backend/app/ai_agents/revision_plan_builder.py` | Findings → targeted `agent.revision_plan.v1` |
| `backend/app/revision_loop_settings.py` | `REVISION_LOOP_*` thresholds / budgets |
| `backend/app/operation_trace.py` | ContextVar operation spans, redacted span logs, run cancel set |
| `backend/app/operation_trace_schemas.py` | `operation.span.v1` and `operation.summary.v1` |
| `backend/app/operation_budget_settings.py` | `OPERATION_*` and `NEURAL_AUDIO_MAX_ATTEMPTS` ceilings |
| `backend/app/services/composition_revision_preserve.py` | Preserve-outside-targets event fingerprint helper |
| `backend/app/routers/critique.py` | `POST /critique/evaluate` (session-only) |
| `backend/app/adaptive_score_schemas.py` | `adaptive.score.v1` graph DTOs plus `adaptive.layer.intensity.v1` (references only; no note events) |
| `backend/app/routers/adaptive_scores.py` | Project adaptive-score CRUD, commands, validate, transitions, layer intensity, playback, and musical-context start/status/samples/stop |
| `backend/app/routers/performance_plans.py` | Project performance-plan CRUD, preset catalog, realize, compare (`performance.plan.v1` / `performance.realization.v1`) |
| `backend/app/services/performance_conductor.py` | Pure deterministic conductor; tick_delta rubato; no FastAPI/SQLite/LLM |
| `frontend/src/components/PerformancePanel.jsx` | Performance tab: catalog clone, plan select, mechanical/performed audition |
| `frontend/src/utils/performanceConductor/scheduleApply.js` | Apply realization deltas for Tone schedule without mutating working V2 |
| `backend/app/routers/musical_universe.py` | Universe membership, commands, validate, and explicit theme reuse. Reuse is the only route that writes destination notes |
| `backend/app/routers/musical_dependency.py` | Theme impact, dependency graph, and edge-only fingerprint refresh. These routes do not write notes |
| `backend/app/routers/adaptive_engine.py` | External `/adaptive/*` session for one stored score: HTTP commands plus events and status sockets. Does not write the score |
| `clients/python/src/mukit_adaptive/client.py` | Synchronous client for that public surface. Does not import `backend/app` or `frontend/src` |
| `clients/python/src/mukit_adaptive/demo.py` | `python -m mukit_adaptive.demo` terminal phase loop |
| `clients/typescript/src/client.ts` | Async client with the same method names |
| `clients/typescript/src/demo.ts` | `node dist/demo.js` after `npm run build` in `clients/typescript` |
| `backend/app/adaptive_engine_schemas.py` | `adaptive.engine.session.v1`, command result, ack, and error documents |
| `backend/app/services/adaptive_engine_service.py` | In-memory engine session. Starts or binds playback and context. Does not write the score |
| `backend/app/services/adaptive_engine_auth.py` | Bearer-or-loopback compare. No FastAPI import. Never logs the token |
| `backend/app/services/adaptive_engine_backpressure.py` | Per-session token bucket and one context slot |
| `backend/app/adaptive_playback_schemas.py` | `adaptive.playback.runtime.v1` snapshot and commands; not stored on the score |
| `backend/app/services/adaptive_playback.py` | Pure session clock; calls the scheduler and layer map; no SQLite, FastAPI, or LLM |
| `backend/app/adaptive_runtime_continuation_schemas.py` | `adaptive.runtime.continuation.v1`, context memory, and the session buffer; no events on the snapshot |
| `backend/app/services/adaptive_runtime_continuation.py` | Pure window planner, memory step, and applicability; no playback, SQLite, or model client |
| `backend/app/services/adaptive_runtime_continuation_service.py` | Session arm, deadline discard, and fallback publish; reads playback and does not command it |
| `backend/app/services/adaptive_playback_service.py` | Load score and timeline projections, then step the clock; no score write |
| `backend/app/services/adaptive_playback_runtime.py` | In-memory one-session playback registry; restart drops it |
| `backend/app/adaptive_musical_context_schemas.py` | `adaptive.context.external.v1`, `adaptive.context.mapping.v1`, and `adaptive.musical_context.v1` |
| `backend/app/services/adaptive_musical_context.py` | Pure bind, EMA, and Schmitt dwell; no playback, SQLite, or LLM |
| `backend/app/services/adaptive_musical_context_service.py` | In-memory context session; emits playback commands only when a clock is running |
| `backend/app/services/adaptive_score_commands.py` | Pure graph edits, including `create_layer`, `edit_layer`, and `delete_layer`; no SQLite |
| `backend/app/services/adaptive_score_layers.py` | Pure runtime-intensity map; no SQLite, FastAPI, or LLM |
| `backend/app/services/adaptive_score_layer_service.py` | Load score and span projections, then return `adaptive.layer.intensity.v1`; no score write |
| `backend/app/services/adaptive_score_store.py` | SQLite `adaptive_scores` CAS; does not load Composition |
| `backend/app/services/adaptive_score_service.py` | Bind, then store writes for PUT and commands |
| `backend/app/services/adaptive_score_transition_service.py` | Load score and timeline, schedule, then hold the pending slot; no score write |
| `backend/app/services/adaptive_score_transitions.py` | Pure grid resolver; no SQLite, FastAPI, or LLM |
| `backend/app/services/adaptive_score_transition_pending.py` | In-memory one-slot pending registry |
| `frontend/src/components/AdaptiveScorePanel.jsx` | Adaptive tab: state cards, transitions, authoring selection, playback, findings |
| `frontend/src/components/MusicalUniversePanel.jsx` | Universe tab: entities, Theme A, variants, usages, and explicit reuse. Opening the tab loads the universe and does not write notes |
| `backend/app/motif_schemas.py` | Motif apply request/response DTOs |
| `backend/app/routers/analysis.py` | `POST /analysis/composition` |
| `backend/app/routers/motifs.py` | `POST /motifs/apply` |
| `backend/app/routers/harmony.py` | `POST /harmony/reharmonize/preview` |
| `backend/app/harmony_schemas.py` | Harmony timeline + reharmonize preview DTOs |
| `backend/app/arrangement_schemas.py` | Arrangement preview request/candidate DTOs, error/warning codes |
| `backend/app/routers/arrangement.py` | `GET /composition/arrangement/instruments`, `POST /composition/arrangement/preview` |
| `backend/app/services/instrument_catalog.py` | Versioned curated GM arrangement catalog + fingerprints |
| `backend/app/services/composition_arrangement_context.py` | Arrangement source context + note refs |
| `backend/app/services/composition_arrangement_patch.py` | Deterministic draft realization |
| `backend/app/services/composition_arrangement_validation.py` | Preservation / range / duplicate postconditions |
| `backend/app/services/llm_composition_arrangement.py` | Multi-candidate arrangement orchestration |
| `backend/app/services/composition_reharmonization.py` | Deterministic reharmonize preview engine |
| `backend/app/services/composition_analysis.py` | Analysis orchestrator + bounded LLM advisory projection |
| `backend/app/services/composition_motif_editor.py` | Canonical motif apply + destination replacement |
| `backend/app/services/composition_theme.py` | Structured theme plan + generation recurrence |
| `backend/app/routers/imports.py` | `POST /imports/midi` and `/imports/musicxml` |
| `backend/app/routers/transcription.py` | `POST /transcription/audio` → `transcription.preview.v1` only |
| `backend/app/routers/neural_audio.py` | `POST/GET/DELETE /neural-audio/renders` (+ `/audio` download); `POST/GET/DELETE /neural-audio/stem-sets` (+ stem rerender / `/stems/{id}/audio`) |
| `backend/app/services/neural_audio_render.py` | Mix job orchestration; read-only composition; never mutates V2 |
| `backend/app/services/neural_audio_stems.py` | Stem-set enqueue/run/selective rerender; never mutates V2 |
| `backend/app/routers/mix_analysis.py` | `POST /mix-analysis/analyze`, `GET/DELETE /mix-analysis/reports` |
| `backend/app/services/mix_analysis/` | DSP measurements, observations, optional interpret, active-head pipeline |
| `backend/app/services/mix_analysis_store.py` | Durable mix.analysis.v1 JSON + SQLite + project-delete GC |
| `backend/app/routers/mix_plan.py` | `POST /mix-plan/preview`, apply, reject, undo; revision audio download |
| `backend/app/services/mix_plan/` | Intent compiler, observation ops, read-only bounce |
| `backend/app/services/mix_plan_store.py` | Preview files + `mix_plan_revisions` + project-delete GC |
| `frontend/src/components/MixAssistPanel.jsx` | Mix assist preview / compare / apply / reject / undo |
| `backend/app/services/composition_import.py` | Shared source → V2 canonicalization |
| `backend/app/services/composition_midi_import.py` | Deterministic MIDI parse |
| `backend/app/services/composition_musicxml_import.py` | Hardened MusicXML/MXL parse |
| `backend/app/services/composition_migration.py` | V1→V2 migration and fidelity gate |
| `backend/app/services/composition_projection.py` | Shared export projection report + issue codes |
| `backend/app/services/fake_llm.py` | Deterministic `LLM_FAKE_MODE` generate/edit/arrangement (incl. V2 expressive fixture) |
| `backend/app/ready.py` | Logging/CORS helpers and readiness report |
| `backend/app/ai_runtime/` | Capability registry, operation routing, typed adapters |
| `backend/app/ai_agents/` | V4 multi-agent registry, spine workflow, progressive realize, revision_loop |
| `backend/app/local_llm_settings.py` | Optional `LOCAL_*` sidecar settings (memory-safe defaults) |
| `backend/app/ai_runtime/local_health.py` | Bounded local sidecar health probe |
| `backend/app/ai_runtime/runtimes/local_language.py` | `LocalLanguageModel` (OpenAI-compatible HTTP only) |
| `backend/app/plugin_sdk/` | Public plugin SDK (`plugin.manifest.v1`, protocols, redacting logger) |
| `backend/app/plugin_host/` | Discover manifests, guard imports, catalog, and invoke enabled plugins. Does not read SQLite |
| `backend/app/services/plugin_lifecycle.py` | Install, enable, disable, config, and startup reconcile. Only writer of desired state |
| `backend/app/services/plugin_installation_store.py` | SQLite `plugin_installations` in `PROJECT_DB_PATH` |
| `backend/app/routers/plugins.py` | `GET /plugins`, install/enable/disable/config, `POST /plugins/reload` |
| `frontend/src/components/PluginsPanel.jsx` | Plugins tab: lifecycle, health, resources, and schema-shaped config |
| `backend/examples/plugins/` | `deterministic_analyzer` and `sample_symbolic_generator` (discovered only when on `PLUGIN_PATHS`; not enabled until install) |
| `docs/plugin-sdk.md` | Plugin lifecycle, resources, isolation refusal, protocols, env, and routes |
| `backend/app/routers/ai_models.py` | `GET /ai/models` (+ `/{id}`) discovery |
| `backend/app/routers/ai_agents.py` | `GET /ai/agents`, `POST /ai/agents/{id}/run`, workflow preview (optional `persist_workspace_artifacts`), autonomous runs |
| `backend/app/services/autonomous_composer.py` | Schedules the brief’s stages, commits score revisions, and owns the autonomous run store |
| `backend/app/services/autonomous_instruction.py` | Closed arrangement-instruction interpreter (`thin_strings` or `unparsed`); never logs the text |
| `backend/app/services/autonomous_progress.py` | Musical progress board for a run’s stages |
| `frontend/src/components/AutonomousComposerPanel.jsx` | Agents-tab brief editor, plan review, and co-producer controls |
| `frontend/src/utils/autonomousControl.js` | Brief fingerprint, start gate, musical board, client instruction check |
| `backend/app/services/agent_artifact_workspace.py` | Immutable typed artifact INSERT/promote/GC; never imported by `ai_agents/` |
| `backend/app/services/artifact_role_map.py` | Validate `artifact_role_map` for `multi-agent-apply` |
| `backend/app/routers/projects.py` | Project CRUD + autosave + revision/branch history APIs |
| `backend/app/collaboration_settings.py` | `COLLABORATION_ENABLED` truthy set; off skips membership and activity |
| `backend/app/services/collaboration_permissions.py` | Pure role matrix and revision origin partition |
| `backend/app/services/collaboration_access.py` | Membership checks; flag off returns before any lookup |
| `backend/app/services/collaboration_store.py` | Actors and one owner membership per project |
| `backend/app/services/collaboration_comments.py` | Anchored comments beside the score |
| `backend/app/services/collaboration_reviews.py` | Open, approve, and reject an immutable revision |
| `backend/app/services/collaboration_activity.py` | Append-only activity; no comment bodies |
| `backend/app/routers/collaboration.py` | Status, actors, members, comments, reviews, activity |
| `backend/app/services/project_history.py` | Revision list/detail, durable commit/restore, branch checkout/apply-as-branch |
| `backend/app/services/project_history_store.py` | SQLite CAS history graph + compressed composition snapshots |
| `frontend/src/utils/compositionVersionComparison.js` | Deterministic working/revision composition compare |
| `frontend/src/utils/playbackSource.js` | Mutual-exclusive working / preview / version audition playback source |
| `frontend/src/utils/playbackMixerControls.js` | Ephemeral per-scope mixer control normalize/merge helpers |
| `frontend/src/utils/tonePlaybackEngine.js` | Tone.js transport, V2 schedule, mixer graph, meters |
| `frontend/src/utils/playbackInstrumentAdapters.js` | Synth + Tone.Sampler adapters with fallback reason codes |
| `frontend/src/components/TrackPlaybackControls.jsx` | Collapsible mixer rows (trim/pan/mute/solo/send/levels) |
| `docs/browser-playback.md` | Browser projection boundary vs FluidSynth export |
| `frontend/src/components/ProjectVersionsPanel.jsx` | Versions tab: branches, compare, audition, restore |
| `backend/app/services/project_store.py` | SQLite project CRUD |
| `backend/app/services/project_history_store.py` | Snapshots, revisions, branches, CAS draft/commit/restore/checkout |
| `backend/app/services/project_history.py` | Domain orchestration for revision/branch commands |
| `backend/app/project_history_schemas.py` | Revision/branch DTOs, operation enum, conflict body |
| `backend/app/services/composition_snapshot_encoding.py` | `composition.snapshot.v1` zlib content-addressed encoding |
| `backend/app/services/composition_change_summary.py` | Null-aware affected bars/tracks + declared-scope enforcement |
| `backend/app/services/persistence_secret_guard.py` | Forbidden secret fields/values before persistence |
| `backend/run.py` / `uvicorn app.main:app` | Backend process entry |
| `frontend/src/main.jsx` | Frontend bootstrap |
| `frontend/src/store/musicStore.js` | Shared UI/application state (incl. import + analysis + harmony + development + arrangement + editor selection/clipboard/loop) |
| `frontend/src/components/PianoRollEditor.jsx` | V2 piano-roll shell (viewport, shortcuts, cursor, selection) |
| `frontend/src/components/piano-roll/` | Note/overlay layers, drag hook, selection inspector, track controls |
| `frontend/src/utils/compositionEditorSelection.js` | Note refs, box/range selection geometry |
| `frontend/src/utils/compositionEditorOperations.js` | Immutable bulk note ops + clipboard |
| `frontend/src/utils/editorNavigation.js` | Edit cursor, bar/section/zoom helpers |
| `frontend/src/utils/playbackLoop.js` | Selection/bar loop range helpers |
| `frontend/src/components/ImportControls.jsx` | Import / replace UX |
| `frontend/src/components/CompositionAnalysisPanel.jsx` | Analysis tab UI |
| `frontend/src/components/CompositionDevelopmentPanel.jsx` | Develop tab candidate workflow |
| `frontend/src/components/ArrangementPanel.jsx` | Arrange tab catalog / preview / apply UI |
| `frontend/src/utils/compositionArrangementCandidates.js` | Arrangement request normalize + Apply verification |
| `frontend/src/components/MotifPanel.jsx` | Motifs tab authoring / apply UI |
| `frontend/src/components/HarmonyTimelinePanel.jsx` | Harmony tab timeline + reharmonize preview/apply |
| `backend/app/routers/composition_development.py` | `POST /composition/development/preview` |
| `backend/app/composition_development_schemas.py` | Development preview request/candidate DTOs |
| `backend/app/services/llm_composition_development.py` | Multi-candidate development orchestration |
| `backend/app/services/composition_development_patch.py` | Append/variation draft realization |
| `docker-compose.yml` | Production-local backend + nginx frontend |
| `compose.dev.yml` | Optional hot-reload + catalog bind-mount example |
| `.env.example` | Env template for LLM/import/arrangement settings |
| `.ai-factory/config.yaml` | AI Factory language/paths/git settings |

## Documentation

| Document | Path | Description |
|----------|------|-------------|
| README | `README.md` | Install, features, env vars, run instructions |
| AI Runtime | `docs/ai-runtime.md` | Capability registry, operation routing, `/ai/models`, fallback, provenance |
| AI job scheduling | `docs/ai-job-scheduling.md` | Capability-aware placement (`resolution_path=schedule`); trust boundaries; reschedule; no SPA |
| Plugin SDK | `docs/plugin-sdk.md` | In-process plugins via `PLUGIN_PATHS`; `runtime=plugin` on `/ai/models`; no marketplace |
| Multi-agent (V4) | `docs/multi-agent.md` | Specialized agents above runtime; workflow preview; Apply CAS |
| Autonomous composer | `docs/autonomous-composer.md` | One brief to a durable multi-section project; Guided checkpoints and pause stay on the same run; spine preview stays preview-only |
| Operation traces | `docs/observability.md` | Run ids, redacted spans, budgets, cancel, crash containment |
| Optional local AI | `docs/local-ai.md` | AMD/ROCm Compose profiles, llama.cpp/vLLM, memory-safe defaults, troubleshooting |
| Composition V2 | `docs/composition-v2.md` | Operational canonical contract and export fidelity |
| Composition Editor | `docs/composition-editor.md` | Piano-roll selection, clipboard, transforms, cursor/loop |
| Browser playback | `docs/browser-playback.md` | Tone.js instruments/mixer/transport; ephemeral session state |
| Performance conductor | `docs/performance-conductor.md` | Durable `performance.plan.v1` + session realizations; same notes, different performances |
| DAW interoperability | `docs/daw-interoperability.md` | SMF Type 1 / MusicXML handoff, Ableton/Reaper recipes, drag/download, V3 acceptance |
| MIDI live input | `docs/midi-live-input.md` | Web MIDI / QWERTY performance capture into V2 |
| Co-performance | `docs/co-performance.md` | Live stream, horizon accompaniment, degradation, predict |
| AI Jam | `docs/ai-jam.md` | Jam modes, belief/hysteresis, multi-track Commit, fallback |
| Audio transcription | `docs/audio-transcription.md` | Monophonic mic/file → preview → Apply into V2 |
| Audio recovery | `docs/audio-recovery.md` | V4 mixed audio → optional stems/scaffolding → Apply→Bind overlay |
| Audio↔symbolic alignment | `docs/audio-symbolic-alignment.md` | Bind-time map, bar↔source seek, waveform sync, soft-stale neural renders |
| Video scoring | `docs/video-scoring.md` | One immutable MP4/MOV (`video.asset.v1`) and `video.scoring.v1` sync. Picture play and Tone share one cursor leader. Spotting cues stay on `hit_points`. `ai_agents/` does not import `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, `video_scoring_map`, `video_spotting`, or `llm_video_spotting` |
| Film scoring | `docs/film-scoring.md` | Explicit `film.score.plan.v1` preview from stored cues, then commit of `composition.v2`. `ai_agents/` does not import film-score or video modules |
| Film score adaptation | `docs/film-score-adaptation.md` | Explicit `film.score.adaptation.v1` for one declared picture edit. Commit writes `composition.v2` only. `ai_agents/` does not import `film_score_adapt` |
| Neural audio rendering | `docs/neural-audio-rendering.md` | Optional generative/neural instrument mix + stem-set egress; licenses; Compose profile |
| Mix analysis | `docs/mix-analysis.md` | DSP measurements + observations over neural stem/mix WAVs; soft-stale; never mutates audio/V2 |
| AI-assisted mixing | `docs/ai-assisted-mixing.md` | Non-destructive mix plans, preview/apply/undo, new mix revisions; stems unchanged |
| Composition Development | `docs/composition-development.md` | Continue / add section / vary; multi-candidate preview |
| Composition Arrangement | `docs/composition-arrangement.md` | Instrumentation / texture redistribution; catalog + preview |
| Composition Critique | `docs/composition-critique.md` | Evaluation engine, strata, climax AC, `/critique/evaluate` |
| Adaptive score | `docs/adaptive-score.md` | `adaptive.score.v1` state graph over V2 references; `adaptive.layer.intensity.v1` selects layers; `adaptive.playback.runtime.v1` is a session clock. `ai_agents/` does not import `adaptive_playback.py`, `adaptive_playback_service.py`, `adaptive_playback_runtime.py`, `adaptive_musical_context_schemas`, `adaptive_musical_context_settings`, `adaptive_musical_context`, `adaptive_musical_context_service`, `adaptive_musical_context_runtime`, `adaptive_runtime_continuation_schemas`, `adaptive_runtime_continuation_settings`, `adaptive_runtime_continuation`, `adaptive_runtime_continuation_fallback`, `adaptive_runtime_continuation_service`, or `adaptive_runtime_continuation_runtime` |
| Musical universe | `docs/musical-universe.md` | `musical.universe.v1` shared by member projects. Themes reference motif occurrences. Explicit reuse writes destination `composition.v2` notes. `ai_agents/` does not import `musical_universe_store` |
| Derived material graph | `docs/derived-material-graph.md` | `musical.dependency.edge.v1` rows for motifs, themes, arrangements, variations, renders, transcriptions, and reference conditioning. Impact lists stale dependents and does not rewrite them. `ai_agents/` does not import `musical_dependency_store` |
| Adaptive musical context | `docs/adaptive-musical-context.md` | Flat external samples mapped onto a closed context; hysteresis emits existing playback commands |
| Adaptive runtime continuation | `docs/adaptive-runtime-continuation.md` | Session buffer ahead of playback; the clock does not await the model |
| Adaptive music engine | `docs/adaptive-music-engine.md` | External `/adaptive/*` session, bearer-or-loopback auth, and context backpressure. `ai_agents/` does not import the engine modules |
| Adaptive music client | `docs/adaptive-music-client.md` | Python `mukit_adaptive` and TypeScript `@mukit/adaptive-music`. Client packages must not import `app`. `app` must not import `mukit_adaptive` |
| Composition Analysis | `docs/composition-analysis.md` | Deterministic sidecar, scopes, warnings, Analysis tab |
| MIDI / MusicXML import | `docs/import.md` | Ingestion mappings, limits, issue codes |
| Symbolic datasets | `docs/datasets.md` | Offline `DATASET_ROOT` corpus pipeline, provenance, CLI |
| Symbolic tokenizer | `docs/tokenizer.md` | Composition V2 ↔ token ids, quantization, CLI, versioning |
| Symbolic embeddings | `docs/embeddings.md` | Handcrafted musical feature embeddings, similarity, reference conditioning |
| Browser WebGPU inference | `docs/browser-webgpu-inference.md` | BrowserModelHost, ship-1 embed twin, WebGPU cosine, public asset policy |
| Composer profiles | `docs/composer-profiles.md` | Durable preference profiles, soft generate conditioning, derive/promote |
| Explicit preference learning | `docs/preference-learning.md` | Opt-in ballots over development and arrangement candidates. A choice does not update `composer.profile.v1`. `ai_agents/` does not import `preference_store` |
| Personal symbolic composer | `docs/personal-symbolic-composer.md` | Opt-in LoRA adapter on selected owned scores; optional hybrid composer |
| Reference features | `docs/reference-features.md` | Selective dimension masks + preserve/borrow/regenerate policy for generate/develop/edit |
| Symbolic Music Transformer | `docs/music-transformer.md` | PyTorch decoder-only LM, train/generate CLI, checkpoint card, optional API |
| Musical workflow evaluation | `docs/workflow-evaluation.md` | Versioned V3/V4 brief suite, hard-metric regression, blinded listening |
| V4 studio operations | `docs/v4-studio-operations.md` | Fake-mode studio scenarios, migration ladder, backup CLI, opt-in Docker gate |
| Hybrid generation | `docs/hybrid-generation.md` | LLM plan + symbolic notes pipelines, seeds, multi-stage provenance |
| Composition V1 | `docs/composition-v1.md` | V1 compatibility, staged generation, region editing |
| Project persistence | `docs/project-persistence.md` | SQLite projects and migrations |
| Collaboration | `docs/collaboration.md` | Optional local actors, roles, comments, reviews, activity |
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
- Treat `composition.v2` `tracks[].events[]` as the only playable source; do not invent notes from `harmony`. V1 is migration input only. Raw MIDI/MusicXML import sets `harmony: []` and does not run musical analysis. `composition.analysis.v1` is a derived sidecar only — never persist it as composition data. Arrangement catalog IDs/ranges are not V2 fields; only applied V2 is persisted. Typed agent plans live in `agent.artifact.v1` envelopes / `agent_artifact_workspace` — never as alternate playable scores (`composition.v4` unsupported); `ai_agents/` must not import the workspace or SQLite. Symbolic embeddings measure affinity from note material — never artist≡style ids; never auto-export projects into `DATASET_ROOT`. Composer profiles (`composer.profile.v1`) are durable soft prefs only — never store event arrays / analysis reports / embedding vectors; never override prompt/hard `GenerationConstraints`; never write to `DATASET_ROOT`. An explicit preference choice is `preference.choice.v1` and does not update `composer.profile.v1`; `ai_agents/` does not import `preference_store`. A personal composer adapter is a user-started LoRA job under `PERSONAL_COMPOSER_ROOT`, not a profile and not a copy in `DATASET_ROOT`; `ai_agents/` must not import `personal_composer_store`. Reference features (`reference.features.v1`) are derived dimension-masked sidecars — never mutate the reference Composition; never copy melodies into prompts; never write `DATASET_ROOT`.
- Prefer extending `routers/` + `services/` over growing unrelated logic in `main.py`
- Never log API keys, full prompts, raw MusicXML/MIDI/WAV payloads, uploaded import source bytes, full analysis reports, event arrays, or arrangement catalog override contents
