# Mix analysis (`mix.analysis.v1`)

Analyze **already-rendered** neural stem-set (and optionally mix) WAVs into concrete, measurable production observations — without mutating stem/mix audio or `composition.v2`.

Distinct from symbolic [`composition.analysis.v1`](composition-analysis.md) (note-event sidecar) and from recovery/Demucs stems ([audio-recovery.md](audio-recovery.md)).

## Summary

| Layer | Source | Notes |
|-------|--------|-------|
| **Measurements** | Deterministic DSP | Peak, RMS, crest, clip, stereo, bands, LF, headroom, masking_proxy, … |
| **Observations** | Pure rules on measurements | Coded findings with stem/track/freq/time locus + reason |
| **Interpretations** | Optional fake / soft AI | Advisory prose; cites measurement/observation codes; never invents numbers |
| **Series** | DSP envelopes | `peak_envelope`, `loudness_envelope`, and `band_energy` (sub/low/low_mid over time) |

**Acceptance:** Analyze a multi-stem render → measurements + ≥1 observation linked to stems/tracks and time/freq ranges.

## User flow

1. Render a complete neural stem set ([neural-audio-rendering.md](neural-audio-rendering.md)).
2. Open **Mix Analysis** under the Neural Audio panel (not a separate Composer tab).
3. Select the stem set, optional dimensions / AI notes / Persist, then **Run mix analysis**.
4. Soft-stale banner appears when report fingerprints diverge from live stem set / composition — **never auto-reanalyzes**.

## Active heads

Default member selection keeps, per `stem_role`, the latest **complete** stem that is **not** referenced as `supersedes_stem_id` by a later complete sibling. Explicit `stem_ids[]` must still be complete + readable. Incomplete sets → `422 mix_analysis_stem_set_incomplete`.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/mix-analysis/analyze` | Sync analyze (`persist` optional) |
| `GET` | `/mix-analysis/reports/{id}` | Persisted report JSON (no PCM) |
| `GET` | `/mix-analysis/reports?project_id=` | List metadata |
| `DELETE` | `/mix-analysis/reports/{id}` | Delete row + JSON file |

Analyze opens stem WAVs **read-only** via neural `absolute_audio_path`. Responses never include PCM. Series are downsampled floats with caps.

### Request sketch

```json
{
  "project_id": "…",
  "stem_set_id": "…",
  "mix_render_id": null,
  "stem_ids": null,
  "composition": null,
  "dimensions": ["peak", "loudness", "clipping", "masking_proxy", "headroom"],
  "include_ai_interpretation": false,
  "persist": false
}
```

Without `composition`, loci use **seconds only** (never invent bars).

## DSP backends

| `dsp_backend` | When |
|---------------|------|
| `stdlib` | Default: Python `wave` + pure Goertzel/band proxies |
| `numpy_scipy` | Optional extras installed (`backend/requirements-mix-analysis.txt`) |
| `fake` | `MIX_ANALYSIS_FAKE_MODE=1` — digest-driven deterministic fixtures for CI |

Reverb/ambience is **not** invented: emit `reverb_estimate_unavailable`. Masking is labeled `masking_proxy` (spectral overlap), not psychoacoustic MOS. No ITU-R BS.1770 certification claim.

## Optional AI interpretation

Mirror critique’s fake path — **no new `AiOperation`**. With `include_ai_interpretation=true`:

- `MIX_ANALYSIS_FAKE_MODE` or `LLM_FAKE_MODE` → deterministic interpretations citing existing codes + locus
- Otherwise → warning `interpretation_unavailable`; DSP layers still returned

Subjective AI cannot use severity `error`.

## Persistence

Optional `persist=true` + `project_id` writes `{project_id}/{report_id}.json` under `MIX_ANALYSIS_ROOT` (default beside project DB) and a `mix_analysis_reports` SQLite row. Project delete runs `cleanup_project_mix_analysis` beside neural/recovery GC.

## Configuration (`MIX_ANALYSIS_*`)

| Var | Purpose |
|-----|---------|
| `MIX_ANALYSIS_ROOT` | Durable report JSON root |
| `MIX_ANALYSIS_FAKE_MODE` | Deterministic measurements + fake interpretations |
| `MIX_ANALYSIS_MAX_AUDIO_SECONDS` | Per-stem decode/analyze cap |
| `MIX_ANALYSIS_MAX_TOTAL_INPUT_BYTES` | Sum of input WAV bytes |
| `MIX_ANALYSIS_MAX_REPORTS_PER_PROJECT` | Persist quota |
| `MIX_ANALYSIS_JOB_TIMEOUT_SECONDS` | Wall clock for sync analyze |
| `MIX_ANALYSIS_SERIES_MAX_POINTS` | Viz series cap |
| `MIX_ANALYSIS_OBSERVATION_MAX` | Finding cap |

See `.env.example`.

## Honesty limits

- Never mutates stem/mix WAV or `composition.v2`
- Never invents `composition.v4` / never writes `DATASET_ROOT`
- Never analyzes recovery Demucs stems in v1
- Never treats AI prose as measurements
- Soft-stale is banner-only

## Testing

```bash
cd backend && MIX_ANALYSIS_FAKE_MODE=1 NEURAL_AUDIO_FAKE_MODE=1 LLM_FAKE_MODE=1 \
  ../.venv/bin/python -m pytest tests/test_mix_analysis.py -q
node --test frontend/src/utils/mixAnalysisUi.test.js
```

## See also

- [neural-audio-rendering.md](neural-audio-rendering.md) — stem/mix egress inputs
- [composition-analysis.md](composition-analysis.md) — symbolic analysis (separate domain)
- [composition-critique.md](composition-critique.md) — finding-shape reference (not HTTP-coupled)
- [testing.md](testing.md)
