# Project Roadmap

> Full-stack LLM music composer: generate and edit canonical playable `composition.v2`, migrate V1 input, persist locally, and export/play with fidelity.

## Milestones

- [x] **Canonical composition.v1** — Playable JSON contract with validation, normalization, and docs
- [x] **Multi-stage LLM composer** — Form → harmony → parts → assemble/validate/repair via LangGraph
- [x] **Generation hard-constraint enforcement** — Immutable request constraints (key/meter/tempo/instruments) through staged generate/validate/repair; requested-instrument assignment invariants
- [x] **Deterministic export** — MusicXML, MIDI, and FluidSynth WAV from the same note events
- [x] **Exact playback & notation** — Tone.js multi-track playback and OSMD MusicXML preview
- [x] **Piano-roll editor** — Edit `tracks[].events[]` with undo/redo, shared with JSON editor
- [x] **Local project persistence** — SQLite CRUD, autosave, Docker volume survive restart
- [x] **AI region editing** — Non-destructive LLM `replace_region` patch workflow
- [x] **Production-local V1 workspace** — Compose healthchecks, env template, polished SPA workflow
- [x] **V1 acceptance suite** — Fake LLM, fixtures, Playwright journey, Docker persistence gate
- [x] **Canonical Composition V2** — Timeline/expression contract, migration, playback/export fidelity, V2 acceptance
- [x] **MIDI and MusicXML import** — Secure MIDI/MusicXML/MXL ingestion into canonical `composition.v2` with session import reports
- [x] **Deterministic composition analysis** — Versioned `composition.analysis.v1` sidecar over V2 scopes (API + Analysis tab); advisory LLM context only; not persisted or playable
- [x] **Motif-aware composition** — Canonical `motifs` metadata, Motifs tab apply/transform, and thematic LangGraph recurrence (`plan_themes` / `realize_themes`)
- [x] **Interactive harmony and reharmonization** — Editable V2 harmony tick spans, Harmony tab, previewed deterministic/AI reharmonize that only mutates notes on explicit apply
- [x] **Context-aware composition development** — Continue / add / vary sections with multi-candidate `POST /composition/development/preview`; fingerprint-gated apply preserves existing material until explicit selection
- [x] **AI-assisted arrangement / orchestration** — Previewed instrumentation/texture redistributions over selected tracks (`POST /composition/arrangement/preview`); melody/harmony preservation until explicit Apply
- [x] **Upgrade V2 music editing workflow** — Canonical multi-note selection/clipboard/transforms, edit cursor navigation, play-from-cursor and selection loops, viewport culling, and large-score editor acceptance
- [x] **Safe AI experimentation and versioning** — Preview-first AI apply, immutable project revisions, named alternative branches, and restart-safe compare/restore
- [x] **Expressive V2 playback and mixing** — Sampled/synth browser performance with velocity and V2 expression, ephemeral mixer (balance/pan/mute/solo/ambience), smooth transport, and local audited assets without changing export
- [x] **Unified AI runtime and model-provider architecture (V3)** — Capability registry, operation routing, typed adapters, `/ai/models` discovery, explicit fallback, and enriched provenance without regressing V2 remote LLM workflows
- [x] **Optional local AI inference (AMD/ROCm)** — Compose profiles for llama.cpp / vLLM sidecars, `LocalLanguageModel` via OpenAI-compatible HTTP, soft readiness, frontend select of `local:*` without changing default `docker compose up`
- [x] **Symbolic music training dataset pipeline** — Offline `DATASET_ROOT` corpus (ingest → normalize → segment → provenance/dedup/split → stats) via `python -m app.dataset.cli`; no tokenizer/training loop; never writes to `PROJECT_DB_PATH`
- [x] **Symbolic music Composition V2 tokenizer** — Versioned REMI-style encode/decode (`tokenizer.v1`), vocab/manifest, repair, stats/viz, CLI; train+inference contract; never `PROJECT_DB_PATH` / FastAPI / weight load
- [x] **Symbolic music PyTorch Music Transformer** — Decoder-only LM over `tokenizer.v1`; offline train/generate CLI, checkpoint card, inference adapter, optional API; CPU tests + ROCm device knob; never `PROJECT_DB_PATH` / GGUF in FastAPI
- [x] **Reproducible symbolic music training and evaluation** — Experiment dirs, resume, AMP/accum/clip, metrics, symbolic eval (not quality), listening set, compare CLI; offline only (never blocks web API)
- [x] **Style/semantic embeddings and conditioning for symbolic composition** — Handcrafted `symbolic.features.v1` registry embedder, scoped similarity, reference provenance, Develop-tab musical reference conditioning; no large net; no silent dataset ingest
- [x] **Hybrid LLM planner + symbolic note generation pipeline** — Pipeline-parameterized LangGraph (`llm_only` / `hybrid_plan_symbolic` / continuation / variation); versioned non-playable `composition.plan.v1`; Music Transformer / `fake:symbolic-tiny` note engine; multi-stage provenance; no silent LLM note fallback
- [x] **MIDI keyboard / live MIDI performance input** — Browser Web MIDI (or QWERTY test mode) performance capture into `composition.v2` with count-in, metronome, optional quantize, timeline extend, and safe disconnect; never required at startup
- [x] **Audio-to-symbolic musical input (monophonic)** — Mic/file monophonic transcription → session `transcription.preview.v1` review (confidence, expressive vs quantize) → Apply into `composition.v2`; local engines + fake CI path; never persists audio or confidence on notes
- [x] **Optional neural audio rendering** — Job-based generative/neural instrument egress (`/neural-audio/renders`) with adapters + fidelity labels; optional MusicGen sidecar profile; never mutates V2 / FluidSynth / Tone.js
- [x] **DAW interoperability and V3 end-to-end platform hardening** — SMF Type 1 + MusicXML DAW handoff (section markers, drag/download UX); durable `generation.provenance.v1` on revisions; checkpoint path confinement; `scripts/v3_docker_acceptance.sh` restart + seeded reproduce gate (fake modes)
- [x] **V4 composer profiles** — Durable `composer.profile.v1` preference documents with Off/Light/Normal/Strong soft generate conditioning; prompt and hard constraints always win; never copies melodies or writes `DATASET_ROOT`
- [x] **V4 reference feature decomposition** — User-selected `reference.features.v1` dimensions condition generate and develop; multi-reference masks; never copies melodies or mutates the reference Composition
- [x] **V4 reference-conditioned generation and editing** — Preserve / borrow / regenerate policy (`reference.conditioning.policy.v1`) with per-dimension strength on generate, develop, and AI region edit; never overrides hard constraints
- [x] **V4 multi-agent music architecture** — Extensible specialized agents (`ai_agents/`) above V3 runtime; typed artifacts + progressive realize; spine workflow preview; `GET/POST /ai/agents*`; durable `agent_id` provenance; Apply via `multi-agent-apply` CAS only
- [x] **V4 co-performance real-time engine** — Shared Tone transport, Transport-synced MIDI stream, horizon accompaniment buffer, local degradation, optional `POST /live/accompaniment/predict`, explicit Commit; never per-note persist / never `composition.v4`
- [x] **V4 AI Jam real-time co-composition** — Jam modes (`user_melody` / `user_chords`), live features + harmony belief/hysteresis, jam controls, multi-role local generators, multi-track Commit (+ optional harmony spans); graceful predict/AbortController fallback; never invents notes from harmony alone
- [x] **V4 full audio-to-symbolic recovery workflow** — Mixed-audio recovery jobs (`/audio-recovery/*`): optional separation, scaffolding, confidence-gated poly/mono notes, Apply→Bind durable source + overlay; never confidence on V2 events; never DATASET_ROOT
- [x] **V4 audio-symbolic alignment and round-trip editing** — `audio.alignment.v1` after Bind; bar↔source seek + waveform sync (HTMLAudio, not Tone); bound discovery hydrate; soft-stale neural renders via snapshot fingerprint; `audio.roundtrip.provenance.v1`; never mutates source WAV / never `composition.v4` / never DATASET_ROOT
- [x] **Stem-aware neural audio rendering** — Stem sets under `/neural-audio/stem-sets` (piano/bass/strings/…); capability adapters; selective stem rerender; explicit FluidSynth stems; sync honesty; never mutates V2 / never silent generative→FluidSynth / never DATASET_ROOT
- [x] **V4 AI-assisted mix analysis** — Deterministic DSP + rule observations (+ optional advisory AI) over completed neural stem/mix WAVs as `mix.analysis.v1`; never mutates audio/V2; never DATASET_ROOT; never conflates with symbolic analysis or recovery stems
- [x] **V4 AI-assisted mixing and mastering** — Inspectable `mix.plan.v1` preview/apply/reject/undo over completed neural stems; new mix revisions; master targets are goals (`guarantee: false`); never rewrites source stems; never `composition.v4` / `DATASET_ROOT`
- [x] **V4 plugin and extension SDK** — In-process `plugin.manifest.v1` packages on `PLUGIN_PATHS`; `runtime=plugin` symbolic composers on `GET /ai/models` and the generate-composer seam; other categories on `GET /plugins`; no marketplace and no `composition.v4` / `DATASET_ROOT` writes
- [x] **Secure plugin lifecycle and permission boundaries** — Explicit install/enable/disable, durable desired state, manifest resource declarations refused in-process, and a Plugins panel. A broken optional plugin does not stop startup or project open. No marketplace
- [x] **V4 observability and resource controls** — Shared run ids and structured spans for workflow preview, agent calls, model calls, and neural render jobs; env budgets for model calls, wall time, tokens, cost, revisions, and render attempts; cancellation reaches in-flight children; `SystemExit` from an agent or model call stays inside the API process
- [x] **V4 autonomous project composer** — One creative brief schedules specialized agents, commits a multi-section `composition.v2` project stage by stage, and can resume after restart. The spine preview stays preview-only. Render stays opt-in
- [x] **V4 autonomous composer co-producer controls** — Brief review before any agent runs, Guided and Balanced checkpoints, pause, arrangement reject-and-continue with a safe instruction, and revision open/branch on the same run. Default mode stays autonomous
- [x] **V4 collaborative project foundations** — Optional local actors and project roles, anchored comments, revision review, and an activity feed on the existing history graph. Collaboration stays off unless `COLLABORATION_ENABLED` is on
- [x] **V4 musical workflow evaluation** — Versioned `workflow.benchmark.v1` suite scored through V3 generate, the V4 spine, and the V4 revision loop, with hard-metric regression against a stored baseline
- [x] **V4 studio acceptance and production hardening** — Fake-mode studio scenarios, an Alembic upgrade that still opens a `composition.v1` project, and an offline SQLite backup CLI
- [x] **V5 Adaptive Score domain model** — Project-scoped `adaptive.score.v1` states, variants, transitions, layers, and stingers that reference `composition.v2` material without copying note events or changing playback
- [x] **V5 Adaptive Score authoring and validation** — Closed graph commands and actionable findings on `adaptive.score.v1`, plus an Adaptive tab for states, transitions, and the selected authoring state. No playback runtime and no `composition.v5`
- [x] **V5 Adaptive Score transition engine** — Bar-aligned `adaptive.transition.schedule.v1` timing, realization, and one in-memory pending request per score. The scheduler does not render audio, write the score, or call an LLM. No `composition.v5`
- [x] **V5 Adaptive Score intensity layers** — Runtime intensity in `0..1` selects synchronized layers on one `adaptive.score.v1` state (`adaptive.layer.intensity.v1`). Fade and exclusive groups stay data. Session mute applies only to `track_range`. The map does not write notes or call an LLM. No `composition.v5`
- [x] **V5 Adaptive Score playback runtime** — Session clock `adaptive.playback.runtime.v1` advances bar and beat on one `adaptive.score.v1` graph, queues state changes, and applies loops, phrases, stingers, and layer fades through the existing scheduler and layer map. Tone.js only seeks, loops, and ramps session gain. The session stays in memory, so the score row and `composition.v2` notes stay unchanged. An invalid state request leaves transport playing. No `composition.v5`
- [x] **V5 Runtime musical context** — A flat external sample maps onto a closed `adaptive.musical_context.v1` session. Smoothing and hysteresis emit existing playback commands without writing `adaptive.score.v1` or calling an LLM. No `composition.v5`
- [x] **V5 Runtime symbolic continuation** — A session buffer fills bars ahead of Adaptive Score playback. Deterministic fallbacks return before the symbolic model, and a late result is discarded. The score stays `adaptive.score.v1` and playback stays `adaptive.playback.runtime.v1`. No `composition.v5`
- [x] **V5 Adaptive Music Engine API** — A game or installation drives one stored `adaptive.score.v1` through `/adaptive/*` (`adaptive.engine.session.v1`) without the studio. The session starts or binds playback, accepts state, intensity, stinger, and context commands, and publishes acknowledgements and throttled status. Bearer-or-loopback auth and a one-slot context coalescer stay in process memory. The score and `composition.v2` notes stay unchanged. No `composition.v5`
- [x] **V5 Adaptive Music Client SDKs** — Python `mukit-adaptive` and TypeScript `@mukit/adaptive-music` drive one stored score over the published `/adaptive/*` HTTP and WebSocket surface, including reconnect and a terminal exploration, danger, combat, and victory demo. They do not author the score, open the studio, or add a server route. No `composition.v5`
- [x] **V5 Video scoring timeline** — One immutable MP4/MOV per project (`video.asset.v1`) and a `video.scoring.v1` sync document. A read-only container probe supplies duration, frame rate, audio presence, and resolution. The Picture tab keeps the composition cursor on the same instant as video timecode and musical bars, including tempo changes. Stored bytes and `composition.v2` note events stay unchanged. No `composition.v5`
- [x] **V5 Film scoring spotting cues** — Authored cues on `video.scoring.v1` `hit_points` with kind, `HH:MM:SS:FF` timecode, frame tolerance, importance, and instruction. A landing check compares note attacks with the cue frame. Suggestions stay an explicit preview and do not write notes. Cues are not markers. No `composition.v5`
- [x] **V5 Film scoring agent** — Explicit `film.score.plan.v1` preview from stored spotting cues, the existing V4 agents, and a deterministic section and tempo compiler. Commit writes an editable `composition.v2` and the sync origin. Cue rows and video bytes stay unchanged. No new agent id and no `composition.v5`
- [x] **V5 Film score adaptation** — An explicit `film.score.adaptation.v1` preview chooses one local repair for a declared picture edit and keeps music outside that edit identical. Commit writes the repaired `composition.v2` through `film-score-adapt-apply`. Cue rows, video bytes, and the sync origin stay stored. A span that covers the whole score is refused and does not generate a replacement. No `composition.v5`
- [x] **V5 Musical Universe** — A project group shares `musical.universe.v1` entities, themes, variants, and usage. Themes reference motif occurrences. An explicit mechanical reuse writes transformed notes into a member `composition.v2`. The universe row stores no pitches. No `composition.v5`
- [x] **V5 Derived material dependency graph** — Explicit edges name which motifs, arrangements, variations, renders, transcriptions, and reference-conditioned revisions came from Theme A. An upstream change lists stale dependents and leaves their notes and audio unchanged. No `composition.v5`
- [x] **V5 Explicit preference learning** — Opt-in records of which generated alternative the user chose among a development or arrangement ballot. A lightweight ranker orders later ballots from those choices, and the user can still select any candidate. Inspect and reset stay available. The score stays `composition.v2`. A choice is not a genre label and not a `composer.profile.v1`. No `composition.v5`
- [x] **V5 Distributed AI execution nodes** — Trusted LAN ExecutionNode registration with authenticated heartbeat, capability/model discovery, availability TTL, and typed remote `LanguageModel` inference through `runtime=execution_node`. No arbitrary remote shell. Task cancel cooperates with operation traces. The score stays `composition.v2`. No `composition.v5`
- [x] **V5 Capability-aware AI job scheduling** — Deterministic placement of LanguageModel jobs across controller-local and trusted-LAN ExecutionNode runtimes by required capability, installed model, memory, GPU class, estimated latency, job priority, and user policy (`prefer_local` / `fastest_available` / `memory_safe` / `fixed_node`). Private workloads never escalate to public cloud unless explicitly allowed. Node loss triggers bounded reschedule without silent trust escalation. The score stays `composition.v2`. No `composition.v5`
- [x] **V5 Browser WebGPU inference for lightweight AI** — Evaluate latency-sensitive lightweight capabilities against backend CPU, local GPU backend, and browser WebGPU; register `runtime=browser_model` through the existing model abstraction with load/cache/capability detection and HTTP fallback; ship at least one public browser-local model (default: symbolic embedding compute) without exposing private server weights. The score stays `composition.v2`. No `composition.v5`
- [x] **V5 Expressive MIDI performance input** — Capability-probed browser MIDI transports for higher-resolution velocity, per-note expression, pitch expression, controllers, MPE mapping, and optional MIDI 2.0/UMP abstractions, with graceful fallback to ordinary Web MIDI / QWERTY and deterministic degrade onto `composition.v2` notes (`velocity` 1–127). Optional performance metadata references event ids and does not invent a second playable score. Simulated-device tests required. No `composition.v5`
- [ ] **V5 AI performance / conductor layer** — A durable `performance.plan.v1` conductor sidecar that projects tempo rubato, dynamics, phrasing, articulation, pedaling, microtiming, accent, and orchestral balance onto a performed event stream without rewriting canonical pitch or harmony. Deterministic presets first (`intimate` / `dramatic` / `restrained` / `mechanical`); optional AI augmentation later. Multiple distinguishable performances per Composition; before/after audition; derived storage only. No `composition.v5`
- [ ] **V5 Spatial music scene and preview** — Project-scoped `spatial.scene.v1` with SpatialMix metadata (azimuth, elevation, distance, spread, optional motion) for tracks and/or completed neural stems. Deterministic `spatial.preview.v1` stereo + FOA Ambisonic-compatible coefficients; synchronized Web Audio / Tone preview without rewriting `composition.v2` notes or stem WAVs. Not a Dolby Atmos workstation. No `composition.v5`

## Completed

| Milestone | Date |
|-----------|------|
| V5 Expressive MIDI performance input | 2026-10-03 |
| V5 Browser WebGPU inference for lightweight AI | 2026-10-03 |
| V5 Capability-aware AI job scheduling | 2026-10-03 |
| V5 Explicit preference learning | 2026-10-03 |
| V5 Distributed AI execution nodes | 2026-10-03 |
| Canonical composition.v1 | 2026-09-07 |
| Multi-stage LLM composer | 2026-09-07 |
| Generation hard-constraint enforcement | 2026-09-07 |
| Deterministic export | 2026-09-07 |
| Exact playback & notation | 2026-09-07 |
| Piano-roll editor | 2026-09-07 |
| Local project persistence | 2026-09-07 |
| AI region editing | 2026-09-07 |
| Production-local V1 workspace | 2026-09-07 |
| V1 acceptance suite | 2026-09-07 |
| Canonical Composition V2 | 2026-09-08 |
| MIDI and MusicXML import | 2026-09-08 |
| Deterministic composition analysis | 2026-09-08 |
| Motif-aware composition | 2026-09-09 |
| Interactive harmony and reharmonization | 2026-09-09 |
| Context-aware composition development | 2026-09-09 |
| AI-assisted arrangement / orchestration | 2026-09-10 |
| Upgrade V2 music editing workflow | 2026-09-10 |
| Safe AI experimentation and versioning | 2026-09-10 |
| Expressive V2 playback and mixing | 2026-09-10 |
| Unified AI runtime and model-provider architecture (V3) | 2026-09-21 |
| Optional local AI inference (AMD/ROCm) | 2026-09-21 |
| Symbolic music training dataset pipeline | 2026-09-21 |
| Symbolic music Composition V2 tokenizer | 2026-09-21 |
| Symbolic music PyTorch Music Transformer | 2026-09-21 |
| Reproducible symbolic music training and evaluation | 2026-09-21 |
| Style/semantic embeddings and conditioning for symbolic composition | 2026-09-21 |
| Hybrid LLM planner + symbolic note generation pipeline | 2026-09-21 |
| MIDI keyboard / live MIDI performance input | 2026-09-21 |
| Audio-to-symbolic musical input (monophonic) | 2026-09-21 |
| Optional neural audio rendering | 2026-09-21 |
| DAW interoperability and V3 end-to-end platform hardening | 2026-09-21 |
| V4 composer profiles | 2026-09-22 |
| V4 reference feature decomposition | 2026-09-22 |
| V4 reference-conditioned generation and editing | 2026-09-22 |
| V4 multi-agent music architecture | 2026-09-22 |
| V4 co-performance real-time engine | 2026-09-23 |
| V4 AI Jam real-time co-composition | 2026-09-23 |
| V4 full audio-to-symbolic recovery workflow | 2026-09-24 |
| V4 audio-symbolic alignment and round-trip editing | 2026-09-24 |
| Stem-aware neural audio rendering | 2026-09-24 |
| V4 AI-assisted mix analysis | 2026-09-24 |
| V4 AI-assisted mixing and mastering | 2026-09-25 |
| V4 plugin and extension SDK | 2026-09-25 |
| Secure plugin lifecycle and permission boundaries | 2026-09-25 |
| V4 observability and resource controls | 2026-09-25 |
| V4 autonomous project composer | 2026-09-26 |
| V4 autonomous composer co-producer controls | 2026-09-26 |
| V4 collaborative project foundations | 2026-09-26 |
| V4 musical workflow evaluation | 2026-09-27 |
| V4 studio acceptance and production hardening | 2026-09-27 |
| V5 Adaptive Score domain model | 2026-09-28 |
| V5 Adaptive Score authoring and validation | 2026-09-28 |
| V5 Adaptive Score transition engine | 2026-09-28 |
| V5 Adaptive Score intensity layers | 2026-09-28 |
| V5 Adaptive Score playback runtime | 2026-09-29 |
| V5 Runtime musical context | 2026-09-29 |
| V5 Runtime symbolic continuation | 2026-09-29 |
| V5 Adaptive Music Engine API | 2026-09-29 |
| V5 Adaptive Music Client SDKs | 2026-09-30 |
| V5 Video scoring timeline | 2026-09-30 |
| V5 Film scoring spotting cues | 2026-09-30 |
| V5 Film scoring agent | 2026-10-01 |
| V5 Film score adaptation | 2026-10-01 |
| V5 Musical Universe | 2026-10-01 |
| V5 Derived material dependency graph | 2026-10-02 |
