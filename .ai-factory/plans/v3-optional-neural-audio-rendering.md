# Implementation Plan: Optional Neural Audio Rendering

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship an **optional neural audio rendering path** for finished Composition V2 scores — job lifecycle, durable render metadata + audio files, unified `AiOperation.AUDIO_RENDER` runtime adapters, explicit MIDI/text adapters, clear deterministic-vs-generative labeling — **without** mutating canonical V2, revisions, Tone.js, MIDI/MusicXML, or FluidSynth WAV export

## Roadmap Linkage
Milestone: "Optional neural audio rendering"
Rationale: Deterministic FluidSynth WAV and browser Tone.js already exist; users still lack a higher-level generative/production-style audio path that stays a pure output layer. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "Audio-to-symbolic musical input (monophonic)" is already complete.

## Goal

Allow a **saved Composition revision** to produce one or more neural audio renders (queued → running → complete|failed) while leaving the source composition completely unchanged. Neural audio is a **rendering/output layer** only — never a second score, never autosaved into `composition_json`, never written into revision snapshots.

```text
Saved Composition V2 + source_revision_id
  + instrumentation / tempo / production instructions / optional genre·mood
  → explicit adapter (midi_projection | melody_conditioning | text_prompt)
  → AiOperation.AUDIO_RENDER model (fake | optional local sidecar | optional provider)
  → neural_audio_render job (queued → running → complete|failed)
  → audio file on NEURAL_AUDIO_RENDER_ROOT + SQLite metadata
  → UI: Render with AI / job list / download
  → V2 / revisions / FluidSynth / Tone.js / MIDI / MusicXML unchanged
```

## Model Evaluation (requirement 1)

| Model / stack | Input | Local? | License / redistribution | Note fidelity | Verdict for Mukit v1 |
|---------------|-------|--------|--------------------------|---------------|----------------------|
| **FluidSynth + GM SF2** (existing) | MIDI from V2 | Yes (core Docker) | FluidSynth LGPL; FluidR3_GM typically redistributable with attribution | **Deterministic note-faithful** | Keep as sole deterministic WAV path — **not neural** |
| **MIDI-DDSP** (Magenta) | MIDI (mono / 13 instruments) | Yes (TF; heavy) | Code Apache-2.0; check weight redistribution | **Neural instrument** — closer pitch/timing alignment; expression generative | **Optional engine** via explicit `midi_projection` adapter; label `neural_instrument` (not note-perfect guarantee) |
| **MusicGen** (Meta AudioCraft) | Text; optional **melody** conditioning | Yes (torch; heavy) or HTTP sidecar | Code MIT-style AudioCraft; model weights under Meta license — **document non-redistribution in default image** | **Generative** — melody conditioning approximates contour, not exact polyphony | **Primary generative engine** for v1 (sidecar preferred); fidelity class `generative` |
| **Stable Audio Open 1.0** | Text (≤~47s) | Yes (diffusers / stable-audio-tools) | Stability AI **Community License** — non-commercial / revenue caps; **do not bake weights into default Compose image** | **Generative** text-only | **Documented optional provider/sidecar**; not default; license gate in docs |
| **Riffusion / spectrogram diffusion** | Text / image | Local possible | Mixed | Generative; weak score coupling | **Reject** for v1 |
| **Cloud music APIs** (proprietary) | Varies | No | Vendor ToS | Generative | **Reject** as default; optional remote adapter only if HTTP OpenAI-compatible-style and clearly labeled remote |
| **NSynth / DDSP alone** | Audio / f0 | Local | Apache-ish | Instrument timbre, not full mix | Deferred (subset of MIDI-DDSP story) |

**Locked selection for v1:**

1. Always-on **`fake:neural-audio`** for CI / Playwright (deterministic short WAV fixture; no quality claim).
2. Optional **MusicGen sidecar** (`compose` profile `neural-audio`) as the default real generative backend — FastAPI talks HTTP only (mirror `LocalLanguageModel` / local-ai pattern).
3. Optional **MIDI-DDSP** (or thin MIDI→neural-synth) as second engine behind `midi_projection` when extras installed — better for “render this MIDI expressively” UX, still not FluidSynth-equivalent fidelity claims.
4. Stable Audio Open: docs + optional env pin only; operator accepts license; never auto-downloaded on `docker compose up`.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Deterministic WAV | `POST /export/wav` → `composition_wav.render_wav_with_report` (MIDI→FluidSynth) | Keep untouched; UI label **Deterministic WAV** |
| MIDI / MusicXML export | `POST /export/midi`, `/export/musicxml` | Unchanged |
| Tone.js preview | `tonePlaybackEngine.js` / mixer | Unchanged session projection |
| AI runtime stub | `AiOperation.AUDIO_RENDER`, `ModelCapability.AUDIO_GENERATION`, `AudioGenerationModel` protocol, `StubAudioGenerationModel`, `local:audio-generation-stub` | **Implement for real** — do not invent a parallel op id |
| Routing env | `AI_OP_AUDIO_RENDER`, `AI_FALLBACK_AUDIO_RENDER` | Wire real model ids |
| Optional heavy deps pattern | `requirements-music-transformer.txt`, `requirements-audio-transcription.txt`, Compose `--profile local-ai` / `training` | Same: optional extras + profile; core startup without weights |
| Fake CI pattern | `LLM_FAKE_MODE`, `AUDIO_FAKE_MODE`, `fake:audio-mono`, `fake:symbolic-tiny` | `NEURAL_AUDIO_FAKE_MODE` + `fake:neural-audio` |
| Project revisions | `project_revisions` + zlib `composition_snapshots` | **Pin** `source_revision_id` on render jobs; never rewrite snapshots |
| Export UI | `ExportControls.jsx` | Add sibling **Render with AI** control / panel — do not overload Export buttons |
| Temp dirs | WAV/MusicXML `TemporaryDirectory` | Use for work dirs during render; durable outputs go to render root |
| MIDI projection | `composition_midi.render_midi_with_report` | Feed `midi_projection` / melody adapters |
| Docs | `docs/ai-runtime.md` marks `audio_render` as stub | Expand + new `docs/neural-audio-rendering.md` |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No real `AudioGenerationModel` | Stub only raises unavailable |
| No render job state machine | First durable async job type in app |
| No render metadata store | No table for render id / model / prompt / revision / path |
| No durable audio blob root | Must not use `DATASET_ROOT` or snapshot BLOB table |
| No explicit adapters | Risk of pretending MusicGen is note-perfect |
| No UI labeling | Export WAV vs AI render must be visually distinct |
| No Compose neural-audio profile | Heavy images must stay optional |
| No license docs | MusicGen / Stable Audio / MIDI-DDSP redistribution rules |

### Coupling risks to avoid

1. Mutating `composition.v2`, autosave, or revision snapshots when a render completes.
2. Storing neural WAV inside `composition_snapshots.payload_zlib` or `projects.composition_json`.
3. Loading MusicGen/MIDI-DDSP weights inside the FastAPI web process by default (OOM / startup block).
4. Silent fallback from generative model to FluidSynth (or fake) without explicit fake mode / user pin.
5. UI copy implying “exact notes” / “same as Export WAV” for generative engines.
6. Replacing or gating Tone.js / FluidSynth behind neural deps.
7. Writing renders under `DATASET_ROOT` or training experiment dirs.
8. Logging full prompts at INFO, raw audio bytes, or full event arrays.
9. Blocking Playwright/CI when neural profile is absent — fake engine + 503 codes.
10. Conflating **audio transcription ingress** (`/transcription/audio`) with **neural render egress**.
11. Growing orchestration inside `ExportControls` without a dedicated panel/module.
12. Claiming Stable Audio Open is freely redistributable in product Docker images.

## Approach Evaluation (locked)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace FluidSynth with neural** | One button | Violates “neural is output layer”; breaks determinism/tests | **Reject** |
| **B. Sync HTTP only (like `/export/wav`)** | Simple | MusicGen minutes-long; timeouts; poor UX | **Reject** as sole path |
| **C. Job table + background worker + optional sidecar** | Matches long renders; multi-render; optional heavy stack | New persistence + polling | **Accepted** |
| **D. Browser-only neural WASM** | No server GPU | Huge bundle; weak CI; license/packaging pain | **Deferred** |
| **E. Cloud-only vendor API** | Quality | Privacy; not local-first; product LLM coupling | **Reject** as default; optional remote HTTP adapter later |

**Decision:** Implement **durable neural render jobs** under `PROJECT` locality: SQLite metadata + filesystem audio under `NEURAL_AUDIO_RENDER_ROOT`. FastAPI owns job API + fake engine in-process. Real generative models run in an **optional Compose sidecar** (HTTP) or optional in-process extra behind an explicit env pin. Adapters are first-class (`midi_projection` | `melody_conditioning` | `text_prompt`) with `fidelity_class` on every job/response. Deterministic exports remain separate buttons and code paths.

## Scope And Decisions

### In scope
- Model evaluation documented (above) + license/redistribution section in docs.
- Wire `AiOperation.AUDIO_RENDER` / `AudioGenerationModel` to real adapters + fake.
- Separate UI action **Render with AI** next to deterministic exports.
- Inputs: Composition V2 (from pinned revision or explicit body + revision id), instrumentation summary, tempo, textual production/render instructions, optional genre/mood context.
- Explicit adapters when model cannot consume MIDI/notes directly.
- Clear deterministic vs generative labeling in API + UI.
- Job states: `queued` | `running` | `failed` | `complete`.
- Output management: render ID, model/version, prompt, source composition revision, generated audio file.
- Multiple renders per composition/project.
- Optional — default app startup never loads heavy models.
- Docker profile + install instructions.
- License documentation.
- Tests with mocked rendering backend; verbose logging; docs; roadmap milestone.

### Out of scope
- Replacing FluidSynth, Tone.js, MIDI, or MusicXML.
- Real-time streaming neural audition in the transport (v1 = downloadable/job audio).
- Training MusicGen/MIDI-DDSP inside Mukit.
- Auto-ingesting renders into `DATASET_ROOT`.
- Polyphonic “mastering suite” / stem separation / vocal synthesis.
- Guaranteeing note-perfect generative audio.
- Changing V2 schema for audio attachments.
- Making neural render required for project save.

### Architecture decisions (locked)

**1. Neural audio is egress only**

```text
composition.v2 (authoritative score)
  → [deterministic] MIDI / MusicXML / FluidSynth WAV / Tone.js
  → [optional generative] neural_audio_render jobs + files
```

Never invent playable notes from neural audio. Never reverse-write audio into V2.

**2. Fidelity classes (API + UI enums)**

| `fidelity_class` | Meaning | UI label |
|------------------|---------|----------|
| `deterministic` | FluidSynth / symbolic export only | Deterministic |
| `neural_instrument` | MIDI-conditioned neural synth (approx notes) | Neural instrument (approximate notes) |
| `generative` | Text/melody generative model | Generative AI (not note-perfect) |

Every neural job stores `fidelity_class`. UI banners must include the not-note-perfect disclaimer for `generative` and approximate disclaimer for `neural_instrument`.

**3. Adapter kinds (`neural_audio.adapter.v1`)**

| Adapter | Transform | Typical engine |
|---------|-----------|----------------|
| `midi_projection` | V2 → MIDI bytes (existing renderer) → engine | MIDI-DDSP / MIDI-capable sidecar |
| `melody_conditioning` | V2 → monophonic melody guide (MIDI or short WAV) + text | MusicGen melody |
| `text_prompt` | Structured text from V2 metadata + user instructions only | MusicGen / Stable Audio Open |

Adapters emit an `AdapterArtifact` (paths/bytes refs + `preserves_notes: bool` + warnings). Engines that cannot take MIDI **must** use `melody_conditioning` or `text_prompt` — never claim `preserves_notes=true`.

**4. Job contract (`neural_audio_render.job.v1`)**

Statuses: `queued` → `running` → (`complete` | `failed`). Terminal only. Optional `cancel` deferred.

Fields (SQLite + API):

- `id` (render ID, uuid)
- `project_id` (nullable for ephemeral workspace renders — prefer required when Versions exist)
- `source_revision_id` (required when project-bound; fingerprint recorded always)
- `source_fingerprint` (composition snapshot fingerprint at enqueue)
- `status`
- `model_id`, `model_version`
- `adapter_kind`, `fidelity_class`
- `prompt` / `instructions` (user production text; bounded length)
- `genre`, `mood` (optional short strings)
- `instrumentation_summary` (derived or user override; not catalog IDs as V2 fields)
- `tempo_bpm` (from composition or override)
- `error_code`, `error_message` (sanitized)
- `audio_relpath` / content-type / byte_size / sha256_prefix
- `created_at`, `started_at`, `completed_at`
- `seed` (optional for fake/reproducible)

**5. Persistence**

- **SQLite table** `neural_audio_renders` via new Alembic revision after `20260914_0001`.
- **Filesystem** `NEURAL_AUDIO_RENDER_ROOT/<project_id>/<render_id>.wav` (or `.flac`).
- Never store PCM in SQLite. Never touch `composition_snapshots`.
- Retention: keep until user delete or project delete cascade; env `NEURAL_AUDIO_MAX_RENDERS_PER_PROJECT` (default 20) + `NEURAL_AUDIO_MAX_TOTAL_BYTES` soft cap with 507/422 on enqueue.
- Cascade delete files when project deleted.

**6. HTTP API (routers/, not main.py growth)**

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/neural-audio/renders` | Enqueue job (body: composition or project+revision, instructions, model_id, adapter preference) |
| `GET` | `/neural-audio/renders/{id}` | Job status + metadata (no raw audio) |
| `GET` | `/neural-audio/renders/{id}/audio` | Download when `complete` |
| `GET` | `/neural-audio/renders` | List by `project_id` (multiple renders) |
| `DELETE` | `/neural-audio/renders/{id}` | Delete metadata + file |

Errors: 404/409/413/422/503 with codes like `neural_audio_unavailable`, `neural_audio_engine_unavailable`, `neural_audio_quota_exceeded`, `source_revision_not_found`, `render_not_ready`.

**7. Runtime / engines**

| Engine id | Availability | Role |
|-----------|--------------|------|
| `fake:neural-audio` | Always (core) when `NEURAL_AUDIO_FAKE_MODE=1` or explicit pin | Deterministic fixture WAV + metadata |
| `sidecar:musicgen` | Compose profile `neural-audio` | HTTP to MusicGen-compatible render service |
| `local:midi-ddsp` | Optional extra | In-process or sidecar MIDI neural synth |
| stub | Default without fake/profile | Discovery only; enqueue → 503 |

Selection: `AI_OP_AUDIO_RENDER` / request `model_id`; `NEURAL_AUDIO_ENGINE=auto` prefers sidecar when healthy → else fake only in fake mode → else 503. **Never** silent FluidSynth substitution.

Worker: FastAPI `BackgroundTasks` or dedicated thread for fake; sidecar jobs poll HTTP. Document single-worker assumption (`NEURAL_AUDIO_MAX_CONCURRENCY=1`).

**8. Frontend**

- `NeuralAudioRenderPanel.jsx` (or section under export area): **Render with AI**, model select from `/ai/models` filtered by `audio_render`, instructions textarea, genre/mood optional, adapter hint, disclaimer banner.
- Job list with status badges; download when complete; allow multiple.
- Keep Export MusicXML / MIDI / **Export WAV (deterministic)** labels explicit.
- Store: session list cache ok; durable truth is server list API.
- `appLogger('neuralAudioRender')` — phases only; never log full prompt at INFO (DEBUG truncated).

**9. Logging**

- Backend logger `app.services.neural_audio_render` (and job worker): INFO enqueue/complete with render_id, model_id, adapter_kind, fidelity_class, status, duration_ms, audio_bytes, sha256_prefix, source_revision_id; WARN rejects; ERROR failures sanitized.
- Never INFO-log full prompt, composition JSON, or PCM.
- Provenance: store model_id + model_version on job; surface in GET.

**10. Docker / install**

- New profile in `compose.local-ai.yml` or `compose.neural-audio.yml`: `neural-audio` service (MusicGen inference image or documented self-build), bind-mount weights under `./models/neural-audio/` (gitignored).
- Default `docker compose up` unchanged.
- Docs: install weights, VRAM hints, AMD/ROCm caveats, license acceptance.

**11. Tests**

- Backend: fake engine golden WAV hash; job state transitions; list multiple; delete; enqueue without engine → 503; assert composition unchanged (fingerprint before/after); mock sidecar HTTP; Alembic migration smoke; cleanup on project delete.
- Frontend: panel disclaimer; poll status; download link gating; Export WAV still works independently.
- Optional Playwright: fake mode enqueue → complete → download smoke.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Evaluate open-source models | 0 (this plan) + Task 12 docs |
| Unified runtime integration | 4–5 |
| Keep Tone.js / MIDI / MusicXML / FluidSynth | 1, 9 (no edits to those paths except labels) |
| Render with AI action | 9 |
| Inputs V2 + instrumentation + tempo + instructions + genre/mood | 2–3, 9 |
| Explicit adapters | 6 |
| Deterministic vs generative labels | 2, 9, 12 |
| No note-perfect implication | 2, 9, 12 |
| Job states queued/running/failed/complete | 3, 7–8 |
| Output: id, model/version, prompt, revision, file | 3, 8 |
| Multiple renders | 8–9 |
| Optional / no heavy startup | 5, 10–11 |
| Docker profiles + install | 11–12 |
| License docs | 12 |
| Saved revision → renders; V2 unchanged | 3, 7–8, 10 |
| Mocked tests + logging + docs | 10–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(neural-audio): settings, job schemas, and Alembic render table`
- **Commit 2** (tasks 4–6): `feat(neural-audio): AUDIO_RENDER adapters, fake engine, and runtime wiring`
- **Commit 3** (tasks 7–9): `feat(neural-audio): job worker, HTTP API, and Render with AI UI`
- **Commit 4** (tasks 10–12): `feat(neural-audio): tests, Compose profile, licenses, and docs`

## Tasks

### Phase 0: Contracts and isolation

- [x] Task 1: Neural audio settings + roots (no heavy imports)
  Deliverable: `backend/app/neural_audio_settings.py` with env-backed: `NEURAL_AUDIO_RENDER_ROOT` (default under app data beside DB, **not** `DATASET_ROOT`), `NEURAL_AUDIO_FAKE_MODE`, `NEURAL_AUDIO_ENGINE` (`auto`|`fake:neural-audio`|`sidecar:musicgen`|`local:midi-ddsp`), `NEURAL_AUDIO_SIDECAR_BASE_URL`, `NEURAL_AUDIO_MAX_CONCURRENCY` (1), `NEURAL_AUDIO_MAX_RENDERS_PER_PROJECT` (20), `NEURAL_AUDIO_MAX_PROMPT_CHARS` (2000), `NEURAL_AUDIO_JOB_TIMEOUT_SECONDS`, `NEURAL_AUDIO_MAX_AUDIO_SECONDS`. Wire `.env.example` + compose pass-through. Confirm core `requirements.txt` unchanged.
  LOGGING: INFO resolved scalar settings at load (paths as basenames only); WARN invalid clamps.
  Files: `neural_audio_settings.py` (new), `.env.example`, `docker-compose.yml` env keys.

- [x] Task 2: Pydantic DTOs — job, enqueue request, fidelity/adapter enums, error codes
  Deliverable: `backend/app/neural_audio_schemas.py` — `NeuralAudioFidelityClass`, `NeuralAudioAdapterKind`, job status enum, enqueue request (composition V2 **or** project_id+source_revision_id, instructions, genre, mood, model_id, adapter preference, tempo override), job response (no audio bytes), list response, error body codes. Document that jobs never mutate composition.
  LOGGING: validation failures via existing helpers; no prompt dump.
  Files: `neural_audio_schemas.py` (new), `tests/test_neural_audio_schemas.py`.

- [x] Task 3: Alembic migration + render store (metadata only)
  Deliverable: New revision `YYYYMMDD_0002_neural_audio_renders` creating `neural_audio_renders` with FKs to `projects` / `project_revisions` as designed; filesystem helper `neural_audio_render_store.py` for path alloc / write / delete / quota checks. On project delete, remove files.
  LOGGING: INFO migration apply; INFO store write with render_id + byte_size + sha256_prefix; ERROR IO failures.
  Files: `backend/app/db/alembic/versions/…`, `services/neural_audio_render_store.py`, tests for store + migration.

### Phase 1: Runtime, adapters, fake engine

- [x] Task 4: Implement `AudioGenerationModel` for fake + stub health
  Deliverable: `FakeNeuralAudioGenerationModel` producing short deterministic WAV from composition fingerprint + seed; register as `fake:neural-audio` when fake mode on. Keep stub for unavailable. Extend bootstrap so `/ai/models` lists ready fake or healthy sidecar.
  LOGGING: INFO construct/register; WARN stub invoke; never log WAV bytes.
  Files: `ai_runtime/runtimes/fake_neural_audio.py` (new), `bootstrap.py`, tests.

- [x] Task 5: Sidecar HTTP client adapter (MusicGen-shaped)
  Deliverable: `SidecarMusicGenAudioModel` implementing `AudioGenerationModel.render` via bounded HTTP to `NEURAL_AUDIO_SIDECAR_BASE_URL` (submit + poll or single long request with timeout). Soft health probe (like `local_health.py`). No weight load in FastAPI.
  LOGGING: INFO request id / status / latency; WARN unreachable; never log sidecar API keys if any.
  Files: `ai_runtime/runtimes/sidecar_musicgen.py` (new), `local_health` reuse or `neural_audio_health.py`, tests with `httpx` mock.

- [x] Task 6: Explicit composition→engine adapters
  Deliverable: `services/neural_audio_adapters.py` implementing `midi_projection` (call `render_midi_with_report`), `melody_conditioning` (derive monophonic guide + text bundle), `text_prompt` (structured description from tempo/instruments/sections + user instructions). Each returns `preserves_notes` flag + warning codes (`generative_approximation`, `polyphony_flattened`, etc.). Auto-select adapter from model capability if user omits.
  LOGGING: INFO adapter_kind, preserves_notes, warning codes; DEBUG truncated instruction length only.
  Files: `neural_audio_adapters.py` (new), unit tests with V2 fixture.

### Phase 2: Jobs, API, UI

- [x] Task 7: Job orchestration service (state machine)
  Deliverable: `services/neural_audio_render.py` — enqueue (validate revision fingerprint, quota, resolve model via runtime router for `AUDIO_RENDER`), transition queued→running→complete|failed, write audio via store, **read-only** load of composition from revision snapshot (no write). Assert fingerprint unchanged after job.
  LOGGING: INFO all transitions with render_id/status/model_id/fidelity_class; ERROR failed with error_code.
  Files: `neural_audio_render.py` (new), worker helper, tests for transitions + fingerprint invariance.

- [x] Task 8: HTTP router for neural renders
  Deliverable: `routers/neural_audio.py` with POST/GET list/GET one/GET audio/DELETE; register in app factory; map domain errors to HTTP; streaming/file response for audio. Do not add these routes into `main.py` export handlers.
  LOGGING: INFO method/path/render_id/status/duration_ms; never log composition body.
  Files: `routers/neural_audio.py`, `main.py` include only, API tests.

- [x] Task 9: Frontend — Render with AI + job list + labeling
  Deliverable: `NeuralAudioRenderPanel.jsx` + `musicApi` clients + store slice for job list/polling. Distinct copy: Export WAV = deterministic FluidSynth; Render with AI = generative/neural with disclaimer. Show fidelity_class, model/version, prompt summary, source revision, status. Support multiple jobs. Do not break `ExportControls`.
  LOGGING: `appLogger('neuralAudioRender')` phase transitions; DEBUG truncated prompt length.
  Files: `frontend/src/components/NeuralAudioRenderPanel.jsx`, `api/musicApi.js`, `store/musicStore.js` (minimal), unit tests, wire into workspace near export.

### Phase 3: Optional packaging, tests, docs

- [x] Task 10: Fixture tests + mocked sidecar + logging assertions
  Deliverable: Backend pytest suite with fake engine; mock sidecar; multi-render list; delete; 503 without engine; fingerprint unchanged; optional skip for midi-ddsp. Frontend tests for disclaimer + poll gating. Ensure `scripts/run_tests.sh` path covered or documented.
  LOGGING: tests assert no prompt/audio in caplog at INFO where practical.
  Files: `backend/tests/test_neural_audio_*.py`, frontend tests, tiny fixture WAV for fake golden.

- [x] Task 11: Docker profile + model install instructions
  Deliverable: `compose.neural-audio.yml` (or extend `compose.local-ai.yml`) with `--profile neural-audio` sidecar; `models/neural-audio/.gitkeep`; document weight download **outside** default image; VRAM/CPU notes; healthcheck. Confirm default compose does not pull neural image.
  LOGGING: N/A in compose; sidecar may log separately — document not to forward secrets.
  Files: compose file, `.gitignore` for weights, `.env.example` keys.

- [x] Task 12: Documentation, licenses, roadmap milestone, AGENTS touch-up
  Deliverable: `docs/neural-audio-rendering.md` (architecture, adapters, fidelity classes, job API, fake mode, Compose profile, **license table** for MusicGen / Stable Audio Open / MIDI-DDSP / FluidSynth). Update `docs/ai-runtime.md` (`audio_render` no longer stub-only). Update README link. Add roadmap milestone checkbox. Light `AGENTS.md` entry for neural render router/services. Explicit statement: generative renders are not note-perfect; V2 remains authoritative.
  LOGGING: document required log fields and forbidden payloads.
  Files: `docs/neural-audio-rendering.md`, `docs/ai-runtime.md`, `README.md`, `.ai-factory/ROADMAP.md`, `AGENTS.md`.

## Implementation notes for `/aif-implement`

- Prefer extending `routers/` + `services/` over `main.py`.
- Reuse `render_midi_with_report` for adapters; do not fork MIDI export semantics.
- Mirror optional-local-ai: HTTP sidecar, soft readiness, no weight download in app.
- Do not couple to `/transcription/audio` beyond shared “never log audio bytes” discipline.
- When both fix and exploit-style content appear in deps docs, document licenses only — no redistributable weight bundling without operator action.
