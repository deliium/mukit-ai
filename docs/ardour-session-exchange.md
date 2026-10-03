# Ardour Session Exchange

Move a **selected MIDI region** between Ardour and AI Composer with tempo, meter, track name, and bar span preserved in an exchange package. Product generation is **V5**; the playable score stays **`composition.v2`**. There is no `composition.v5`.

Mukit never edits Ardour `.ardour` session XML and never remote-executes Lua.

## Workflows

### Ardour → Composer

1. Select an 8-bar (or other) MIDI idea in Ardour.
2. Run operator-installed `export_selected_midi_region.lua` (writes `manifest.json` + `material.mid` under `ARDOUR_EXCHANGE_ROOT`), or zip that package and upload it in the Studio **Ardour → Exchange** section.
3. Ingest builds a session-only `ardour.exchange.preview.v1`.
4. Explicit **Apply (replace score)** calls SPA `completeImport` — replace working `composition.v2`, same as MIDI import replace. Merge-into-existing is out of ship-1.

### Composer → Ardour (cello counter-melody acceptance)

1. With a preview (or applied score) available, choose **Realize → Counter-melody**.
2. That runs arrangement `create_countermelody` with a catalog **cello** (GM 42) `role: countermelody` part. Apply the candidate manually.
3. **Prepare outbound package** writes `direction=outbound` with the same alignment fields (`start_bar`, `bar_count`, tempo, meter, `start_samples`, `track_name`).
4. Run `import_exchange_package.lua` (or Session → Import) so Ardour places the MIDI at `start_samples` / the same bars.

```text
Ardour selection → Lua export → ingest → preview → Apply (completeImport)
        → realize create_countermelody → Apply candidate
        → prepare outbound → Lua import at start_samples
```

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

Docker: bind-mount the same host path into the backend as `ARDOUR_EXCHANGE_ROOT` and point Lua at that host path.

Companion OSC (`ARDOUR_COMPANION_ENABLED`) is optional for file-only exchange. See [ardour-companion.md](ardour-companion.md).

## HTTP surface

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ardour/exchange/status` | Always 200 |
| GET | `/ardour/exchange/context` | OSC enrichment |
| POST | `/ardour/exchange/ingest` | Zip or `{ package_id }` |
| GET/DELETE | `/ardour/exchange/preview` | Session preview |
| POST | `/ardour/exchange/apply` | SPA payload only — no project row write |
| POST | `/ardour/exchange/realize` | Arrangement/development preview bridge |
| POST | `/ardour/exchange/prepare` | Outbound package |
| GET | `/ardour/exchange/packages/{id}/download` | Zip |

## Honesty limits

- No `.ardour` XML / playlist edits from Mukit.
- No remote Lua execution.
- No note streaming over OSC as the material path.
- No auto-Apply on ingest or realize.
- Stem WAV optional; neural stems are not claimed sample-locked to regenerated MIDI.
- Opening the Ardour tab never auto-ingests, applies, or prepares.

## See also

- [ardour-companion.md](ardour-companion.md) — OSC transport/mixer
- [daw-interoperability.md](daw-interoperability.md) — whole-score file handoff
- `backend/examples/ardour/README.md` — Lua install
