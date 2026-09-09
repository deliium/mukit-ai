[← Composition V2](composition-v2.md) · [Back to README](../README.md) · [MIDI/MusicXML Import →](import.md)

# Composition Analysis

Deterministic musical analysis over strict `composition.v2`. The API returns a separate `composition.analysis.v1` sidecar report. Analysis is **not** part of the canonical composition, is **not** stored in project `composition_json`, and is never consumed by playback, notation, or export.

Native Python over integer ticks and MIDI pitch classes is authoritative. `music21` is optional and non-authoritative for public results.

## Sidecar and cache semantics

| Rule | Detail |
|------|--------|
| Schema | `schema_version: "composition.analysis.v1"` |
| Algorithm identity | `algorithm_version` (currently `analysis.native.v1`) |
| Source identity | `source_fingerprint` from analysis-relevant V2 fields only |
| Persistence | No database migration; no server-side analysis cache |
| Frontend cache | At most one successful report in Zustand, keyed by contract version + full `compositionRevision` + normalized scope |
| Authority | Client-provided cached analysis is never accepted as input authority |

Identical requests produce the same fingerprint, ordering, rounded floats, warning codes, and JSON payload. No LLM call is required for `POST /analysis/composition`.

## Canonical authority

| Source | Role |
|--------|------|
| `tracks[].events[]` | Only playable notes and primary analytical evidence |
| Declared `key`, `key_changes`, `harmony`, sections, instruments, roles | Metadata for comparison and context — never invent notes |
| Analysis report | Derived observations only; must not be submitted as composition data |

Overlap occupancy uses `note.start < scope.end` and `note.end > scope.start`. Attack counts use attack-at-onset. Duration metrics clip at section/bar boundaries.

## Scopes

| `kind` | Selector | Behavior |
|--------|----------|----------|
| `composition` | (none) | All tracks and bars |
| `section` | Required `section_index`; optional `section_id` / bar / tick bounds verify identity | Clips ensemble evidence to one canonical section (ID-less sections remain selectable) |
| `track` | Required `track_id` | Clips note-derived track metrics; retains declared timeline and bounded ensemble context for harmony/tension |

Frontend: whole-composition, explicit section selection, or the shared piano-roll track (`pianoRollTrackId`).

## Versioning and fingerprints

| Field | Purpose |
|-------|---------|
| `algorithm_version` | Semantic algorithm/profile version for report identity and freshness |
| `source_schema_version` | Always `composition.v2` |
| `source_fingerprint` | Hash of profile `analysis.source.v1` over sorted-key compact JSON of analysis-relevant fields |
| Fingerprint includes | Tempo/meter/key timelines, sections, track identity/role/drum metadata, note pitch/timing/velocity/staff/voice/tie/articulations, declared harmony |
| Fingerprint excludes | Markers, volume/pan/expression automation, dynamics, sustain pedals, UI state, the analysis report itself |

Logs use a short fingerprint prefix (12 characters), not the full hash.

## Result groups

| Group | Contents |
|-------|----------|
| `tonality` | Declared / inferred / effective global and local key spans, candidates, contradictions |
| `harmony` | Inferred chord spans, harmonic rhythm, declared-harmony comparison |
| `scale_degrees` | Summary distributions (not per-event lists by default) |
| `melody` | Range, tessitura, contour, phrases, cadences; skyline reduction when polyphonic |
| `density` | Attacks, IOI, union occupancy vs note load, simultaneity, section-relative density |
| `roles` | Declared / inferred / effective track roles (does not mutate import roles) |
| `repetition` | Bounded exact / transposed / rhythm-only flat `motifs` **plus** grouped `motif_families` (fingerprint-bound note refs); section fingerprints |
| `tension` | Duration-weighted interval-class dissonance plus optional tonal components |
| `section_summaries` | Bounded per-section rollups |
| `warnings` | Stable non-blocking codes (see below) |

Report `status`: `ok` | `partial` | `empty` | `failed`. Musical warnings and abstention return HTTP **200**.

## Confidence and abstention

Confidence is deterministic evidence strength (0–1), not probability or musical truth.

| Inference `status` | Meaning |
|--------------------|---------|
| `ok` | Accepted with confidence/evidence |
| `ambiguous` | Close competitors (e.g. relative major/minor) |
| `insufficient_evidence` | Too little pitched mass / coverage |
| `not_applicable` | e.g. percussion-only for pitched metrics |
| `truncated` | Caps applied; see limitation warnings |

Declared and inferred values stay separately visible. Effective values may combine them without treating metadata as proof.

## Stable warning codes

From `ANALYSIS_WARNING_CODES` / `WARNING_REGISTRY`. Automatic warnings cover data quality and conspicuous technical conditions — not stylistic taste.

| Code | Severity | Category |
|------|----------|----------|
| `note_outside_instrument_range` | warning | range |
| `dense_overlapping_material` | warning | density |
| `empty_analysis_scope` | info | data_quality |
| `timing_grid_anomaly` | warning | timing |
| `overlapping_same_pitch_timing` | warning | timing |
| `declared_key_conflicts_with_inference` | warning | metadata_conflict |
| `declared_key_change_conflicts_with_inference` | warning | metadata_conflict |
| `declared_harmony_conflicts_with_inference` | warning | metadata_conflict |
| `declared_harmony_unparseable` | warning | metadata_conflict |
| `excessive_duplicate_notes` | warning | data_quality |
| `result_truncated` | info | limitation |
| `evidence_truncated` | info | limitation |
| `melody_skyline_reduction` | info | limitation |
| `motif_search_truncated` | info | limitation |
| `relative_key_ambiguity` | info | ambiguity |
| `insufficient_tonal_evidence` | info | ambiguity |
| `unsupported_sustain_interpretation` | info | limitation |
| `percussion_only_scope` | info | limitation |

Warning `details` are bounded scalars/counts/thresholds and locators — not raw note sequences or full event payloads.

### HTTP error codes (structured `422` / sanitized)

| Code | When |
|------|------|
| `analysis_invalid_composition` | Non-V2 or structurally invalid document |
| `analysis_invalid_scope` | Missing, ambiguous, or mismatched selectors |
| `analysis_complexity_exceeded` | Exceeds configured analysis size limits |
| `analysis_internal_error` | Unexpected failure (sanitized; may map to 500) |

## Algorithms and thresholds (factual)

| Area | Definition |
|------|------------|
| Tonality | Duration- and metric-weighted non-drum pitch-class histograms; 24 major/minor candidates; local 2-bar windows with transition smoothing; relative ambiguity margin `0.15`; contradiction margin `0.35` |
| Chords | Onset/offset + beat/bar frames; fixed triad/seventh/suspension vocabulary; abstain on weak dyads/noise; merge equal adjacent frames |
| Scale degrees | From accepted local/global key at logical onsets; aggregate by scope/phrase/track |
| Melody | Declared melody/lead first, else high-confidence inferred role; monophonic direct; polyphonic → deterministic skyline (highest MIDI at attack) |
| Density | Union occupancy vs polyphonic note load (only note load may exceed `1.0`) |
| Roles | Instrument identity + register/monophony/attack features; abstain on weak margins; never mutate declared roles |
| Repetition | Exact / transposed / rhythm / inversion / rational 2:1–1:2 scaling tokens; motif length 3–12 notes; max 64 flat motifs + family grouping; SHA-256-derived family IDs; rolling hash + exact verify |
| Tension | Versioned interval-class table; tonal add-ons only when key/chord evidence exists |
| Dense overlap warn | Note load ≥ `8.0` or max simultaneity ≥ `12` |
| Timing grid warn | ≥ 4 off-grid attacks and ≥ 35% of attacks off a 16th-note grid |
| Instrument range | Normalized instrument identity (not substring match); role fallback only if identity unknown; drums skip pitched range |

Floats use shared half-even rounding to 6 decimal places. No NaN/infinity in reports.

## Limits and caps

| Cap | Value |
|-----|-------|
| Evidence locators | 32 |
| Candidates | 24 |
| Chord / key spans | 512 |
| Phrases | 256 |
| Motifs | 64 |
| Warnings | 256 |
| Section summaries | 512 |
| Track role summaries | 64 |
| LLM advisory projection | ≤ 6144 characters (hard clamp ≤ 8192) |

Scale target matches import ceilings (about 100,000 notes / 64 tracks / 512 bars) with explicit truncation codes rather than unbounded arrays.

## Declared vs inferred provenance

| Declared | Inferred | Comparison |
|----------|----------|------------|
| Root `key`, `key_changes` | Global/local key spans | Conflict only after evidence + margin |
| `harmony[]` chord symbols | Chord spans / harmonic rhythm | Agreement, partial, conflict, unparseable, or insufficient |
| Track `role` | Role estimates | Declared / inferred / effective remain separate |
| Section boundaries | Phrase/cadence boundaries | Sections are not forced to be phrases |

Raw MIDI/MusicXML import still sets `harmony: []` and does **not** run analysis (see [import.md](import.md)).

## Known limitations (v1)

- Heuristic key, chord, phrase, cadence, role, and tension outputs may abstain or be ambiguous
- No pedal-aware sounding-duration analysis (`unsupported_sustain_interpretation`)
- Section IDs are optional; selection uses index + optional verification fields
- Scale degrees are summary-level by default (not per-event lists)
- Motif search is bounded and may emit `motif_search_truncated`
- Derived `motif_families` group reference + matched occurrences with fingerprint-bound note refs (event IDs when present, else indexes tied to `source_fingerprint`); flat `motifs` remains for older consumers — **families** are authoritative for grouping
- Derived families are not canonical `composition.v2` motifs and cannot be submitted as edit authority without resolving against the exact fingerprint
- No persisted server-side analysis cache
- Approximation/limitation warnings carry codes, counts, and thresholds — not raw music payloads

## Logging

Verbosity follows `LOG_LEVEL`.

| Safe to log | Prohibited |
|-------------|------------|
| `algorithm_version`, fingerprint prefix, scope kind, track/note/bar counts, elapsed ms, status, warning codes, truncation flags | Full compositions, full reports, prompts, event arrays, note/pitch sequences, freeform finding prose dumps, API keys |

## API

### `POST /analysis/composition`

Accepts the **complete current** V2 document plus a scope (for unsaved `editedMusicJson`, not a project ID).

```json
{
  "composition": { "schema_version": "composition.v2", "tracks": [], "sections": [] },
  "scope": { "kind": "composition" }
}
```

Section example:

```json
{
  "composition": { "schema_version": "composition.v2" },
  "scope": { "kind": "section", "section_index": 0, "section_id": null }
}
```

Track example:

```json
{
  "composition": { "schema_version": "composition.v2" },
  "scope": { "kind": "track", "track_id": "melody" }
}
```

**200** — `CompositionAnalysisReport` (including warnings / abstention).  
**422** — invalid composition, scope, or limits (sanitized `code` / `message` / bounded `details`).

Proxied in Vite and nginx as `/analysis` (same-origin with the SPA).

## Frontend UX

- Composer workspace **Analysis** tab (`CompositionAnalysisPanel`)
- Debounced recompute while the tab is visible; retain stale results during refresh/failure
- Discard stale/out-of-order responses via request sequence + request-key equality
- Clear analysis state on generation, import, project hydration/replacement/deletion
- Present confidence and declared-vs-inferred without asserting heuristics as facts

## Bounded AI context

`build_llm_analysis_context()` projects a compact advisory summary (≤ ~6 KB) for:

- Region edit draft/repair prompts (selected-scope context)
- Generation **only** after a validated V2 candidate exists, and **only** for targeted repair

User instructions, hard constraints, canonical events, and deterministic patch validation remain authoritative. Fake LLM provider behavior does not depend on analysis.

## Implementation map

| Layer | Location |
|-------|----------|
| DTOs / codes / caps | `backend/app/analysis_schemas.py` |
| Fingerprint | `backend/app/services/composition_fingerprint.py` |
| Context / indexes | `backend/app/services/composition_analysis_context.py` |
| Orchestrator + LLM projection | `backend/app/services/composition_analysis.py` |
| Analyzers | `composition_tonality`, `*_harmony_analysis`, `*_melody_analysis`, `*_density_analysis`, `*_role_analysis`, `*_repetition_analysis`, `*_tension_analysis`, `*_analysis_warnings` |
| HTTP | `backend/app/routers/analysis.py` |
| Client API | `frontend/src/api/musicApi.js` (`analyzeComposition`) |
| Scope / freshness | `frontend/src/utils/compositionAnalysis.js` |
| Store | `frontend/src/store/musicStore.js` |
| UI | `frontend/src/components/CompositionAnalysisPanel.jsx` |

## See Also

- [Composition V2](composition-v2.md) — operational canonical contract (analysis is not part of it)
- [MIDI and MusicXML import](import.md) — raw import performs no analysis; `harmony: []`
- [Composition V1](composition-v1.md) — staged generation and region editing
- [Testing](testing.md) — focused, scale, and Playwright analysis commands
