# Implementation Plan: V4 Full Audio-to-Symbolic Recovery Workflow

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-23
Improved: 2026-09-23 (`/aif-improve` — lock Apply→bind asset protocol + `project_id`; project-delete FS GC; overlay prune/`user_edited` lifecycle; `ensureRecoveryRoleTracks` (no jam import); Demucs→product stem map; `run_inline` jobs; HTMLAudio vs Tone Transport; narrow Task 9; phase exclusion; fix parent plan paths + task deps; defer `/ai/models` soft-readiness)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: extend shipped **V3 monophonic** `transcription.preview.v1` into a **V4 mixed-audio recovery workflow** — optional source separation into stems, per-stem/combined estimation of tempo / beat grid / structure / key / harmony, practical polyphonic transcription where confidence supports it, durable related project assets (original audio + recovery result with confidence), side-by-side audio vs transcription vs piano roll/notation, user correction of uncertain material **without fabricating notes**, and **optional** heavy Docker/model sidecars — **without** promising perfect transcription, **without** putting `confidence` on playable `composition.v2` note events (`extra="forbid"`), **without** inventing playable notes from harmony alone, and **without** writing to `DATASET_ROOT`
- Parent plans / shipped foundations:
  - `.ai-factory/plans/audio-to-symbolic-musical-input.md` (**shipped** — mono mic/file → session preview → Apply; confidence off V2; audio deleted; `docs/audio-transcription.md`)
  - `.ai-factory/plans/optional-neural-audio-rendering.md` (**shipped** — SQLite metadata + filesystem audio root pattern; optional Compose profile; never mutates V2)
  - `.ai-factory/plans/midi-musicxml-import-composition-v2.md` (**shipped** — multipart limits, content sniff, session reports; no retained source bytes)
  - `.ai-factory/plans/deterministic-composition-v2-analysis.md` (**shipped** — `composition.analysis.v1` sidecar over V2; not playable)
  - `.ai-factory/plans/interactive-harmony-reharmonization.md` (**shipped** — V2 `harmony[]` tick spans; Apply fingerprint gate)
  - `.ai-factory/plans/expressive-v2-playback-and-mixing.md` (**shipped** — Tone Transport; browser playback)
  - `.ai-factory/plans/v4-ai-jam-realtime-co-composition.md` (**adjacent V4** — confidence/hysteresis *live MIDI* analysis; **not** a dependency for audio recovery hot path; reuse only terminology discipline for confidence vs belief-like holds — **do not** import `ensureJamRoleTracks` / jam modules)
  - `.ai-factory/plans/v4-multi-agent-music-architecture.md` (**shipped** — never `composition.v4`; agents above runtime)

## Roadmap Linkage
Milestone: "V4 full audio-to-symbolic recovery workflow" *(proposed — all current ROADMAP items are checked, including mono audio transcription and AI Jam; `/aif-implement` docs checkpoint or `/aif-roadmap` must add this unchecked milestone)*
Rationale: V3 delivers monophonic hum→preview→Apply with ephemeral audio; V4 is the product recovery path for **mixed musical recordings** into an editable project with durable audio+symbolic assets and honest confidence — a distinct milestone from mono transcription and from neural *egress* rendering.

## Goal

Allow a user to **import a short musical demo recording**, optionally separate stems, estimate musical scaffolding (tempo, beat grid, structure, key, harmony), recover **editable symbolic tracks** where confidence supports transcription (including practical polyphony), keep **original audio and recovery metadata as related project assets**, review/correct uncertain material side-by-side with audio and piano roll/notation, then continue editing with existing V2 tools — **without claiming perfect transcription**.

```text
Mixed audio upload (WAV preferred; bounded)
        │
Optional separation sidecar / fake stems
  vocals|melody · bass · drums · harmonic · other
        │
Recovery pipeline (job; run_inline like neural-audio)
  ├─ global: tempo, beat grid, structure sections
  ├─ global/per-stem: key, harmony spans (metadata only)
  └─ per-stem: note events + confidence (mono reuse + optional poly)
        │
audio.recovery.preview.v1  (session review — uncertain stays uncertain)
        │
User correct / exclude / remappable stems → tracks
        │
Apply (client V2 transaction) → Bind (POST …/bind + project_id)
  ├─ composition.v2 tracks[].events[]  (NO confidence fields)
  ├─ optional harmony[] + sections metadata
  └─ durable assets: source audio + audio.recovery.result.v1 overlay
        │
Side-by-side: HTMLAudio source · recovery overlay · piano roll / OSMD
  (Tone Transport / playbackSource unchanged for composition audition)
```

**Acceptance one-liner:** User imports a short fixture demo → obtains an editable project with estimated tempo, structure, harmony, and transcribed symbolic tracks showing confidence indicators; low-confidence material is visible/excludable, not silently invented; original audio remains playable beside the score.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Audio recovery** | V4 workflow from mixed audio → estimated scaffolding + symbolic candidates; product name for the feature (not MIDI import, not neural render egress) |
| **V3 mono transcription** | Shipped `POST /transcription/audio` → `transcription.preview.v1` → Apply; remains supported for simple melodies |
| **Recovery preview** | Session-only `audio.recovery.preview.v1` (or versioned successor) — notes/stems/scaffolding + confidence; non-playable |
| **Recovery result asset** | Durable `audio.recovery.result.v1` related asset — confidence/stem linkage keyed by `event_id` after Bind; **not** a second score |
| **Source audio asset** | Durable project-related WAV (or accepted decode) under `AUDIO_RECOVERY_ASSET_ROOT`; metadata in SQLite; never inside `composition_json` / snapshots |
| **Stem** | Separated or virtual role channel: `vocals` \| `melody` \| `bass` \| `drums` \| `harmonic` \| `other` |
| **Separation** | Optional model/sidecar producing stems; may be skipped when mono/simple or engine unavailable |
| **Scaffolding** | Estimated tempo, beat grid, structure sections, key, harmony spans — drives alignment; harmony/sections are V2 **metadata** only |
| **Confidence overlay** | Per-detected-note (and optional span) confidence living on preview/result assets — never on `CompositionV2NoteEvent` |
| **Uncertain material** | Below threshold or engine-omitted content: excluded by default, marked in UI, **never fabricated** to fill gaps |
| **Recovery job** | Job status like neural renders: queued → running → complete \| failed; **v1 runs inline** (`run_inline=True`, single-worker; no Celery) |
| **Bind** | `POST /audio-recovery/jobs/{id}/bind` — persists source audio + result overlay after client V2 Apply; requires `project_id` + preview fingerprint + `provisional_id→event_id` map |
| **Side-by-side review** | Simultaneous original-audio audition (`HTMLAudioElement` / Web Audio) + recovery overlay + piano roll and/or OSMD from applied or provisional ticks — **not** a new `playbackSource` / Tone Transport owner |

Reuse V3 terms (`AUDIO_*` mono limits, provisional_id, include-low-confidence) where they still apply; introduce `AUDIO_RECOVERY_*` for V4 limits rather than overloading mono settings blindly.

## Non-goals (v1)

- Perfect transcription / mastering-grade stem isolation claims
- Cloud vendor ASR/music APIs as the default path
- Writing `confidence` (or stem ids) onto playable `composition.v2` note events
- Inventing playable notes from estimated `harmony[]` alone
- Replacing FluidSynth export or Tone.js with neural audio
- Auto-ingest into `DATASET_ROOT` / training corpora
- Full DAW take lanes / punch recording / stem DAW mixer product
- Real-time streaming separation on the AI Jam / co-performance hot path
- Routing recovery through LLM `generate` / hybrid symbolic composer / multi-agent spine as the note engine
- Inventing `composition.v4`
- Lyrics / speech-to-text as the melody source
- Unlimited duration uploads (keep hard caps; demo-length first)
- Baking Demucs/Basic-Pitch-poly weights into the default Mukit image
- External job queue / multi-worker Celery (v1 = neural-audio `run_inline` pattern only)
- Mandatory soft-readiness / `GET /ai/models` discovery for recovery/separation sidecars (docs + fake mode suffice for AC; follow-on okay)
- Importing `ensureJamRoleTracks` / `liveJam*` into recovery Apply

## Approach Evaluation (locked)

### Part A — Extend V3 vs greenfield ingress

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace mono endpoint with recovery-only** | One path | Breaks shipped mono UX/CI; overkill for hums | **Reject** |
| **B. Parallel recovery API + keep mono** | Clear product split; reuse engines/apply helpers | Two panels to maintain | **Accepted** |
| **C. Only client-side WASM poly** | Privacy | Weak CI; large FE; retention/asset hard | **Reject** as sole path |

**Locked:** Keep `POST /transcription/audio` + `AudioInputPanel` for mono. Add recovery routes/UI (`/audio-recovery/*`) that **reuse** decode limits, fake engines, tick alignment, and Apply helpers. Docs distinguish mono vs recovery.

### Part B — Where confidence lives after Apply

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Extend V2 note `extra` / optional confidence** | Simple UI | Violates `extra="forbid"`; pollutes export/MIDI; revision bloat | **Reject** |
| **B. Session-only forever (V3)** | Safe | Fails req #5–6 durability + side-by-side after reload | **Reject** for V4 AC |
| **C. Related asset overlay keyed by `event_id`** | Honest; V2 stays clean; reloadable | Needs asset store + sync on edit/delete | **Accepted** |

**Locked:** Playable notes remain strict V2. Confidence (and stem provenance) lives on `audio.recovery.result.v1` mapping `event_id → {confidence, stem, provisional_id?, status}`. Piano-roll confidence styling reads the overlay. Edits that delete notes prune overlay entries; pitch/time changes on recovered ids mark `user_edited`; new user-drawn notes have no recovery confidence (or `status: user`).

### Part C — Persist original audio

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep V3 delete-always** | Simple | Fails req #6–7 | **Reject** for recovery path |
| **B. SQLite BLOB in projects row** | One file | DB bloat; secret-guard risk; bad for large WAV | **Reject** |
| **C. Filesystem root + SQLite metadata (neural-audio pattern)** | Proven; quotaable; path confinement | New Alembic + GC | **Accepted** |

**Locked:** Mirror `neural_audio_renders`: tables for jobs/assets + files under `AUDIO_RECOVERY_ASSET_ROOT` (default beside `PROJECT_DB_PATH`, **never** `DATASET_ROOT`). Durable source audio + result JSON only after successful **Bind** (requires `project_id`). Ephemeral job workdirs until Bind or cancel/GC. Quotas: max assets/project, max bytes, max duration. Wire `cleanup_project_audio_recovery` into `project_store.delete_project` beside `cleanup_project_neural_audio` (SQLite CASCADE alone is insufficient for filesystem orphans).

### Part D — Source separation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always separate** | Uniform stems | Heavy; fails on mono/acapella wastefully | **Reject** |
| **B. Policy: skip when mono-enough / user opts out; else optional sidecar** | Matches “where appropriate” | Heuristic tuning | **Accepted** |
| **C. Manual stem upload only** | No models | Misses AC for mixed demos | **Insufficient alone** |

**Locked:** Separation is **optional**. Heuristic/issue codes: `separation_skipped_mono`, `separation_unavailable`, `separation_partial`. Fake engine returns deterministic stem role partitions from fixture digests for CI. Real path: HTTP sidecar (Demucs-shaped) via Compose profile — FastAPI never imports torch/Demucs. When separation unavailable, run **combined** analysis + combined transcription with reduced confidence and clear warnings — do not invent stem tracks.

**Product stem enum (closed v1):** `vocals`, `melody`, `bass`, `drums`, `harmonic`, `other`.

**Sidecar → product stem map (locked):** Demucs-style `vocals/bass/drums/other` maps to product stems — `vocals` → `vocals` and optionally `melody` (policy: derive melody destination from vocals when no separate lead stem; issue `melody_derived_from_vocals`), `bass` → `bass`, `drums` → `drums`, `other` → `harmonic` and/or `other` with `separation_partial` when ambiguous. Never invent empty stem WAVs to satisfy the enum.

### Part E — Scaffolding estimation (tempo / beat / structure / key / harmony)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Call full `POST /analysis/composition` on empty V2** | Reuse | Wrong input (needs notes first); circular | **Reject** as sole path |
| **B. Audio-native estimators → provisional scaffolding DTO → optional V2 metadata on Apply** | Matches AC | New estimators | **Accepted** |
| **C. LLM listens to audio** | Fancy | Non-local; unstable; not CI | **Reject** default |

**Locked:** Deterministic / local estimators (fake + optional libs) emit scaffolding inside the recovery preview:
- `tempo_bpm` + confidence + source
- `beat_grid` (downbeat offset, PPQ alignment hints)
- `structure[]` (section labels + tick ranges + confidence)
- `key` (tonic/mode + confidence)
- `harmony[]` candidates (symbol + tick span + confidence) — **metadata only**

On Apply: user may install tempo/meter into V2 timeline, sections into `sections[]`, harmony into `harmony[]` — never audible from spans alone. After notes exist, optional **advisory** `composition.analysis.v1` remains available as today (not a substitute for recovery scaffolding).

### Part F — Polyphonic transcription

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always force poly on every stem** | Ambitious | Noise on vocals/drums; slow | **Reject** |
| **B. Per-stem policy: poly where practical (harmonic/other); mono engines for melody/vocals; drums as onset/percussion events or skip notes** | Honest | Drum symbolic limited | **Accepted** |
| **C. Defer all poly to later milestone** | Smaller | Fails req #4 / AC | **Reject** |

**Locked:** Reuse V3 mono engines for `vocals`/`melody`. Add optional poly engine adapter (`fake:audio-poly`, optional `basic_pitch` poly mode or sidecar) for `harmonic`/`other` when available. Drums: v1 may emit sparse onset events with low default confidence **or** mark `drums_transcription_deferred` — do not invent pitched kits. Overlaps allowed only when engine returns multi-pitch with per-note confidence; never fill missing voices with guessed chord tones.

### Part G — Sync vs async jobs

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Sync only (V3 style)** | Simple | Separation+poly exceeds request timeouts | **Insufficient** |
| **B. Always async external queue** | Uniform | Overkill; new infra | **Reject** for v1 |
| **C. Job API + `run_inline` (neural-audio pattern); fake/short may complete before response returns** | Practical; proven | Single-worker assumption | **Accepted** |

**Locked:** `POST /audio-recovery/jobs` creates job and **runs inline** by default (`run_inline=True`, optional thread only if nested event-loop — mirror `neural_audio_render`). Poll `GET /audio-recovery/jobs/{id}` for UI consistency; cancel/delete with file GC. No Celery/RQ in v1. Pattern after neural-audio jobs, not after LLM generate.

### Part H — Side-by-side + correction UX

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Apply-only then edit piano roll** | Reuses editor | Loses audio sync / pre-apply correction | **Insufficient** |
| **B. Recovery workspace: HTMLAudio scrub synced to tick grid + provisional overlay + editable provisional table/piano-roll ghost notes** | Meets #7–9 | More FE | **Accepted** |
| **C. New playbackSource / own Tone Transport** | Unified transport | Fights working/preview audition ownership | **Reject** |

**Locked:** Sibling panel to `AudioInputPanel`: recovery review with `HTMLAudioElement` (or Web Audio) for source (and optional stem solo), tick mapping from scaffolding tempo/grid — **do not** register a new `playbackSource` or schedule Tone from recovery scrub. Composition audition stays on existing PlaybackControls / `playbackSource`. Corrections: include/exclude, pitch nudge, split/merge provisional notes, stem remaps — **before** Apply. Post-Bind: confidence overlay + “Re-open recovery result”; user piano-roll edits are canonical (overlay marks `user_edited` / prune on delete).

### Part I — Apply then Bind (asset durability)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Persist audio on job create** | Simple | Orphans without project; disk leak on Discard | **Reject** |
| **B. Client-only localStorage audio** | No BE | Fails reload/project AC | **Reject** |
| **C. Client V2 Apply → `POST …/bind` with project_id + id map** | Undo-friendly V2; durable assets gated | Two steps | **Accepted** |

**Locked sequence:**
1. User reviews preview (session).
2. Client `commitCompositionTransaction` (`action: 'audio-recovery-apply'`) writes V2 notes/metadata only (strip confidence) via `ensureRecoveryRoleTracks` — **not** `ensureJamRoleTracks`.
3. Client `POST /audio-recovery/jobs/{id}/bind` with `{ project_id, preview_fingerprint, event_map: [{ provisional_id, event_id, track_id }], install_flags… }`.
4. BE validates fingerprint/job complete, writes source WAV + `audio.recovery.result.v1` under project segment, returns asset ids.
5. Discard/cancel without Bind → GC job temps; **no** durable audio.

Reject Bind without `project_id` (`audio_recovery_project_required`). Failed Bind after V2 Apply leaves editable notes without overlay (WARN UX: “Confidence overlay not saved”) — do not auto-rollback V2.

## Audit Summary (current state)

### What already exists (reuse — do not rebuild)

| Area | Path | Recovery reuse |
|------|------|----------------|
| Mono preview contract | `audio_transcription_schemas.py` (`transcription.preview.v1`) | Keep; do not overload — new recovery schemas |
| Mono settings / sniff | `audio_transcription_settings.py` | Share format sniff helpers; add `AUDIO_RECOVERY_*` |
| Engines + alignment | `services/audio_transcription/` | Reuse mono engines + seconds→ticks; extend registry for poly adapters |
| Mono HTTP | `routers/transcription.py` | Leave intact |
| FE capture / apply | `audioRecorder.js`, `audioTranscriptionApply.js`, `AudioInputPanel.jsx` | Reuse WAV capture & take-apply pattern; new recovery panel/store |
| Piano-roll provisional overlay | `PianoRollOverlayLayer.jsx` | Extend for multi-stem + post-Bind overlay |
| Filesystem audio + SQLite meta | `neural_audio_render_store.py`, Alembic `20260921_0002` | **Template** for recovery assets (ingress, not egress) |
| Project-delete FS cleanup | `project_store.delete_project` → `cleanup_project_neural_audio` | Mirror with `cleanup_project_audio_recovery` |
| Inline jobs | `neural_audio_render.enqueue_*` (`run_inline`) | Same single-worker pattern |
| Ensure-track pattern | `liveJamEnsureTracks.js` | **Pattern only** → new `audioRecoveryEnsureTracks.js` |
| Optional Compose sidecar | `compose.neural-audio.yml`, `compose.local-ai.yml` | New `compose.audio-recovery.yml` profile |
| Harmony spans / sections | V2 schema + Harmony tab | Apply estimated spans as metadata |
| Analysis sidecar | `composition.analysis.v1` | Post-Apply advisory only |
| Fake CI patterns | `AUDIO_FAKE_MODE`, `NEURAL_AUDIO_FAKE_MODE` | `AUDIO_RECOVERY_FAKE_MODE` + fixture digests |
| Phase exclusion | midi ↔ live guards in `musicStore.js` | Extend for `recoveryPhase` vs midi/live/mono record |
| Persistence secret guard | `persistence_secret_guard.py` | Ensure assets/metadata never store API keys |
| Docs / testing | `docs/audio-transcription.md`, `docs/testing.md` | New `docs/audio-recovery.md`; keep mono doc honest |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| Recovery schemas (`preview` / `result` / job / bind DTOs) | Stems, scaffolding, confidence overlay, issue codes, bind body |
| `AUDIO_RECOVERY_*` settings + quotas | Duration/bytes/jobs/assets; independent of mono where needed |
| Separation adapter + Compose profile + licenses | Fake stems; Demucs→product map; optional HTTP sidecar |
| Audio scaffolding estimators | Tempo/beat/structure/key/harmony with confidence |
| Poly engine path | Practical poly for harmonic stems; drums policy |
| Job orchestration (`run_inline`) + asset store + Alembic | Source audio + result JSON; GC; path confinement |
| Project-delete FS cleanup hook | Beside neural-audio cleanup |
| `ensureRecoveryRoleTracks` + Apply + Bind | Role→track map; strip confidence; `project_id` required on bind |
| Overlay prune / `user_edited` on editor ops | event_id map stays honest after delete/edit |
| Side-by-side recovery UI (HTMLAudio) | Audio scrub + overlay + correction; no Tone takeover |
| Phase exclusion | vs midi capture / live running / mono recording |
| Mixed fixture audio + tests + licensing docs | Short demo WAV(s); model license table |
| ROADMAP milestone + AGENTS/DESCRIPTION | Proposed V4 milestone |

### Coupling risks to avoid

1. Putting confidence/stem fields on `CompositionV2NoteEvent` (breaks `extra="forbid"` / export).
2. Treating recovery as MIDI import or neural *render* egress (wrong direction).
3. Persisting PCM inside SQLite or revision snapshots.
4. Writing under `DATASET_ROOT` or auto-corpus ingest.
5. Silently fabricating notes/chords for gaps or failed stems.
6. Importing Demucs/torch into FastAPI process (sidecar only).
7. Blocking default `docker compose up` on heavy models.
8. Breaking V3 mono path / `AUDIO_FAKE_MODE` CI.
9. Logging PCM, full note arrays, or asset paths beyond basename/sha prefix at INFO.
10. Inventing playable notes from harmony metadata.
11. Calling LLM generate for pitch/onset.
12. Unbounded job retention / disk leak without GC (incl. project delete).
13. Requiring hardware mic for CI — file-upload fixtures only.
14. Wiring multi-agent spine as the transcription engine.
15. Promising “perfect” or “studio stem” quality in UI copy.
16. Persisting durable audio without `project_id` / Bind.
17. Importing `ensureJamRoleTracks` / jam hot-path modules into recovery.
18. Hijacking Tone Transport / `playbackSource` for source-audio scrub.
19. Assuming an external async worker queue in v1.

## Scope And Decisions

### In scope
- Mixed audio upload with safe limits and content sniff
- Optional source separation (fake + optional sidecar) with closed stem enum + sidecar map
- Scaffolding estimation: tempo, beat grid, structure, key, harmony (all with confidence)
- Per-stem / combined note transcription (mono reuse + practical poly) with per-note confidence
- Session recovery preview + user correction (exclude/edit/remap) without fabrication
- Client Apply → Bind → editable multi-track `composition.v2` + durable source audio + recovery result assets
- Overlay lifecycle (prune / `user_edited`) after Bind
- Side-by-side original audio (HTMLAudio) / transcription overlay / piano roll (+ OSMD from V2)
- Phase exclusion vs midi/live/mono record
- Optional Docker/model services; fake CI path; fixtures; tests; logging; licensing docs; ROADMAP milestone

### Out of scope (v1)
- See Non-goals above (incl. Celery, jam ensure-track import, mandatory `/ai/models` soft-readiness)

### Architecture decisions (locked)

**1. Contract stack**

```text
audio.recovery.job.v1          — job status envelope
audio.recovery.preview.v1      — session review document (may equal job result payload)
audio.recovery.result.v1       — durable overlay + scaffolding snapshot + asset refs
audio.recovery.stem.v1         — per-stem notes + engine + issues (embedded)
audio.recovery.bind.v1         — bind request/response (project_id, fingerprint, event_map)
```

Preview/result note items: `provisional_id`, `stem`, `pitch`, `start_tick`, `duration_ticks`, `velocity`, `confidence`, optional `polyphony_group_id`.  
Result overlay entries after Bind: `event_id`, `confidence`, `stem`, `status` (`recovered` \| `user_edited` \| `excluded` \| `user`).

**2. V2 remains sole playable score**

Apply writes only legal V2 fields. Overlay and assets are related, not alternate scores. No `composition.v4`.

**3. Separation policy**

| Condition | Behavior |
|-----------|----------|
| User disables separation | Combined path only |
| Energy/mono heuristic says mono-enough | Skip; issue `separation_skipped_mono`; reuse V3-like stream |
| Sidecar healthy | Separate → map stems → per-stem transcribe |
| Sidecar down / fake off | Combined + `separation_unavailable`; degrade confidence; still return scaffolding if estimators work |
| Fake mode | Deterministic stems from fixture digest |

**4. Uncertainty policy (req #9)**

- Default Apply selection excludes notes below `AUDIO_RECOVERY_CONFIDENCE_INCLUDE_THRESHOLD` (default 0.5, tunable).
- Omitted/low-energy/failed stems → issues, empty note lists — **never** pad with scale tones.
- Harmony spans below threshold are not written to V2 unless user opts in (“Include low-confidence harmony”).
- UI copy: “Estimated / low confidence” — never “detected with certainty” for sub-threshold items.

**5. Settings (illustrative defaults)**

| Env | Role |
|-----|------|
| `AUDIO_RECOVERY_MAX_UPLOAD_BYTES` | Cap (e.g. 25 MiB) |
| `AUDIO_RECOVERY_MAX_DURATION_SECONDS` | Demo-length (e.g. 90–120s) |
| `AUDIO_RECOVERY_ASSET_ROOT` | Beside project DB; never DATASET_ROOT |
| `AUDIO_RECOVERY_MAX_ASSETS_PER_PROJECT` | Soft quota |
| `AUDIO_RECOVERY_ENGINE` | `auto` \| fake \| local estimators \| sidecar ids |
| `AUDIO_RECOVERY_SEPARATION_ENGINE` | `off` \| `auto` \| `fake:stems` \| `sidecar:demucs` |
| `AUDIO_RECOVERY_FAKE_MODE` | CI |
| `AUDIO_RECOVERY_SIDECAR_BASE_URL` | HTTP only |
| `AUDIO_RECOVERY_CONFIDENCE_INCLUDE_THRESHOLD` | Default exclude gate |
| `AUDIO_RECOVERY_SYNC_MAX_SECONDS` | Hint for UI; jobs still `run_inline` |

**6. Licensing**

Document upstream licenses for any optional separation/transcription weights (e.g. Demucs, Basic Pitch) in `docs/audio-recovery.md` license table — same honesty bar as `docs/neural-audio-rendering.md`. Operators must accept licenses before downloading weights; default image stays free of those weights.

**7. Relationship to AI Jam**

AI Jam is live MIDI co-composition. Audio recovery is offline/import recovery. Do **not** couple Transport/horizon/predict. Do **not** import jam ensure-track helpers — copy the *pattern* into `ensureRecoveryRoleTracks`. Optional future: feed recovered V2 into Jam — out of this plan.

**8. Alembic**

New migration revises current head (`20260922_0004_composer_profiles` at plan time — verify head at implement). Tables: `audio_recovery_jobs`, `audio_recovery_assets` (names may consolidate).

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Mixed audio input | 1–3, 10 |
| Separation where appropriate | 4, 11 |
| Tempo / beat / structure / key / harmony estimates | 5, 8–9 |
| Notes where confidence supports; poly where practical | 6–7, 9 |
| Per-note confidence metadata | 2, 7–9, 12 |
| Preserve original audio + symbolic as related assets | 3, 8, 12 |
| Side-by-side audio / transcription / piano roll | 10–11 |
| User can correct transcription | 9–11 |
| Uncertain stays uncertain (no fabrication) | 5–7, 9, 13 |
| Heavy models optional Docker/services | 4, 11, 14 |
| Import short demo → editable project with scaffolding + confidence UI | 8–11, 13–14 |
| Fixture audio, tests, logging, licensing docs | 12–14 |
| Overlay stays honest after edit/delete | 8–9, 11, 13 |
| Project delete cleans FS assets | 3, 13 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(audio-recovery): settings, schemas, asset/job store, and project-delete GC`
- **Commit 2** (tasks 4–6): `feat(audio-recovery): separation adapters, scaffolding estimators, poly engines`
- **Commit 3** (tasks 7–9): `feat(audio-recovery): pipeline, Apply+Bind, overlay lifecycle, correction helpers`
- **Commit 4** (tasks 10–11): `feat(audio-recovery): store slice, phase guards, side-by-side review UI`
- **Commit 5** (tasks 12–14): `feat(audio-recovery): fixtures, tests, Compose profile, docs, roadmap`

## Tasks

### Phase 1: Contracts, assets, job shell

- [x] Task 1: `AUDIO_RECOVERY_*` settings + format/quota policy
  Deliverable: New `audio_recovery_settings.py` (do not silently overload all mono knobs). Env-backed upload/duration/sample-rate caps, asset root resolution (default under project-db parent / `audio_recovery_assets`), per-project asset/job quotas, confidence threshold, engine enums (`AUDIO_RECOVERY_ENGINE`, `AUDIO_RECOVERY_SEPARATION_ENGINE`), `AUDIO_RECOVERY_FAKE_MODE`, sidecar URL, sync-max hint. Reuse content-signature helpers from mono settings via shared module or import — keep `AUDIO_*` mono defaults unchanged. Wire `.env.example` + compose comments.
  LOGGING: INFO resolved scalar limits + asset_root basename only; WARN clamps; never log full paths with user filenames at INFO.
  Files: `backend/app/audio_recovery_settings.py` (new), optional shared `audio_format_policy.py`, `.env.example`, `docker-compose.yml` env pass-through comments.

- [x] Task 2: Recovery schemas + error/issue code registries
  Deliverable: Pydantic DTOs for `audio.recovery.preview.v1`, embedded stems, scaffolding (tempo/beat/structure/key/harmony with confidence), job envelope, durable `audio.recovery.result.v1` overlay entries, **`audio.recovery.bind.v1`** request/response (`project_id`, `preview_fingerprint`, `event_map[]`), HTTP error codes (reuse audio_* where identical; add `audio_recovery_*` including `audio_recovery_project_required`, `audio_recovery_bind_fingerprint_mismatch`). `extra=forbid` on nested models. Explicit docs: non-playable; confidence never on V2 events.
  LOGGING: validation failures via existing helpers without dumping note arrays.
  Files: `backend/app/audio_recovery_schemas.py` (new), `backend/tests/test_audio_recovery_schemas.py`.

- [x] Task 3: Alembic + asset/job store shell + project-delete FS GC (depends on 1)
  Deliverable: Migration (revise current Alembic head) adding metadata tables (e.g. `audio_recovery_jobs`, `audio_recovery_assets`) — filesystem holds WAV + result JSON; SQLite holds ids, project_id, status, sha256_prefix, byte_size, relpaths, engine ids, error codes. Path confinement helper (reject escapes). Soft FK/CASCADE like neural renders. GC helpers: delete job temps; **`cleanup_project_audio_recovery(project_id)`** deleting FS tree under asset root; wire into `project_store.delete_project` beside `cleanup_project_neural_audio` (WARN on cleanup failure, do not fail delete). Quota enforcement. **No** PCM in SQLite; **no** writes into `composition_snapshots`. Durable writes only via Bind path (Task 8).
  LOGGING: INFO job_id / status transitions / byte_size / sha prefix; WARN cleanup failures; ERROR path confinement; never PCM.
  Files: `backend/app/db/alembic/versions/*_audio_recovery_*.py`, `backend/app/services/audio_recovery_store.py` (new), `project_store.py` cleanup hook, store unit tests with tmp roots.

### Phase 2: Separation, scaffolding, transcription engines

- [x] Task 4: Separation adapter (fake + optional sidecar) (depends on 1–2)
  Deliverable: Protocol + registry. `fake:stems` maps fixture digests to deterministic stem WAV snippets or virtual role partitions without claiming quality. `sidecar:demucs` (stable id) — health probe, bounded timeout, stem download/write under job work dir; apply **Demucs→product stem map** (Part D) with issue codes. Unavailable → structured `separation_unavailable` (not crash). Mono-enough heuristic emits `separation_skipped_mono`. Compose profile file `compose.audio-recovery.yml` with weights bind-mount under `models/audio-recovery/` gitignored + `.gitkeep`. FastAPI talks HTTP only.
  LOGGING: INFO separation engine selected / skipped reason / mapped stem keys; WARN sidecar unhealthy; DEBUG stem counts; never log audio bytes.
  Files: `backend/app/services/audio_recovery/separation.py` (new package layout), `compose.audio-recovery.yml`, `models/audio-recovery/.gitkeep`, requirements optional extras doc only.

- [x] Task 5: Scaffolding estimators (tempo / beat / structure / key / harmony) (depends on 2)
  Deliverable: Pure-ish estimators over PCM or stem PCM: tempo+confidence, beat grid offset, coarse structure sections, key, harmony span candidates. Fake estimators return golden scaffolding for fixtures. Optional local DSP extras when installed; else degrade with issue codes — **do not** invent high-confidence harmony. Outputs nest under recovery preview scaffolding object.
  LOGGING: INFO tempo_source / key_confidence scalars; WARN low-confidence scaffolding; DEBUG section counts only.
  Files: `backend/app/services/audio_recovery/scaffolding.py`, unit tests with fixed vectors / fixtures.

- [x] Task 6: Per-stem transcription (mono reuse + practical poly) (depends on 2, 5 for tempo alignment inputs)
  Deliverable: Orchestrate stem→notes: call existing mono engines for melody/vocals; poly adapter (`fake:audio-poly` + optional real) for harmonic/other; drums policy (`drums_transcription_deferred` or sparse onsets with low confidence). Per-note confidence required. Combined path when no stems. Never pad missing polyphony with chord-tone invention; emit issues instead.
  LOGGING: INFO per-stem note_count / low_confidence_count / engine_id; WARN poly collapsed / deferred drums; ERROR engine failures sanitized.
  Files: extend `services/audio_transcription/engines.py` or `services/audio_recovery/transcribe.py`; tests for fake poly + mono reuse.

### Phase 3: Pipeline, Apply, Bind, overlay

- [x] Task 7: Recovery pipeline orchestration + HTTP API (depends on 3, 4, 5, 6)
  Deliverable: Service that runs separation→scaffolding→transcribe inside job workdir; **`run_inline=True` by default** (neural-audio pattern; optional single-thread executor only if nested loop — no Celery). Routes: `POST /audio-recovery/jobs` (multipart), `GET /audio-recovery/jobs/{id}`, `DELETE` cancel/GC, asset GET after Bind. Map domain errors to HTTP. Register router; do not grow `main.py` with logic. Fake/short jobs may return `status: complete` from POST.
  LOGGING: INFO duration_ms, stages completed, stem list, note totals; never filenames beyond basename; never full preview JSON at INFO.
  Files: `backend/app/routers/audio_recovery.py`, `backend/app/services/audio_recovery/pipeline.py`, API tests with fake mode.

- [x] Task 8: Apply → Bind → durable assets + overlay lifecycle (depends on 2, 3, 7)
  Deliverable:
  - Pure `ensureRecoveryRoleTracks(composition, stemRoleMap, instrumentSet, { ensure_missing_tracks })` in `frontend/src/utils/audioRecoveryEnsureTracks.js` — valid V2 tracks only; **do not** import jam modules; map product stems → supported V2 `track.role`.
  - Client Apply: selected notes → events **without** confidence; optional tempo/sections/harmony toggles; one undoable `commitCompositionTransaction` (`action: 'audio-recovery-apply'`).
  - Client then `POST /audio-recovery/jobs/{id}/bind` with `project_id` (required), preview fingerprint, `provisional_id→event_id` map; BE persists source audio + `audio.recovery.result.v1`; reject without project.
  - Overlay lifecycle helpers used by store/editor: prune entries when recovered `event_id` deleted; mark `user_edited` on pitch/time change for recovered ids; new drawn notes get no overlay (or `status: user`).
  - Discard without Bind → no durable audio; failed Bind after Apply → WARN, leave V2 notes.
  LOGGING: FE INFO applied/excluded/stems/asset ids; WARN bind failure codes; BE INFO asset write scalars / bind rejects.
  Files: `audioRecoveryEnsureTracks.js`, `audioRecoveryApply.js`, `audioRecoveryOverlay.js` (new), bind route in `routers/audio_recovery.py`, store actions, tests.

- [x] Task 9: Correction + uncertainty gate helpers (pure) (depends on 2, 8)
  Deliverable: Pure helpers only (no panel UI): default selection excluding sub-threshold notes; include/exclude toggles; provisional pitch/time nudge; stem remap; “include low-confidence harmony/sections” flags; no-fabrication invariants (empty stems / failed poly → issues + zero invented notes). Overlay status helpers (`user_edited` / prune) shared with Task 8. UI wiring is Task 11.
  LOGGING: DEBUG correction op types/counts; INFO gate threshold; WARN empty Apply selection attempts.
  Files: FE utils (e.g. `audioRecoveryGates.js`); BE preview validation helpers if needed; unit tests for no-fabrication + gate defaults.

### Phase 4: Side-by-side UX

- [x] Task 10: Recovery store slice + API client + phase exclusion (depends on 7, 8)
  Deliverable: Ephemeral FE state: `recoveryPhase`, job status, preview, stem solo selection, scaffolding toggles, destination stem→track map, confidence threshold, error codes, linked asset ids after Bind. Clear on project switch. `musicApi` methods for jobs/bind/assets/audio blob URL. **Phase exclusion:** reject starting recovery upload/record while `midiPhase` is capturing, `livePhase` is running/degraded, or mono `audioPhase` is recording (and inverse guards as appropriate) — mirror midi↔live patterns. No recovery preview inside autosave composition payload.
  LOGGING: INFO phase/job transitions / exclusion rejects; WARN poll failures with codes only.
  Files: `musicStore.js` slice, `musicApi.js`, `musicStore.audioRecovery.test.js`.

- [x] Task 11: Side-by-side Recovery panel + overlays (depends on 8, 9, 10)
  Deliverable: `AudioRecoveryPanel.jsx`: upload mixed demo; separation status; scaffolding summary with confidence badges; stem list; provisional note table + piano-roll ghost overlay (multi-stem colors); **HTMLAudioElement / Web Audio** source (+ optional stem solo) with tick-synced playhead from scaffolding — **do not** add `playbackSource` or drive Tone Transport from scrub. Apply → Bind / Discard; post-Bind confidence styling from overlay; wire correction helpers from Task 9. Wire near existing Audio mono panel without removing it. OSMD reflects applied V2 only (provisional piano-roll-only in v1 if OSMD ghost is costly — document choice).
  LOGGING: UI actions via store namespaces `audioRecovery` / `audioRecoveryUi`; no console dumps of previews.
  Files: `AudioRecoveryPanel.jsx`, workspace integration, overlay extensions, styled-components consistent with AudioInputPanel.

### Phase 5: Fixtures, tests, docs, licensing

- [x] Task 12: Fixture mixed audio + golden fake recovery (depends on 4–6)
  Deliverable: Short mixed/synthetic WAVs under `backend/tests/fixtures/audio/recovery/` (e.g. melody+bass bed) with golden fake job preview JSON (tempo, structure, stems, confidences). Expand mono fixtures only if needed for regression. Document digest→fake mapping and Demucs→product map used by fake.
  LOGGING: Tests assert scalar log extras where useful; production paths remain scalar-only at INFO.
  Files: fixtures + golden JSON; fake engine digest table.

- [x] Task 13: Backend/frontend/e2e test suite (depends on 7–12)
  Deliverable: Pytest: settings clamps, schema forbid, store path confinement, project-delete FS cleanup, fake job happy path (`run_inline`), separation unavailable degradation, bind requires `project_id`, no-fabrication cases, overlay prune/`user_edited`. FE unit: phase exclusion, store gates, apply strips confidence, bind sequence, overlay prune. Optional Playwright: upload fixture → review confidence → Apply → Bind → see overlay + audio element. `AUDIO_RECOVERY_FAKE_MODE=1` in CI; never require sidecar.
  LOGGING: Quiet e2e; document manual log observation points.
  Files: `backend/tests/test_audio_recovery_*.py`, FE `*.test.js`, optional `frontend/e2e/audio-recovery.spec.js`, `docs/testing.md` snippets.

- [x] Task 14: Documentation, licensing, ROADMAP, AGENTS (depends on 1–13 for accuracy)
  Deliverable: New `docs/audio-recovery.md` — workflow diagram (Apply→Bind), honesty (“not perfect”), mono vs recovery, stems + sidecar map, scaffolding, confidence overlay vs V2, asset retention/GC/quotas/project-delete, `run_inline` jobs, HTMLAudio vs Tone, optional Compose profile, **model license table**, logging forbidden payloads, acceptance checklist. Optionally note that soft-readiness/`GET /ai/models` for recovery sidecars is a follow-on (not v1 AC). Update `docs/audio-transcription.md` “See also” + out-of-scope pointer to V4 recovery. Link README, testing, neural-audio (contrast egress vs ingress), import, AI runtime, browser-playback (Transport ownership). Add ROADMAP unchecked milestone. Update `AGENTS.md` + `.ai-factory/DESCRIPTION.md` one bullet each. Note adjacent AI Jam is unrelated hot path.
  LOGGING: Document namespaces `audio.recovery` / `audioRecovery` and INFO scalar policy.
  Files: docs + README + ROADMAP + AGENTS + DESCRIPTION + `.env.example` commentary.

## Implementation Notes For Agents

- Prefer `routers/audio_recovery.py` + `services/audio_recovery/` over growing `main.py` or overloading mono `routers/transcription.py` with mixed-job logic.
- Reuse mono decode/engines/alignment and neural-audio store/`run_inline`/path-confinement/project-delete cleanup patterns; do not clone divergent security policy.
- V2 `extra=forbid` is non-negotiable — confidence overlay asset + Bind is the designed escape hatch.
- Durable audio requires Bind + `project_id`; never persist on job create alone.
- `ensureRecoveryRoleTracks` only — never import `ensureJamRoleTracks`.
- Source audition = HTMLAudio; composition audition = existing PlaybackControls / Tone.
- Keep UI copy humble: estimated, confidence-gated, optional separation.
- Fake modes must exercise Apply → Bind + overlay + side-by-side for AC without GPUs.
- Related plans: `.ai-factory/plans/audio-to-symbolic-musical-input.md` (parent mono), `.ai-factory/plans/optional-neural-audio-rendering.md` (asset/`run_inline` pattern), `.ai-factory/plans/v4-ai-jam-realtime-co-composition.md` (adjacent V4 only).

## Out-of-Scope Reminders (do not implement in this plan)

- Perfect transcription guarantees
- Cloud vendor default engines
- Confidence fields on V2 note events
- Playable notes from harmony-only
- Demucs/torch in-process in FastAPI
- DATASET_ROOT ingest
- Real-time Jam/co-performance coupling
- Full drum kit pitched transcription quality
- External Celery/RQ workers
- Mandatory recovery soft-readiness / `/ai/models` discovery (follow-on)
- Importing jam ensure-track / live modules into recovery
- New `playbackSource` for recovery scrub
- `composition.v4`
