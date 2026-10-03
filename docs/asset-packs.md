# Soundtrack / production asset packs

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`.

An **asset pack** turns one franchise brief into several independent soundtrack projects (Main Theme, Menu, Combat, …) that share a Musical Universe, optional Composer Profile soft prefs, and shared production targets — after an inspectable **AssetPackPlan**.

```text
asset.pack.brief.v1
        │ compile (no notes)
        ▼
asset.pack.plan.v1  (AssetPackPlan)
        │ explicit Generate
        ▼
musical.universe.v1  ◄── membership ──►  project per slot (composition.v2)
composer.profile.v1  ── soft brief fold ─►  each slot autonomous run
asset.pack.production.v1 ── shared loudness / render / palette
adaptive.score.v1 (optional per asset) ── state labels from slot roles
musical.dependency.edge.v1 ── theme → asset / variation_of
```

## Terminology

| Term | Meaning |
|------|---------|
| **Asset pack** | Durable `asset.pack.v1` run/document: plan digest, universe id, profile id, slot→`project_id` map. Not a score. |
| **AssetPackPlan** | Non-playable `asset.pack.plan.v1`. Required before generate. |
| **Slot** | One planned soundtrack asset (`slot_id` + label / adaptive label). |
| **Asset project** | Ordinary SQLite project with its own `composition.v2` and revisions. |
| **Shared production targets** | `asset.pack.production.v1` (master target, loudness goal, instrument palette, neural policy). |
| **Theme vocabulary** | Musical Universe Theme A (+ variants) referenced by motif pins — not pitches on the pack. |
| **Partial regeneration** | Rewrite named `slot_ids` only; untouched projects stay byte-identical. |

## Workflow

1. **Brief** — `asset.pack.brief.v1` (title, `game_soundtrack_v1` preset or custom slots, optional `composer_profile_id` + strength, production prefs, seed).
2. **Preview plan** — `POST /asset-packs/plan/preview` compiles AssetPackPlan. No projects created.
3. **Save pack** — `POST /asset-packs` persists plan + slot rows (`status=planned`). Still no generate.
4. **Generate** — `POST /asset-packs/{id}/generate` runs **synchronously**: for each slot in order, write status → autonomous/fake generate → next. Seed binds Theme A; non-seed slots get mechanical `reuse_theme` per the locked propagate table.
5. **Partial regen** — `POST /asset-packs/{id}/regenerate` with `slot_ids[]`. Unknown slots → `422 asset_pack_slot_unknown`.

Opening the Agents-tab **Asset Pack** panel lists packs and never auto-generates, never joins a universe, and never starts neural renders.

## Composer Profile (soft only)

Ship-1 does **not** extend `AutonomousRunStartV1` with `profile_id`. The pack orchestrator calls `resolve_profile_merge` and folds soft prefs into each slot `creative.brief.v1` (narrative / instrumentation hints). Plan hard constraints always win. Pack provenance stamps `composer_profile_id` + strength.

## Theme consistency

- Seed slot (default `main_theme`) must expose a motif whose `label` matches `motif_label` (default `Theme A`) with an `original` occurrence — else `asset_pack_seed_motif_missing`.
- Non-seed slots reuse Theme A with the locked propagate ops (repeat / transpose semitones). Destination gets a pitched landing track (non-melody preferred when clearing for overlap).
- Dependency graph records `variation_of` edges for reuse. Impact lists stale dependents; it does **not** auto-rewrite them.

## Adaptive scaffolds

When `include_adaptive_scaffolds` is true, each completed slot may get a minimal per-asset `adaptive.score.v1`: state id from the slot adaptive label, material = first section or `bar_range` 1..`bar_count`, empty transitions/layers/stingers. Invalid material → WARN skip, not pack fail. One adaptive score never spans multiple projects.

## Production / render honesty

- `guarantee` is always `false`. Loudness goals and master targets are not loudness guarantees.
- `include_rendering` defaults **false**. Symbolic generate + theme trace is hard acceptance; neural enqueue is optional policy on the production document.

## Routes

| Method | Path | Effect |
|--------|------|--------|
| POST | `/asset-packs/plan/preview` | Compile plan from brief. No SQLite project create |
| POST | `/asset-packs` | Persist pack + plan (`planned`) |
| GET | `/asset-packs` | List packs |
| GET | `/asset-packs/{id}` | Pack + plan |
| GET | `/asset-packs/{id}/slots` | Slot status rows |
| POST | `/asset-packs/{id}/generate` | Sync generate (plan digest + revision CAS) |
| POST | `/asset-packs/{id}/regenerate` | Partial slot rewrite |

## UI

Agents tab → `AssetPackPanel` (mounted from `MultiAgentPanel` beside Autonomous / Film Score). Ordered steps: brief → plan preview → save → generate → slot status → multi-select regenerate. Profile picker strength defaults to `normal`.

## Logging / redaction

INFO may include truncated pack id, plan digest prefix, `slot_id`, truncated `project_id`, status transitions, regenerate slot count.

DEBUG may include compiler slot counts, truncated universe / profile ids, adaptive scaffold created/skipped.

Never log prompts, soft fragments, event arrays, full brief narrative at INFO, or WAV bytes. Pack/plan bodies reject embedded note keys (`events`, `notes`, `pitch`, …).

## Agent boundary

`ai_agents/` must not import `asset_pack_store`, `asset_pack_service`, `asset_pack_generate`, or `asset_pack_brief`. Orchestration lives in `services/` + `routers/`.

## See also

- [Musical universe](musical-universe.md) — Theme A bind and mechanical reuse
- [Autonomous composer](autonomous-composer.md) — per-slot generate engine
- [Composer profiles](composer-profiles.md) — soft preference merge
- [Adaptive score](adaptive-score.md) — per-project state graphs (refs only)
- [Derived material graph](derived-material-graph.md) — `variation_of` edges without auto-rewrite
