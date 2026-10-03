# Implementation Plan: V5 Spatial Music Scene and Preview Engine

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: thin Spatial studio tab — scene select/create, per-source SpatialMix controls (azimuth / elevation / distance / spread), optional motion keyframes list, stereo vs Ambisonic-inspect toggle, soft-stale banner; no piano-roll redesign, no Dolby Atmos object bed editor, no full 3D viewport sculpting
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Task 1 locks concrete SpatialMix caps (`MAX_SOURCES=32`, `MAX_MOTION_KEYFRAMES=64`, `D_REF=1.0`, `D_MAX=100`, distance_gain `[0.05, 1.0]`), azimuth **0° = front / +azimuth = left (CCW)**, FOA ACN/SN3D formulas, motion sample primary=`at_tick`, stem-set fingerprint = sorted joined `sha256_prefix`, **no JS FOA twin**, motion never mutates note ticks / `Transport.bpm`; Task 2 adds `identity_digest` reuse + refuse codes (`scene_embeds_events` / `harmony` / `pcm`); Task 4 depends on compile; Tasks 5–6 lock `expected_document_revision` CAS + soft-stale vs **request** fingerprints; Task 6 requires `composition` when any `track` source and stem-set metadata when any `stem` source + `enforce_current`; new Task 7 preset catalog (no auto-INSERT on tab open); Task 8 audible path = `Panner3D` or API-compiled stereo (no FOA twin) + Transport-synced stems; Task 9 wires `ComposerWorkspace` + `PLAYBACK_SOURCE_KIND_SPATIAL` + draft `compile`; Task 10 adds unsaved-draft / catalog / no-write acceptance
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: introduce a durable spatial scene sidecar separate from Composition and from `mix.plan.v1` so tracks/stems can be positioned for immersive preview without mutating source notes or stem WAVs. Deterministic compile + Web Audio stereo spatial preview ship first; Ambisonic-compatible FOA coefficients are inspectable/exportable where practical. Never invent `composition.v5`. Never silently rewrite pitch, harmony, or stem PCM. Predecessor: expressive mixer (`Tone.Panner`), neural stem sets, mix-plan (2D ops only), performance conductor sidecar pattern (already implemented — orthogonal; do not stack in ship-1).

## Roadmap Linkage
Milestone: "V5 Spatial music scene and preview"
Rationale: Browser playback already has stereo pan/trim, and neural stems plus `mix.plan.v1` cover 2D mix revisions, but there is no project-scoped spatial scene that positions multiple sources for synchronized immersive preview without mutating `composition.v2` or stem bytes. The incomplete conductor milestone is a different feature; linkage is enabled so this plan appends a dedicated spatial milestone. Implementation does not edit `ROADMAP.md`.

## Goal

Allow tracks and/or completed neural stems to be positioned in a spatial scene and heard in a synchronized stereo (and Ambisonic-compatible) preview without changing canonical notes or source stem WAVs.

Ship:

1. Versioned non-playable document **`spatial.scene.v1`** (`extra=forbid`) holding per-source **SpatialMix** metadata: azimuth, elevation, distance, spread, optional motion/automation keyframes.
2. Session compile **`spatial.preview.v1`**: deterministic stereo L/R gains (or pan+distance attenuation) plus first-order Ambisonic (FOA, ACN/SN3D) coefficients per source — never a second playable score and never PCM in the document.
3. Pure **deterministic spatial compiler** (backend) for validation, soft-stale, and Ambisonic/stereo coefficient parity tests.
4. Closed **catalog presets** (`front_stereo`, `circle_ensemble`, `close_intimate`) — clone into SQLite rows; never auto-INSERT on tab open.
5. **Web Audio preview engine** (frontend) that applies SpatialMix onto Tone track graphs and/or decoded stem buffers with one shared transport clock.
6. Durable SQLite storage (multiple scenes per project), CAS via `expected_document_revision`, composition fingerprint pin and optional stem-set fingerprint pin, soft-stale banners against **request** fingerprints.
7. Thin Spatial tab: position multiple sources, hear synchronized preview, clear on project switch.
8. Tests proving one fixture multi-source scene yields distinguishable stereo/FOA digests while composition identity and stem sha256 stay unchanged; docs for contracts and honesty limits.

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

**Terminology lock:** Product generation is **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**. A **Spatial Scene** is `spatial.scene.v1` — durable derived layout, not notes. **SpatialMix** means the closed per-source position metadata (`azimuth`, `elevation`, `distance`, `spread`, optional `motion`). A **spatial preview** is `spatial.preview.v1` — session compiled gains/coefficients; consumers may ignore it. **Stereo spatial preview** means equal-power / distance-attenuated L/R (or `PannerNode` / `Panner3D` HRTF when available) over headphones/speakers. **Ambisonic-compatible** means ship-1 FOA (W,Y,Z,X in ACN order, SN3D normalization) coefficient vectors — not a certified Atmos/ADM authoring chain, not higher-order Ambisonics, not object beds. **Source** is either a Composition `track_id` (symbolic Tone path) or a completed neural `stem_id` binding (`stem_set_id` required on the scene when any stem source exists) — never an embedded event array. **`mix.plan.v1`** remains the 2D AI-assisted mix revision document; this plan does not widen it into 3D. **Ephemeral Tone mixer** (`panOffset` / trim) stays orthogonal session UI; Spatial audition uses mixer scope `preview`. **Preset catalog** is the static three named seeds; a **cloned scene** is a SQLite row. Predecessor: `.ai-factory/plans/v5-ai-performance-conductor-layer.md`, `docs/ai-assisted-mixing.md`, `docs/browser-playback.md`, neural stem sets. Performance conductor stacking (performed ticks into a spatialized graph) is **out of scope** for ship-1.

## Approach Evaluation (locked)

### Part A — Where SpatialMix lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Embed azimuth/elevation on `tracks[]` inside `composition.v2`** | Single document | Mutates canonical score; breaks export/tokenizer assumptions; couples immersive layout to notes | **Reject** |
| **B. Widen `mix.plan.v1` ops with 3D parameters and Apply→new mix WAV** | Reuses mix assist | Conflates 2D mastering helper with interactive scene; Apply rewrites mix PCM; wrong for live symbolic preview | **Reject** |
| **C. Session-only frontend state with no durable document** | Fast demo | No restart-safe scenes; no backend parity tests; no project sharing | **Reject** for acceptance |
| **D. Sidecar `spatial.scene.v1` + session `spatial.preview.v1`; Composition and stem WAVs stay read-only** | Matches performance-plan / adaptive-score style; multi-scene; clear soft-stale | New schema, migration, API, Web Audio wire | **Accepted** |

### Part B — Preview compute locus

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Backend-only PCM bounce of spatial mix for every audition** | Server-truth audio | Latency; duplicates mix-plan DSP; wrong for interactive reposition | **Reject** as primary |
| **B. Frontend-only math with no backend compile** | Snappy UI | Dual drift vs tests; Ambisonic coeffs untestable in pytest | **Reject** alone |
| **C. Backend pure `compile_spatial_preview(scene) → spatial.preview.v1` (coeffs only); frontend Web Audio applies scene/preview for audible stereo; golden parity tests on coeffs** | Single coeff truth; interactive audition; no PCM in API | Needs JS apply helpers | **Accepted** |
| **D. Full JS twin of FOA encoder that may diverge from Python** | Offline | Drift risk | **Reject** as authority — JS may apply `Panner3D` / compiled stereo for audible path but FOA golden vectors come from backend compile only |

### Part C — Audible spatialization strategy (ship-1)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Equal-power stereo pan from azimuth only (ignore elevation/distance)** | Tiny | Fails distance/elevation acceptance feel | **Insufficient** |
| **B. `PannerNode` / Tone `Panner3D` with listener at origin; distance model + cone/spread mapping; fallback equal-power+distance when 3D unavailable** | Real Web Audio; immersive enough; no Atmos claim | Browser HRTF variance | **Accepted** for audible path |
| **C. Full higher-order Ambisonic decode + custom binaural** | “True” Ambisonics | Out of scope / workstation creep | **Defer** |
| **D. Claim Dolby Atmos / ADM / bed+objects** | Marketing | Explicitly out of goal | **Reject** |

### Part D — Ambisonic representation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Skip Ambisonics entirely** | Faster | Misses “Ambisonic-compatible where practical” | **Reject** |
| **B. Persist PCM Ambisonic WAV on every save** | Audible B-format | Large; mutates asset root; CI heavy | **Defer** |
| **C. Deterministic FOA ACN/SN3D per-source (or summed) coefficient vectors on `spatial.preview.v1` + optional inspect UI; no PCM required for acceptance** | Testable; portable; honest “compatible” | Not a full decoder product | **Accepted** |
| **D. HOA order ≥ 3** | Richer | Complexity / claim creep | **Defer** |

### Part E — Source binding

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Tracks only (Tone symbolic)** | Simple | Ignores “rendered stems” acceptance | **Insufficient** |
| **B. Stems only (neural WAVs)** | Matches game audio demos | Breaks when no stem set; ignores symbolic preview | **Insufficient** |
| **C. Closed `source_kind`: `track` \| `stem`; bind `track_id` or **required** `stem_id` + scene-level `stem_set_id`; optional `stem_role` is display/hint only; missing/incomplete stem → soft warning, skip that source in audible path** | Meets acceptance; flexible | Validation rules needed | **Accepted** |
| **D. Embed PCM or note events in scene body** | Self-contained | Forbidden; secret/logging risk | **Reject** |

### Part F — Motion / automation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Freeform JS expressions** | Flexible | Untestable; logging risk | **Reject** |
| **B. Optional closed keyframe list per source: `{ tick, azimuth, elevation, distance, spread }` with linear interpolation at compile/preview; hard caps on count; optional `time_seconds` only when tick absent** | Validatable; deterministic | Limited curves | **Accepted** for ship-1 |
| **C. Full DAW automation lane UI** | Familiar | Out of thin-UI budget | **Reject** |

### Part G — Product surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Schema + store + HTTP + compile + Web Audio preview + thin Spatial tab + tests + docs** | Meets acceptance | No Atmos workstation | **Accepted** |
| **B. Also bake spatial into default SMF / FluidSynth export** | DAW hears positions | SMF has no standard 3D; would invent claim | **Reject** for ship-1 |
| **C. Wire into `ai_agents/` as required stage** | Agent visibility | Couples agents to spatial; violates sidecar isolation | **Reject** — `ai_agents/` must not import spatial modules |
| **D. Auto-INSERT a default scene on project create / tab open** | Instant UI | Silent writes | **Reject** — catalog clone only |

### Part H — Relationship to mix.plan and Tone mixer

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Spatial Apply writes a new `mix.plan` revision WAV** | Reuses egress | Wrong layer for interactive scene; conflates features | **Reject** for ship-1 |
| **B. Spatial audition is session-only; never writes stem/mix WAV; never writes working Tone mixer into Composition** | Clear honesty | No durable PCM product | **Accepted** |
| **C. Overload working `panOffset` as azimuth storage** | Reuses UI | Loses elevation/distance/spread; pollutes working mixer | **Reject** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Canonical notes | `composition.v2` `tracks[].events[]` | Read-only identity; Tone schedule unchanged except spatial gain/pan nodes |
| Identity digest | `performance_identity.identity_digest` (pitch/harmony/event ids) | **Reuse** for spatial immutability asserts — do not invent a divergent algorithm |
| Browser playback | `tonePlaybackEngine.js` uses `Tone.Panner` (stereo); track graph gain→panner→master | Extend with optional `Panner3D` / spatial bus when source kind is spatial; keep mechanical path |
| Mixer controls | `playbackMixerControls.js` ephemeral `panOffset` / trim | Orthogonal; spatial audition must not persist into working controls |
| Playback source | `playbackSource.js` already has `PLAYBACK_SOURCE_KIND_PERFORMANCE` → scope `preview` | Add `PLAYBACK_SOURCE_KIND_SPATIAL` → same `preview` scope pattern |
| Neural stems | `/neural-audio/stem-sets`, `/neural-audio/stems/{id}/audio`, `sha256_prefix` | Bind `stem_id` + `stem_set_id`; decode AudioBuffer for spatial stem players |
| Mix plan | `mix.plan.v1` gain/pan/EQ Apply→new mix WAV | **Do not extend**; document orthogonality |
| Sidecar CAS | `performance_plans` / adaptive_scores: `body_json` + `document_revision` + `expected_document_revision` CAS | Clone for `spatial_scenes` |
| Fingerprints | `composition_snapshot_fingerprint` | **Locked** soft-stale pin for composition (not a different algorithm) |
| Stem honesty | stem `sha256_prefix` on neural/mix paths | Stem-set fingerprint = sorted joined prefixes of bound stems |
| Collaboration | `enforce_current` on performance-plan routes | Same guard on spatial-scene routes |
| Workspace tabs | `ComposerWorkspace.jsx` `TABS` (includes `performance`) | Add `{ id: 'spatial', label: 'Spatial' }` |
| Secret guard | `persistence_secret_guard` | Before INSERT/UPDATE |
| Alembic head | `20261003_0026` (`performance_plans`) | Next revision `20261003_0027` |
| Performance conductor | **Implemented** (`performance.plan.v1` / realize / Performance tab) | Orthogonal expressive timing; spatial must not import conductor modules in ship-1 |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| `spatial.scene.v1` schema | No SpatialMix document; must refuse embedded events/PCM |
| `spatial.preview.v1` | No stereo/FOA compile DTO |
| Deterministic spatial compiler | Azimuth/elevation/distance/spread → L/R + FOA; concrete caps |
| Preset seed tables + catalog API | front_stereo / circle_ensemble / close_intimate; no auto-INSERT |
| Web Audio spatial apply | Panner3D / compiled-stereo fallback; stem buffer sync on Transport |
| SQLite `spatial_scenes` + Alembic | CAS `expected_document_revision` |
| HTTP CRUD + compile | Composition **required** when any track source; stem-set metadata **required** when any stem source |
| Playback source `spatial` | Missing in `playbackSource.js` |
| Thin Spatial tab UI | `ComposerWorkspace` + panel |
| Motion keyframe interp | Missing |
| Identity / stem-immutability + unsaved-draft tests | Missing |
| Docs `docs/spatial-music.md` | Missing |

### Coupling risks to avoid

1. Inventing `composition.v5` or storing note `events[]` / pitches on the scene.
2. Rewriting stem WAVs, neural mix WAVs, or `mix.plan` revisions from Spatial Apply (there is no PCM Apply in ship-1).
3. Widening `mix.plan.v1` into a 3D Atmos document.
4. Claiming Dolby Atmos, ADM, HOA, or certified binaural quality.
5. Making LLM / AI required for positioning or preview.
6. `ai_agents/` importing spatial store/compiler.
7. Logging full scene bodies, PCM, or composition event arrays at INFO.
8. Writing `DATASET_ROOT` or treating scenes as training corpus.
9. Editing `ROADMAP.md` during implementation.
10. Auto-INSERT of a default scene on tab open or project create.
11. Polluting working Tone mixer controls with SpatialMix fields.
12. Dual FOA math that drifts between Python and JS (no JS FOA twin).
13. Requiring a completed stem set when the user only wants track spatialization (or vice versa).
14. Silent mutation of `Tone.Transport.bpm` or note start ticks for spatial motion (motion moves **position**, not musical time).
15. Confusing Spatial “spread” with mix-plan stereo widen ops.
16. Soft-stale against saved project JSON while compiling an unsaved draft body.
17. Role-only stem binds without `stem_id` (ambiguous when multiple stems share a role).
18. Stacking performance-conductor realizations into spatial preview in ship-1.

## Scope And Decisions

### In scope
- `spatial.scene.v1` + `spatial.preview.v1` Pydantic (+ frontend scene-body validation for UI edits — not an FOA twin).
- SpatialMix fields with Task 1 numeric caps; optional motion keyframes.
- Deterministic compile → stereo coefficients + FOA ACN/SN3D vectors.
- Three named presets as a **catalog**; clone creates SQLite rows; opening the Spatial tab never writes.
- Web Audio / Tone synchronized preview for multiple `track` and/or `stem` sources.
- SQLite `spatial_scenes` (project-scoped, many rows, CAS `expected_document_revision`).
- HTTP: list/create/get/put/delete, preset catalog, `POST .../compile` — composition required when any track source; stem-set metadata required when any stem source.
- Soft-stale when scene pins ≠ fingerprints of **request** composition / stem-set metadata.
- Thin Spatial tab in `ComposerWorkspace.jsx`: catalog clone, scene select, SpatialMix controls, stale banner.
- Tests: schema, compiler distinguishability, identity invariant, unsaved draft, store CAS, API, catalog, apply helpers, playback source.
- Docs: `docs/spatial-music.md` + short links from `docs/browser-playback.md`, `docs/ai-assisted-mixing.md`, `docs/neural-audio-rendering.md`, `AGENTS.md` entry points.
- `.env.example`: `SPATIAL_PREVIEW_MAX_SOURCES` / `SPATIAL_PREVIEW_MAX_MOTION_KEYFRAMES` documenting Task 1 caps (optional override only if settings module exposes them; defaults match constants).

### Out of scope
- Dolby Atmos / ADM / bed+object workstation.
- Higher-order Ambisonics (HOA ≥ 2) decode product.
- Baking spatial into default SMF / MusicXML / FluidSynth WAV export.
- PCM Ambisonic file egress (may be a later opt-in).
- Merging into `mix.plan.v1` Apply path.
- Full 3D scene viewport / game-engine integration.
- Adaptive Score runtime driving SpatialMix (future).
- Performance conductor stacking (future; do not block).
- Editing `ROADMAP.md` during implementation.
- Marketplace of scene presets beyond the three catalog seeds.

### Architecture decisions (locked)

**1. Document `spatial.scene.v1`**

| Field | Rule |
|-------|------|
| `schema_version` | `"spatial.scene.v1"` |
| `id` | uuid string |
| `project_id` | owning project |
| `name` | display |
| `listener` | optional `{ position: [0,0,0], yaw_deg }` — ship-1 default origin; listener faces **+Z** (front) |
| `sources` | list of SpatialMix sources (`MAX_SOURCES=32`) |
| `source_composition_fingerprint` | pin at last bind/save; algorithm = `composition_snapshot_fingerprint` |
| `source_stem_set_id` / `source_stem_set_fingerprint` | required on the scene when any source has `source_kind=stem` |
| `engine_version` | string constant for compile math |
| `extra` | forbid |

Scene body **must not** contain `events`, note pitches, `harmony`, or PCM/base64 audio — validation refuses with stable reason codes (`scene_embeds_events`, `scene_embeds_harmony`, `scene_embeds_pcm`).

**SpatialMix source object:**

| Key | Meaning |
|-----|---------|
| `id` | stable source row id within scene |
| `source_kind` | `track` \| `stem` |
| `track_id` | required when `track` |
| `stem_id` | **required** when `stem` (role alone refused) |
| `stem_role` | optional display/hint only |
| `azimuth_deg` | −180..180; **0° = front; positive = left (CCW)** |
| `elevation_deg` | −90..90 |
| `distance` | 0..`D_MAX` (100); 0 uses `D_REF` clamp |
| `spread` | 0..1 (0 = point; 1 = max allowed width mapping) |
| `gain` | optional 0..1 linear trim (session; does not write Composition volume) |
| `motion` | optional keyframes (`MAX_MOTION_KEYFRAMES=64` per source); empty/omit = static |
| `muted` | optional bool |

**2. Preview `spatial.preview.v1`**

Session DTO (not stored in ship-1):

```text
{
  schema_version: "spatial.preview.v1",
  scene_id, scene_revision, engine_version,
  source_composition_fingerprint,
  source_stem_set_fingerprint?,
  stale: { composition: bool, stem_set: bool },
  listener: {...},
  sources: [{
    source_id, source_kind, track_id?, stem_id?,
    stereo: { left_gain, right_gain },
    foa: { w, y, z, x },
    distance_gain,
    spread,
  }],
  metrics: { source_count, mean_distance, stereo_imbalance, foa_energy, ... }
}
```

Rules:
- No PCM fields.
- Every `track` source must resolve on the **request** composition; unresolved → stable warning/skip code, not silent invent.
- Every `stem` source must resolve on the bound stem set; incomplete stems → skip with warning.
- Compile is pure and seed-free; motion sampled at `at_tick` (default 0); `at_seconds` only when tick not provided.
- Motion changes SpatialMix position only — never note `start_tick` / Transport BPM.
- Soft-stale compares scene pins to fingerprints of the **request** composition / stem-set metadata, not silently to a different saved project row.

**3. Task 1 freezes (later tasks must not invent alternate math)**

| Topic | Lock |
|-------|------|
| Coordinate convention | Right-handed; listener at origin facing +Z; azimuth **0° = front; +azimuth = left (CCW)**; elevation + = up |
| Caps | `MAX_SOURCES=32`; `MAX_MOTION_KEYFRAMES=64`; `D_REF=1.0`; `D_MAX=100` |
| Distance model | `distance_gain = clamp(D_REF / max(distance, D_REF), 0.05, 1.0)` |
| Spread mapping | `spread` widens stereo toward center-fill (`left/right` move toward equal) and reduces FOA directional energy toward W; no PCM convolution reverb |
| Stereo path | Equal-power from azimuth: `left = cos((az+90°)*π/180/…)` style Task-1 formula × elevation mild attenuate × `distance_gain` × spread bias — freeze exact formula in `spatial_constants.py` |
| FOA | First-order only; ACN order **W,Y,Z,X**; SN3D: `W=1/√2`, `Y=sin(az)cos(el)`, `Z=sin(el)`, `X=cos(az)cos(el)` (az/el in radians after convention); then × `distance_gain` × source `gain` |
| Stem fingerprint | `source_stem_set_fingerprint = sha256(":".join(sorted(sha256_prefix for each bound stem)))[:64]` (or full hex — freeze length in Task 1) |
| Composition fingerprint | `composition_snapshot_fingerprint` only |
| Motion sample | Primary key = `tick`; linear interp between surrounding keyframes; `at_seconds` only if no tick; never changes musical time |
| Audible browser path | Prefer `Panner3D` when available from scene SpatialMix; else apply **API-compiled** `stereo.left_gain/right_gain` + distance on `Tone.Panner` / gain |
| FOA authority | Python compile only — **no JS FOA encoder twin** |
| Identity | Reuse `performance_identity.identity_digest` (or thin re-export) — canonical pitch/harmony/event ids only |
| Stem bind | `stem_id` required; `stem_role` alone → validation refuse |
| Export | Default SMF/WAV unchanged |

**4. Optional seed scenes (catalog)**

| Preset | Intent |
|--------|--------|
| `front_stereo` | Melody L, accompaniment R-ish, low distance |
| `circle_ensemble` | Tracks/roles evenly spaced on azimuth ring |
| `close_intimate` | Small distances, mild spread |

Catalog is readable without SQLite writes. Clone/create-from-preset creates one row. Opening Spatial tab never writes. Same scene template + different presets must produce **distinguishable** compile metric digests (Task 10 asserts).

**5. Identity / immutability postconditions**

After compile and after spatial audition:

```text
identity_digest(composition) unchanged
stem sha256 / sha256_prefix for each bound stem unchanged
projects.composition_json unchanged
```

Spatial preview must not be written back into Composition or stem files. Compiling with a draft body that differs from the saved project must not write the project row.

**6. Storage row**

Table `spatial_scenes`:

- `id`, `project_id` FK CASCADE, `name`, `body_json`, `schema_version`, `document_revision`, `source_composition_fingerprint`, `source_stem_set_id` nullable, `source_stem_set_fingerprint` nullable, `created_at`, `updated_at`
- PUT requires `expected_document_revision`; conflict → 409
- Secret guard on body
- Project delete GC via FK

**7. HTTP surface** (under `/projects/{project_id}/spatial-scenes`)

| Method | Behavior |
|--------|----------|
| `GET /` | list summaries (may be empty) |
| `GET /presets` | static catalog (no DB write) |
| `POST /` | create (empty / from preset / custom body) |
| `GET /{id}` | get |
| `PUT /{id}` | CAS replace (`expected_document_revision`) |
| `DELETE /{id}` | delete |
| `POST /{id}/compile` | returns `spatial.preview.v1`; **requires `composition` when any track source**; **requires stem-set id + fingerprint/metadata when any stem source**; accepts `at_tick` (default 0); soft-stale flags vs request fingerprints |

All routes: `enforce_current`. No composition write routes. No stem rewrite routes. No PCM upload on this router.

**8. Playback / preview wire**

- Extend `playbackSource.js` with `PLAYBACK_SOURCE_KIND_SPATIAL`; mixer scope **`preview`**.
- Audition calls `POST .../compile` with current `editedMusicJson` (unsaved draft) + stem metadata as needed, then apply helpers.
- When active: Tone track nodes use spatial apply helper; stem sources schedule decoded buffers on the **same** `Tone.Transport` origin.
- Switching to working restores non-spatial graph.
- Clear spatial audition on project switch.
- Do not persist SpatialMix into Composition or working mixer.

**9. Logging**

| Level | What |
|-------|------|
| DEBUG | source counts by kind, engine_version, distance clamps, motion sample tick, soft-stale, fallback reason codes |
| INFO | create/compile with `{scene_id, source_count, track_count, stem_count, stale, metrics.summary}` |
| WARN | soft-stale, unresolved source skip, CAS conflict, embed refuse |
| ERROR | unexpected compile/apply failures (sanitized) |

Never: full scene JSON dumps, PCM, composition event arrays, FOA coeff arrays, or API keys at INFO (DEBUG may log capped coeff summaries).

## Commit Plan
- **Commit 1** (after tasks 1–2): `docs(spatial): lock spatial scene and preview contracts`
- **Commit 2** (after tasks 3–4): `feat(spatial): add deterministic spatial compiler and presets`
- **Commit 3** (after tasks 5–7): `feat(spatial): persist scenes, HTTP compile, and preset catalog`
- **Commit 4** (after tasks 8–10): `feat(spatial): Web Audio preview, Spatial tab, and acceptance tests`
- **Commit 5** (after task 11): `docs(spatial): spatial music guide and AGENTS entry points`

## Tasks

### Phase 1: Contracts and safety invariants
- [x] Task 1: Freeze in `docs/spatial-music.md` (stub ok) + `spatial_constants.py`: **`MAX_SOURCES=32`**, **`MAX_MOTION_KEYFRAMES=64`**, **`D_REF=1.0`**, **`D_MAX=100`**, distance_gain clamp **`[0.05, 1.0]`**, azimuth **0° = front / +azimuth = left (CCW)**, listener faces +Z, exact stereo equal-power + FOA ACN/SN3D formulas (`W=1/√2`, `Y=sin(az)cos(el)`, `Z=sin(el)`, `X=cos(az)cos(el)`), `engine_version`, stem-set fingerprint = sorted joined `sha256_prefix`, composition fingerprint = `composition_snapshot_fingerprint`, motion sample primary=`at_tick`, **no JS FOA twin**, motion never mutates note ticks / `Transport.bpm`, `stem_id` required for stem sources, orthogonality vs `mix.plan.v1` / Tone working mixer / performance conductor, no Atmos claims, default export unchanged, compile authority = Python.

  LOGGING: n/a for docs; constants module may DEBUG log `engine_version` only in tests.

  Files: `docs/spatial-music.md`, `backend/app/spatial_constants.py` (new), optional `frontend/src/utils/spatialMusic/constants.js`

- [x] Task 2: Add Pydantic models for `spatial.scene.v1` and `spatial.preview.v1` (`extra=forbid`), validators (caps, source_kind rules, **refuse** `scene_embeds_events` / `scene_embeds_harmony` / `scene_embeds_pcm`), and pure metric helpers. **Reuse `performance_identity.identity_digest`** (or thin re-export module) for composition identity postconditions — do not invent a divergent digest. Frontend scene-body validators for UI edits (**not** an FOA compile twin). Unit tests for accept/reject fixtures including embed refuse + stem_id-required.

  LOGGING: validation failures DEBUG with reason codes only.

  Files: `backend/app/spatial_schemas.py` (new), `backend/tests/test_spatial_schemas.py` (new), `frontend/src/utils/spatialMusic/sceneValidation.js` (new), optional thin `backend/app/services/spatial_identity.py` re-exporting identity_digest

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Deterministic compiler and presets
- [x] Task 3: Implement pure `compile_spatial_preview(scene, *, at_tick=0, at_seconds=None) -> spatial.preview.v1` covering static + linear motion sampling, stereo coeffs, FOA coeffs, distance_gain, spread within Task 1 formulas. Postcondition: when a composition is provided for identity checks in tests, `identity_digest` unchanged. No FastAPI/SQLite/LLM/PCM imports. Golden vector tests for front/left/right/rear and motion midpoint. (depends on 1, 2)

  LOGGING: DEBUG per-source summary counts only; INFO not used inside pure module (caller logs).

  Files: `backend/app/services/spatial_compiler.py` (new), `backend/tests/test_spatial_compiler.py` (new)

- [x] Task 4: Encode catalog presets (`front_stereo`, `circle_ensemble`, `close_intimate`) as data + `clone_preset(preset_id) -> scene` helper. **Distinguishability asserts must call `compile_spatial_preview`** from Task 3 (pairwise metric digests differ). (depends on 1, 3)

  LOGGING: DEBUG preset clone `{preset_id}`.

  Files: `backend/app/fixtures/spatial_presets.v1.json` (new) or constants, `backend/app/services/spatial_presets.py` (new), `backend/tests/test_spatial_presets.py` (new)

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: Persistence, HTTP, preset catalog
- [x] Task 5: Alembic migration `20261003_0027` creating `spatial_scenes` with FK CASCADE, `document_revision`, composition + optional stem-set fingerprint columns. Store service: list/create/get/put/delete + secret guard + **CAS on `expected_document_revision`** (409 on conflict). Soft-stale helpers compare pins to **provided** fingerprints. Project-delete via FK. Service tests with temp DB. (depends on 2)

  LOGGING: INFO create/update/delete with ids + revision; WARN CAS conflict; never body_json dumps.

  Files: `backend/app/db/alembic/versions/20261003_0027_spatial_scenes.py` (new), `backend/app/services/spatial_scene_store.py` (new), `backend/tests/test_spatial_scene_store.py` (new)

- [x] Task 6: Router `spatial_scenes.py`: CRUD + `compile`. **`enforce_current` on every route.** Compile: **require `composition` when any source is `track`**; **require `stem_set_id` + stem fingerprint/metadata when any source is `stem`**; soft-stale = scene pins vs `composition_snapshot_fingerprint(request.composition)` and request stem-set fingerprint. PUT CAS via `expected_document_revision`. Register router in `main.py`. Document caps in `.env.example` if a thin `spatial_settings.py` exposes overrides (defaults = Task 1 constants). API tests with fixture composition (+ fake stem metadata when needed). (depends on 3, 4, 5)

  LOGGING: INFO compile `{scene_id, source_count, stale, duration_ms}`; WARN soft-stale / missing required body; never preview coeff arrays at INFO.

  Files: `backend/app/routers/spatial_scenes.py` (new), `backend/app/services/spatial_scene_service.py` (new), optional `backend/app/spatial_settings.py` (new), `backend/tests/test_spatial_scenes_api.py` (new), `backend/app/main.py`, `.env.example`

- [x] Task 7: Preset **catalog** without SQLite writes: `GET .../spatial-scenes/presets` listing the three seeds; `POST` create-from-preset clones one row. Opening the Spatial tab / listing scenes **never** auto-creates rows. Tests: empty list on fresh project; clone increases count by one; catalog GET is side-effect free. (depends on 4, 5, 6)

  LOGGING: DEBUG catalog serve `{preset_count}`; INFO clone `{preset_id, scene_id}`.

  Files: extend `backend/app/routers/spatial_scenes.py`, `backend/app/services/spatial_presets.py`, `backend/tests/test_spatial_scenes_api.py`

<!-- Commit checkpoint: tasks 5-7 -->

### Phase 4: Web Audio / Tone synchronized preview + UI
- [x] Task 8: Frontend spatial apply helpers: map scene SpatialMix → `Panner3D` when available; else apply **API-compiled** `stereo.left_gain/right_gain` + distance on `Tone.Panner` / gain. **No JS FOA encoder.** Stem path fetches `/neural-audio/stems/{id}/audio`, decodes buffer, schedules on shared `Tone.Transport` origin (`Transport.schedule` / start at transport 0); teardown on source clear; fallback reason codes. Unit tests with mocked audio nodes where practical. (depends on 2, 3)

  LOGGING: frontend DEBUG/INFO via `createAppLogger('spatialMusic')` with source counts and fallback reason codes only — never buffers or FOA arrays.

  Files: `frontend/src/utils/spatialMusic/spatialApply.js` (new), `frontend/src/utils/spatialMusic/stemSpatialPlayer.js` (new), `frontend/src/utils/spatialMusic/*.test.js` (new), `frontend/src/utils/tonePlaybackEngine.js` (minimal hook points)

- [x] Task 9: Frontend: `spatialSceneApi.js`, thin `SpatialScenePanel.jsx`, wire `{ id: 'spatial', label: 'Spatial' }` in `ComposerWorkspace.jsx` `TABS`. Catalog clone buttons + scene list (empty-safe). Extend `playbackSource.js` with `PLAYBACK_SOURCE_KIND_SPATIAL` (mixer scope `preview`). Audition **calls `POST .../compile`** with current `editedMusicJson` (+ stem metadata when needed), then apply helpers without mutating pitches/harmony/stem bytes. Soft-stale banners. Clear spatial audition on project switch. **Opening the tab never INSERT.** Unit tests for source resolution + apply helpers. (depends on 6, 7, 8)

  LOGGING: DEBUG audition toggles; INFO `{sceneId, sourceCount}`; WARN stale; never dump preview coeff arrays.

  Files: `frontend/src/api/spatialSceneApi.js` (new), `frontend/src/components/SpatialScenePanel.jsx` (new), `frontend/src/components/ComposerWorkspace.jsx`, `frontend/src/utils/playbackSource.js`, `frontend/src/utils/playbackSource.test.js`, `frontend/src/utils/spatialMusic/*`, `frontend/src/store/musicStore.js` (thin slice), colocated `*.test.js`

<!-- Commit checkpoint: tasks 8-9 -->

### Phase 5: Acceptance tests and documentation
- [x] Task 10: Acceptance tests: (1) multi-source scene + presets → distinguishable compile metric digests via `compile_spatial_preview`; (2) `identity_digest` invariant; (3) stem `sha256_prefix` unchanged when stem sources present (fake/tiny WAV); (4) synchronized preview schedule does not rewrite V2 events; (5) CAS conflict 409; (6) embed refuse; (7) **catalog GET side-effect free** / tab-open creates zero rows; (8) saving a scene does not change `projects.composition_json`; (9) **compile with request composition ≠ saved project JSON still does not write the project row**; (10) soft-stale when request fingerprint ≠ scene pin; (11) track-only compile without stem-set succeeds; stem-only without composition succeeds; mixed missing required body → 422. (depends on 3, 4, 6, 7, 8, 9)

  LOGGING: tests assert reason codes / metrics only.

  Files: `backend/tests/test_spatial_acceptance.py` (new), frontend unit tests under `spatialMusic/` / `playbackSource.test.js`, extend API/store tests as needed

- [x] Task 11: Finalize `docs/spatial-music.md` — architecture diagram, Task 1 freezes, presets/catalog clone, compile API (composition/stem requirements), soft-stale vs **request** fingerprints, Web Audio apply (no FOA twin), logging redaction, orthogonality to `mix.plan.v1` / Tone working mixer / performance conductor, honesty limits (no Atmos); link from `docs/browser-playback.md`, `docs/ai-assisted-mixing.md`, `docs/neural-audio-rendering.md`; update `AGENTS.md` / DESCRIPTION bullet if structure warrants. Mandatory `/aif-docs` checkpoint after implement. (depends on 1–10)

  LOGGING: n/a.

  Files: `docs/spatial-music.md`, `docs/browser-playback.md`, `docs/ai-assisted-mixing.md`, `docs/neural-audio-rendering.md`, `README.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md` (short bullet)

<!-- Commit checkpoint: tasks 10-11 -->

## Acceptance Criteria

1. User can position multiple tracks and/or completed stems in a `spatial.scene.v1` and hear a synchronized stereo spatial preview.
2. Compile/audition never changes event ids, pitches, `harmony[]`, `projects.composition_json`, or stem WAV bytes (including when the request composition differs from the saved project JSON).
3. `spatial.preview.v1` exposes deterministic stereo gains and FOA ACN/SN3D coefficients; no PCM in the document.
4. Preset catalog is readable without writes; opening the Spatial tab does not auto-create scenes.
5. Soft-stale banners compare scene pins to **request** composition / stem-set fingerprints.
6. Not presented as a Dolby Atmos / ADM workstation.
7. Automated tests cover schema, compiler (incl. motion midpoint), identity invariant, store CAS, API, catalog, unsaved-draft no-write, and audition source helpers.

## Acceptance Criteria Mapping

| Criterion | Tasks |
|-----------|-------|
| Position multiple stems/tracks in a spatial scene | 2, 5, 6, 7, 9 |
| Hear synchronized preview | 8, 9, 10 |
| Without changing source notes/stems | 3, 10 (identity + sha + no-write asserts) |
| SpatialMix azimuth/elevation/distance/spread + optional motion | 1, 2, 3, 9 |
| Stereo spatial preview | 3, 8 |
| Ambisonic-compatible representation where practical | 1, 3 (FOA ACN/SN3D on preview) |
| Not a Dolby Atmos workstation | 1, 11 (docs honesty) |
| Integrate Web Audio preview + rendered stems | 8, 9 |
| Preserve canonical Composition | 2, 6, 10 |
| Tests + documentation | 10, 11 |

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer new `spatial_*` modules + thin wires into playback/store; do not grow unrelated logic in `main.py` beyond router include.
3. Never invent `composition.v5`; never treat preview as playable without source Composition/stems.
4. Do not widen `mix.plan.v1` or overload working `panOffset` as SpatialMix storage.
5. `ai_agents/` must not import spatial modules.
6. Keep default MIDI/MusicXML/WAV export unchanged.
7. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
8. Verbose logging with redaction; no scene/PCM/FOA dumps at INFO.
9. Caps and freezes from Task 1 are locked — later tasks must not invent alternate FOA formulas, a JS FOA twin, Transport.bpm motion, or role-only stem binds.
10. Do not stack performance-conductor realizations into spatial preview in this milestone.
11. Soft-stale and compile always key off **request** fingerprints (Analysis-style draft body), not a silent load of a different saved composition.
