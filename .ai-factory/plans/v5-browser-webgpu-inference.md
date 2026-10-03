# Implementation Plan: V5 Browser WebGPU Inference for Lightweight AI Capabilities

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: thin integration only — wire browser-local embed into existing Develop / reference / similarity session paths; no new studio tab. Capability status may surface as a compact readiness line where embeddings already run
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Acceptance split local-vs-WebGPU; Task 1 default-locks batched cosine WGSL; parity tolerance + fingerprint/scope digest twins; `browser_model` excluded from scheduling candidates and server embed resolve; `/ready` is registry-only; Task 7 scoped to kernel + `computeEmbedding` wire; Task 8 folded into Tasks 5–7 (thin cross-cut + Playwright remain); Task 3/7 depend on Task 1; Jam/CREPE neural heads stay deferred
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: evaluate lightweight V5 AI candidates against backend CPU / local GPU backend / browser WebGPU; register a `BrowserModel` runtime through the existing `ai_runtime` abstraction; ship model loading, caching, capability detection, and graceful fallback; keep private server weight trees off the public surface. The working `composition.v2` is never written by browser inference

## Roadmap Linkage
Milestone: "V5 Browser WebGPU inference for lightweight AI"
Rationale: ExecutionNodes and capability-aware scheduling already place LanguageModel jobs across controller-local and trusted-LAN peers, but latency-sensitive lightweight ops (especially symbolic embeddings) still always round-trip to the backend. This milestone adds a browser-local runtime for selected public, lightweight models with WebGPU when justified and HTTP fallback otherwise. The milestone is appended to `.ai-factory/ROADMAP.md` because linkage is enabled and no incomplete milestone remained. Implementation does not edit `ROADMAP.md`.

## Goal

Reduce backend round trips for latency-sensitive lightweight AI by running selected models in a supported browser, without migrating workloads to WebGPU merely because the API exists, and without exposing private server model files.

Ship:

1. A **candidate evaluation + benchmark gate** comparing backend CPU, local GPU backend (when available), and browser WebGPU for each proposed use.
2. A **`runtime=browser_model`** registration path on the existing `ModelDescriptor` / `GET /ai/models` surface (BrowserModel), discovery-only on the server (never selected by server embed resolve or `schedule_ai_job` candidates).
3. A frontend **BrowserModelHost**: WebGPU/capability probe, public asset load, Cache API / IndexedDB caching, session warm-up, and ordered fallback.
4. **At least one** latency-sensitive lightweight workload executing locally in a supported browser with backend fallback (locked default below).
5. Benchmark tables and browser compatibility documentation.

Acceptance:

1. With browser-local path enabled, ship-1 `computeEmbedding` produces a valid `composition.embedding.v1` card **without** calling `POST /embeddings/compute` (browser **CPU** twin is sufficient for this criterion).
1b. When Task 1 locks a WebGPU kernel (default: batched cosine WGSL), that kernel runs under real or stubbed `navigator.gpu` and produces a correct result for its sub-path without HTTP; WebGPU init failure must not fail ship-1 compute (falls back to browser CPU cosine / HTTP as ordered).
2. With WebGPU unavailable (or EP/kernel init failure), the same operation still succeeds via browser CPU (when the workload has a CPU twin) or backend HTTP — never a hard failure solely because WebGPU is missing.
3. Backend HTTP remains the authority for golden parity: browser-local vectors match backend within the locked numeric tolerance on fixture compositions (max abs component error ≤ `1e-5`, or cosine similarity ≥ `1 − 1e-9`). Cards also match `source_fingerprint` and `scope_digest` via JS ports of the backend digest algorithms.
4. Private trees (`models/llm/`, `models/neural-audio/`, `PERSONAL_COMPOSER_ROOT`, `DATASET_ROOT`, ExecutionNode weight paths) are never listed or served as browser assets.
5. `GET /ai/models` can list the BrowserModel descriptor(s) with `runtime=browser_model` and non-secret limits (asset digest prefix, max input notes, device preference) — never absolute server weight paths. Server `_resolve_embedding_model` and `build_scheduling_candidates` never select `runtime=browser_model`.
6. Benchmark artifact (checked-in markdown table + machine-readable JSON fixture) records the three-way comparison for evaluated candidates and the reason each non-shipped candidate was deferred or rejected.
7. Browser inference never writes `tracks[].events[]`, never logs full vectors / prompts / weight bytes, and never invents `composition.v5`.

```text
SPA latency-sensitive op (e.g. embed compute)
        │
        ▼
BrowserModelHost.resolve(operation)
  probe WebGPU / WASM / CPU
  load+cache public manifest asset (if needed)
        │
   ┌────┴────────────────────────┐
   ▼                             ▼
local execute                 HTTP fallback
(browser_model)          (existing /embeddings/*)
        │                             │
        └────────────┬────────────────┘
                     ▼
              session UX (unchanged Apply rules)
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. A **BrowserModel** is a registry `ModelDescriptor` with `runtime=browser_model` intended for client-side execution of a **public** lightweight asset (or a pure client twin with no weight file). A **browser capability probe** is a non-playable session snapshot (`browser.capability.v1`) describing `webgpu` / `wasm` / `cpu` support — never stored on the Composition. A **browser model manifest** is `browser.model.manifest.v1` naming public asset URLs, digests, ops, and size caps. **Ship-1** is the first accepted latency-sensitive workload (default: symbolic embedding compute). **Private server model files** means GGUF/checkpoints under host bind mounts, personal adapters, dataset corpora, and any path refused by `storage_root_policy` — they must not become browser fetch targets. Predecessor scheduling/ExecutionNode plans remain for LanguageModel placement; this milestone does not schedule browser peers as ExecutionNodes and does not stream neural PCM through WebGPU.

Predecessor: `.ai-factory/plans/v5-capability-aware-distributed-ai-job-scheduling.md` (registry candidates, trust boundaries). Capability taxonomy: `backend/app/ai_runtime/capabilities.py`. Embedding engine: `backend/app/embeddings/` + `runtime=symbolic_features`. Frontend embed client: `frontend/src/api/musicApi.js` `computeEmbedding`.

## Approach Evaluation (locked)

### Part A — Where BrowserModel lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Ad-hoc ONNX / WebGPU calls inside each React panel** | Fast to hack | Diverges per surface; no discovery; hard to test fallback | **Reject** |
| **B. New parallel ML stack outside `ai_runtime`** | Isolated | Breaks `GET /ai/models` / provenance story; duplicates capability taxonomy | **Reject** |
| **C. Register `runtime=browser_model` on existing `ModelDescriptor`; SPA BrowserModelHost executes; backend HTTP remains fallback authority** | Matches abstraction; discoverable; testable | Client must report capability; catalog status is advisory for browser rows | **Accepted** |

### Part B — Which workloads may use WebGPU in this milestone

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Port Music Transformer / LLM / MusicGen weights to WebGPU** | Flashy | Heavy downloads; private/licensed weights; violates “lightweight” and private-file rules | **Reject** |
| **B. Force WebGPU for every candidate including handcrafted 81-D histograms** | Uniform device story | Migrates “because WebGPU exists”; likely slower than JS CPU for ship-1 | **Reject** |
| **C. Benchmark each candidate; ship browser-local symbolic embedding compute as the latency win; default WebGPU sub-path = batched cosine WGSL when the gate allows; keep handcrafted feature extraction on browser CPU** | Honors evaluation gate; meets acceptance; keeps private weights off-browser | Two device paths to maintain for ship-1 | **Accepted** |

### Part C — Candidate shortlist (evaluation order)

| Candidate | Today | Latency-sensitive? | Browser fitness hypothesis | WebGPU hypothesis |
|-----------|-------|--------------------|----------------------------|-------------------|
| **Symbolic embeddings compute** (`AiOperation.EMBED`, `symbolic.features.v1`) | Backend Python handcrafted features via `POST /embeddings/compute` | Yes (Develop reference / similar sections) | **High** — pure math twin; public algorithm; no private weights | **Low for single-card extract**; **default WebGPU sub-path: batched cosine** |
| **Pitch / register classifiers over live MIDI histograms** | Already client rule features in `livePerformanceFeatures.js` | Yes (Jam) | Medium — already local; neural head optional | Deferred — out of scope for implementation tasks |
| **Preference linear ranker** | Backend/process CPU | Mild | Medium — tiny; little round-trip pain today | **Reject WebGPU** (overkill) |
| **Deterministic composition analysis** | Backend orchestrator | Mild | Medium as JS port | **Reject WebGPU** |
| **Small symbolic inference (MT / personal LoRA)** | Offline / server | High cost | **Low** — large/private | **Reject** this milestone |
| **Audio pitch models (CREPE-class)** | Transcription path | Yes | Medium–low (PCM + model size) | Deferred — out of scope for implementation tasks |

**Ship-1 lock (default, Task 1 may only tighten, not widen):** browser-local **symbolic embedding compute** for scopes used by Develop / similarity session, with backend `POST /embeddings/compute` fallback. Task 1 must record why other candidates are deferred and must not add Jam neural / CREPE implementation tasks.

### Part D — Public assets vs private weights

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Proxy `/models/**` from host bind mounts to the SPA** | Convenient | Exposes private GGUF / neural / personal adapters | **Reject** |
| **B. Embed opaque binary blobs in JS with no digest** | Simple | Cache busting / integrity weak; review hard | **Reject** |
| **C. Only intentionally published assets under `frontend/public/browser-models/` (git-tracked or release-attached) with `browser.model.manifest.v1` digests; handcrafted twin may ship with `asset_kind=none`** | Explicit allowlist; auditable | Operators must publish to promote a new browser model | **Accepted** |

### Part E — Fallback and parity

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require WebGPU or fail the UX** | Forces modern browsers | Breaks Firefox/Safari CI and many users | **Reject** |
| **B. Browser path with different dims / algorithm than backend** | Easy | Breaks similarity corpus compatibility | **Reject** |
| **C. Ordered fallback `webgpu → browser_cpu → http_backend`; golden vector + fingerprint/scope-digest parity; provenance records `execution_device` + `fallback_reason`** | Graceful; safe | Slightly more host code | **Accepted** |

### Part F — Scheduling / ExecutionNode interaction

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Register browsers as ExecutionNodes** | Unified scheduler | Trust/auth/heartbeat nonsense for SPA tabs; privacy mismatch | **Reject** |
| **B. Ignore BrowserModel in server `schedule_ai_job`; SPA chooses browser vs HTTP before/around the existing API client; explicitly skip `runtime=browser_model` in candidate projection and server embed resolve** | Clear boundary | Two decision points (client then server) | **Accepted** |
| **C. Teach server scheduler about `navigator.gpu`** | Centralized | Server cannot see client GPU; stale | **Reject** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Capability taxonomy | `ModelCapability.EMBEDDING`, `AiOperation.EMBED` | BrowserModel primary capability for ship-1 |
| Runtime literal union | `ModelRuntimeId` in `ai_runtime/types.py` | Add `browser_model` |
| Registry / discovery | `bootstrap.py`, `GET /ai/models`, `ai_runtime_schemas.py` | Register browser descriptors; public limits only |
| Symbolic embed engine | `embeddings/features.py` (81-D handcrafted), `runtimes/symbolic_features.py` | Golden parity source; algorithm twin in JS |
| Embed fixtures | `backend/tests/fixtures/embeddings` + `test_embeddings_features.py` | Export / check golden vectors for frontend twin |
| Fingerprint / scope digest | `composition_source_fingerprint`, `embed_scope_digest` | Must be ported to JS for card parity |
| Embed HTTP | `routers/embeddings.py`, `services/composition_embedding.py` | Fallback authority; must refuse `browser_model` |
| Scheduling candidates | `build_scheduling_candidates` / `CONTROLLER_LOCAL_RUNTIMES` | Must skip `browser_model` (unknown local runtimes currently fall through to `controller_local`) |
| Frontend embed client | `musicApi.computeEmbedding`, `musicStore` similarity session | Insert BrowserModelHost before axios |
| Capability probe pattern | `midiInputSupport.js` / `midiInputAccess.js` | Mirror for `navigator.gpu` |
| Graceful live fallback | Jam predict AbortController → local fill | Same UX spirit |
| Static assets | Vite `public/` → dist; nginx `try_files` serves `/browser-models/*` | No special nginx location required |
| Docs | `docs/embeddings.md`, `docs/ai-runtime.md` | Link new browser-inference page |
| Latest migration | `20261003_0025` scheduling | No DB migration required |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Candidate benchmarks | No three-way latency/throughput table for embed / classifiers / analysis |
| `runtime=browser_model` | Not in `ModelRuntimeId` / bootstrap / catalog |
| Server exclusion | Catalog list must not imply server/schedule selection |
| BrowserModelHost | No load / cache / probe / fallback module |
| JS twin of `symbolic.features.v1` | Embed always hits HTTP today |
| JS fingerprint / scope digest twins | Required for `composition.embedding.v1` card parity |
| Public model manifest | No `browser.model.manifest.v1` or `frontend/public/browser-models/` |
| WebGPU batched cosine | Zero ONNX/WebGPU usage in repo; default ship kernel |
| Parity tests + tolerance | No cross-runtime golden vectors / locked epsilon |
| Compatibility docs | No browser matrix for WebGPU inference |

### Coupling risks to avoid

1. Writing `tracks[].events[]` or treating browser output as a second playable score.
2. Serving or linking `DATASET_ROOT`, `PERSONAL_COMPOSER_ROOT`, `models/llm`, neural-audio private weights, or ExecutionNode filesystem paths to the SPA.
3. Silent algorithm drift (browser dims/profile ≠ backend `symbolic.features.v1`).
4. Requiring WebGPU for basic embed UX.
5. Registering SPA tabs as ExecutionNodes or letting `build_scheduling_candidates` emit `browser_model` rows.
6. Logging full embedding vectors, composition event arrays, or model bytes.
7. Importing `onnxruntime-web` into the critical first-paint path without dynamic import.
8. Inventing `composition.v5` or a new agent id.
9. Editing `ROADMAP.md` during implementation.
10. Treating `VITE_BROWSER_MODELS_ENABLED` default `true` as unconditional — effective enable still requires probe + twin ready; disable cleanly when probe fails.
11. Using WebGPU for handcrafted histogram extraction without a benchmark win.
12. Changing `SymbolicEmbeddingModel` / `LanguageModel` protocol method signatures in a breaking way (additive helpers only).
13. Implementing Jam neural pitch heads or CREPE-class audio ONNX in this milestone.

## Scope And Decisions

### In scope
- Evaluation/benchmark harness + checked-in results for the candidate table.
- `runtime=browser_model` + catalog descriptors + soft `/ready` note from **registry counts only** (never fails overall readiness; no SPA filesystem crawl).
- Explicit exclusion of `browser_model` from scheduling candidates and server embed model resolve.
- `browser.capability.v1` probe + `browser.model.manifest.v1` + public asset directory policy.
- BrowserModelHost: detect, load, cache, execute, fallback, provenance fragment fields.
- Ship-1: browser-local symbolic embedding compute with HTTP fallback; default WebGPU sub-path = batched cosine WGSL when the gate allows.
- Parity contract (vectors + digests) with locked tolerance; golden fixtures from backend embedding fixtures.
- Frontend unit tests covering probe/cache/twin/fallback/WebGPU-off; optional Playwright smoke when WebGPU is available (skip/xfail otherwise).
- Docs: `docs/browser-webgpu-inference.md` (benchmarks, compatibility, asset policy) + links from embeddings / ai-runtime.

### Out of scope
- Porting Music Transformer, llama.cpp GGUF, MusicGen, Demucs, or personal LoRA adapters to the browser.
- Browser as ExecutionNode / scheduler candidate.
- New studio “Models” or “WebGPU” tab (beyond compact status in existing embed UX).
- Changing similarity corpus indexing on the server (project-corpus search stays HTTP; in-request section batch scoring may use the WebGPU cosine kernel when Task 1 locks it).
- Jam neural pitch classifiers / CREPE-class audio models (evaluate-and-defer only in Task 1).
- WebNN-only path (may be noted as future; not required for acceptance).
- Editing `ROADMAP.md` during implementation.

### Architecture decisions (locked)

**1. Runtime and descriptors**

Add `browser_model` to `ModelRuntimeId`. Ship-1 descriptor id: `browser:symbolic-features-v1`.

| Field | Rule |
|-------|------|
| `runtime` | `browser_model` |
| `primary_capability` | `embedding` for ship-1 |
| `locality` | `local` |
| `supported_operations` | `(embed,)` |
| `status` | `ready` when twin/manifest present; client probe may mark session use `unsupported_device` / `degraded` |
| `limits` | `{ profile_id, algorithm_version, asset_digest_prefix?, max_note_count, webgpu_optional, torch_required: false }` — **no paths** |

Backend catalog lists BrowserModel rows for discovery. Server-side `resolve_model_for_operation` / `_resolve_embedding_model` do **not** select `browser_model` (server keeps `symbolic_features`). `build_scheduling_candidates` **skips** `runtime=browser_model` entirely. The SPA selects browser vs HTTP.

**2. Documents**

- `browser.capability.v1` (frontend + optional mirrored Zod-less JSON validate): `webgpu_adapter`, `webgpu_ready`, `wasm_simd`, `device_memory_hint_mb`, `reason_codes[]`.
- `browser.model.manifest.v1`: `model_id`, `profile_id`, `ops[]`, `asset_kind` (`none` \| `onnx` \| `raw_weights`), `asset_url` (site-relative under `/browser-models/`), `sha256`, `size_bytes` (cap), `input_spec`, `output_spec`.

Forbidden in manifests/logs: absolute server paths, API keys, prompts, full vectors at INFO.

**3. Execution order (ship-1 embed compute)**

1. If `VITE_BROWSER_MODELS_ENABLED` falsy → HTTP.
2. Probe capability; if composition note count exceeds `max_note_count` (default **20000**) → HTTP (`fallback_reason=input_too_large`).
3. Run handcrafted feature twin on **browser CPU** (parity with `embeddings/features.py`).
4. If WebGPU sub-path is enabled (default batched cosine for in-request batches) and `webgpu_ready`, try it; on failure → browser CPU cosine / continue with CPU card.
5. On any local structural failure → HTTP `computeEmbedding`.
6. Record provenance on the session card path: `execution_runtime=browser_model|http`, `execution_device=webgpu|browser_cpu|server_cpu`, `fallback_reason?`.

**4. Caching**

- Manifest + ONNX/raw assets: Cache API (preferred) with digest key; IndexedDB fallback if Cache API missing.
- Warm-up: lazy on first embed / optional idle preload when Develop tab opens (dynamic `import()`, not main bundle).
- Handcrafted twin: module-level memo by `(algorithm_version, source_fingerprint, scope_digest)` mirroring server cache key fields (session-only; never persist vectors into `localStorage`).

**5. Benchmark gate (Task 1 deliverable)**

For each candidate in Part C, measure (or estimate with recorded methodology) p50/p95 latency and correctness notes on:

| Backend | Environment |
|---------|-------------|
| Backend CPU | FastAPI + `symbolic_features` (or relevant service) on CI CPU |
| Local GPU backend | Optional; skip with `skipped_no_gpu` when no ROCm/CUDA device — do not block ship |
| Browser WebGPU | Chromium with native WebGPU or stubbed harness for CI unit timing of kernels |

**WebGPU sub-path lock (Task 1):**

1. **Default:** (a) batched cosine WGSL for in-request section batches.
2. (b) tiny public ONNX linear head — only if benchmarks beat (a) on the same harness.
3. (c) defer WebGPU kernel — only if both miss the ship rule; still ship probe + CPU twin; record measured rejection table. Prefer not taking (c).

**Ship rule:** implement WebGPU for a sub-path only if p50 local WebGPU ≤ 0.85 × browser CPU for that sub-path **or** it unlocks a workload impossible on CPU within the UX budget. Handcrafted single-scope extract stays CPU.

**6. Parity contract (locked)**

| Item | Rule |
|------|------|
| `profile_id` | `symbolic.features.v1` |
| `algorithm_version` | `symbolic.features.v1.algo.1` |
| `dims` | `81` (`SYMBOLIC_FEATURES_V1_DIMS`) |
| `model_id` (browser card) | `browser:symbolic-features-v1` |
| `projection_id` | `none` for ship-1 |
| Vector tolerance | max abs component error ≤ `1e-5` **or** cosine ≥ `1 − 1e-9` vs backend golden |
| `source_fingerprint` | JS port of `composition_source_fingerprint` / analysis-relevant projection |
| `scope_digest` | JS port of `embed_scope_digest` |
| Golden source | Vectors/digests derived from `backend/tests/fixtures/embeddings` (checked-in frontend fixture JSON generated from backend) |

**7. Dependencies**

- Prefer **dynamic import** of any ONNX package only when Task 1 chose (b).
- Default (a) is pure WGSL / WebGPU compute — no weight file required (`asset_kind=none` for the handcrafted twin).
- Do not add native Node GPU deps to the Vite SPA beyond optional dynamic web packages.

**8. Logging**

| Level | What |
|-------|------|
| DEBUG | probe fields, cache hit/miss, device chosen, digest prefix, note_count, dims |
| INFO | first successful local embed per session; fallback transitions with reason code |
| WARN | WebGPU init failure; parity mismatch triggering HTTP |
| ERROR | unexpected host exceptions (sanitized) |

Never: full vectors, event arrays, asset bytes, absolute disk paths.

**9. Feature flag**

- `VITE_BROWSER_MODELS_ENABLED` — default `true`; effective enable still requires probe + twin ready.
- `VITE_BROWSER_MODELS_WEBGPU` — default `true`; when false, skip WebGPU sub-path (CPU/HTTP only) for debugging.

**10. Soft `/ready`**

Fragment `browser_models`: `{ descriptor_count, model_ids_prefix_or_list_capped, limits_digest_prefixes }` from the **AI registry only**. Never scan `frontend/public` from the backend process.

## Commit Plan
- **Commit 1** (after tasks 1–2): `docs(ai): add browser WebGPU evaluation gate and candidate benchmarks`
- **Commit 2** (after tasks 3–4): `feat(ai-runtime): register browser_model and exclude from server schedule/resolve`
- **Commit 3** (after tasks 5–7): `feat(frontend): BrowserModelHost with embed twin, WebGPU cosine, and fallback`
- **Commit 4** (after task 8): `docs(ai): browser WebGPU inference compatibility and AGENTS entry points`

## Tasks

### Phase 1: Evaluate and lock ship-1 kernels
- [x] Task 1: Run / record the candidate evaluation against backend CPU, local GPU backend (or explicit skip), and browser WebGPU harness. Produce `docs/browser-webgpu-inference.md` §Benchmarks plus `frontend/src/utils/browserModels/benchmarks.fixture.json` summarizing p50/p95 and verdicts. Confirm ship-1 = symbolic embedding compute. **Default-lock WebGPU sub-path (a) batched cosine WGSL** for in-request section batches; allow (b) ONNX linear head only if it beats (a); use (c) measured deferral only if both miss the 0.85× gate (still ship probe + CPU twin). Defer Jam neural / CREPE with recorded reasons — do not widen implementation scope. Also lock the parity contract constants from Architecture decision 6 into the benchmark/docs stub.

  LOGGING: n/a for the markdown table; harness DEBUG prints timing codes only (no vectors).

  Files: `docs/browser-webgpu-inference.md` (stub §Benchmarks ok), `frontend/src/utils/browserModels/benchmarks.fixture.json`, optional `scripts/browser_model_bench.md` notes

- [x] Task 2: Freeze public asset policy: directory `frontend/public/browser-models/`, manifest schema, max size cap, digest verification algorithm, and explicit refuse list (no proxy to `models/llm`, neural-audio, personal composer, dataset). Note that Vite/`nginx` `try_files` already serves `/browser-models/*` from static root — no new proxy location. Document in the same docs page. (depends on 1)

  LOGGING: n/a (policy/docs).

  Files: `frontend/public/browser-models/README.md`, `docs/browser-webgpu-inference.md`

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Registry contracts and server exclusion
- [x] Task 3: Extend `ModelRuntimeId` with `browser_model`; add bootstrap/catalog descriptor(s) for `browser:symbolic-features-v1` (and optional ONNX companion id **only if** Task 1 chose (b)); ensure server resolve for `EMBED` still uses `symbolic_features`. **Exclude `runtime=browser_model` from `build_scheduling_candidates` and from `composition_embedding._resolve_embedding_model`** (and any direct resolve that could construct a Python executor for it). Unit-test: catalog lists browser rows; schedule candidates omit them; HTTP embed resolve ignores/refuses browser ids. (depends on 1)

  LOGGING: INFO once on bootstrap `{browser_model_count}`; DEBUG descriptor ids — never paths; DEBUG skip reason `browser_model_not_server_executable` when excluded.

  Files: `backend/app/ai_runtime/types.py`, `backend/app/ai_runtime/bootstrap.py`, `backend/app/services/scheduling_candidates.py`, `backend/app/services/composition_embedding.py`, `backend/tests/test_ai_runtime_browser_model.py` (new), `backend/tests/test_scheduling_candidates.py` (extend)

- [x] Task 4: Soft `/ready` fragment `browser_models` from **registry descriptor count + non-secret limit digest prefixes only** (never fails readiness; never scans SPA `public/`). `.env.example` note that browser flags are Vite-side. (depends on 3)

  LOGGING: DEBUG ready block assembly.

  Files: `backend/app/ready.py`, `.env.example`

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: BrowserModelHost + ship-1 embed
- [x] Task 5: Implement `frontend/src/utils/browserModels/` — `capabilityProbe.js` (`browser.capability.v1`), `manifest.js`, `assetCache.js` (Cache API + IndexedDB fallback), `browserModelHost.js` (resolve/execute/fallback). Mirror MIDI probe style: pure probe, injectable globals for tests. Dynamic-import any ONNX/WebGPU heavy deps. **Tests in this task:** probe reasons, cache hit/miss, host throw → HTTP fallback with mocked axios, oversized note count → HTTP, manifest refuse forbidden path prefixes. (depends on 2)

  LOGGING: DEBUG probe + cache; INFO first local success; WARN on fallback reasons (`webgpu_unavailable`, `asset_integrity`, `parity_mismatch`, `input_too_large`, `host_error`).

  Files: `frontend/src/utils/browserModels/*`, `frontend/src/utils/browserModels/*.test.js`

- [x] Task 6: Port handcrafted `symbolic.features.v1` extract + L2 normalize to JS with golden parity against backend fixtures (`backend/tests/fixtures/embeddings`). Emit full `composition.embedding.v1` card shape: `profile_id`, `algorithm_version`, `dims=81`, `model_id=browser:symbolic-features-v1`, `projection_id=none`, matching `source_fingerprint` (JS port of `composition_source_fingerprint`) and `scope_digest` (JS port of `embed_scope_digest`). Lock tolerance: max abs component error ≤ `1e-5` or cosine ≥ `1 − 1e-9`. Check in generated golden JSON under `frontend/src/utils/browserModels/fixtures/`. No artist dimensions. **Tests in this task:** golden vectors + digest equality on fixtures. (depends on 1, 5)

  LOGGING: DEBUG `{dims, note_count, fingerprintPrefix}` only.

  Files: `frontend/src/utils/browserModels/symbolicFeaturesV1.js`, `frontend/src/utils/browserModels/compositionSourceFingerprint.js` (or equivalent), `frontend/src/utils/browserModels/embedScopeDigest.js`, `frontend/src/utils/browserModels/symbolicFeaturesV1.test.js`, `frontend/src/utils/browserModels/fixtures/*`

- [x] Task 7: Implement Task 1’s locked WebGPU sub-path (default batched cosine WGSL; ONNX head only if Task 1 chose (b)) behind `VITE_BROWSER_MODELS_WEBGPU`, with CPU fallback for that sub-path. Wire **`computeEmbedding` only** through BrowserModelHost before axios; preserve existing error codes for pure HTTP failures. Session provenance fields for device/fallback. Optional in-request section similarity batch uses the cosine kernel **only if** Task 1 locked (a) — do not expand to project-corpus HTTP replacement. **Tests in this task:** WebGPU forced-off still succeeds on CPU; stubbed `navigator.gpu` path for the locked kernel; optional Playwright skip when `navigator.gpu` missing. (depends on 1, 5, 6)

  LOGGING: INFO device selection; WARN WebGPU→CPU/HTTP.

  Files: `frontend/src/api/musicApi.js`, `frontend/src/store/musicStore.js` (only if session fields required), `frontend/src/utils/browserModels/webgpu/*`, tests, optional `frontend/e2e/*browser-model*`

<!-- Commit checkpoint: tasks 5-7 -->

### Phase 4: Docs and AGENTS
- [x] Task 8: Complete `docs/browser-webgpu-inference.md` (architecture, asset policy, fallback order, benchmark table, compatibility matrix Chrome/Edge/Firefox/Safari, flags, parity tolerance, server exclusion rules); link from `docs/embeddings.md` and `docs/ai-runtime.md`; update `AGENTS.md` entry points for BrowserModelHost + public `browser-models/`. (depends on 1–7)

  LOGGING: n/a (docs).

  Files: `docs/browser-webgpu-inference.md`, `docs/embeddings.md`, `docs/ai-runtime.md`, `AGENTS.md`

<!-- Commit checkpoint: task 8 -->

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer extending `frontend/src/utils/browserModels/` + thin `musicApi` wiring over new panels.
3. Server `symbolic_features` remains the embed authority for HTTP and golden vectors; `browser_model` is discovery + client execution only.
4. Do not register browsers with ExecutionNode scheduling; skip them in candidate projection.
5. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
6. Prefer shipping the default WebGPU batched-cosine kernel so the milestone title stays honest; only take measured deferral (c) with a recorded rejection table.
7. Never log or expose private server model files.
8. Do not implement Jam neural pitch heads or CREPE-class audio models in this milestone.
