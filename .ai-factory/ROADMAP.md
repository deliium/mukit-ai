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
| Deterministic composition analysis | 2026-09-09 |
