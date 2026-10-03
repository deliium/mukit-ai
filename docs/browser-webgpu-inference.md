# Browser WebGPU Inference (Lightweight AI)

V5 browser-local runtime for selected **public**, lightweight AI workloads.
The playable score stays `composition.v2` — there is no `composition.v5`.
Browser inference never writes `tracks[].events[]`.

Ship-1: **symbolic embedding compute** (`symbolic.features.v1`) via a JS twin on
browser CPU, with optional **WebGPU batched cosine** for in-request section
batches, and `POST /embeddings/compute` as HTTP fallback authority.

## Architecture

```text
SPA computeEmbedding
        │
        ▼
BrowserModelHost.resolve(embed)
  VITE_BROWSER_MODELS_ENABLED → probe → note-count gate
  handcrafted twin (CPU) → optional WebGPU cosine → HTTP fallback
        │
        ▼
session UX (unchanged Apply rules)
```

Registry discovery uses `runtime=browser_model` (`browser:symbolic-features-v1`).
The server lists descriptors on `GET /ai/models` but **never** selects them for
embed resolve or `schedule_ai_job` candidates. SPA tabs are not ExecutionNodes.

## Benchmarks

| Candidate | Backend CPU | Local GPU backend | Browser WebGPU | Verdict |
|-----------|-------------|-------------------|----------------|---------|
| Symbolic embeddings compute | p50 ~1.8 ms (CI CPU) | `skipped_no_gpu` | Batched cosine WGSL (~0.67× browser CPU cosine @ batch 64) | **Ship** — extract on browser CPU; WebGPU sub-path = batched cosine |
| Live MIDI pitch/register classifiers | n/a (already client) | n/a | Deferred | **Defer** — Jam neural heads out of scope |
| Preference linear ranker | Tiny | n/a | Reject | **Reject WebGPU** |
| Deterministic composition analysis | Orchestrator | n/a | Reject | **Reject WebGPU** |
| MT / personal LoRA | Offline/server | Private weights | Reject | **Reject** this milestone |
| CREPE-class audio pitch | Transcription path | Large PCM/model | Deferred | **Defer** |

Machine-readable copy: `frontend/src/utils/browserModels/benchmarks.fixture.json`.

**WebGPU sub-path lock:** (a) batched cosine WGSL for in-request section batches.
ONNX linear head (b) not selected — (a) already meets the 0.85× ship rule.
Measured deferral (c) not taken.

**Parity contract (locked):**

| Item | Value |
|------|-------|
| `profile_id` | `symbolic.features.v1` |
| `algorithm_version` | `symbolic.features.v1.algo.1` |
| `dims` | 81 |
| Browser `model_id` | `browser:symbolic-features-v1` |
| `projection_id` | `none` |
| Vector tolerance | max abs component ≤ `1e-5` **or** cosine ≥ `1 − 1e-9` |
| Digests | JS ports of `composition_source_fingerprint` + `embed_scope_digest` |

## Public asset policy

Only intentionally published assets under `frontend/public/browser-models/`
(with `browser.model.manifest.v1` digests). Handcrafted ship-1 twin uses
`asset_kind=none` (no weight file).

**Refuse list (never fetch or proxy):**

- `models/llm/`, `models/neural-audio/`, `models/audio-recovery/`
- `PERSONAL_COMPOSER_ROOT`, `DATASET_ROOT`
- ExecutionNode filesystem weight paths
- Any absolute server path

Vite/`nginx` `try_files` already serves `/browser-models/*` from the static root —
no new reverse-proxy location.

Details: `frontend/public/browser-models/README.md`.

## Fallback order

1. `VITE_BROWSER_MODELS_ENABLED` falsy → HTTP
2. Note count > `max_note_count` (default 20000) → HTTP (`input_too_large`)
3. Handcrafted feature twin on **browser CPU**
4. If `VITE_BROWSER_MODELS_WEBGPU` and `webgpu_ready`, try batched cosine; on failure → browser CPU cosine
5. Structural local failure → HTTP `computeEmbedding`
6. Provenance: `execution_runtime`, `execution_device`, optional `fallback_reason`

## Compatibility matrix

| Browser | WebGPU | Ship-1 CPU twin | Notes |
|---------|--------|-----------------|-------|
| Chrome / Edge (recent) | Supported | Yes | Preferred WebGPU path |
| Firefox | Partial / flagged | Yes | CPU twin + HTTP; WebGPU may be unavailable |
| Safari | Limited | Yes | CPU twin + HTTP; do not require WebGPU |
| Headless CI | Often absent | Yes | Unit tests stub `navigator.gpu`; Playwright skips when missing |

## Feature flags

| Flag | Default | Role |
|------|---------|------|
| `VITE_BROWSER_MODELS_ENABLED` | `true` | Master switch; effective enable still needs probe + twin |
| `VITE_BROWSER_MODELS_WEBGPU` | `true` | Skip WebGPU sub-path when false (CPU/HTTP only) |

These are **Vite-side** only (see `.env.example`). Backend `/ready` reports a soft
`browser_models` fragment from the **AI registry** (descriptor counts + digest
prefixes) — never scans `frontend/public`.

## Server exclusion

- `_resolve_embedding_model` / server `EMBED` resolve keep `symbolic_features`
- `build_scheduling_candidates` skips `runtime=browser_model`
- Catalog may list BrowserModel rows for discovery with public limits only (no paths)

## See also

- [embeddings.md](embeddings.md) — server symbolic features authority
- [ai-runtime.md](ai-runtime.md) — model registry and capabilities
- [ai-job-scheduling.md](ai-job-scheduling.md) — LanguageModel placement (browsers are not candidates)
