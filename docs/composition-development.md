[← Composition V2](composition-v2.md) · [Back to README](../README.md) · [Composition Arrangement →](composition-arrangement.md)

# Composition Development

Stateless multi-candidate continuation and variation over strict `composition.v2`. Preview returns 1–4 independently generated candidates; nothing is persisted until the client **Apply**s one candidate through the normal composition edit path.

## Endpoint

`POST /composition/development/preview`

| Rule | Detail |
|------|--------|
| Input | Canonical `CompositionV2` only (V1 rejected) |
| Persistence | None — candidates are ephemeral |
| Playable source | `tracks[].events[]` only; harmony/motifs/analysis are context |
| Track topology | Preserved (no add/remove tracks in v1) |
| Fingerprint | Full-document `composition.edit.v1` (`edit_source_fingerprint`) |

## Operations

| Operation | Behavior |
|-----------|----------|
| `continue` | Append `output_bars` after current duration; source prefix exact |
| `add_section` | Append bars as a named section (`target_section_type` required) |
| `vary_section` | Replace inclusive source bar/section range at same duration |

Musical direction is separate: `development_intent` = `continue` \| `develop` \| `contrast`.  
Strength: `variation_strength` = `conservative` \| `balanced` \| `experimental` (identity/seam thresholds + prompt guidance).

## Candidates

- `candidate_count` 1–4; each candidate is a **separate** provider call.
- Partial success returns valid candidates plus warning codes; `502` only when none survive.
- Response includes complete validated candidate compositions, ranges, change summaries, preservation assertions, and identity diagnostics.
- Select by stable `candidate_id`, never array index.

## Apply lifecycle (frontend)

1. Preview — does not mutate `editedMusicJson`, history, dirty/autosave, analysis, or notation.
2. Select / audition — transport may play the selected candidate; working composition unchanged.
3. Apply — rechecks edit source + candidate fingerprints, requires assertions, one undo entry, dirty + autosave.

## Seam policy

Append leaves the entire source document as an exact prefix. Boundary-crossing source notes/ties that would require mutating the prefix are **rejected** in this version (no silent split).

Mixed meter: append duration uses compiled active ending meter — never root meter × bars alone.

## Limits (selected)

| Limit | Value |
|-------|-------|
| Candidates | 1–4 |
| Output bars | 1–64 |
| Instruction | 500 chars |
| Context budget | up to 48_000 chars (default 12_000) |

## Fake mode

With `LLM_FAKE_MODE=1`, drafts are deterministic and still pass production realization/validation. `LLM_FAKE_INJECT_MALFORMED=development` exhausts candidates for failure tests.

## Logging (allowed vs prohibited)

**Allowed:** operation, intent, strength, stage, provider/model, candidate ordinal/counts, timings, stable codes, fingerprint prefixes.  
**Prohibited:** API keys, prompts/instructions, full compositions/analysis, event/harmony arrays, provider raw output.

## Error codes (stable)

Includes `development_source_required`, `development_output_bars_required`, `development_preservation_failed`, `development_seam_crossing`, `development_candidate_exhausted`, `development_provider_unavailable`, and related domain codes in `composition_development_schemas.py`.

## Acceptance scenario

Finished 16-bar A → request 8-bar continuation with 3 candidates → working piece unchanged → apply one candidate → canonical 24-bar result with exact original 16-bar prefix.

## See Also

- [Composition V2](composition-v2.md) — canonical contract
- [Composition Arrangement](composition-arrangement.md) — instrumentation / texture redistribution preview
- [Testing](testing.md) — pytest / Playwright commands
- [Composition Analysis](composition-analysis.md) — advisory sidecar (not editable authority)
