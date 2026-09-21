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
- [x] **V4 multi-agent music architecture** — Extensible specialized agents (`ai_agents/`) above V3 runtime; typed artifacts + progressive realize; spine workflow preview; `GET/POST /ai/agents*`; durable `agent_id` provenance; Apply via `multi-agent-apply` CAS only

## Completed

| Milestone | Date |
|-----------|------|
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
| V4 multi-agent music architecture | 2026-09-22 |
