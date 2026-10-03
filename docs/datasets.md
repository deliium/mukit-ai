# Symbolic music datasets

Offline pipeline that turns licensed MIDI, MusicXML/MXL, and Composition V2 JSON into a **versioned, provenance-gated training corpus** under `DATASET_ROOT`. This is separate from user projects (`PROJECT_DB_PATH`) and does **not** run a model training loop. Tokenization of examples is a separate package ([tokenizer.md](tokenizer.md)).

## Relationship to other features

| Feature | Role |
|---------|------|
| [Import](import.md) | HTTP multipart → workspace `composition.v2`; reused as parsers here |
| [Tokenizer](tokenizer.md) | REMI-style Composition V2 ↔ token ids (`python -m app.tokenizer.cli`); consumes examples/splits read-only |
| [Music Transformer](music-transformer.md) | Experiment train/eval/listen/compare over tokenizer-encoded examples; never `PROJECT_DB_PATH`; metrics ≠ musical quality |
| [Rights governance](rights-governance.md) | Shared evaluator + `rights/` siblings (`index.jsonl`, `manifest.json`); `reference_only` never enters train splits |
| [Local AI](local-ai.md) | Optional inference sidecars; Compose `--profile training` points at offline Music Transformer CLI (not the web API) |
| Projects / revisions | Never used; corpora are filesystem-only |

## Layout

```text
$DATASET_ROOT/<dataset_name>/<dataset_version_id>/
  config.snapshot.yaml
  manifest.json          # dataset.manifest.v1
  items/<item_id>.json   # dataset.item.v1
  blobs/v2/<hash>.json   # optional CAS Composition V2
  examples/<id>.json     # dataset.example.v1 windows
  splits/train.jsonl
  splits/validation.jsonl
  splits/test.jsonl
  rights/index.jsonl     # rights.registry.entry.v1 lines (sibling; not in version digest)
  rights/manifest.json   # model.data.provenance.manifest.v1 (dataset_train_split)
  stats.json             # dataset.stats.v1
  BUILD_ID
```

`dataset_version_id` is a short hex of SHA-256 over `{pipeline_version, config_digest, sorted item content digests, split_seed, schema versions}`. Wall-clock `created_at` is stored in the manifest but **excluded** from the version digest so rebuilds are bit-identical.

## Provenance (mandatory)

Every source needs a status (sidecar `*.meta.json` / `*.meta.yaml`, or pipeline defaults). Fail closed if missing.

| Status | Train-eligible? |
|--------|-----------------|
| `verified_redistributable` | yes (license + URL/reference required) |
| `user_owned` | yes (`user_owned_attested: true` required) |
| `public_domain` | yes (URL/reference required) |
| `restricted` | **no** (inventory only) |
| `unknown` | **no** (inventory only) |

`unknown` / `restricted` never appear in `splits/train.*` unless `eligibility.allow_unsafe_train_pollution` is explicitly true (logs ERROR).

Train eligibility also consults the shared rights evaluator after mapping
sidecars (including optional `use_policy`). A source with
`use_policy=reference_only` is excluded from train even when legacy
`provenance_status` looks train-shaped. Rights sibling bytes are not folded into
`dataset_version_id`. Details: [rights-governance.md](rights-governance.md).

## CLI

From `backend/`:

```bash
python -m app.dataset.cli build --config path/to/pipeline.yaml --out ../datasets
python -m app.dataset.cli verify --dataset-dir ../datasets/<name>/<version_id>
```

Or `scripts/dataset_build.sh --config …`. Documented phase subcommands (`ingest`, `normalize`, `segment`, `dedup`, `split`, `stats`) currently run the full reproducible `build` path.

### Pipeline YAML (`dataset.pipeline.v1`)

Minimal example (see `backend/tests/fixtures/dataset/pipeline.yaml`):

```yaml
schema_version: dataset.pipeline.v1
dataset_name: my_corpus
sources:
  - path: /licensed/midi
    glob: "**/*.{mid,midi,musicxml,xml,mxl,json}"
    default_provenance: verified_redistributable
    license_spdx: CC-BY-4.0
    source_url: https://example.com/corpus
segmentation:
  modes: [bars, token_limit]
  token_limit: 256
  token_proxy: note_events   # proxy only — real vocab length is app.tokenizer (optional later)
split_seed: 20260921
train_ratio: 0.8
val_ratio: 0.1
test_ratio: 0.1
```

Per-file sidecars override defaults: `score.mid.meta.json` with `provenance_status`, license fields, labels.

## Normalization

Reuses MIDI/MusicXML canonicalize + Composition JSON normalize (V1 → V2 migrate only). Dataset post-pass:

- Target PPQ (default 480)
- Optional exact duplicate-note collapse (`DATASET_COLLAPSE_DUP_NOTES=1`)
- Dataset fingerprint profile `dataset.fingerprint.v1` (notes/timing/instruments) — distinct from analysis/edit fingerprints

Raw MIDI/MusicXML still yield `harmony: []`. Analysis is never written into playable V2; stats may report declared key/meter distributions only.

## Segmentation

| Mode | Behavior |
|------|----------|
| `bars` | One example per bar |
| `sections` | One per section (trivial full-score `unsectioned` may be skipped) |
| `token_limit` | Pack bars until note/control proxy ≥ limit |
| `phrases` | Motifs / phrase-like markers / typed sections; else fallback (`bars`/`sections`/`skip`) |

## Dedup and splits

Exact: source bytes hash + normalized content hash. Near-dup: coarse buckets + quantized onset-pitch Jaccard. Split assignment is by **cluster** (seeded RNG); examples inherit the parent cluster. Clusters never span train/val/test.

## Configuration (env)

Optional for normal app up — see `.env.example`:

```text
DATASET_ROOT=./datasets
DATASET_TARGET_PPQ=480
DATASET_COLLAPSE_DUP_NOTES=1
DATASET_MAX_SOURCE_BYTES=50000000
DATASET_MAX_NOTES=500000
DATASET_MAX_BARS=4096
DATASET_SPLIT_SEED=20260921
```

Limits are independent of `IMPORT_*` upload caps. Default Compose does **not** mount `DATASET_ROOT`.

## Logging / secrets

Structured extras via `LOG_LEVEL`: dataset name, version id prefixes, item/source ids, provenance status, counts, issue codes, elapsed_ms. Never log raw MIDI/MusicXML bytes, full event arrays, API keys, or full license file bodies (SPDX/id + path only).

## Handoff to future trainers

| Artifact | Use |
|----------|-----|
| `splits/*.jsonl` | example_id + relative path |
| `examples/*.json` | Composition V2 windows |
| `manifest.json` / `stats.json` | Experiment tracking |

Trainers must still refuse to override provenance filters without an explicit flag.

## Troubleshooting

- **Provenance missing** — add sidecar or `default_provenance` on the source entry.
- **Verify mismatch** — config or item content changed; rebuild instead of editing `manifest.json`.
- **Empty train split** — only non-eligible sources present, or too few clusters for the seed/ratios.
- **Phrase warnings** — expected when scores lack motifs/markers; configure `phrase_fallback`.
