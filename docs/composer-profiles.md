# Composer Profiles

Durable, **user-controlled musical preference documents** (`composer.profile.v1`) that
optionally soft-condition `/llm/generate-music-json`. Profiles are **not** playable
scores, not `composition.analysis.v1`, not embedding vector dumps, and never training
rows under `DATASET_ROOT`.

## Summary

- Named multi-profile library in local SQLite (`PROJECT_DB_PATH`).
- **Explicit** preferences (user-authored or promoted) vs **derived** statistics
  (advisory aggregates from selected projects).
- Generation strength: **Off · Light · Normal · Strong** (soft fragment gain only).
- **Priority lock:** project prompt + hard `GenerationConstraints` always win.
  Profiles never rewrite instruments, key, meter, tempo, sections, duration,
  instructions, genre, mood, or complexity on the request DTO.

## User-facing behavior

1. Open the **Profiles** workspace tab.
2. Create a profile manually, or **Derive** from one or more projects (abstract
   bands/histograms/labels only — no melodies or event arrays).
3. Optionally **Promote derived → explicit**, edit notes, compare, export/import JSON.
4. In Music Generator, pick a profile and strength (default **Off** / no profile).
5. Generate: soft fragment is additive in planner prompts; provenance records
   `composer_profile_id` + `profile_strength` only.

Copy guidance: say “Composer profile” / “Musical preferences” — never
“in the style of ⟨Artist⟩”. Musical **style reference** (embeddings) remains a
separate per-request control.

## Additive merge contract

```text
prompt hard/soft lines
  → composer-profile soft fragment (strength-scaled; explicit over derived)
  → masked reference feature fragments (or legacy style_reference summary)
```

| Strength | Behavior |
|----------|----------|
| `off` | Ignore `profile_id`; no injection |
| `light` | Short soft fragment; fewer keys |
| `normal` | Standard soft fragment |
| `strong` | Fuller soft fragment (still capped); still cannot override hard constraints |

Fake LLM applies a deterministic velocity soft-marker when strength ≠ off while
continuing to honor prompt instruments/key constraints.

## Derive privacy

- Loads project working composition or optional revision read-only.
- Aggregates embeddings `symbolic.features.v1` bands + bounded track/section labels.
- Skips empty/invalid sources; fails only if zero usable sources
  (`composer_profile_derive_empty`).
- Records `source_projects[]` ids + fingerprint prefixes — never composition bodies.
- **Never** writes `DATASET_ROOT`.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/composer-profiles` | List |
| `POST` | `/composer-profiles` | Create |
| `GET` | `/composer-profiles/{id}` | Full profile |
| `PUT` | `/composer-profiles/{id}` | Edit (requires `expected_updated_at` CAS → 409) |
| `POST` | `/composer-profiles/{id}/reset` | Clear explicit and/or derived/sources |
| `POST` | `/composer-profiles/{id}/promote` | Copy derived → explicit |
| `DELETE` | `/composer-profiles/{id}` | Delete |
| `POST` | `/composer-profiles/derive` | Preview derive; optional `save_as` |
| `POST` | `/composer-profiles/{id}/preview` | Resolve soft fragment at strength |
| `POST` | `/composer-profiles/compare` | Field-level diff |
| `GET` | `/composer-profiles/{id}/export` | `composer.profile.export.v1` envelope |
| `POST` | `/composer-profiles/import` | Import envelope (missing sources → warnings) |

Generate request fields:

```json
{
  "profile_id": "prof_…",
  "profile_strength": "off|light|normal|strong"
}
```

## Configuration

| Env | Default role |
|-----|----------------|
| `COMPOSER_PROFILE_MAX_PROFILES` | Cap stored profiles |
| `COMPOSER_PROFILE_MAX_SOURCE_PROJECTS` | Cap per derive |
| `COMPOSER_PROFILE_MAX_EXPORT_BYTES` | Import/export size |
| `COMPOSER_PROFILE_FRAGMENT_MAX_CHARS` | Soft fragment cap |
| `COMPOSER_PROFILE_MAX_LIST_ITEMS` | Chord/instrument list cap |

See `.env.example`.

## Follow-on (out of scope here)

Wiring `profile_id` / `profile_strength` into Develop, arrangement, or multi-agent
preview endpoints is intentionally deferred; generate path is the acceptance surface.

## See also

- [Symbolic embeddings](embeddings.md) — per-request musical reference conditioning
- [Hybrid generation](hybrid-generation.md) — planner + symbolic pipelines
- [Project persistence](project-persistence.md) — `PROJECT_DB_PATH` / Alembic
