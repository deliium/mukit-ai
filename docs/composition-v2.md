[Back to README](../README.md) · [Composition V1 →](composition-v1.md)

# Composition V2

`composition.v2` is the **operational canonical** JSON contract for generation, editing, persistence, browser playback, and export. `composition.v1` remains an accepted **migration and parser compatibility** input only; API responses and stored projects normalize to V2.

Playable pitches live **only** in `tracks[].events[]`. Timeline metadata, markers, harmony, and track expression direct deterministic **projections** — they never synthesize notes.

## Version dispatch

| Input | Path |
|-------|------|
| Unversioned legacy top-level `notes` | Legacy → V1 → V2 |
| `schema_version: "composition.v1"` | Validate V1 → migrate to V2 |
| `schema_version: "composition.v2"` | Validate V2 directly |
| Unknown explicit version | `unsupported_schema_version` (no legacy fallback) |

V2 persisted nested models use `extra="forbid"`. V1 keeps ignored-extra compatibility for older projects. There is **no** V2→V1 downgrade.

## Field ownership

| Category | Fields | Rule |
|----------|--------|------|
| Musical content | note pitch/start/notated duration, ties, articulations, meter/key timelines | Authored notes and score structure |
| Semantic / navigation | sections (type, boundaries, optional `id`/`label`), harmony, rehearsal/text `markers` | UI, notation, LLM context — never audible notes |
| Playback / performance | note velocity, tempo timeline, track volume/pan/expression, dynamic marks, sustain spans, automation | Deterministic projections — never replaces note content |

## Root fields (V1 + timeline)

V1 root fields are preserved. **Initial** conductor state is at tick `0`:

| Field | Description |
|-------|-------------|
| `schema_version` | Must be `composition.v2` |
| `tempo` | BPM at tick `0` (`40`–`240`) |
| `key` | Key at tick `0` (e.g. `C major`, `F# minor`) |
| `time_signature` | Meter at tick `0` (e.g. `4/4`, `3/4`, `6/8`) |
| `ticks_per_quarter` | Integer PPQ (default `480`) |
| `bar_count`, `duration_ticks` | Total length; `duration_ticks` must match compiled bar map |
| `sections` | Contiguous boundaries; optional stable `id` and free-text `label` |
| `tracks` | Ordered track list with events and track-local expression |
| `harmony` | Chord-symbol metadata only |

**Timeline change arrays** contain transitions **after** tick `0` only (no duplicate tick-`0` entries):

| Array | Item shape | Constraints |
|-------|------------|-------------|
| `tempo_changes` | `{tick, bpm}` | Instantaneous steps; ticks in `(0, duration_ticks)` |
| `time_signature_changes` | `{tick, time_signature}` | Must land on a derived bar boundary; integral bar lengths at PPQ |
| `key_changes` | `{tick, key}` | Must land on a derived bar boundary |
| `markers` | `{id?, tick, kind, label}` | `kind` is `rehearsal` or `text`; multiple distinct markers may share a tick |

Section positions are **not** duplicated in `markers`. Use section `label` for form names; use `markers` for rehearsal letters and free text.

## Note events

Each playable note extends V1 with optional expression metadata:

```json
{
  "type": "note",
  "id": "m1",
  "pitch": "G4",
  "start_tick": 960,
  "duration_ticks": 480,
  "velocity": 86,
  "staff": null,
  "voice": null,
  "articulations": ["staccato"],
  "tie": { "group_id": "tie-g4", "type": "start" }
}
```

### Articulations

Allowed values (duplicate-free subset): `staccato`, `staccatissimo`, `tenuto`, `accent`, `marcato`.

| Articulation | Performed transform |
|--------------|---------------------|
| `staccato` | 50% gate |
| `staccatissimo` | 25% gate |
| `tenuto` | 100% gate |
| `accent` | +12 velocity |
| `marcato` | 75% gate, +20 velocity |

Gate rounds to nearest tick (minimum 1 tick). Velocity clamps to `1..127`. **Notated duration stays canonical.**

Rejected combinations: `staccato`+`staccatissimo`, either short articulation+`tenuto`, `accent`+`marcato`. Tie chains allow attack articulations only on the **head**; gate-shortening articulations are rejected anywhere in the chain.

### Ties

`tie: {group_id, type}` where `type` is `start`, `continue`, or `stop`.

Within one track, each chain must have exactly one `start` and one `stop`, zero or more `continues`, same written pitch/staff/voice, contiguous boundaries, and deterministic order. Only the chain head velocity starts a sound; consumers collapse the chain to one attack/release before format-specific measure fragmentation.

## Track expression

| Field | Description |
|-------|-------------|
| `expression` | Initial expression `0..127` (default `127`) |
| `dynamic_marks` | `[{tick, level}]` — levels `ppp` … `fff` |
| `sustain_pedals` | `[{start_tick, duration_ticks}]` — binary, half-open, non-overlapping |
| `automation` | At most one lane each for `volume`, `pan`, `expression` |

Each automation lane: `{parameter, interpolation, points}` where `interpolation` is `step` or `linear` and each point is `{tick, value}`. Point ticks are strictly increasing and `> 0`. Volume/expression values are `0..127`; pan is `-64..63`. Static `volume`, `pan`, and `expression` are tick-zero state.

### Dynamic + automation precedence

Dynamic level → expression factor: `ppp=32`, `pp=48`, `p=64`, `mp=80`, `mf=96`, `f=112`, `ff=120`, `fff=127`. Before the first mark, factor is `127` (no attenuation).

**Effective expression** = round(clamp(current automation/lane expression × active dynamic / 127)). Velocity remains separate attack intensity. UI mute/solo gain is **not** persisted and stays separate from automation.

Linear automation is evaluated in tick space. MIDI samples at endpoints and intervals ≤ `max(1, ticks_per_quarter // 16)`; Tone.js evaluates the same segments as parameter ramps.

## Timeline math

Python (`composition_timing.py` / `composition_timeline.py`) and JavaScript (`frontend/src/utils/compositionTimeline.js`) share one rule set:

- Bar boundaries from the compiled meter map (including mid-piece meter changes)
- Active tempo/meter/key lookup at any tick
- Piecewise `tick_to_seconds` / `seconds_to_tick`
- Variable-width piano-roll bars and cursor labels derive from this map; **note start/duration ticks are never rewritten**

At equal ticks, projection order is stable: conductor changes → markers/directions → track automation/pedal → note-offs → note-ons → end cleanup. Tie boundaries produce no off/on pair.

## Migration V1 → V2

`migrate_v1_to_v2()` in `composition_migration.py` is non-mutating on the source document.

**Preserved exactly:** root metadata, section positions/types, harmony, track order/metadata, event order, and every note `id`, pitch spelling, timing, velocity, staff, voice.

**Added/changed only:** `schema_version` → `composition.v2`, deterministic section `id` when absent, empty/default V2 collections (`tempo_changes`, `markers`, `articulations`, etc.).

**Equality gate:** After migration, projecting V2 back onto the V1 field set must match the source V1 document (except schema version, section IDs, and documented empty V2 defaults). Failure code: `v1_v2_migration_fidelity_failed` — stored JSON is **not** rewritten.

**Project open:** `normalize_composition_json` always returns V2. V1 rows migrate on read and may rewrite the DB row when successful. Repeated opens are idempotent.

Ignored V1 extra fields are collected in the migration report (sanitized paths) but are not treated as supported V1 data.

## Export fidelity matrix

| V2 field | Tone.js | MIDI / WAV | MusicXML / OSMD |
|--------|---------|------------|-----------------|
| Note pitch/start/notated duration/velocity | Compiled timeline schedule | Note events at canonical ticks; WAV inherits MIDI | Notes/rests/voices from events |
| Articulations | Gate/velocity approximation | Same approximation | Native articulation symbols |
| Semantic ties | One attack/release per chain | One logical note; tie IDs lost in MIDI | Native ties; fragmented at barlines |
| Dynamic marks | Expression-gain mapping | Combined into CC11; WAV inherits | Native dynamic marks |
| Sustain spans | Deferred releases | CC64; WAV inherits | Pedal directions |
| Volume/pan/expression automation | Gain/panner/expression ramps | CC7/CC10/CC11 with sampled linear curves | Omitted from score (structured issue) |
| Tempo changes | Piecewise timing | Conductor tempo events (integer BPM) | Metronome marks |
| Meter changes | Bar/cursor/grid map | Conductor time-signature events | Time-signature attributes per part |
| Key changes | Display/navigation only | Key-signature events where supported | Key-signature attributes per part |
| Section labels, markers | Display/navigation | Marker/text meta events | Section labels, rehearsal/text expressions |
| Harmony | Ignored (no invented notes) | Ignored | Chord-symbol projection only |

WAV is FluidSynth output of the **exact MIDI bytes** for that request. SoundFont identity across versions is not guaranteed.

## Projection issues and response headers

Every export renderer returns a shared `ProjectionReport` (`composition_projection.py`). Download routes attach compact headers (also exposed through CORS):

| Header | Meaning |
|--------|---------|
| `X-Mukit-Projection-Status` | `exact`, `approximated`, `omitted`, or `failed` |
| `X-Mukit-Projection-Issues` | Comma-separated stable issue codes (bounded length) |
| `X-Mukit-Projection-Exact-Count` | Count of exact projections |
| `X-Mukit-Projection-Approximated-Count` | Approximation count |
| `X-Mukit-Projection-Omitted-Count` | Intentional omission count |
| `X-Mukit-Projection-Failed-Count` | Hard failure count |

Stable issue codes:

| Code | Meaning |
|------|---------|
| `tempo_quantized` | MIDI integer BPM differs from authored tempo |
| `automation_sampled` | Linear automation sampled onto bounded tick grid |
| `automation_omitted_from_notation` | Track automation has no MusicXML representation |
| `articulation_transformed` | Gate/velocity approximation applied |
| `key_spelling_unsupported` | Key spelling unsupported in target format |
| `tie_ids_lost` | Semantic tie group IDs not retained in MIDI |
| `pitch_spelling_lost` | Pitch spelling identity lost in target |
| `marker_normalized` | Marker text/kind normalized for target |
| `midi_channel_control_conflict` | Conflicting CC streams on a shared MIDI channel — **export error** |
| `expression_combined` | Dynamic marks merged into expression/CC11 |
| `sustain_projected` | Sustain spans projected to CC64 or pedal directions |

The frontend parses headers in `musicApi.js` and surfaces warnings on export actions without embedding raw reports in binary payloads.

## Operator notes

### Fake LLM expressive fixture

With `LLM_FAKE_MODE=1`, generation prefers the native V2 fixture `backend/app/fixtures/composition_v2_expressive.json` when prompt duration matches (4 bars). Otherwise fake mode loads V1 fixtures and migrates. The expressive fixture exercises tempo change at bar 3, articulations, ties, dynamic marks, sustain pedal, expression automation, section labels, and rehearsal/text markers — useful for credit-free playback/export/notation checks.

Mirror copies: `backend/tests/fixtures/composition_v2_expressive.json`, `frontend/src/utils/fixtures/composition_v2_expressive.json`.

### Projection headers in operations

- Expect `approximated` or `omitted` on exports that include linear automation, tempo quantization, or articulation transforms.
- Treat `failed` + `midi_channel_control_conflict` as a configuration error (overlapping track-local CC on one channel).
- Backend logs carry issue **codes and counts** only — never MusicXML text, MIDI/WAV bytes, or full compositions.

### Logging (safe fields)

| Surface | Control | Safe DEBUG/INFO fields |
|---------|---------|------------------------|
| Backend | `LOG_LEVEL` | schema version, migration path, bar/track/note/change counts, projection status, issue codes, export byte length |
| Frontend | browser devtools | version normalization, revision transitions, projection status, schedule summaries |
| Forbidden everywhere | — | API keys, full prompts/instructions, raw composition JSON, MusicXML, MIDI, WAV |

## Minimal example

See `backend/app/fixtures/composition_v2_expressive.json` for a full 4-bar multi-track document. Abbreviated shape:

```json
{
  "schema_version": "composition.v2",
  "tempo": 100,
  "key": "C major",
  "time_signature": "4/4",
  "ticks_per_quarter": 480,
  "duration_ticks": 7680,
  "bar_count": 4,
  "tempo_changes": [{ "tick": 3840, "bpm": 80 }],
  "time_signature_changes": [],
  "key_changes": [],
  "markers": [{ "id": "rehearsal-a", "tick": 0, "kind": "rehearsal", "label": "A" }],
  "sections": [
    { "id": "section-1", "type": "intro", "label": "Opening", "start_bar": 1, "bar_count": 2, "start_tick": 0, "duration_ticks": 3840 }
  ],
  "tracks": [
    {
      "id": "melody-1",
      "instrument": "piano",
      "role": "melody",
      "expression": 127,
      "dynamic_marks": [{ "tick": 0, "level": "mf" }],
      "sustain_pedals": [{ "start_tick": 0, "duration_ticks": 1920 }],
      "automation": [],
      "events": [
        { "type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 90, "articulations": ["accent"], "tie": null }
      ]
    }
  ],
  "harmony": [{ "bar": 1, "chord": "C" }]
}
```

## Release checklist (V2 enablement)

Before treating V2 responses as production-ready, verify:

- [ ] V1→V2 migration equality gate green (`test_composition_v2_migration.py`)
- [ ] Project open/save/duplicate round-trip preserves every V2 field (`test_project_persistence_acceptance.py`)
- [ ] Frontend dirty/notation revisions include all persisted and audible fields
- [ ] Tone playback timing matches timeline compiler at tempo/meter boundaries
- [ ] MIDI/WAV/MusicXML projections match golden vectors or emit documented issue codes
- [ ] No silent field loss across create/open/save/edit/export
- [ ] Logs remain count/code-based with no secrets or raw music payloads

## Consumers

- **Generation / edit:** `POST /llm/generate-music-json` and `POST /llm/edit-composition-region` return V2 in `music` (input may be V1 or V2).
- **Projects:** SQLite stores V2 after open/save; V1 migrates on read. See [project-persistence.md](./project-persistence.md).
- **Playback:** `tonePlaybackEngine.js` compiles V2 expression with piecewise tempo; mute/solo is UI-only.
- **Exports:** `/export/musicxml`, `/export/midi`, and `/export/wav` accept V1 or V2 input, normalize to V2, and attach `X-Mukit-Projection-*` headers (CORS-exposed). MusicXML may report notation omissions such as `automation_omitted_from_notation`; MIDI/WAV inherit the shared MIDI projection report (tempo quantization, automation sampling, articulation transforms, and related codes). WAV uses FluidSynth on the same MIDI bytes; env vars: `FLUIDSYNTH_BIN`, `COMPOSITION_WAV_SOUNDFONT` (Docker default `/usr/share/sounds/sf2/FluidR3_GM.sf2`), `COMPOSITION_WAV_SAMPLE_RATE`, `COMPOSITION_WAV_GAIN`, `COMPOSITION_WAV_TIMEOUT_SECONDS`. Missing FluidSynth/SoundFont → `503`.
- **Editors:** Piano roll edits notes (articulations, ties); JSON editor holds full V2 including automation/timeline arrays.

Staged generation, region editing, and V1 compatibility details: [composition-v1.md](./composition-v1.md).

## See Also

- [Composition V1](composition-v1.md) — staged generation, region editing, V1 parser compatibility
- [Project persistence](project-persistence.md) — migrate-on-open, autosave, SQLite
- [Testing](testing.md) — V2 fixtures, pytest targets, projection tests
