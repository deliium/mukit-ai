# Symbolic Music Tokenizer (Composition V2)

Versioned **train + inference** codec that converts canonical `composition.v2` (and `dataset.example.v1` windows) into a closed REMI-style token vocabulary and reconstructs Composition V2 with **documented quantization fidelity**.

CLI / library only: `python -m app.tokenizer.cli`. Never writes to `PROJECT_DB_PATH`, project revision history, or FastAPI routes. Does **not** run model training or load weights.

## Hard rules

- Playable notes come **only** from `tracks[].events[]`. Never invent notes from `harmony`, markers, sections, or `composition.analysis.v1`.
- One scheme for training collation and inference detokenization (`encode` / `decode` share `tokenizer.v1`).
- Logging: structured counts and issue codes only; never API keys, raw MIDI/MusicXML/WAV, full event arrays, or full token-id dumps at INFO (DEBUG may log a bounded prefix, e.g. first 64 tokens).

## Canonical temporal scheme

REMI-style **bar + in-bar position + duration** on a fixed grid:

| Setting | Default | Meaning |
|---------|---------|---------|
| `ticks_per_quarter` | 480 | Must match composition PPQ |
| `grid_subdivisions_per_quarter` | 4 | Sixteenth-note grid → `grid_ticks = 120` |
| `velocity_bins` | 32 | Velocity → bin index; decode restores bin center |
| `max_dur_steps` | 64 | Clamp long notes |
| `profile` | `core` | Omits articulations/ties/automation/motifs; `core_harmony` adds coarse `HARM_*` context |

**Quantization (round-trip contract)**

| Attribute | Encode | Decode | Guarantee |
|-----------|--------|--------|-----------|
| Onset | Nearest grid (half-up / away from zero) | `bar_start + pos * grid_ticks` | Equal after snap |
| Duration | Nearest ≥ 1 step; clamp `max_dur_steps` | `dur * grid_ticks` | Equal after snap |
| Pitch | MIDI 0–127 | Deterministic sharp spelling | MIDI preserved; spelling may change |
| Velocity | Bin 0..bins-1 | Bin center 1..127 | Within bin width |
| Tracks | Slot order = V2 track order | `track_{slot}` ids | Program / slot / `is_drum` preserved |
| Articulations / ties / automation / motifs | Omitted in `core` | Empty defaults | Explicitly lossy |

Emission order: `BOS` → optional conditioning → tempo/meter/key → track headers → per bar `BAR` [meter/tempo/section] → `POS` → notes (`TRACK_SLOT`? `PITCH` `VEL` `DUR`) → `EOS`.

## Special tokens

`PAD` (id 0), `BOS`, `EOS`, `UNK`, `BAR`, `TRACK`, `SECTION`, `DRUM`, plus factored families `POS_*`, `DUR_*`, `PITCH_*`, `VEL_*`, `METER_*`, `TEMPO_*`, `KEY_*`, `TRACK_SLOT_*`, `TRACK_PROG_*`, `SECTION_TYPE_*`, optional `COND_*` and `HARM_*`.

Practical size guard: `TOKENIZER_MAX_VOCAB_SIZE` (default 8192). Default closed vocab is ~1k tokens.

## Conditioning

Optional prefix from explicit args or dataset labels: key, genre, mood, instrument set, desired section type. Unknown labels → `COND_*_UNK`. Decode exposes conditioning in the decode report; it is not forced into V2 fields except where key/meter already appear as structure tokens.

## Invalid sequences

Pipeline: `validate_tokens` → optional `repair_tokens` → `decode`.

- **Repair:** drop before BOS / after EOS; strip PAD; clamp POS/DUR; ignore orphan VEL/DUR; drop harmony when disabled; coalesce duplicate BAR.
- **Reject:** missing BOS/EOS when required; garbage rate above threshold; empty payload when `on_invalid=reject`.
- Never silently invent pitches to fill bars.

## Versioning

- Code constant: `TOKENIZER_VERSION = "tokenizer.v1"` (bump on vocab or quantization rule changes).
- Artifact `tokenizer.manifest.v1`: `tokenizer_version`, `vocab_hash`, `config_digest`, `special_token_ids`, grid/bins/profile, optional `created_at`.
- `vocab_hash` = SHA-256 of canonical JSON token→id map (key-sorted). Config digest excludes wall-clock.
- Future trainers **must** store `expected_tokenizer_version` + `vocab_hash` (see `tokenizer.model_expectation.v1`). CLI `verify` checks match.

## CLI

```bash
cd backend
python -m app.tokenizer.cli encode   --input piece.json --out tokens.json
python -m app.tokenizer.cli decode   --input tokens.json --out composition.json [--require-version]
python -m app.tokenizer.cli roundtrip --input piece.json
python -m app.tokenizer.cli stats    --inputs a.json b.json --out stats.json
python -m app.tokenizer.cli viz      --input tokens.json --out sequence.txt
python -m app.tokenizer.cli vocab    --out vocab.json --manifest-out tokenizer.manifest.json
python -m app.tokenizer.cli verify   --manifest tokenizer.manifest.json [--require-version]
```

`--require-version` on `decode` fails when `tokens.tokenizer_version` / `vocab_hash` disagree with the running codec. On `encode`, pair it with `--expectation` (a `tokenizer.model_expectation.v1` sidecar). On `verify`, it hard-fails version drift; without the flag, hash/digest checks still run and version drift is a warning.
Thin wrapper: `./scripts/tokenizer_roundtrip.sh path/to/v2.json`.

Optional env knobs (`TOKENIZER_*`) are documented in `.env.example`. They are not required for `docker compose up`.

## Statistics & visualization

`tokenizer.stats.v1` includes vocab size, avg tokens/bar, max sequence length, unknown/invalid event rate, snap/drop aggregates, and coarse histograms.

`viz` writes a human-readable token stream (bar breaks) or a compact HTML table (`bar | pos | track | pitch | vel | dur`). Large sequences truncate with a notice.

## Library API

```python
from app.tokenizer.schemas import default_tokenizer_config
from app.tokenizer.vocab import build_vocab
from app.tokenizer.encode import encode_composition
from app.tokenizer.decode import decode_tokens

config = default_tokenizer_config()
vocab = build_vocab(config)
sequence = encode_composition(composition_v2, config, vocab=vocab)
restored, report = decode_tokens(sequence, config, vocab=vocab)
```

## Limits / out of scope

- No FastAPI routes, no project DB persistence, no weight loading / GGUF export.
- Core profile does not round-trip articulations, ties, automation, motifs, staff/voice, or free-text labels.
- Dataset `token_limit` remains a note-count **proxy** until a future optional call to real tokenizer length.
- Next handoff: Composer training loop consumes `tokenizer.v1` + `tokenizer.manifest.v1` (see [local-ai.md](local-ai.md) for optional inference sidecars; training is separate).

## See also

- [Composition V2](composition-v2.md)
- [Symbolic datasets](datasets.md)
- [Optional local AI](local-ai.md)
- Package: `backend/app/tokenizer/`
