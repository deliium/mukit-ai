# Implementation Plan: V5 Film Score Adaptation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-01

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Agents tab review of `film.score.adaptation.v1` (edit span, chosen strategy, bars touched, unchanged/shifted/removed event counts, hit status). An explicit Commit writes the candidate. Preview does not run when the tab opens, when the Picture tab saves cues, or when a picture file is replaced. The Picture tab stays the cue editor
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-01 (`/aif-improve`). `local_tempo` is chosen from the raw second delta before any half-bar is removed, and it is illegal for `move_hit`. Vector C's next picture is duration `63` with `hit_bbbb0002` at `35`. Vector D moves `hit_aaaa0001` to `6.25` seconds. Pitches are scientific names starting at `C4`. A crossing note is truncated in place. The empty-bar repair is one tonic note inside `film_score_adapt.py`. Remember reads `getVideoAsset` plus `pictureScoring`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next film-scoring milestone and does not edit `ROADMAP.md`
- Scope: an explicit preview that chooses a local musical repair for a declared picture edit, then an explicit commit of the repaired `composition.v2`. Unchanged music stays identical. A full film-score generation stays on the existing preview

## Roadmap Linkage
Milestone: "V5 Film score adaptation"
Rationale: A recut of several seconds needs a local repair of the existing `composition.v2` so motifs, harmony, climax, and instrumentation survive. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A composer who already has a score can declare how the picture timing changed, inspect a localized adaptation, and only then commit it. The adaptation prefers the smallest musical change that puts the surviving sync cues back on the grid. Regions the edit does not touch stay musically identical.

Ship:

1. A non-playable `film.score.adaptation.v1` compiled from the previous picture timeline, an explicit edit list, the current timeline, the working `composition.v2`, and the hit-point changes those edits imply.
2. One strategy per edit, chosen in a fixed cheapest-first order: unchanged, local tempo, transition shorten or extend, phrase contraction or extension, bar removal or insertion, silence insertion, then targeted regeneration. The user does not pick the strategy.
3. A session preview that returns the proposal and a candidate. Commit recomputes that candidate and writes it through `film-score-adapt-apply`. Cue rows, video bytes, and the sync origin stay as stored.
4. A refusal when the edit covers the whole music window or needs more than 32 bars. That refusal does not call the film-score agent and does not invent a replacement score.
5. Regression tests whose main vector deletes 6 seconds inside one section and proves the earlier notes are unchanged, the later notes keep pitch, duration, velocity, and id with one tick shift, and the climax and the original motif remain.

Acceptance: at `24/1`, `4/4`, 120 bpm, and 480 ticks per quarter, deleting video seconds `[16, 22)` from a 32-bar score removes exactly three bars inside the second section. Events that start before that section stay canonical-equal. Events that start after the removed bars keep their musical identity and shift earlier by `5760` ticks. Track ids and the instrument id stay equal. The climax label and the original motif occurrence stay. The strategy list is `phrase_contract` only. `targeted_regenerate` is absent. A one-second deletion `[16, 17)` changes only that section's tempo to `128` and restores `120` at the next section start, leaves every note event identical, and ripples the later cue from `36` seconds to `35` on a picture whose duration is `63`.

```text
previous timeline + edit list
        ↓
expected next timeline  (must match the stored picture within one frame)
        ↓
working composition.v2
        ↓
film.score.adaptation.v1   (one strategy per edit, no note events)
        ↓
local candidate            (prefix identical, suffix shifted or untouched)
        ↓
preview (committed false) → explicit commit → composition.v2
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. Picture documents stay `video.asset.v1` and `video.scoring.v1`. There is no `video.scoring.v2`. **Film score plan** remains `film.score.plan.v1` and is the full generation path. **Adaptation proposal** is `film.score.adaptation.v1`. It is inspectable and non-playable. **Previous timeline** is the `FilmTimelineSnapshot` on the preview request. It is not a second scoring schema and it is not stored in SQLite. **Edit** is one closed picture operation (`delete_span`, `insert_span`, or `move_hit`). **Strategy** is the musical repair chosen for that edit. **Local** means the candidate is produced by retiming the working score. **Musically identical** for a kept event means the same `id`, `pitch`, `duration_ticks`, and `velocity`. A constant `start_tick` shift is allowed only after the edit point. Events before the edit point also keep `start_tick`. **Sync cue** keeps the film-score definition: importance `high` or `critical` and kind `hit_point`, `reveal`, `cut`, `action`, or `emotional_cue`. **Climax** is the section `resolve_climax_section_index` returns from a section label or id containing `climax`, with chorus-as-climax left off and with no brief. **Important melody** is any event id named by a motif occurrence. **Original motif** is the occurrence whose `relationship` is `original`. **Commit** is the only write of `tracks[].events[]` on this path. It does not rewrite cue rows, video bytes, `video_origin_seconds`, or `musical_origin_tick`. **Automatic** means the strategy is chosen by the compiler. It does not mean an HTTP call on save, upload, or tab open.

Predecessor: `.ai-factory/plans/v5-film-scoring-agent.md`. Do not reopen cue fields, the landing definition, the film-score agent order, the section-boundary tempo policy for generation, or `replace_existing` on film-score commit. Do not add a `MarkerKind`. Do not add an agent id. Do not write cue rows from this path.

## Approach Evaluation (locked)

### Part A — What triggers adaptation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. After every scoring PUT or video upload, generate a new film score** | Always in sync | Replaces the score the user asked to keep. The film-score commit already does this when `replace_existing` is true | **Reject** |
| **B. Infer every recut by diffing cue times** | No edit form | A shared shift does not locate the cut. The wrong bars would be removed. The store keeps only the current timeline, so the previous times are gone after PUT | **Reject** |
| **C. The request carries the previous timeline and an explicit edit list. The router reads the current asset duration, the current hits, and the working score. The service checks that the edits explain the current timeline within one frame, then chooses a strategy** | The cut point is known. The compiler stays pure. Picture save does not rewrite notes | The client must still remember the previous timeline until the user previews | **Accepted** |

### Part B — Where the previous timeline lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A new scoring history table** | Survives restart | Alembic plus a second picture document. The current row is already the only scoring document | **Reject** |
| **B. Fields on `video.scoring.v1`** | One GET | `extra=forbid`. That would be a second schema on the cue document | **Reject** |
| **C. The previous snapshot is a request field. The Agents tab can remember the current picture in session state when the user asks. It is not autosaved and not written to SQLite** | No migration. Tests pass the snapshot directly | A reload clears the remembered baseline until the user captures it again | **Accepted** |

### Part C — How much of the score may change

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Call `run_film_score_preview` and commit with `replace_existing`** | One code path | The acceptance case would replace every region, including bars the cut never touched | **Reject** |
| **B. Change tempo at every moved hit** | Hits land | The film-score plan already rejected per-hit tempo. Later phrases change feel even when their picture time only shifted | **Reject** |
| **C. Choose the cheapest strategy that repairs the edited span. Keep events before the span canonical-equal. Shift later events by one tick delta, or leave every event identical when tempo alone absorbs the span. Restore the previous bpm at the next section start. Refuse a span that covers every bar or needs more than 32 bars, and do not generate a replacement score** | Matches the shortening acceptance. Full generation stays a separate explicit preview | Some cuts stay `unsatisfiable` inside the tempo step and the protected bars | **Accepted** |

### Part D — Where regeneration sits

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The symbolic composer rewrites every edited section** | New material fills the gap | Important melodies change even when a shift would do | **Reject** |
| **B. Never regenerate; leave a miss as `unsatisfiable`** | Maximal preservation | A hit that moves inside one bar and cannot be met by tempo never gets a local repair | **Reject** |
| **C. Regenerate only for a `move_hit` that is off the beat grid. `local_tempo` is not eligible because the picture duration did not change. Move the nearest melody attack in that one bar onto the cue tick, keeping pitch and velocity. If the bar has no movable melody attack, insert one tonic note in `film_score_adapt.py`. Motif-linked event ids are not replaced. No other bar changes. `apply_film_score_accents` and the film-score agent sequence are not called** | Sync can still be repaired. Pitches of existing melody notes stay. The adapt module does not import the video modules the accent helper imports | A bar with only accompaniment gains one new tonic attack | **Accepted** |

### Part E — How the candidate is committed

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `multi-agent-apply` with an artifact role map** | Same route as film score | This path has no agent artifacts. The promotion branch would run for an empty map | **Reject** |
| **B. Trust the client candidate when its fingerprint matches** | Same as film-score commit | A client could send a fully generated score with a matching hash of that other score | **Reject** |
| **C. Add `film-score-adapt-apply`. Commit loads the working score and the stored picture again, reruns the pure compiler, and accepts the body only when the recomputed candidate fingerprint matches. Omit `declared_scope` because a `bar_count` change is derived as whole-document scope. No Alembic: `operation_type` is already unbounded text** | The committed notes are the local repair. History stays reviewable as AI | Callers must still hold the preview inputs until Commit | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Film generation | `film.score.plan.v1`, `compile_film_score_plan`, `POST /projects/{id}/film-score/preview` and `/commit` | Leave it. Adaptation does not call `run_film_score_preview` |
| Tempo step | `FILM_TEMPO_STEP_MAX = 12` in `backend/app/services/film_score_tempo.py`. Root mismatch writes no `tempo_changes` row. Tick of a later change is a section start. Frame test uses `floor(seconds * 24 + 1e-9)` | Import the step constant. Use the same frame floor |
| Bar length | `bar_duration_ticks` and `round_half_away_from_zero` in `backend/app/composition_schemas.py`. `compile_timeline` in `backend/app/services/composition_timeline.py` | Map the edit into bars |
| Cue snapshot | `FilmCueSnapshot` in `backend/app/film_score_schemas.py` | Previous and next cues use it. Do not copy `instruction` or `label` |
| Outside-region proof | `compare_preserved_regions` and development `_outside_range_equal` | Same fields for the prefix. The suffix needs a shift-aware compare because start ticks move |
| Region patch | `apply_region_replacement_patch` | Not used. Regeneration moves one existing attack or inserts one tonic note |
| Accent | `apply_film_score_accents` requires `FilmScorePlanV1`, imports `video_scoring_schemas` and `video_spotting`, and passes `musical_origin_tick` `0` | Do not call it. The adapt module inserts the tonic note itself |
| Canonical JSON | `canonical_edit_json_dumps` in `backend/app/services/composition_edit_fingerprint.py`. `composition_snapshot_fingerprint` in `backend/app/services/composition_snapshot_encoding.py` | Timeline fingerprint and candidate fingerprint |
| Picture asset | `getVideoAsset` in `frontend/src/api/videoScoringApi.js`. Duration is not on `pictureScoring` | Remember reads both |
| Climax | `resolve_climax_section_index` in `backend/app/services/composition_critique_checks.py` | Label or id match. Do not call `/critique/evaluate` or an LLM |
| Motifs | `CompositionV2MotifDefinition.occurrences` with exactly one `original`. Occurrences store `event_ids`, not ticks | Protect those events |
| Sections | `CompositionV2Section` types in `SUPPORTED_SECTION_TYPES` | Keep ids and types. Change `bar_count`, `start_bar`, `start_tick`, and `duration_ticks` only as the shift requires |
| History | `DurableCommitRequest`, `commit_revision`, `RevisionOperationType` | New enum value `film-score-adapt-apply`. Add it to `AI_ORIGIN_OPERATIONS` |
| Change summary | `summarize_composition_changes` treats a `bar_count` change as the whole document | Omit `declared_scope` on this commit |
| UI session | `filmScorePreview` is not autosaved. `FilmScorePanel` does not preview on mount | Same rule for the adaptation slice |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Adaptation document | No `film.score.adaptation.v1` |
| Edit list | No `delete_span`, `insert_span`, or `move_hit` |
| Bar insert or removal in the middle | Development vary keeps `bar_count`. Continue only appends |
| Strategy chooser | Film tempo reacts to misses while generating. It does not shorten an existing phrase |
| Local commit | Film-score commit replaces the working events |

### Coupling risks to avoid

1. Importing `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, `video_scoring_map`, `video_spotting`, or `llm_video_spotting` from `backend/app/services/film_score_adapt.py`. The router builds snapshots.
2. Importing `film_score_adapt` from `ai_agents/`. The existing import-ban test must grow to reject `film_score_adapt`.
3. Calling `run_film_score_preview`, `generate_symbolic_composition`, `apply_film_score_accents`, or the agent registry from the adaptation path. `film_score_accents.py` imports the video modules this service must not import.
4. Writing `tempo_changes` at a cue tick. Local tempo ticks are the affected section start and, when a later section exists, the next section start.
5. Letting picture save, upload, GET scoring, or Agents-tab mount POST the preview.
6. Persisting the proposal, the previous snapshot, or the edit list on `composition_json` or `video.scoring.v1`.
7. Rewriting cue rows, video bytes, or the sync origin on commit.
8. Logging cue labels, instructions, briefs, note arrays, or video bytes.
9. Adding Alembic, a revision-operation check constraint, `composition.v5`, or a new agent id.
10. Editing `ROADMAP.md`.

## Scope And Decisions

### In scope
- `film.score.adaptation.v1`, the preview request, and the commit request.
- A pure check that the edit list explains the stored timeline.
- A pure strategy chooser and a pure applier for the working composition.
- `POST /projects/{project_id}/film-score/adapt/preview` and `POST /projects/{project_id}/film-score/adapt/commit`.
- Agents-tab inspect and Commit.
- Vectors A through F below.
- Docs named in Task 8.

### Out of scope
- Scene detection, ffmpeg, shot detection, and any new media file.
- Guessing a recut from two cue lists.
- Changing spotting storage, the Picture ruler, or the film-score generation preview.
- A durable previous-timeline table.
- Adaptive score, neural render, mix plan, and arrangement.
- Rewriting harmony symbols to new chords.
- A new Alembic revision or `composition.v5`.

### Architecture decisions (locked)

**1. Documents**

`FilmTimelineSnapshot` in `backend/app/film_score_adapt_schemas.py`. `extra=forbid`. Fields: `duration_seconds` (finite, `> 0`, `<= 3600`), `frame_rate_numerator` and `frame_rate_denominator` (integers `>= 1`, the rate closed and positive), `video_origin_seconds` (finite, `>= 0`), `musical_origin_tick` (int `>= 0`), `cues` (0..64 `FilmCueSnapshot`). Reuse `FilmCueSnapshot` from `film_score_schemas.py`. This module does not import video schemas.

`FilmPictureEdit` is a discriminated union. `extra=forbid`. `op_id` matches `^edit_[0-9a-f]{8}$`.

| Kind | Fields | Effect on the previous timeline |
|------|--------|--------------------------------|
| `delete_span` | `start_seconds`, `end_seconds` with `end_seconds > start_seconds` and both finite `>= 0` | Duration loses `end - start`. A cue strictly inside the span must be absent from the next snapshot. A cue at or after `end_seconds` moves earlier by `end - start` |
| `insert_span` | `at_seconds` finite `>= 0`, `duration_seconds` finite `> 0` and `<= 600` | Duration grows by `duration_seconds`. A cue at or after `at_seconds` moves later by that duration |
| `move_hit` | `cue_id`, `from_seconds`, `to_seconds` | That cue's seconds become `to_seconds`. Duration is unchanged. Every other cue stays within one frame |

`FilmScoreAdaptPreviewRequest`. `extra=forbid`. `previous` snapshot, `edits` length 1..16, `expected_source_fingerprint` non-empty string. No `brief`. No `replace_existing`. No `instruments`.

`FilmScoreAdaptationV1` schema version `film.score.adaptation.v1`. `extra=forbid`. Reject the same top-level playable keys as `film.score.plan.v1`.

| Field | Rule |
|-------|------|
| `project_id` | string |
| `source_fingerprint` | the working score fingerprint the preview used |
| `scoring_document_revision` | int `>= 0` |
| `previous_timeline_fingerprint` | SHA-256 hex of `canonical_edit_json_dumps` applied to `FilmTimelineSnapshot.model_dump(mode="json")`. Log only the 12-character prefix |
| `operations` | 1..16 of `{op_id, kind, strategy, start_bar, end_bar, bars_delta, tempo_bpm, section_id, reason_code}` |
| `hit_changes` | one row per cue id in the union of previous and next: `{cue_id, change, previous_seconds, next_seconds, status}` |
| `preserved` | `{motifs, melodies, harmony, climax, instrumentation}` booleans. A flag is false only when that material was edited, and a warning is set |
| `counts` | `{events_unchanged, events_shifted, events_removed, events_added}` |
| `warnings` | codes only, max 32 |
| `committed` | always false |

`strategy` is `unchanged`, `local_tempo`, `transition_shorten`, `transition_extend`, `phrase_contract`, `phrase_extend`, `bar_remove`, `bar_insert`, `silence_insert`, or `targeted_regenerate`.

`reason_code` is `picture_shorten`, `picture_extend`, `hit_move`, or `silent_gap`.

`change` on a hit row is `unchanged`, `moved`, `added`, or `removed`. `status` reuses `aligned`, `soft`, `unsatisfiable`, and `boundary`. Added and removed rows use `status` `soft`. `music_start` and `music_stop` stay `boundary`.

Warning codes: `hit_unsatisfiable`, `climax_region_edited`, `motif_occurrence_trimmed`, `melody_protected`, `tempo_restored`, `note_truncated`, `film_origin_unchanged`.

Preview response `film.score.adaptation.preview.v1`: `proposal`, `candidate` (`composition.v2` or null), `candidate_fingerprint` (null when candidate is null), `committed: false`.

Commit request: `previous`, `edits`, `candidate`, `candidate_fingerprint`, `expected_source_fingerprint`, `expected_document_revision`, plus the durable branch fields film-score commit already sends (`branch_id`, `expected_active_branch_id`, `expected_working_version`, `expected_head_revision_id`). No artifact role map. No `replace_existing`.

Error codes on `FilmAdaptError`: `film_adapt_invalid` (422), `film_adapt_score_empty` (422), `film_adapt_span_too_large` (422), `film_adapt_frame_rate_required` (422), `film_adapt_asset_missing` (404), `film_adapt_timeline_mismatch` (409), `film_adapt_conflict` (409).

**2. Explaining the new timeline**

Constants in `backend/app/services/film_score_adapt.py`: `FILM_ADAPT_OPS_MAX = 16`, `FILM_ADAPT_SHIFT_BARS_MAX = 32`, `FILM_TRANSITION_BARS = 2`. Reuse `FILM_TEMPO_STEP_MAX`. Do not copy a second tempo policy.

The service applies `edits` in order to `previous` and builds the expected duration and the expected cue seconds. The router passes a next snapshot built from the stored asset `duration_seconds`, the stored frame rate, the stored origin, and `FilmCueSnapshot` copies of the stored hits. Comparison uses `floor(seconds * frame_rate + 1e-9)` on both sides, with `frame_rate = numerator / denominator`. Duration uses that same floor. A mismatch raises `film_adapt_timeline_mismatch` and builds no candidate.

Hit changes are the id-set difference plus the seconds delta after the edits have been applied. They are reported. They are not written.

**3. Choosing a strategy**

Map video seconds through the working score. Score seconds are `tick_to_seconds(musical_origin_tick) + (video_seconds - video_origin_seconds)` using `compile_timeline` on the working composition. Do not import `video_scoring_map`. The edit point is the first tick of the mapped span. Decide `local_tempo` from the raw second length of the span before any bar is removed. Only after tempo cannot absorb that span inside the step of 12 does the chooser quantize to whole bars. A partial bar is then included when its overlap is at least half of that bar's seconds, using `round_half_away_from_zero` on the overlap ratio. Vector C overlaps bar 9 by exactly half a bar and still removes zero bars, because tempo wins first.

Protected bars:

- Any bar that contains the start tick of an event id listed on the `original` motif occurrence.
- Any bar of the climax section when a climax index exists.
- The bar of a surviving sync cue, unless that cue's id was removed by the edit.

The chooser walks the edits in order. The first strategy that repairs that edit wins.

1. `unchanged` when the mapped bar delta is 0, the leftover seconds floor to 0 frames, and every surviving sync cue in the touched section still lands within `tolerance_frames`.
2. `local_tempo` only for `delete_span` and `insert_span`. A `move_hit` keeps the picture duration, so a bpm change would slide every later cue, and this strategy is illegal for it. An integer bpm inside `max(40, section_bpm - 12) .. min(240, section_bpm + 12)` must make that section's new video length match its musical length within one frame. After the optional restore row, every surviving sync cue in the whole score, not only the edited section, must land within its `tolerance_frames`. Search the smallest absolute bpm delta, then the smallest remaining frame error. The change tick is the section start. A change to the section that starts at bar 1 writes `tempo` and writes no row at tick 0. When a later section exists, write a second change at that section's start that restores the bpm the later section already had, unless the restored bpm equals the new bpm. That row's presence adds warning `tempo_restored`. Cap 8 tempo rows on the candidate. If the cap would break, or a later cue would miss, skip `local_tempo`.
3. `transition_shorten` or `transition_extend` when the mapped bars lie only in the last `FILM_TRANSITION_BARS` of a section or the first `FILM_TRANSITION_BARS` of the next, and that section's type is `bridge` or its label or id contains `transition`. The mechanical edit is still a bar removal or insertion of those edge bars.
4. `phrase_contract` when a delete removes one or more whole bars and the section still has at least one bar afterward. `bar_remove` when the delete removes a whole section that is not the climax section and is not the only section. `phrase_extend` and `bar_insert` are the insert mirrors. Inserted bars copy the previous bar's events into each new bar at the same offset. New ids are `adapt_` plus 8 hex characters from a stable hash of the source event id and the new bar index. Harmony chord symbols are copied with the new ticks. This copy is not regeneration.
5. `silence_insert` when an `insert_span` has a `music_stop` at or before `at_seconds` and a `music_start` at or after it on the previous snapshot. The new bars contain no events. Later events shift right. The containing section's `bar_count` grows. Reason code `silent_gap`.
6. `targeted_regenerate` only for a `move_hit` whose cue is a sync cue, whose new time is inside one existing bar, and whose frame distance to the nearest beat is greater than `tolerance_frames`. Move the melody attack in that bar whose start is nearest the cue onto the cue tick computed from the snapshot origin. Keep its pitch and velocity. If any event id in that bar is motif-linked, skip those events. If no movable melody attack remains, insert one note on the melody track: pitch `{key token}4`, velocity `96`, duration one beat, start at that cue tick. Do not import or call `apply_film_score_accents`. Set `preserved.melodies` false only when that new note is inserted. No other bar's events change.

If the needed bar count is greater than `FILM_ADAPT_SHIFT_BARS_MAX`, or the union of the mapped spans covers every bar of the score, raise `film_adapt_span_too_large` and return no candidate. Do not call the film-score workflow.

A working score with no note events raises `film_adapt_score_empty`.

Climax bars are not removed. If the only way to meet the delta would remove a climax bar, skip those bars, set warning `climax_region_edited` only when the edit span overlaps the climax section and a non-climax bar inside the overlap was removed, and leave the climax events identical or, when they sit after the cut, shifted. If a climax section cannot be repaired without removing it, the hit status is `unsatisfiable` and the climax events stay.

The original motif occurrence is not removed. If a removal would drop that occurrence, choose the nearest unprotected bars in the same section. If none exist, `film_adapt_span_too_large` is the wrong code: leave the score unchanged for that op, set the hit `unsatisfiable`, and set warning `melody_protected`. Other motif occurrences whose every event was inside removed bars are dropped, and the warning is `motif_occurrence_trimmed`. An occurrence that would lose only some of its events is dropped entirely rather than retargeted, with the same warning. The `original` occurrence is never that occurrence.

Instrumentation is unchanged: same track ids, roles, and instrument ids, in the same order. `preserved.instrumentation` stays true.

Harmony chord symbols that lie fully before the edit stay canonical-equal, including their ticks. Spans fully after the edit shift by the same tick delta as the notes. A span that crosses the cut is truncated on the kept side and keeps its chord symbol. `preserved.harmony` stays true when every surviving chord symbol is one that existed before. It is false only if a symbol was dropped because its whole span was removed, with no extra warning code beyond the operation itself.

**4. Identity of the candidate**

`counts.events_unchanged` is the number of events with canonical-equal `id`, `pitch`, `start_tick`, `duration_ticks`, and `velocity`. `counts.events_shifted` is the number that match those fields except `start_tick`, which differs by exactly one candidate-wide shift for the suffix of that operation. Stacked operations apply in order, so the tests build the expected shift as the sum. `counts.events_removed` and `events_added` cover deletions and new ids. Added events are only the deterministic `adapt_` copies from an extension, or the single tonic note. A truncated note keeps its id, pitch, velocity, and start tick, and its `duration_ticks` ends at the cut. It is not one of the four counts. Warning `note_truncated` records it. Vector A has no such note, so the four counts sum to 32.

The same tick delta shifts markers, `tempo_changes`, `key_changes`, `time_signature_changes`, dynamic marks, sustain pedals, and automation points that start after the cut. Items whose ticks sit inside the removed span are dropped. A shifted `time_signature_changes` or `key_changes` tick must still land on a bar boundary. A note or harmony span that starts before the cut and ends inside it is truncated so its end is the cut tick. After the rewrite, sections are contiguous, each `duration_ticks` matches its `bar_count` under the meter map, and the sections cover `bar_count` and `duration_ticks`.

`key`, `time_signature`, and `ticks_per_quarter` stay equal. `tempo` stays equal unless strategy `local_tempo` changed the opening section. `tempo_changes` gain only the rows that strategy wrote, plus any pre-existing rows that were shifted rather than dropped.

Section ids and types stay. The affected section changes `bar_count` and `duration_ticks`. Later sections change `start_bar` and `start_tick` by the bar and tick delta. Labels stay, including `climax`.

**5. Routes**

`POST /projects/{project_id}/film-score/adapt/preview` in `backend/app/routers/film_score.py`. `enforce_current(..., "write_score")`. Load the project composition, the scoring document, and the asset. Missing asset is `film_adapt_asset_missing`. A rate that is not positive and closed is `film_adapt_frame_rate_required`. Fingerprint mismatch against `expected_source_fingerprint` is `film_adapt_conflict`. Build the next snapshot in the router. Call `compile_film_score_adaptation`. Write nothing.

`POST /projects/{project_id}/film-score/adapt/commit` loads the same inputs, runs the same compiler, and requires `composition_snapshot_fingerprint` of the recomputed candidate to equal `candidate_fingerprint`. A mismatch, a stale `expected_document_revision`, or a stale branch CAS field is `film_adapt_conflict` and writes nothing. On success the route builds a `DurableCommitRequest` whose `composition` is that recomputed `CompositionV2`, whose `operation_type` is `RevisionOperationType.FILM_SCORE_ADAPT_APPLY` (`"film-score-adapt-apply"`), and whose `ai` is `AiProvenance(provider="film-score", operation="film-score-adapt-apply")`. `generation_parameters` carry the strategy names and the warning codes only. Omit `declared_scope`. `commit_revision` promotes artifacts only for `MULTI_AGENT_APPLY`, so this operation does not. Do not call `put_video_scoring`. The response warning list includes `film_origin_unchanged`. Cue rows and the asset hash stay as they were.

`revision_origin("film-score-adapt-apply")` returns `ai`.

**6. UI**

`frontend/src/components/FilmScoreAdaptPanel.jsx`, mounted from `MultiAgentPanel.jsx` under the existing film-score panel. Controls: Remember current picture, rows for kind and seconds, Preview, summary, Commit.

Remember calls `getVideoAsset` and reads `pictureScoring`. Duration comes from the asset. Frame rate, origin, and hits come from the scoring document. Each hit becomes a `FilmCueSnapshot` with no `label` and no `instruction`. The control stays disabled when the asset or the scoring document is missing. The baseline is stored in `filmAdaptBaseline`. That slice is not part of `autosaveBodyFromState`. Preview does not run from the button's parent effect. Commit stays disabled until `candidate_fingerprint` is set. The summary lists each `op_id`, strategy, bar range, `bars_delta`, and the four counts, then each cue id with `change` and `status`, then warning codes. Successful commit clears the adaptation preview and reloads the project the way film-score commit already does. It does not clear the picture cue editor.

`frontend/src/utils/filmScoreAdapt.js` formats that summary. It does not choose a strategy.

**7. Vectors**

Shared fixture for A through E: 32 bars, 4/4, tempo 120, 480 ticks per quarter, so one bar is `1920` ticks and `2` seconds. Four sections of 8 bars. Section 3 label `climax`, type `chorus`. One melody track, instrument `acoustic_grand_piano`, role `melody`. One note per bar, duration exactly `1920`, velocity `80`, so no note crosses a barline. Pitch is scientific notation: bar 1 is `C4`, and each next bar is one semitone higher (`C#4`, `D4`, …). Event ids are `note_bar_01` through `note_bar_32`. Motif `original` occurrence owns `note_bar_01` and `note_bar_02`. One `rehearsal` marker labeled `later` sits on the bar-20 downbeat, tick `36480`. Kind stays `rehearsal`. Do not add a `MarkerKind`. Harmony symbols `C`, `G`, `F`, `C` on the four sections. Frame rate `24/1`. Origin seconds `0`, origin tick `0`. Previous duration `64`. Sync cues `hit_aaaa0001` at `6` seconds and `hit_bbbb0002` at `36` seconds, both critical `hit_point`, `tolerance_frames` `0` except where a vector says otherwise.

| Vector | Edit | Required result |
|--------|------|-----------------|
| A | `delete_span` `[16, 22)` | Next duration `58`. `hit_bbbb0002` at `30`. Strategy `phrase_contract`. Remove bars 9 through 11. `bars_delta` `-3`. Prefix events canonical-equal. Suffix events shift by `-5760`. The rehearsal marker moves from `36480` to `30720`. Later cue status `aligned`. Climax label remains. Original motif event ids remain. Track instrument id remains. Harmony `F` remains. `note_truncated` absent. `targeted_regenerate` absent. `events_added` `0` |
| B | `move_hit` of `hit_bbbb0002` by one frame (`1/24` second) with `tolerance_frames` `1` | Strategy `unchanged`. Duration stays `64`. Every event canonical-equal. `tempo_changes` unchanged |
| C | `delete_span` `[16, 17)` | Next duration `63`. `hit_aaaa0001` stays at `6`. `hit_bbbb0002` moves to `35` and stays `aligned`. Strategy `local_tempo`. Zero bars removed. Section bpm `128` at tick `15360`. Restore bpm `120` at tick `30720`. Root tempo stays `120`. Every note event canonical-equal. Warning `tempo_restored` |
| D | `move_hit` of `hit_aaaa0001` from `6` seconds to `6.25` seconds, `tolerance_frames` `0`. Duration stays `64`. The other cue stays at `36` | `local_tempo` is not considered. `6.25` seconds is 6 frames from the beats at `6.0` and `6.5`. Strategy `targeted_regenerate` on bar 4. `note_bar_04` keeps its pitch and velocity and its start moves to the cue tick. Every other event canonical-equal. `apply_film_score_accents` is not called. Agents are not called |
| E | Previous `music_stop` at `16` and `music_start` at `16`, plus `insert_span` `at_seconds` `16`, `duration_seconds` `4` | Strategy `silence_insert`. Two new bars contain no events. Prefix canonical-equal. Suffix shifts by `+3840` |
| F | `delete_span` `[0, 64)` | `film_adapt_span_too_large`. `candidate` is null. Film-score workflow is not called. The stored composition fingerprint is unchanged after the preview route |

Vector C's bpm arithmetic is `8 * 4 * 60 / 15 = 128`. Its next picture is 63 seconds, and the cue that was at 36 seconds is at 35. Section 1 still lasts 16 seconds, section 2 lasts 15, and two bars into section 3 at 120 bpm land on that 35-second cue. Vector A's 6 seconds are three bars. The same section cannot absorb 6 seconds inside `±12` bpm (`192` is outside the step), so A must not take `local_tempo`. Vector D must not take `local_tempo` either: the picture length did not change, and `6.5` seconds would have been the wrong target because it is already a beat.

## Tasks

### Phase 1: Contract and selection
- [x] Task 1: Add the adaptation documents
- [x] Task 2: Check that the edit list explains the stored picture
- [x] Task 3: Choose one strategy per edit

### Phase 2: Local score
- [x] Task 4: Apply structural edits and prove unchanged regions
- [x] Task 5: Regenerate one bar only, and refuse a full-score replacement

### Phase 3: HTTP and review
- [x] Task 6: Preview and commit the local candidate
- [x] Task 7: Review the proposal from the Agents tab

### Phase 4: Docs
- [x] Task 8: Document film-score adaptation

## Commit Plan
- **Commit 1** (after tasks 1-3): "feat: choose a local film-score adaptation for a picture edit"
- **Commit 2** (after tasks 4-5): "feat: retarget an existing score around a shortened scene"
- **Commit 3** (after tasks 6-7): "feat: preview and commit a localized film-score adaptation"
- **Commit 4** (after task 8): "docs: describe film-score adaptation after a picture edit"

## Tasks (detail)

### Task 1: Add the adaptation documents

Add `FilmTimelineSnapshot`, `FilmPictureEdit`, `FilmScoreAdaptPreviewRequest`, `FilmScoreAdaptationV1`, the preview response, the commit request, and `FilmAdaptError` in `backend/app/film_score_adapt_schemas.py` using the tables above. `extra` stays `forbid`. Reject forbidden playable keys with `film_adapt_invalid`. `committed` on the proposal is false. `FilmCueSnapshot` is imported from `film_score_schemas.py`. This file does not import video modules.

`backend/tests/test_film_score_adapt_schemas.py` accepts a proposal with one `phrase_contract` operation, empty warnings, and `committed` false. It rejects `tracks`, `events`, a 17th edit, a `delete_span` whose end is not after its start, an unknown strategy, and a request that includes `replace_existing`.

LOGGING: DEBUG on schema rejection with the model name and the error code. Do not log cue labels or note fields. Levels follow `LOG_LEVEL`.

Files: `backend/app/film_score_adapt_schemas.py`, `backend/tests/test_film_score_adapt_schemas.py`.

### Task 2: Check that the edit list explains the stored picture

Add `expected_timeline` in `backend/app/services/film_score_adapt.py`. It applies the ordered edits to the previous snapshot and returns the expected duration and cue seconds. It imports neither FastAPI, SQLite, nor video schemas. Frame equality uses `floor(seconds * frame_rate + 1e-9)`.

`backend/tests/test_film_score_adapt_timeline.py` covers vector A's `[16, 22)` deletion: duration `58`, `hit_aaaa0001` stays at `6`, `hit_bbbb0002` moves from `36` to `30`. Vector C's `[16, 17)` deletion yields duration `63`, the first cue stays at `6`, and `hit_bbbb0002` moves from `36` to `35`. A cue placed at `18` seconds on the previous snapshot is required to be absent on the next snapshot. One extra frame of error on the moved cue raises `film_adapt_timeline_mismatch`. A `move_hit` leaves duration unchanged.

LOGGING: DEBUG with edit count and the 12-character previous-timeline fingerprint prefix. Do not log the cue list. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Choose one strategy per edit

Add `choose_film_score_adaptations` in the same module. Inputs are the previous snapshot, the edits, the next snapshot, and the working `CompositionV2`. Output is the operation list and hit rows before any note rewrite. Use `compile_timeline`, `bar_duration_ticks`, and `round_half_away_from_zero`. Import `FILM_TEMPO_STEP_MAX` from `film_score_tempo.py`. Call `resolve_climax_section_index` with chorus-as-climax off and no brief.

`backend/tests/test_film_score_adapt_strategy.py` asserts vector A selects `phrase_contract` and not `local_tempo` or `targeted_regenerate`. Vector C selects `local_tempo` with bpm `128`, a restore at the next section, `bars_delta` `0`, and `hit_bbbb0002` `aligned` at `35` seconds. Vector B selects `unchanged`. Vector D selects `targeted_regenerate` and not `local_tempo`. A delete mapped only onto the last two bars of a section whose label contains `transition` selects `transition_shorten`. A span of 33 bars raises `film_adapt_span_too_large`.

LOGGING: DEBUG one line per operation with `op_id`, strategy, `start_bar`, and `bars_delta`. Do not log note arrays. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Apply structural edits and prove unchanged regions

Add `apply_film_score_adaptation` in the same module. It returns the candidate composition and the counts. Prefix events stay canonical-equal. Suffix events keep `id`, `pitch`, `duration_ticks`, and `velocity` and share one `start_tick` delta. Pitches are the scientific names from the fixture, starting at `C4`. Section ids, labels, and types stay, and the sections stay contiguous and cover the new `bar_count`. Later sections move by the bar delta. Harmony symbols before the cut stay equal. Chord symbols after the cut shift. A span or note that crosses the cut is truncated and adds `note_truncated`. Markers, tempo rows, key changes, meter changes, dynamic marks, pedals, and automation shift or drop with the same tick rule. Track ids and instrument ids stay equal. Original motif event ids stay on the track. Dropping a non-original occurrence whose events were removed sets `motif_occurrence_trimmed`.

`backend/tests/test_film_score_adapt_apply.py` builds the shared fixture and asserts vector A: removed bars are 9 through 11, shift `-5760`, the rehearsal marker tick is `30720`, climax label still present, original motif ids still present, instrument id unchanged, harmony symbol `F` present, `note_truncated` absent, `events_added` is 0, and the candidate `bar_count` is 29. Bar 1's pitch is `C4`. Vector C asserts every note event is canonical-equal to the source, `tempo` is still 120, and `tempo_changes` are exactly tick `15360` bpm `128` and tick `30720` bpm `120`. Vector E asserts the two inserted bars have no events and the suffix shift is `+3840`. The candidate passes `CompositionV2` validation.

LOGGING: INFO apply finished with project-less counts (`events_unchanged`, `events_shifted`, `events_removed`, `events_added`) and warning codes. DEBUG the tick shift. Do not log event arrays. Levels follow `LOG_LEVEL`.

Depends on Task 3.

### Task 5: Regenerate one bar only, and refuse a full-score replacement

Extend the applier with the `targeted_regenerate` rule from the decisions. The empty-bar fallback inserts one tonic note in this module and does not call `apply_film_score_accents`. The module must not import `film_score_workflow`, `film_score_accents`, or `ai_agents`.

`backend/tests/test_film_score_adapt_apply.py` adds vector D: only `note_bar_04` changes start tick, its pitch stays, and a registry fake recorded in the test is never called. The source text of `film_score_adapt.py` does not mention `apply_film_score_accents`. Add a test that a motif-linked attack in that bar is not moved and warning `melody_protected` is set when no other attack can move. Vector F raises `film_adapt_span_too_large` with a null candidate. An empty event list raises `film_adapt_score_empty`.

Update `backend/tests/test_film_score_workflow.py` so the `ai_agents/` import scan also fails on `film_score_adapt`.

LOGGING: DEBUG regeneration with cue id and start tick. WARNING `film_adapt_span_too_large` and `film_adapt_score_empty` with the code only. Do not log pitches. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Preview and commit the local candidate

Add the two routes on `backend/app/routers/film_score.py`. Mounting stays the existing `include_router`. Preview writes nothing. Commit reruns `apply_film_score_adaptation`, compares `composition_snapshot_fingerprint`, and passes the recomputed `CompositionV2` on a `DurableCommitRequest`. It does not commit the client dict by itself. The operation is `RevisionOperationType.FILM_SCORE_ADAPT_APPLY`. Do not call `put_video_scoring`. Add the enum value in `backend/app/project_history_schemas.py` and add `"film-score-adapt-apply"` to `AI_ORIGIN_OPERATIONS` in `backend/app/services/collaboration_permissions.py`.

`backend/tests/test_film_score_adapt_routes.py` stores the vector A picture and score. Preview returns `committed` false, strategy `phrase_contract`, and a candidate whose early-bar event ids match the stored score. The stored hit ids, `musical_tick` values, origin fields, and asset SHA-256 match the pre-call snapshot. Commit persists that candidate. A second GET of scoring shows the same cue rows and the same origin. A body whose candidate is a different composition returns `film_adapt_conflict` and leaves the stored fingerprint unchanged. A stale `expected_document_revision` does the same. Vector F through the route returns `film_adapt_span_too_large` and does not change the score. Missing picture returns `film_adapt_asset_missing`. `revision_origin("film-score-adapt-apply")` is `ai`.

LOGGING: INFO preview finished with project id, edit count, strategy names, counts, and warning codes. INFO commit finished with project id and the 12-character fingerprint prefix. WARNING conflict codes with project id. Do not log the candidate events. Levels follow `LOG_LEVEL`.

Depends on Task 5.

### Task 7: Review the proposal from the Agents tab

Add `frontend/src/api/filmScoreAdaptApi.js`, `frontend/src/utils/filmScoreAdapt.js`, and `FilmScoreAdaptPanel.jsx`. Add `filmAdaptBaseline` and `filmAdaptPreview` to the music store. Mount the panel from `frontend/src/components/MultiAgentPanel.jsx`. Remember calls `getVideoAsset` and reads `pictureScoring`, drops `label` and `instruction`, and stays disabled when either payload is missing. Preview posts `previous`, `edits`, and `expected_source_fingerprint`. The summary renders the proposal fields from Task 1. Commit posts the CAS fields and the same `previous` and `edits` used for the preview. Commit stays disabled until `candidate_fingerprint` is set.

`frontend/src/utils/filmScoreAdapt.test.js` covers vector A's `phrase_contract` row, vector C's tempo row, a disabled commit when the fingerprint is null, and the absence of `filmAdaptPreview` and `filmAdaptBaseline` from the composition object autosave would send.

LOGGING: the panel does not log cue labels or note events. API failures surface the error code on the panel.

Depends on Task 6.

### Task 8: Document film-score adaptation

Add `docs/film-score-adaptation.md` with the proposal, the edit kinds, the strategy order (`local_tempo` before bar removal, and not for `move_hit`), the identity rule for unchanged, shifted, and truncated events, the vectors A, C, and D numbers, and the statement that commit does not rewrite cue rows, video bytes, or the sync origin. Link it from `docs/film-scoring.md` and `docs/video-scoring.md` in a short pointer. Add the module, routes, and panel to `AGENTS.md` and the architecture note in `.ai-factory/ARCHITECTURE.md` that `ai_agents/` does not import `film_score_adapt` and that `film_score_adapt.py` does not import `film_score_accents`.

Do not document a new agent id. Do not change `ROADMAP.md`.

Depends on Tasks 6 and 7.
