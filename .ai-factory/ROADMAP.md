# Project Roadmap

> Full-stack LLM music composer: generate and edit canonical playable `composition.v1`, persist locally, and export/play with fidelity.

## Milestones

- [x] **Canonical composition.v1** — Playable JSON contract with validation, normalization, and docs
- [x] **Multi-stage LLM composer** — Form → harmony → parts → assemble/validate/repair via LangGraph
- [x] **Deterministic export** — MusicXML, MIDI, and FluidSynth WAV from the same note events
- [x] **Exact playback & notation** — Tone.js multi-track playback and OSMD MusicXML preview
- [x] **Piano-roll editor** — Edit `tracks[].events[]` with undo/redo, shared with JSON editor
- [x] **Local project persistence** — SQLite CRUD, autosave, Docker volume survive restart
- [x] **AI region editing** — Non-destructive LLM `replace_region` patch workflow
- [x] **Production-local V1 workspace** — Compose healthchecks, env template, polished SPA workflow
- [x] **V1 acceptance suite** — Fake LLM, fixtures, Playwright journey, Docker persistence gate

## Completed

| Milestone | Date |
|-----------|------|
| Canonical composition.v1 | 2026-09-07 |
| Multi-stage LLM composer | 2026-09-07 |
| Deterministic export | 2026-09-07 |
| Exact playback & notation | 2026-09-07 |
| Piano-roll editor | 2026-09-07 |
| Local project persistence | 2026-09-07 |
| AI region editing | 2026-09-07 |
| Production-local V1 workspace | 2026-09-07 |
| V1 acceptance suite | 2026-09-07 |
