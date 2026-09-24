# Mix analysis synthetic WAV fixtures

Short stdlib-generated PCM16 WAV files for DSP unit tests (peak, clipping, stereo
imbalance, pairwise masking_proxy). Rebuild with:

```bash
cd backend && ../.venv/bin/python -m tests.fixtures.audio.mix_analysis.builders
```

Never used as `DATASET_ROOT` corpus input. Analysis must leave these bytes unchanged.
