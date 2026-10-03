[← Browser Playback](browser-playback.md) · [Back to README](../README.md) · [AI-assisted mixing →](ai-assisted-mixing.md) · [Neural audio →](neural-audio-rendering.md)

# Spatial Music Scene and Preview

Product generation **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**.

A **Spatial Scene** (`spatial.scene.v1`) is a durable, project-scoped SpatialMix layout for tracks and/or completed neural stems. A **spatial preview** (`spatial.preview.v1`) is a session compile of stereo gains and first-order Ambisonic (FOA, ACN/SN3D) coefficients. Consumers may ignore it. Canonical notes and stem WAV bytes never change under spatial audition.

```text
composition.v2  (canonical notes; authoritative)
neural stem WAVs (optional completed stem-set; byte-immutable)
        │  read-only bind (fingerprints)
        ▼
spatial.scene.v1  (durable SpatialMix positions / motion)
        │  deterministic compile
        ▼
spatial.preview.v1  (session stereo + FOA coeffs — no PCM)
        │
        ▼
Web Audio / Tone spatial preview graph (canonical export + stem bytes unchanged)
```

## Task 1 freezes

| Topic | Lock |
|-------|------|
| Coordinate convention | Right-handed; listener at origin facing **+Z** (front); azimuth **0° = front; +azimuth = left (CCW)**; elevation + = up |
| Caps | `MAX_SOURCES=32`; `MAX_MOTION_KEYFRAMES=64`; `D_REF=1.0`; `D_MAX=100` |
| Distance model | `distance_gain = clamp(D_REF / max(distance, D_REF), 0.05, 1.0)` |
| Spread mapping | Widens stereo toward center-fill; reduces FOA directional energy toward W; no PCM convolution reverb |
| Stereo path | Equal-power from azimuth (`pan = 0.5 - 0.5·sin(az)` → left/right via `cos/sin(pan·π/2)`) × mild elevation atten × `distance_gain` × spread bias × source gain |
| FOA | First-order only; ACN order **W,Y,Z,X**; SN3D: `W=1/√2`, `Y=sin(az)cos(el)`, `Z=sin(el)`, `X=cos(az)cos(el)`; then × `distance_gain` × source `gain` × spread bias |
| Stem fingerprint | `sha256(":".join(sorted(sha256_prefix…)))` hex length **64** |
| Composition fingerprint | `composition_snapshot_fingerprint` only |
| Motion sample | Primary key = `at_tick` (default 0); linear interp; `at_seconds` only if tick absent; **never** changes musical time / `Transport.bpm` |
| Audible browser path | Prefer `Panner3D` from scene SpatialMix; else apply **API-compiled** stereo gains on `Tone.Panner` / trim |
| FOA authority | Python compile only — **no JS FOA encoder twin** |
| Identity | Reuse `performance_identity.identity_digest` |
| Stem bind | `stem_id` required; `stem_role` alone refused |
| Export | Default SMF / MusicXML / WAV unchanged |
| Orthogonality | Does not widen `mix.plan.v1`; does not pollute working Tone mixer; does not stack performance-conductor realizations in ship-1 |
| Honesty | Not Dolby Atmos / ADM / HOA / certified binaural |

`engine_version`: `spatial.preview.v1.0` (see `backend/app/spatial_constants.py`).

## Presets (catalog)

Closed seeds: `front_stereo`, `circle_ensemble`, `close_intimate`.

- `GET /projects/{id}/spatial-scenes/presets` — static catalog, **no SQLite writes**
- `POST /projects/{id}/spatial-scenes` with `preset_id` — clones one row
- Opening the Spatial tab / listing scenes never auto-INSERTS

## HTTP surface

Under `/projects/{project_id}/spatial-scenes`:

| Method | Behavior |
|--------|----------|
| `GET /` | list summaries (may be empty) |
| `GET /presets` | static catalog |
| `POST /` | create from preset clone or custom body |
| `GET /{id}` | get |
| `PUT /{id}` | CAS replace (`expected_document_revision`) |
| `DELETE /{id}` | delete |
| `POST /{id}/compile` | returns `spatial.preview.v1`; **composition required when any track source**; **stem-set metadata required when any stem source**; accepts `at_tick` (default 0) and optional draft `scene` body |

All routes use `enforce_current`. No composition write routes. No stem rewrite routes. Soft-stale compares scene pins to fingerprints of the **request** composition / stem-set metadata (not a silent load of a different saved project row).

## Web Audio audition

1. Spatial tab selects a scene and toggles **Spatial preview**.
2. SPA calls `POST .../compile` with current `editedMusicJson` when any track source exists, plus stem-set metadata (`stem_set_id` + per-stem `sha256_prefix` from `GET /neural-audio/stem-sets/{id}`) when any stem source exists. Draft scene body is allowed; compile never writes the project.
3. `Panner3D` is preferred when available from scene SpatialMix; otherwise API-compiled stereo L/R maps to preview-scope `panOffset` / `trimDb`.
4. Playback source kind `spatial` uses mixer scope `preview`.
5. Stem sources fetch `/neural-audio/stems/{id}/audio`, decode, and schedule on the shared `Tone.Transport` origin through the engine master bus (`getMasterInput`); missing/incomplete stems are skipped with a soft warning. Teardown on clear / project switch.
6. Optional per-source motion keyframes edit position only (tick-primary); they never change note ticks or `Transport.bpm`.
7. **Working** clears spatial audition and restores the non-spatial graph.

Pitch, harmony, and stem bytes are never rewritten. Motion changes position only — never note `start_tick` or `Transport.bpm`.

## Soft-stale

When the request composition fingerprint ≠ scene `source_composition_fingerprint` (or stem-set pin ≠ request stem fingerprint), compile sets `stale.composition` / `stale.stem_set` and the UI shows a banner. Audition still uses the request body and does not write `projects.composition_json`.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `SPATIAL_PREVIEW_MAX_SOURCES` | `32` | Cap on sources per scene (optional override) |
| `SPATIAL_PREVIEW_MAX_MOTION_KEYFRAMES` | `64` | Cap on motion keyframes per source |

See `.env.example`.

## Logging redaction

| Level | What |
|-------|------|
| DEBUG | source counts by kind, engine_version, distance clamps, motion sample tick, soft-stale, fallback reason codes |
| INFO | create/compile with `{scene_id, source_count, track_count, stem_count, stale, metrics.summary}` |
| WARN | soft-stale, unresolved source skip, CAS conflict, embed refuse |
| ERROR | unexpected compile/apply failures (sanitized) |

Never: full scene JSON dumps, PCM, composition event arrays, FOA coeff arrays, or API keys at INFO.

## Orthogonality

| Layer | Relationship |
|-------|----------------|
| `mix.plan.v1` | Remains 2D AI-assisted mix revision / Apply→WAV. Spatial does not widen it. |
| Ephemeral Tone mixer | Working `panOffset` / trim stay session UI. Spatial audition uses mixer scope `preview`. |
| Performance conductor | Orthogonal expressive timing. Ship-1 does not stack realizations into the spatial graph. |
| `ai_agents/` | Must not import spatial store/compiler modules. |

## Honesty limits

This is **not** a Dolby Atmos / ADM / bed+object workstation, not higher-order Ambisonics, and not a certified binaural product. Ship-1 FOA coefficients are Ambisonic-**compatible** inspectable vectors from the Python compile.
