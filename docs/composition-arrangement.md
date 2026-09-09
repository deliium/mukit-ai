[← Composition Development](composition-development.md) · [Back to README](../README.md) · [Composition Analysis →](composition-analysis.md)

# Composition Arrangement

Stateless multi-candidate instrumentation and texture redistribution over strict `composition.v2`. Preview returns 1–4 independently realized candidates; nothing mutates, dirties, autosaves, or persists until the client **Apply**s one verified candidate through the normal composition edit path. Previews are **session-only**; only the applied Composition V2 is stored in projects.

## Summary

| Rule | Detail |
|------|--------|
| Endpoints | `GET /composition/arrangement/instruments`, `POST /composition/arrangement/preview` |
| Input | Canonical `CompositionV2` only (V1 rejected) |
| Persistence | None on preview — candidates are ephemeral |
| Playable source | `tracks[].events[]` only; harmony/analysis never invent notes |
| Catalog | Versioned curated GM palette (`arrangement.instruments.v1`); IDs/ranges are **not** persisted on V2 tracks |
| Fingerprint | Full-document `composition.edit.v1` (`edit_source_fingerprint`) plus catalog/profile fingerprints |
| Apply | Frontend verifies fingerprints, topology manifest, assertions, then one undo entry + dirty/autosave |

## Usage / user-facing behavior

1. Open the **Arrange** tab with a valid V2 composition.
2. Load the instrument catalog (`GET /composition/arrangement/instruments`) — used for selectable targets and role vocabulary.
3. Choose an operation, source/protected tracks, and explicit **before** / **after** part inventories.
4. Preview — working `editedMusicJson`, history, dirty/autosave, analysis, and notation stay unchanged.
5. Select / audition a candidate (transport may play the candidate; source composition unchanged).
6. Apply — client rechecks edit-source + candidate + catalog fingerprints, topology vs manifest, and required assertions; one undo entry; dirty + autosave of applied V2 only.
7. Discard or regenerate when the source revision or catalog fingerprint changes (previews become stale).

Preferred-range warnings (`questionable_range`) require explicit confirmation before Apply. Absolute range failures cannot be overridden.

## Configuration

| Setting | Purpose |
|---------|---------|
| `ARRANGEMENT_INSTRUMENT_CATALOG_PATH` | Optional **absolute path inside the backend process** to a validated `arrangement.instruments.v1` JSON file. Unset = packaged fixture `backend/app/fixtures/arrangement_instruments.v1.json` |
| `LOG_LEVEL` | Backend verbosity (`DEBUG`/`INFO`/…) |
| `VITE_LOG_LEVEL` | Frontend logger gate (`debug`/`info`/`warn`/`error`/`silent`); production defaults reduce noise |
| `LLM_FAKE_MODE` / provider `fake` | Deterministic arrangement drafts without network |
| `LLM_FAKE_INJECT_MALFORMED=arrangement` | Exhaust candidates for failure tests |

Invalid configured catalog → `/ready` is **false** and catalog/preview return `503` `arrangement_catalog_unavailable`. Readiness reports version/fingerprint/`source_path_category` only — never override file contents.

### Catalog override (Docker)

The path must be **visible inside the backend container**. An arbitrary host path does **not** work automatically.

Example read-only bind (with `compose.dev.yml` or an equivalent volume) plus env:

```yaml
# compose.dev.yml (excerpt) — uncomment and set a real host file
volumes:
  - ./path/to/arrangement_instruments.v1.json:/data/arrangement_instruments.v1.json:ro
```

```bash
# .env / Compose environment (container path, not the host path)
ARRANGEMENT_INSTRUMENT_CATALOG_PATH=/data/arrangement_instruments.v1.json
```

`docker-compose.yml` already forwards `ARRANGEMENT_INSTRUMENT_CATALOG_PATH` into the backend service.

## Endpoints

### `GET /composition/arrangement/instruments`

Returns curated profiles plus the complete canonical track-role vocabulary (`track_roles` = full `SUPPORTED_TRACK_ROLES`). Per-instrument `suggested_roles` are advisory only.

Response fields include `catalog_version`, `range_policy_version`, catalog `fingerprint`, `source_path_category` (`packaged` \| `override` \| …), and per-profile concert-pitch ranges / GM metadata / profile `fingerprint`.

### `POST /composition/arrangement/preview`

Stateless multi-candidate preview. Never reads or writes projects.

| Field | Notes |
|-------|-------|
| `composition` | Strict V2 |
| `operation` | One of the ten operations below |
| `source_track_ids` / `protected_track_ids` | Unique existing IDs; must be disjoint |
| `instrumentation.before` / `.after` | One row per part: `part_id`, `instrument_id`, optional `role`, `source_track_ids`, `doubling_policy` |
| `allow_unlisted_after` | Default `false` |
| `preserve_melody` / `preserve_harmony` | Default `true` |
| `range_adjustment` | `reject` (default) or `octave_shift_unprotected` (non-melody only) |
| `candidate_count` | 1–4 |
| `instruction` | Optional, normalized, ≤ 500 chars |
| `selection` / `options` | Provider/model; repairs and context budget (default 12_000, max 48_000 chars) |

## Operations

| Operation | Authorized transformation | Required invariant |
|-----------|---------------------------|--------------------|
| `change_instrumentation` | Re-instrument selected tracks without topology/event changes | Selected events/roles exact; after inventory differs |
| `add_accompaniment` | Add harmony/rhythm/pad/bass tracks | Source tracks exact; audible event/track count increases |
| `remove_accompaniment` | Remove selected accompaniment-role tracks | Melody/lead + unselected/protected exact |
| `orchestrate_selected_tracks` | Redistribute selected material among targets | Every protected source note has exactly one primary representation |
| `piano_to_ensemble` | Split piano among ≥ 2 target instruments | Distributed, not wholesale cloned; melody preservation passes |
| `simplify_arrangement` | Remove/consolidate authorized non-melody | Density/simultaneity decreases; no new material |
| `increase_texture_density` | Add compatible notes/parts | At least one targeted density metric increases |
| `decrease_texture_density` | Remove authorized non-melody | Density decreases; melody protected |
| `create_countermelody` | Add `countermelody` role part(s) | Harmonically compatible; not an excessive melody clone |
| `double_melody` | Declared instrument/register doubling | Exactly one authorized doubling relationship |

## Role versus instrument

- **Role** (`track.role` / inventory `role`) = musical function (`melody`, `bass`, `harmony`, …).
- **Instrument** = sounding identity (`track.instrument` label + `midi_program` for GM export).
- A cello may carry melody, bass, harmony, or countermelody. Same-instrument parts are not duplicates solely by sharing a program; part IDs, roles, source mapping, and `doubling_policy` make intent explicit.
- Rendering/export must not override explicit instrument/program identity from role.

## Catalog and range policy

| Concept | Detail |
|---------|--------|
| Scope | Curated practical GM palette (piano, organ, guitars, basses, strings, winds, brass, choir, common synth lead/pad, drum kit). Full 128-program GM table remains available for **import** only |
| Versions | `catalog_version` = `arrangement.instruments.v1`; `range_policy_version` = `arrangement.ranges.v1` |
| Fingerprints | Canonical catalog fingerprint + per-profile fingerprints; mismatch invalidates previews |
| Ranges | Concert/sounding MIDI: `playable_low/high` (absolute) and `preferred_low/high`. Policies: `absolute`, `unbounded`, `unknown` |
| Hard vs soft | Absolute out-of-range on **changed/created** targets → hard candidate error. Preferred-range outliers → `questionable_range` warning. Untouched source outliers → baseline findings only |
| Not in V2 | Catalog `instrument_id` and range metadata are **never** persisted on tracks; Apply materializes existing V2 fields (`instrument`, `role`, `midi_program`, `channel`, `is_drum`) |

## Explicit before / after instrumentation (example)

Piano sketch → piano + cello + string ensemble (`piano_to_ensemble`):

```json
{
  "operation": "piano_to_ensemble",
  "source_track_ids": ["piano-melody", "piano-accomp", "bass"],
  "protected_track_ids": [],
  "preserve_melody": true,
  "preserve_harmony": true,
  "instrumentation": {
    "before": [
      {
        "part_id": "before-melody",
        "instrument_id": "acoustic_grand_piano",
        "role": "melody",
        "source_track_ids": ["piano-melody"],
        "doubling_policy": "none"
      },
      {
        "part_id": "before-accomp",
        "instrument_id": "acoustic_grand_piano",
        "role": "harmony",
        "source_track_ids": ["piano-accomp"],
        "doubling_policy": "none"
      },
      {
        "part_id": "before-bass",
        "instrument_id": "acoustic_grand_piano",
        "role": "bass",
        "source_track_ids": ["bass"],
        "doubling_policy": "none"
      }
    ],
    "after": [
      {
        "part_id": "after-piano",
        "instrument_id": "acoustic_grand_piano",
        "role": "melody",
        "source_track_ids": ["piano-melody"],
        "doubling_policy": "none"
      },
      {
        "part_id": "after-cello",
        "instrument_id": "cello",
        "role": "bass",
        "source_track_ids": ["bass"],
        "doubling_policy": "none"
      },
      {
        "part_id": "after-strings",
        "instrument_id": "string_ensemble_1",
        "role": "harmony",
        "source_track_ids": ["piano-accomp"],
        "doubling_policy": "none"
      }
    ]
  },
  "candidate_count": 3
}
```

`before` is a precondition (validated before the provider). `after` is a postcondition on realized candidates. Aggregate instrument/role counts are derived from list multiplicity (one row per part).

## Safety guarantees

| Guarantee | Behavior |
|-----------|----------|
| Source immutability | Preview never mutates the request composition or project store |
| Explicit events | Candidates are strict V2 with explicit `tracks[].events[]` only |
| Harmony / form | Top-level key, sections, tempo/meter, duration, markers, timeline, and `harmony` stay byte-equivalent |
| Melody | Cross-track semantic multiset of logical notes; redistribution allowed; undeclared clones rejected |
| Harmony compatibility | `preserve_harmony` checks generated notes against unchanged declared/inferred context without persisting analysis |
| Source-note redistribution | Drafts use request-local note refs; every selected note is retained, moved, removed, or doubled as authorized |
| Duplicate / doubling | Accidental clones rejected; `doubling_policy` `unison` \| `octave` \| `declared` exempts only declared overlap |
| Channels | Deterministic allocation on 15 non-drum channels; channel 10 reserved for drums; unsafe topologies fail before Apply/export |
| Topology manifest | Every base/candidate track explained; Apply rejects unexplained track/event changes |
| Motifs | Occurrences remapped or pruned only when authorized; split-across-targets rejected |
| Catalog staleness | Catalog fingerprint change → existing previews stale; regenerate required |

## Candidates, fingerprints, and lifecycle

Each candidate includes: full `CompositionV2`, `candidate_id`, `candidate_fingerprint`, `edit_source_fingerprint`, `algorithm_version` (`composition.arrangement.v1`), catalog/range versions, `catalog_fingerprint`, `target_profile_fingerprints`, before/after inventories, topology `manifest`, event counts, density metrics, range/duplicate findings, harmony compatibility, assertions, and warning codes.

Rejected attempts are a **separate** array: ordinal, stage, stable codes, bounded reasons — never a partial composition.

Frontend lifecycle: Preview → Select/Audition → Apply (verify) → Undo/Redo. Project hydrate/import/generation resets arrangement state.

## Fake mode

With `LLM_FAKE_MODE=1` (or provider `fake`), `draft_fake_composition_arrangement` returns deterministic relative drafts that still pass production realization/validation. Malformed injection: `LLM_FAKE_INJECT_MALFORMED=arrangement`.

## Error and warning codes

### HTTP / domain errors (`CompositionArrangementError` / route mapping)

| Code | Typical HTTP | When |
|------|--------------|------|
| `arrangement_request_too_large` | 422 | Size/budget limits |
| `arrangement_invalid_operation` | 422 | Inconsistent operation parameters |
| `arrangement_noop_instrumentation` | 422 | Before/after identical when change required |
| `arrangement_invalid_accompaniment_role` | 422 | `remove_accompaniment` non-accompaniment roles |
| `arrangement_piano_required` | 422 | `piano_to_ensemble` without piano before |
| `arrangement_ensemble_targets_required` | 422 | `piano_to_ensemble` needs ≥ 2 after instruments |
| `arrangement_countermelody_required` | 422 | Missing countermelody after role |
| `arrangement_doubling_required` | 422 | `double_melody` needs exactly one doubling |
| `arrangement_source_protected_overlap` | 422 | Source ∩ protected nonempty |
| `arrangement_invalid_source` | 422 | Source/protected authorization failed |
| `arrangement_inventory_mismatch` | 422 | Before/after inventory validation failed |
| `arrangement_preservation_failed` | 422/502* | Required preservation failed |
| `arrangement_range_failed` | 422/502* | Absolute range / policy on targets |
| `arrangement_draft_invalid` | 502* | Provider draft failed relative validation |
| `arrangement_candidate_exhausted` | 502 | No candidate survived generation/repair |
| `arrangement_provider_unavailable` | 503 | No usable provider |
| `arrangement_provider_error` | 502/503 | Sanitized provider failure |
| `arrangement_catalog_unavailable` | 503 | Catalog missing/invalid |
| `arrangement_internal_error` | 500 | Unexpected sanitized failure |

\*Provider-generated postcondition failures that exhaust repairs/candidates surface as **502**; impossible request/preflight issues are **422**.

### Warning codes (non-blocking / partial success)

Includes: `candidate_failed_validation`, `candidate_failed_preservation`, `candidate_failed_range`, `candidate_failed_density`, `candidate_failed_duplicate`, `candidate_repaired`, `candidate_partial_success`, `context_truncated`, `empty_harmony_context`, `questionable_range`, `baseline_range_retained`, `baseline_instrument_mismatch`, `ambiguous_role`, `mild_harmony_tension`, `density_metric_advisory`, `motif_occurrence_pruned`, `declared_doubling_applied`, `octave_adjustment_applied`, `unlisted_after_allowed`.

Range finding codes: `absolute_out_of_range`, `questionable_range`, `baseline_range_retained`, `unbounded_policy`, `unknown_policy`.  
Duplicate finding codes: `exact_clone`, `high_overlap_clone`, `cross_instrument_clone`, `cross_role_clone`, `octave_melody_clone`, `declared_doubling`, `legitimate_same_instrument_parts`.

Stable definitions live in `backend/app/arrangement_schemas.py`.

## Logging (privacy-safe)

**Allowed structured fields:** operation, stage, provider, model, catalog/range versions, `source_path_category`, candidate ordinal/counts, timings (`elapsed_ms`), stable error/warning codes, fingerprint **prefixes** (12 chars), part/track/event **counts**, instruction **length** (not text).

**Prohibited:** API keys; prompt/instruction text; provider raw output; full compositions or analysis reports; event/pitch/harmony arrays; catalog override file contents; uploaded/export payloads.

Frontend arrangement loggers use `VITE_LOG_LEVEL` via `createAppLogger` and must stay payload-free in production.

## Limits (selected)

| Limit | Value |
|-------|-------|
| Candidates | 1–4 |
| Instruction | 500 chars |
| Context budget | up to 48_000 chars (default 12_000) |
| Source / after parts | ≤ 64 each |
| Events / tracks (request) | ≤ 50_000 / 64 |
| Draft notes per part / source-note refs | ≤ 4_096 / 8_192 |

## Acceptance scenario

Seed piano melody + piano accompaniment + bass → Arrange `piano_to_ensemble` for `acoustic_grand_piano` / `cello` / `string_ensemble_1` with three candidates → working piece unchanged during preview → apply a non-first candidate → melody and harmony verifiably preserved, explicit target events, GM-consistent programs/channels, no accidental clone tracks → save/reopen persists only applied V2. Playwright: `frontend/e2e/arrangement-workflow.spec.js` with `LLM_FAKE_MODE=1`.

## Testing

Focused backend:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_instrument_catalog.py \
  tests/test_arrangement_schemas.py \
  tests/test_composition_arrangement_context.py \
  tests/test_composition_arrangement_patch.py \
  tests/test_composition_arrangement_validation.py \
  tests/test_llm_composition_arrangement.py \
  tests/test_composition_arrangement_routes.py
```

Full backend / frontend / E2E:

```bash
cd backend && ../.venv/bin/python -m pytest
cd frontend && npm test && npm run build
cd frontend && LLM_FAKE_MODE=1 npm run test:e2e -- e2e/arrangement-workflow.spec.js
```

Frontend unit focus: `compositionArrangementCandidates.test.js`, `musicStore.arrangement.test.js`, arrangement paths in `musicApi.test.js`.

## Deferred scope

Not in this release: section/bar-scoped orchestration; mid-track instrument changes; divisi/staff-group engraving; transposed written-pitch notation; articulation/technique-specific ranges; seating/balance simulation; complete orchestration knowledge for every GM program; reharmonization during arrangement; persisting catalog IDs or range metadata inside V2.

## Module map

| Layer | Location |
|-------|----------|
| DTOs / codes | `backend/app/arrangement_schemas.py` |
| Catalog | `backend/app/services/instrument_catalog.py`, `fixtures/arrangement_instruments.v1.json` |
| Context / patch / validation | `composition_arrangement_context.py`, `*_patch.py`, `*_validation.py` |
| Orchestration | `backend/app/services/llm_composition_arrangement.py` |
| Fake drafts | `backend/app/services/fake_llm.py` (`draft_fake_composition_arrangement`) |
| HTTP | `backend/app/routers/arrangement.py` |
| Readiness | `backend/app/ready.py` (`arrangement_catalog`) |
| Client API | `frontend/src/api/musicApi.js` |
| Candidates / verify | `frontend/src/utils/compositionArrangementCandidates.js` |
| Store / UI | `musicStore.js` arrangement slice, `ArrangementPanel.jsx` |

## See Also

- [Composition V2](composition-v2.md) — canonical contract (catalog IDs/ranges are not V2 fields)
- [Composition Development](composition-development.md) — continue / vary (topology-preserving sibling preview)
- [Composition Analysis](composition-analysis.md) — advisory range/role findings; not arrangement authority
- [Testing](testing.md) — focused and Playwright commands
- [Project persistence](project-persistence.md) — only applied V2 is stored
