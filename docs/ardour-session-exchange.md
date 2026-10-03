# Ardour Session Exchange

Move a **selected MIDI region** between Ardour and AI Composer with tempo, meter, track name, and bar span preserved in an exchange package. Product generation is **V5**; the playable score stays **`composition.v2`**. There is no `composition.v5`.

Mukit never edits Ardour `.ardour` session XML and never remote-executes Lua.

## Workflows

### Connected Ardour workflow (Studio tab)

The **Ardour** tab is an ordered surface: session → transport/timecode → musical scope → **Send to AI Composer** → AI op / alternatives → **Send back**. AI Composer holds a temporary working score for AI ops; **Ardour remains the DAW**. Opening the tab never auto-connects, auto-ingests, or auto-applies.

### Ardour → Composer

1. Select an 8-bar (or other) MIDI idea in Ardour.
2. Run operator-installed `export_selected_midi_region.lua` (writes `manifest.json` + `material.mid` under `ARDOUR_EXCHANGE_ROOT`), or zip that package and upload it under **Send to AI Composer**.
3. Ingest builds a session-only `ardour.exchange.preview.v1`.
4. Explicit **Apply inbound (replace score)** calls SPA `completeImport` — replace working `composition.v2`, same as MIDI import replace. Merge-into-existing is out of ship-1.

### Composer → Ardour (return)

1. Choose an AI op → **Realize** (preview only) → review alternatives → **Apply realize candidate** (seeds arrangement/development/harmony store surfaces, then existing Apply helpers).
2. **Prepare outbound package** prefers the SPA **working** `composition.v2` when present (post-Apply), with preview alignment. Fallback remains the inbound draft when no working composition is sent.
3. Run `import_exchange_package.lua` (or Session → Import) so Ardour places the MIDI at `start_samples` / the same bars.

Acceptance asserts **realized material in outbound MIDI**, not alignment-only.

```text
Ardour selection → Lua export → ingest → preview → Apply inbound (completeImport)
        → realize (intent) → Apply candidate → prepare (working V2 + alignment)
        → Lua import at start_samples
```

## Realize intents

| Musician op | Intent | Engine surface | Notes |
|-------------|--------|----------------|-------|
| Generate accompaniment | `add_accompaniment` | arrangement `add_accompaniment` | Melody promote; after = melody + `acoustic_grand_piano` `role: harmony` |
| Generate counterpoint | `counter_melody` | arrangement `create_countermelody` | Cello GM 42 |
| Create variation | `regenerate_region` | development `vary_section` | Alignment bars |
| Reharmonize selection | `reharmonize_selection` | harmony `preview_reharmonization` | Locked `deterministic` / `reharmonize` / `preserve_harmony_adapt_melody`; empty `harmony: []` may invent spans then adapt melody |
| Orchestrate selection | `orchestrate_selection` | arrangement `orchestrate_selected_tracks` | After = violin `melody` + cello `bass` + `string_ensemble_1` `harmony` |
| Arrangement variation (legacy) | `arrangement_variation` | arrangement `change_instrumentation` | Kept for backward compatibility |
| Create stems / render | SPA-only | neural stem-set + prepare `stem_id` | No new exchange route; optional; not sample-locked to MIDI |

## Prepare working-composition exception

Forbidden-key scan still refuses `composition` / `events` / … on **manifest JSON and ingest** bodies. **`ArdourExchangePrepareRequestV1` may include top-level `composition`** as playable `composition.v2` (nested event arrays expected). Realize request bodies still must not carry event arrays or full compositions.

## Package layout

```text
<aex_…>/
  manifest.json     # ardour.exchange.manifest.v1
  material.mid      # SMF
  audio/            # optional stem_*.wav
```

## Alignment authority

| Source | Authority |
|--------|-----------|
| Lua / package **manifest** | Tempo, meter, `start_bar`, `bar_count`, `start_samples`, track name |
| OSC **session context** | Live locate samples, selected strip, optional `sample_rate` when companion connected (`SET_SURFACE_FEEDBACK=9243`) |
| SMF conductor | Notes + tempo events; may be incomplete for a region export |

Ship-1 alignment math assumes constant tempo across the region.

## Environment

| Variable | Default | Notes |
|----------|---------|-------|
| `ARDOUR_EXCHANGE_ENABLED` | off | Unrecognized → off; status GET still 200 |
| `ARDOUR_EXCHANGE_ROOT` | unset | Mukit-owned; refuses `DATASET_ROOT` / `PROJECT_DB_PATH` |
| `ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES` | 25 MiB | Multipart ingest via `read_upload_bounded` → 413 |
| `ARDOUR_EXCHANGE_MAX_PACKAGES` | 32 | GC cap |
| `ARDOUR_EXCHANGE_PACKAGE_TTL_SECONDS` | 86400 | GC TTL |

Docker: bind-mount the same host path into the backend as `ARDOUR_EXCHANGE_ROOT` and point Lua at that host path. Install helpers: `./scripts/install_ardour_helpers.sh`.

Companion OSC (`ARDOUR_COMPANION_ENABLED`) is optional for file-only exchange. See [ardour-companion.md](ardour-companion.md).

## HTTP surface

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ardour/exchange/status` | Always 200 |
| GET | `/ardour/exchange/context` | OSC enrichment |
| POST | `/ardour/exchange/ingest` | Zip or `{ package_id }` |
| GET/DELETE | `/ardour/exchange/preview` | Session preview |
| POST | `/ardour/exchange/apply` | SPA payload only — no project row write |
| POST | `/ardour/exchange/realize` | Arrangement / development / harmony preview bridge |
| POST | `/ardour/exchange/prepare` | Outbound package (optional working `composition`) |
| GET | `/ardour/exchange/packages/{id}/download` | Zip |

## Honesty limits

- No `.ardour` XML / playlist edits from Mukit.
- No remote Lua execution.
- No note streaming over OSC as the material path.
- No auto-Apply on ingest or realize.
- Stem WAV optional; neural stems are not claimed sample-locked to regenerated MIDI.
- Opening the Ardour tab never auto-ingests, applies, or prepares.
- OSC + Lua + exchange satisfies acceptance **without LV2** (see companion “Why not LV2”).

## See also

- [ardour-companion.md](ardour-companion.md) — OSC transport/mixer + workflow UI + LV2 decision
- [daw-interoperability.md](daw-interoperability.md) — whole-score file handoff
- `backend/examples/ardour/README.md` — Lua install + connected-workflow quickstart
- `scripts/install_ardour_helpers.sh` — copy Lua recipes
