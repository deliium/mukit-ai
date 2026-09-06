---
name: composition-v1
description: Canonical playable composition.v1 JSON contract for Mukit AI — schema rules, staged LLM generation, region editing, export/playback fidelity. Use when changing composition schema, generators, editors, piano roll, MusicXML/MIDI/WAV export, or Tone.js playback.
metadata:
  author: mukit-ai
  version: "1.0"
---

# Composition V1

## Overview

`composition.v1` is the single source of truth for playable music in this project. Harmony metadata must never invent audible notes. Piano roll, JSON editor, notation, playback, and exports all consume `tracks[].events[]`.

Authoritative docs: `docs/composition-v1.md`. Pydantic models: `backend/app/schemas.py`.

## Hard Rules

1. **Playable notes live only in `tracks[].events[]`** — never synthesize notes from `harmony` for playback or export.
2. **Preserve identity fields** when patching (`tempo`, `key`, `time_signature`, `ticks_per_quarter`, `duration_ticks`, `bar_count`, `sections`) unless the feature explicitly changes them.
3. **Required playable roles** for LLM generation: melody, bass, and harmony/accompaniment note events.
4. **Region edits** return a `replace_region` patch only — never a full regenerated composition from the provider.
5. **Sanitize logs** — no API keys, full prompts, or raw MusicXML/MIDI/WAV payloads.

## Staged Generation (backend)

LangGraph stages in order: `plan_form` → `plan_harmony` → `compose_melody` → `compose_bass` → `compose_accompaniment` → `assemble_composition` → `validate_composition` → optional `repair_composition`.

Practical LLM bounds: up to **32 bars** and **6 non-drum instruments** (HTTP 422 if oversized). Repair exhaustion → HTTP 502.

## Region Editing

`POST /llm/edit-composition-region`: instruction + inclusive bar selection + optional `track_ids`. Defaults: `allow_harmony_changes=false`, `allow_added_tracks=false`. Apply patches immutably via `composition_region_patch.py`.

## Export / Playback Contract

| Surface | Source of truth |
|---------|-----------------|
| MusicXML preview/download | `tracks[].events[]` via music21 |
| MIDI | same events via mido |
| WAV | FluidSynth from MIDI bytes |
| Browser playback | Tone.js from same events |
| Piano roll / JSON editor | Zustand `editedMusicJson` |

## Checklist

- [ ] Schema version remains `composition.v1`
- [ ] Events are tick-based with valid pitch/duration/velocity
- [ ] Harmony treated as metadata only
- [ ] Frontend and backend validators agree on canonical shape
- [ ] Tests cover normalize/validate/export fidelity for the change
