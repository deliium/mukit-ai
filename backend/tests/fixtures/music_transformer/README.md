# Music Transformer fixtures

Tiny CPU-only assets for unit/acceptance tests.

| File | Purpose |
|------|---------|
| `tiny_arch.json` | Micro `music_transformer.config.v1` (2 layers, d_model=64) |
| `tiny_train.json` | Short train loop knobs |
| `seed_one_bar.json` | One-bar Composition V2 seed (prefix / train input) |
| `listening_set.v1.json` | Fixed listening prompts (prefix = `seed_one_bar.json`) |

## CPU smoke train

From `backend/` with torch installed (`pip install -r requirements-music-transformer.txt`):

```bash
python -m app.music_transformer.cli train \
  --arch-config tests/fixtures/music_transformer/tiny_arch.json \
  --config tests/fixtures/music_transformer/tiny_train.json \
  --inputs tests/fixtures/music_transformer/seed_one_bar.json \
  --out /tmp/mt_smoke.pt \
  --steps 2 --device cpu
```
