# V5 Composer OS platform

Product generation label **V5** covers the Adaptive / Picture / Agents / Profiles / Lab / Ardour surfaces built on canonical **`composition.v2`**. There is no `composition.v5` migration and no alternate playable score schema.

## Architecture

- **Source of truth:** audible notes come from `composition.v2` `tracks[].events[]`, or from an explicitly labeled session buffer/preview (continuation, arrangement, ensemble Apply, etc.). Harmony, MusicState, film plans, adaptive graph refs, and ensemble reports alone are not playable.
- **Session vs durable:** continuous MusicState, continuation buffers, playback clocks, musical context, performance realizations, and spatial previews are process-memory (or session) documents unless an explicit Commit/Apply writes V2.
- **Agents isolation:** `ai_agents/` must not import continuous / MusicState / continuation / rights store / Model Lab store / universe store modules (architecture forbid tokens).

## Continuous music

Unbounded-in-session generative windows past stored `bar_count` with MusicState and fallback-first maintain. Contract: [continuous-music.md](continuous-music.md). Predecessor: [adaptive-runtime-continuation.md](adaptive-runtime-continuation.md).

## Migration (V1 → V5 product surface)

1. Plant a `composition.v1` project on an older Alembic revision.
2. Upgrade to head.
3. Open as `composition.v2`.
4. Assert V5 sidecar tables exist and are empty-safe (`adaptive_scores`, `rights_registry_entries`, content provenance, `execution_nodes`, universe/asset/spatial/performance/model_lab as present). MusicState has **no** Alembic table.

Ladder tests: `backend/tests/studio_acceptance/test_migration_ladder.py`.

## Studio scenario matrix

Fake-mode suite under `backend/tests/studio_acceptance/v5/`:

| Group | Scenarios |
|-------|-----------|
| 11a core | Adaptive game, continuation, external context, film scoring, picture adapt, universe, continuous music, cancel / node-failure |
| 11b remaining | Personal adapter, preference, distributed inference, WebGPU stub, expressive MIDI stub, conductor, spatial, Ardour guards, asset pack, provenance, rights, Model Lab, ensemble |

Each scenario asserts the composition source-of-truth helper. Tiny models / mocks / fixtures only — no live GPU or Ardour XML mutation.

## CI vs opt-in

| Lane | Command | Notes |
|------|---------|-------|
| Default CI | `./scripts/run_tests.sh` | ESLint, pytest, unit tests; includes V5 matrix |
| Opt-in Docker | `RUN_DOCKER_ACCEPTANCE=1 ./scripts/v5_docker_acceptance.sh` | Fake modes; restart reopen fingerprint; **not** called by `run_tests.sh` |
| Hardware / torch | `importorskip` / env gates in feature tests | Stay opt-in |

## Operations

- Continuous flags: `ADAPTIVE_CONTINUOUS_ENABLED`, `ADAPTIVE_ENGINE_CONTINUOUS_ENABLED` (default off; see `.env.example`).
- Ardour refuse paths never edit `.ardour` XML (`session_xml` / path escape schema guards).
- Rights hard-fail train/reference when policy refuses.
- Late continuous inference must not stop playback.

## Definition of Done (honesty)

Default CI proves the Composer OS with fakes and stubs. It does not prove live Ardour, GPU LoRA, or production WebGPU devices. Opt-in Docker proves restart durability of a fake autonomous score under V5 continuous/rights overlays.
