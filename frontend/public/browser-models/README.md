# Public browser model assets

Only **intentionally published** lightweight assets for SPA `BrowserModelHost`.

## Layout

```text
browser-models/
  README.md                          # this file
  symbolic-features-v1.manifest.json # browser.model.manifest.v1 (asset_kind=none)
```

Site-relative URLs are served as `/browser-models/<file>` via Vite/`nginx`
`try_files` — no special proxy location and **no** bind-mount of private trees.

## Manifest rules (`browser.model.manifest.v1`)

| Field | Rule |
|-------|------|
| `model_id` | Registry id (e.g. `browser:symbolic-features-v1`) |
| `profile_id` | Algorithm profile (e.g. `symbolic.features.v1`) |
| `ops` | Closed list (`embed`, …) |
| `asset_kind` | `none` \| `onnx` \| `raw_weights` |
| `asset_url` | Site-relative under `/browser-models/` only |
| `sha256` | Hex digest of asset bytes (`""` when `asset_kind=none`) |
| `size_bytes` | Declared size; host refuses loads above the cap |
| `input_spec` / `output_spec` | Non-secret shape hints |

**Max size cap:** 32 MiB per asset (`BROWSER_MODEL_ASSET_MAX_BYTES` in host).

**Digest verification:** SHA-256 over fetched bytes; Cache API / IndexedDB keys
include `sha256` so integrity mismatches bust cache.

## Refuse list

Never list, fetch, or document as browser assets:

- Host trees `models/llm/`, `models/neural-audio/`, `models/audio-recovery/`
- `PERSONAL_COMPOSER_ROOT`, `DATASET_ROOT`
- ExecutionNode weight paths or any absolute server filesystem path
- Opaque blobs without a checked-in manifest digest

Ship-1 handcrafted twin uses `asset_kind=none` — algorithm lives in JS modules
under `frontend/src/utils/browserModels/`, not as a weight file.
