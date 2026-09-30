# Implementation Plan: V5 Film Scoring Spotting and Hit Points

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-30

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Picture tab video timeline (cue ruler under the video, in-place cue editor, optional suggestion review) plus the existing musical bar ruler
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-09-30 (`/aif-improve`). A present timecode stores unclamped `video_seconds`. The tick uses `score_seconds_from_video` and `seconds_to_tick` only inside the composition; past the score, the nearest end tick is stored and the Picture tab marks it. `cue_frame` is `frames_from_zero` from `parse_timecode`. A null timecode verifies from `video_seconds`. `spotting_suggest` joins `creative_chat_operations()` and `AI_OP_SPOTTING_SUGGEST` in the same change. Suggest requires a picture and a closed rate
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next spotting milestone and does not edit `ROADMAP.md`
- Scope: richer spotting cues on the existing `video.scoring.v1` hit list, a deterministic landing check against note attacks, cue editing on the picture timeline, and an explicit suggestion preview that never runs by itself

## Roadmap Linkage
Milestone: "V5 Film scoring spotting cues"
Rationale: A scoring session needs authored picture cues and a tolerance check that a musical attack lands on that timecode. Recorded in `ROADMAP.md` on 2026-09-30 after verification, checked complete.

## Goal

A composer can mark where music should begin, end, or react to a picture event, edit that cue on the video timeline, and later check whether a note attack falls inside the cue's frame tolerance.

Ship:

1. Spotting cues stored on the existing `hit_points` list: kind, exact `HH:MM:SS:FF` timecode, frame tolerance, musical importance, and a short instruction.
2. Cue kinds `music_start`, `music_stop`, `hit_point`, `reveal`, `cut`, `action`, `dialogue`, `emotional_cue`, and `user_defined`.
3. In-place editing of those cues on a video-time ruler in the Picture tab. The musical bar ruler keeps projecting the same cues.
4. A pure landing check: a note attack lands when its picture frame is within `tolerance_frames` of the cue frame.
5. An explicit AI suggestion preview. Upload, open, and playback never call it. Accepting a suggestion writes cues through the existing scoring PUT. It does not write notes.

Acceptance: with frame rate `24/1` and start timecode `00:00:00:00`, the user can store a hit whose timecode is `00:03:42:12` (`video_seconds` `222.5`). A later check reports `landed` when a note attack maps to that frame, `landed` when the attack is inside the configured tolerance, and `missed` when the attack is outside that tolerance or when only the note's sustain covers the frame. The stored video bytes and `composition.v2` note events stay unchanged. No model call is required for authoring or for the check.

```text
timecode HH:MM:SS:FF
        ↓
parse_timecode − frames(start_timecode) → video_seconds
        ↓
video.scoring.v1 hit_points (kind, tolerance, importance, instruction)
        ↓
CompiledTimeline + video_seconds_from_score
        ↓
|event_frame − cue_frame| <= tolerance_frames
```

**Terminology lock:** Product generation is **V5**. The asset document stays `video.asset.v1`. The sync document stays `video.scoring.v1`. There is no `composition.v5` and no `video.scoring.v2`. A **cue** is one item in `hit_points`. A **hit point** is the cue kind `hit_point`, which is also the default kind for a row saved before this plan. **Marker** stays `CompositionV2Marker` (`rehearsal` or `text`). **Timecode** is the authored `HH:MM:SS:FF` address. **Tolerance** is an integer frame count around that address. **Importance** is `low`, `medium`, `high`, or `critical` and does not change notes, velocity, or the mix. **Instruction** is composer text stored on the cue. It does not run by itself. **Landing** compares a note attack's picture frame with the cue frame. **Suggestion** is a session preview. **Spotting** is authoring cues. It is not shot detection, burned-in timecode OCR, or a scene cut detector.

Predecessor: `.ai-factory/plans/v5-video-scoring-timeline.md`. Do not reopen the probe, the one-asset store, the sync-origin map, cursor leadership, or the decision that hit points are not notes. Do not add a `MarkerKind`. Do not write `tracks[].events[]`.

## Approach Evaluation (locked)

### Part A — Where a spotting cue lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New `MarkerKind` values on `composition.v2`** | Markers already sit on the score | `extra=forbid`. SMPTE, tolerance, and importance are not rehearsal marks. MIDI import already rejects SMPTE division | **Reject** |
| **B. A second document `video.spotting.v1` beside `hit_points`** | Old hit rows stay untouched | The Picture ruler already reads `hit_points`. Two lists drift | **Reject** |
| **C. Additive fields on `HitPointV1` inside `video.scoring.v1`. Schema version stays `video.scoring.v1`. `body_json` already stores the document** | One list. Old rows load through defaults. No Alembic revision | A PUT that sends a timecode resnaps that cue's tick | **Accepted** |

### Part B — What the exact timecode is

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep only `video_seconds` and format a timecode in the UI** | No new field | The address the user typed moves when start timecode or rate changes, and the server cannot tell an authored address from a formatted view | **Reject** |
| **B. Store only the timecode string and recompute seconds on every read** | The address is the source of truth | Every reader must repeat the formula. Legacy rows have seconds and a tick and no address | **Reject** |
| **C. Store `timecode` when the user authored one. On that PUT, derive unclamped `video_seconds` from the address, then `musical_tick` from `score_seconds_from_video` and `seconds_to_tick` while that score time is inside the composition. A legacy body that omits `timecode` or sends JSON `null` keeps the seconds and the tick it sent, and the server fills `timecode` with `format_timecode` when a closed rate exists** | `00:03:42:12` stays `222.5` even when the picture or the score is shorter. The existing route test that stores tick `480` at `1.0` s stays valid | A later tempo edit can still show the cue off-map until the user edits the address again | **Accepted** |

### Part C — What "lands within tolerance" means

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Any note whose sustain overlaps the window** | Easy overlap helper already exists | A held pad reports a hit on a cut it does not accent | **Reject** |
| **B. Compare the cue's stored `musical_tick` with the note tick, ignoring picture time** | No frame conversion | The acceptance address is `00:03:42:12`. An off-map tick would answer a different question | **Reject** |
| **C. Map each note `start_tick` through the existing sync origin to a picture frame. Landed when `abs(event_frame - cue_frame) <= tolerance_frames`** | The window is the timecode tolerance. Tempo changes stay in `CompiledTimeline` | Sustain across the frame does not count | **Accepted** |

### Part D — How AI suggestions enter the spotting list

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A spine agent that spots on upload or on Picture-tab mount** | Suggestions appear without a click | The user forbade required automatic detection. `ai_agents/` must not import the video modules | **Reject** |
| **B. Scan pixels, the audio track, or a shot detector** | Finds cuts without a brief | Probe stays box-level. No ffmpeg, no new media file, no detection requirement | **Reject** |
| **C. `POST .../spotting/suggest` only after an explicit click. Language planner returns cue drafts. Fake mode returns one fixed draft. Accept copies drafts into `hit_points` through the existing PUT** | Manual spotting works with no model. Tests do not call a network. Nothing is stored until Accept | The model can return unusable text, which the service drops | **Accepted** |

### Part E — Where the cue is edited

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace the musical bar ruler with a video ruler** | One timeline | Bars and composition markers are the picture map the previous plan shipped | **Reject** |
| **B. A form that is not on the timeline** | Simple fields | The user asked for editing on the video timeline | **Reject** |
| **C. A video-time cue ruler under the `<video>` element, selection that opens kind / timecode / tolerance / importance / instruction in place, and the existing bar ruler as a projection of the same ids** | Picture time and musical position stay visible together. Dragging the video ruler edits the address | Two rulers must use the same cue id | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Cue row | `HitPointV1` in `backend/app/video_scoring_schemas.py`: `id` `hit_[0-9a-f]{8}`, `label` 1..80, `video_seconds`, `musical_tick`. Max 64. Unique ids | Add fields with defaults. Do not change the id pattern or the cap |
| Sync document | `VideoScoringV1` / `VideoScoringUpdateV1`. PUT CAS via `expected_document_revision`. `body_json` in `video_scoring` | Same PUT. No new table and no revision `20260930_0020` |
| Timecode | `format_timecode` and `parse_timecode` in `backend/app/services/video_scoring_map.py`. Closed rates only. Drop-frame is `30000/1001` | The only frame conversion. Cue seconds are `frames_from_zero * denominator / numerator` |
| Map | `score_seconds_from_video`, `video_seconds_from_score`, `CompiledTimeline.tick_to_seconds` / `seconds_to_tick`. `map_video_to_music` clamps video time to the asset and score time to the composition before it picks a tick | Landing and PUT use the unclamped pair. Do not store `map_video_to_music(...).tick`. Do not add a second tempo integral |
| Picture tab | `frontend/src/components/VideoScoringPanel.jsx` adds a hit at `video.currentTime` with label `Hit N` and removes by id. Bar ruler projects markers and hits. `hitPointOffMap` is display-only | Keep add/remove. Extend the row and add the video-time ruler |
| Session hits | `pictureScoring` in `musicStore.js` is not autosaved into `composition_json` | Suggestions are a second session slice. Cues become durable only through PUT |
| Preview AI | Arrangement and development return drafts; Apply is a later write. `LLM_FAKE_MODE` is `backend/app/services/fake_llm.py`. Operations live in `AiOperation` | New operation `spotting_suggest` follows that split. The check itself is not a model call |
| Note attack | `CompositionV2NoteEvent.start_tick` and `duration_ticks`. `attack_in_interval` exists for tick windows | Landing is frame-based, so do not switch the definition to sustain overlap |
| Permissions | `enforce_current`: `write_score` for POST/PUT/DELETE, `read` for GET | Verify is `read`. Suggest is `write_score` |
| Tests | `backend/tests/test_video_scoring_schemas.py`, `test_video_scoring_map.py`, `test_video_scoring_routes.py`. Frontend `frontend/src/utils/videoScoringMap.test.js` | Extend those files. The `00:03:42:12` vector is new |
| Alembic head | `20260930_0019_video_scoring.py` | Unchanged |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Cue kind, timecode, tolerance, importance, instruction | `HitPointV1` has none. The UI cannot edit label, time, or kind |
| Video-time ruler | Hits are drawn on the musical bar ruler only |
| Landing check | Nothing compares a note attack with a cue frame |
| Suggestion preview | No route, no fake draft, no session list |
| Legacy rows | Stored hits have no `timecode`. They must still load |

### Coupling risks to avoid

1. Adding a `MarkerKind` or writing cue fields into `composition.v2` or `tracks[].events[]`.
2. Letting `music_start` or `music_stop` start or stop Tone, the video element, or the adaptive clock.
3. Letting importance change velocity, mixer state, or generation constraints.
4. Importing `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, `video_scoring_map`, `video_spotting`, or `llm_video_spotting` from `ai_agents/`.
5. Running suggestion from upload, from GET scoring, from picture playback, or from project open.
6. Calling ffmpeg, ffprobe, a shot detector, or a sample decoder. Suggestion context is the brief, duration, the closed rate, and the existing cue fields. It does not include note pitches or video bytes.
7. Logging instruction text, cue labels in bulk, the brief, video bytes, or note events.
8. Resnapping `musical_tick` on a PUT that omits `timecode` or sends JSON `null`. That would move the existing `hit_0123abcd` row at tick `480`.
9. Storing the tick from `map_video_to_music`. That function clamps first, so a short picture or a short score would save the end tick and the off-map label would never appear.
10. Treating a sustained note that merely covers the cue frame as a landing.
11. Clamping an out-of-range attack into the asset duration and then counting it as a hit.
12. Choosing suggestion timecodes with `random`. Fake mode returns the fixed draft below.
13. Editing `ROADMAP.md` or adding a `video.scoring.v2` schema.

## Scope And Decisions

### In scope
- Additive cue fields and the landing check.
- PUT derivation of `video_seconds` and `musical_tick` when `timecode` is present.
- `POST /projects/{project_id}/video-scoring/spotting/verify` and `POST /projects/{project_id}/video-scoring/spotting/suggest`.
- Picture-tab video-time ruler, in-place editor, and suggestion accept/dismiss.
- Tests for `00:03:42:12`, tolerance, sustain exclusion, legacy rows, and fake suggestion that does not persist.
- Docs named in Task 7.

### Out of scope
- Scene detection, silence detection, burned-in timecode OCR, waveforms, and thumbnails.
- ffmpeg, ffprobe, and a new media file.
- Generating, moving, or deleting notes from a cue or from a suggestion.
- Transport behavior for `music_start` and `music_stop`.
- A new marker kind, a new Alembic revision, or `composition.v5`.
- Changing the sync-origin formula, probe, cursor leadership, or asset replace rules.
- Automatic suggestion. The suggest button is the only trigger.

### Architecture decisions (locked)

**1. Cue document**

`HitPointV1` gains fields. Omitted keys on load use the defaults, so a row written before this plan still validates.

| Field | Rule |
|-------|------|
| `id` | unchanged `^hit_[0-9a-f]{8}$`, unique in the list |
| `kind` | `music_start`, `music_stop`, `hit_point`, `reveal`, `cut`, `action`, `dialogue`, `emotional_cue`, or `user_defined`. Default `hit_point` |
| `label` | 1..80 characters. For `user_defined` this is the custom name. Default is not applied; the client still sends a label |
| `timecode` | `HH:MM:SS:FF` or null. Null means a legacy row |
| `video_seconds` | finite, `>= 0`. When `timecode` is present on PUT, the server replaces this value |
| `musical_tick` | int, `>= 0`. When `timecode` is present on PUT, the server replaces this value from unclamped score seconds. Inside the composition, that is `seconds_to_tick`. Outside it, that is the nearest end (`0` or `duration_ticks`) |
| `tolerance_frames` | int, `0..240`. Default `0` (exact frame) |
| `importance` | `low`, `medium`, `high`, or `critical`. Default `medium` |
| `instruction` | 0..240 characters. Default `""` |

Unknown `kind` or `importance`, a negative tolerance, a tolerance above 240, or an instruction longer than 240 characters is `video_scoring_invalid`. `HitPointV1` checks the `HH:MM:SS:FF` shape only. `VideoScoringV1` and `VideoScoringUpdateV1`, which know the closed rate, reject a cue frame at or past the nominal rate, hours above 23, and a negative frame offset from `start_timecode` as `video_timecode_invalid`. `user_defined` uses the same label rule as every other kind.

The list cap stays 64. Delete-asset still clears `hit_points`. Frame rate, start timecode, and sync origin stay.

Address derivation, for a present `timecode`, a closed rate, and the document's `timecode_mode` and `start_timecode`:

```text
frames_from_zero = parse_timecode(cue.timecode) - parse_timecode(start_timecode)
video_seconds = frames_from_zero * frame_rate_denominator / frame_rate_numerator
score_seconds = score_seconds_from_video(video_seconds)
musical_tick = seconds_to_tick(score_seconds)    # only while score_seconds is inside the composition
```

`frames_from_zero` must be `>= 0`. `video_seconds` stays `222.5` even when that is past `duration_seconds`. The server ignores client `video_seconds` and `musical_tick` for that cue. The origin is the one in the same PUT body. Do not call `map_video_to_music` to choose the stored tick: it clamps video time to the asset and score time to the composition first. If `score_seconds` is below `0` or above `total_duration_seconds()`, store `musical_tick` as `0` or `duration_ticks`. The Picture tab marks that cue past the score. `seconds_to_tick` already returns `duration_ticks` once seconds reach the end, so the inside-range call is only for `0 <= score_seconds < total`. If the rate is not a closed rate, a present `timecode` returns `video_frame_rate_required`.

A body that omits `timecode`, or sends JSON `null`, keeps the submitted `video_seconds` and `musical_tick`. When a closed rate is available, the stored `timecode` becomes `format_timecode(video_seconds)` and the tick does not move.

**2. Landing check**

Pure `verify_cue_landings` in `backend/app/services/video_spotting.py`. No SQLite, FastAPI, file I/O, or model client. It may call `video_scoring_map` and `CompiledTimeline`.

For each cue and a closed rate:

```text
cue_frame = frames_from_zero                         # parse_timecode(cue.timecode) - parse_timecode(start)
                                                     # when timecode is null: floor(video_seconds * numerator / denominator + 1e-9)
event_video = video_seconds_from_score(tick_to_seconds(note.start_tick))
event_frame = floor(event_video * numerator / denominator + 1e-9)
landed when abs(event_frame - cue_frame) <= tolerance_frames
```

Do not recompute an authored `cue_frame` by flooring `video_seconds * numerator / denominator`. At `30000/1001` that recount can move one frame off the timecode the user typed. A null `timecode`, including a legacy row, uses the stored `video_seconds` and the same floor the formatter already uses.

Only events with `type == "note"` are attacks. `duration_ticks` does not widen the window. An attack whose video seconds fall outside `[0, duration_seconds]` is not clamped into range and is not a match. The closest attack is the smallest absolute frame delta, then the smaller `start_tick`, then the earlier track. `delta_frames` is `event_frame - cue_frame` for that closest attack.

Per cue, `status` is `landed`, `missed`, or `empty`. `empty` means the composition has no note attacks. `missed` means there is at least one attack and none inside the tolerance. The result includes `match_count` and at most 8 match refs `{track_index, event_index, start_tick, pitch}`. The function does not sort or copy the composition into the result beyond those refs.

Response document `video.spotting.verification.v1`: `project_id`, `document_revision`, and `cues[]` with `id`, `kind`, `timecode`, `tolerance_frames`, `status`, `delta_frames`, `match_count`, and `matches`. No write.

Locked vector, rate `24/1`, start `00:00:00:00`, timecode `00:03:42:12`:

```text
frames_from_zero = ((3 * 60) + 42) * 24 + 12 = 5340
video_seconds = 5340 / 24 = 222.5
```

The note's expected tick is `seconds_to_tick(222.5)` on that composition's timeline, not a constant BPM formula copied into the spotting module. Three frames at `24/1` are `0.125` seconds. An attack that far away is `missed` at tolerance `1` and `landed` at tolerance `3`. An attack on frame `5340` is `landed` at tolerance `0`.

A drop-frame document uses `parse_timecode` for the address. The test for that path can use a short code; it does not have to use `00:03:42:12`.

**3. HTTP**

Mounted on the existing video scoring router. Paths stay under `/projects`.

| Route | Auth | Behavior |
|-------|------|----------|
| `POST /projects/{project_id}/video-scoring/spotting/verify` | `read` | Optional JSON `cue_id`. Loads the stored scoring document and the stored composition. Returns `video.spotting.verification.v1`. Missing asset is `video_asset_missing` (404). Missing closed rate is `video_frame_rate_required`. Does not write |
| `POST /projects/{project_id}/video-scoring/spotting/suggest` | `write_score` | JSON `brief`, 0..500 characters. Returns `video.spotting.suggestion.v1` with `persisted: false`. No picture is `video_asset_missing` (404). A scoring rate that is not closed is `video_frame_rate_required`. Does not write scoring or the composition. Does not read video bytes |

`video.spotting.suggestion.v1` items have no `id`. Fields: `kind`, `label`, `timecode`, `tolerance_frames`, `importance`, `instruction`. The client assigns `hit_` ids when the user accepts. Accept is the existing scoring PUT.

Suggestion context sent to the model: asset `duration_seconds`, the closed frame rate, `timecode_mode`, `start_timecode`, the brief, and the current cues (`kind`, `timecode`, `importance`, `instruction`, `label`). No note events, no video bytes, no file path.

Invalid model items are dropped. If every item is unusable, the response is 200 with `suggestions: []` and warning `video_spotting_unparsed`. A provider or transport failure is 503 `video_spotting_model_unavailable`. The Picture tab still edits cues by hand. A brief longer than 500 characters is 422 `video_spotting_brief_invalid`.

New operation `AiOperation.SPOTTING_SUGGEST` (`spotting_suggest`), env key `AI_OP_SPOTTING_SUGGEST`, default capability `language_planner`. In the same change, add the enum member, the `_OP_ENV_KEYS` entry, and the member of `creative_chat_operations()`. Chat models only advertise that tuple, and `operation_env_key` walks every `AiOperation` for `/ready`. `LLM_FAKE_MODE` returns one draft and does not open a socket: kind `hit_point`, label `Suggested hit`, timecode `00:00:01:00`, tolerance `2`, importance `high`, instruction `""`. That draft is not the acceptance hit. The acceptance hit is authored by the user.

**4. Picture timeline**

In `VideoScoringPanel.jsx`, under the video element, a cue ruler is positioned by `video_seconds / duration_seconds`. A cue past the asset duration sits on the ruler's end and is labeled past the picture. Selecting a mark shows the editor: kind, label, timecode, tolerance frames, importance, instruction, Remove. Save sends the cue list through the existing PUT and assigns `pictureScoring` from the response, which is what `save` already does with `setPictureScoring(saved)`. Dragging a mark sets a new timecode from the pointer's video time and includes that timecode so the server resnaps the tick. The musical bar ruler continues to use `musical_tick / durationTicks` and `hitPointOffMap`. When unclamped `score_seconds_from_video` is outside the composition, the mark is labeled past the score even if the stored end tick matches the clamped map.

Suggest is a button. It is not called from the upload effect or from the panel mount. Results live in `pictureSpottingSuggestions` on the session store. Accept appends the draft with `nextHitId()` and PUTs. Dismiss clears the session list. The cap of 64 refuses another accept and leaves the stored document as it was.

The working composition is checked in the browser with the twin of `verify_cue_landings`, so an unsaved generated event can be verified before autosave. The server route checks the stored project. Both use the `222.5` vector.

Frontend twins live in `frontend/src/utils/videoScoringMap.js` next to the existing map helpers. New helpers: cue seconds from an authored timecode, and the landing check. They must match the backend vector, including one composition that changes tempo.

**5. Logging**

`LOG_LEVEL` controls verbosity. INFO: verify started and finished with project id, cue count, and counts of `landed` / `missed` / `empty`; suggest started and finished with project id, brief length, suggestion count, and warning code if any; accept count. DEBUG: cue id, kind, timecode, tolerance, importance, `delta_frames`, and status. WARNING: schema rejection code, model unavailable code, unparsed suggestion. ERROR: unexpected verification or suggestion failure with the error code. Never log instruction text, the brief, cue labels in a list, video bytes, paths, or note arrays. Pitch may appear in the HTTP response and must not be written to the log line.

## Tasks

### Phase 1: Cue contract and landing check
- [x] Task 1: Extend `HitPointV1` with spotting fields
- [x] Task 2: Add timecode derivation and the landing check

### Phase 2: HTTP
- [x] Task 3: Derive cue time on PUT and add the verify route
- [x] Task 4: Add the explicit spotting suggestion preview

### Phase 3: Picture timeline
- [x] Task 5: Add frontend cue and landing twins
- [x] Task 6: Edit cues on the video timeline and accept suggestions

### Phase 4: Docs
- [x] Task 7: Document spotting cues, verification, and suggestions

## Commit Plan
- **Commit 1** (after tasks 1-2): "feat: check spotting cues against note attacks"
- **Commit 2** (after tasks 3-4): "feat: verify and suggest film spotting cues"
- **Commit 3** (after tasks 5-6): "feat: edit spotting cues on the picture timeline"
- **Commit 4** (after task 7): "docs: describe film scoring spotting cues"

## Tasks (detail)

### Task 1: Extend `HitPointV1` with spotting fields

Add `kind`, `timecode`, `tolerance_frames`, `importance`, and `instruction` to `HitPointV1` in `backend/app/video_scoring_schemas.py` using the defaults and closed sets in the decisions. `extra` stays `forbid`. `HitPointV1` accepts a null `timecode` and checks only the `HH:MM:SS:FF` shape when one is present. `VideoScoringV1` and `VideoScoringUpdateV1` keep the same list field and the 64 cap, and they reject a cue whose frame is outside the nominal rate or whose frame offset from `start_timecode` is negative (`video_timecode_invalid`). Add error codes `video_spotting_unparsed`, `video_spotting_model_unavailable`, and `video_spotting_brief_invalid` to `VideoScoringErrorCode`, messages, and HTTP status (`503` only for the model code; brief stays 422).

`backend/tests/test_video_scoring_schemas.py` still accepts the current golden hit that has only id, label, seconds, and tick, and that row's kind is `hit_point`, tolerance is `0`, importance is `medium`, instruction is `""`, and timecode is null. Explicit `"timecode": null` loads the same way. Also accept one cue of each kind, including `user_defined` with a custom label. Reject an unknown kind, a tolerance of `-1` and of `241`, an importance outside the set, an instruction of 241 characters, a duplicate id, and, on the scoring document at `24/1` with start `00:00:00:00`, a cue timecode whose frames are `24` or whose address is before the start timecode.

LOGGING: DEBUG on schema rejection with the model name and error code. Do not log the instruction or the label. Levels follow `LOG_LEVEL`.

Files: `backend/app/video_scoring_schemas.py`, `backend/tests/test_video_scoring_schemas.py`.

### Task 2: Add timecode derivation and the landing check

Add `cue_video_seconds` in `backend/app/services/video_scoring_map.py` using `parse_timecode` and the formula in the decisions. It returns the unclamped seconds. Add `verify_cue_landings` in `backend/app/services/video_spotting.py`. The module imports the map and the timeline only. It does not import FastAPI, SQLite, or `ai_agents`.

`cue_frame` for a present timecode is `frames_from_zero` from `parse_timecode`, not `floor(video_seconds * numerator / denominator)`. A null `timecode` uses that floor on the stored `video_seconds`, with the same `+ 1e-9` the formatter uses. Note attacks use that floor. An attack outside `[0, duration_seconds]` is not clamped into a match.

`backend/tests/test_video_scoring_map.py` asserts the locked vector: rate `24/1`, start `00:00:00:00`, timecode `00:03:42:12` yields `222.5` seconds even when the asset duration is shorter, and a negative frame offset from the start timecode raises `video_timecode_invalid`.

`backend/tests/test_video_spotting.py` builds a composition long enough to contain `222.5` score seconds and asserts the expected tick with `seconds_to_tick`, not a handwritten BPM product. Cases: attack on that tick with tolerance `0` is `landed`; an attack `0.125` s away is `missed` at tolerance `1` and `landed` at tolerance `3`; a note that starts before the window and whose `duration_ticks` covers the cue frame is `missed`; a composition with no notes is `empty`; one tempo-change composition does not use a single root-tempo tick; an attack past `duration_seconds` is not a match. A cue with `timecode` null and `video_seconds` `222.5` uses frame `5340`. One drop-frame case at `30000/1001` checks that `cue_frame` is the parsed `frames_from_zero` and would differ if the code refloored the float seconds. The tick helper returns `duration_ticks` when score seconds are past the composition, and returns the unclamped `video_seconds` unchanged. Persistence of that pair is the route test in Task 3.

LOGGING: DEBUG each landing row with cue id, timecode, tolerance, status, and `delta_frames`. Do not log pitches or the event list. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Derive cue time on PUT and add the verify route

In `scoring_from_update` (`backend/app/video_scoring_schemas.py`), called by `put_video_scoring`, apply the derivation rules. The route already loads the timeline in `write_video_scoring`, so pass that timeline into the derivation rather than calling the clamping map inside the store. A present `timecode` overwrites `video_seconds` with the unclamped address and sets `musical_tick` from `score_seconds_from_video` plus `seconds_to_tick` while that score time is inside the composition. Outside it, store `0` or `duration_ticks`. An omitted `timecode` and an explicit JSON `null` preserve `video_seconds` and `musical_tick`, and fill the display timecode when the rate is closed. Use the origin from the same request body.

Add `POST /projects/{project_id}/video-scoring/spotting/verify` with `read` access. It loads the stored composition and scoring document, calls `verify_cue_landings`, and returns `video.spotting.verification.v1`. Optional `cue_id` limits the list. Unknown `cue_id` is `video_scoring_invalid`. A stored cue with null `timecode` is still verified from `video_seconds`.

`backend/tests/test_video_scoring_routes.py` keeps the existing PUT of `hit_0123abcd` at `video_seconds` `1.0` and `musical_tick` `480` when `timecode` is omitted and when it is JSON `null`, and asserts the asset SHA-256 is unchanged. A new case PUTs timecode `00:03:42:12` at `24/1` with start `00:00:00:00` on a short composition and asserts stored `video_seconds` is still `222.5` and `musical_tick` is `duration_ticks`. The landing case uses a composition long enough for `222.5` seconds, stores a note attack on that tick, and asserts `landed`, then asserts `tracks[].events[]` and the media hash are unchanged. A sustain-only case asserts `missed`.

LOGGING: INFO one line when verify finishes, with project id, revision, and the three status counts. DEBUG the per-cue status. Never log the composition body. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Add the explicit spotting suggestion preview

Add `AiOperation.SPOTTING_SUGGEST` in `backend/app/ai_runtime/operations.py` in the same edit as the `_OP_ENV_KEYS` entry `AI_OP_SPOTTING_SUGGEST` and a place in `creative_chat_operations()`. Map it to `language_planner` in `backend/app/ai_runtime/capabilities.py`. Implement `suggest_spotting_cues` in `backend/app/services/llm_video_spotting.py`. Fake mode, in `backend/app/services/fake_llm.py`, returns the single fixed draft from the decisions and ignores the brief text. Validate model items against the cue kinds and drop the rest.

Add `POST /projects/{project_id}/video-scoring/spotting/suggest` on the video router with `write_score`. No asset returns `video_asset_missing`. A scoring rate that is not closed returns `video_frame_rate_required`. Response `video.spotting.suggestion.v1` with `persisted: false`. Do not call the store write helper.

`backend/tests/test_video_spotting_suggest.py` uses `LLM_FAKE_MODE`. The suggest call returns the fixed `00:00:01:00` draft, leaves `hit_points` and the composition events identical to the pre-call snapshot, and does not require an existing cue. A brief of 501 characters returns `video_spotting_brief_invalid`. Missing picture and a null scoring rate return the codes above. `creative_chat_operations()` includes `spotting_suggest`, and `operation_env_key` resolves `AI_OP_SPOTTING_SUGGEST`. The upload route test still finishes with an empty hit list and no suggestion field. Search `backend/app/ai_agents` for `video_spotting`, `llm_video_spotting`, and `video_scoring` and assert no matches.

LOGGING: INFO suggest finished with project id, brief length, and suggestion count. DEBUG each kept suggestion's kind, timecode, tolerance, and importance. Do not log the brief, the instruction, or the raw model text. WARNING on `video_spotting_unparsed` and `video_spotting_model_unavailable` with the code only. Levels follow `LOG_LEVEL`.

Depends on Task 1. Independent of the landing check.

### Task 5: Add frontend cue and landing twins

Extend `frontend/src/utils/videoScoringMap.js` with the cue-seconds helper and the landing check described in the decisions. `cueFrame` for an authored timecode is `frames_from_zero`. A null timecode uses `floor(video_seconds * numerator / denominator + 1e-9)`. Use the existing `parseTimecode` / `formatTimecode` twins. Do not add a second tempo integral.

`frontend/src/utils/videoScoringMap.test.js` asserts `00:03:42:12` at `24/1` and start `00:00:00:00` is `222.5` seconds, the tolerance `1` versus `3` case at a `0.125` s offset, the sustain-is-missed case, a null timecode at `222.5` seconds landing on frame `5340`, and one tempo-change composition whose tick matches the backend `seconds_to_tick` expectation for `222.5`.

LOGGING: the browser helpers do not log note arrays. A single `console.debug` may record cue id, status, and `delta_frames` when the panel asks. No instruction text.

Depends on Task 2 for the vectors. This task ships in commit 3 with Task 6.

### Task 6: Edit cues on the video timeline and accept suggestions

Update `frontend/src/components/VideoScoringPanel.jsx`, `frontend/src/api/videoScoringApi.js`, and the picture slice of `frontend/src/store/musicStore.js`.

The video-time ruler places each cue by `video_seconds / duration_seconds`. A cue past the asset duration sits on the end of that ruler and reads as past the picture. Selecting one cue shows kind, label, timecode, tolerance, importance, and instruction. Saving and dragging send `timecode` so the server owns `video_seconds` and `musical_tick`. After PUT, keep `setPictureScoring(saved)` so the bar ruler uses the tick the server stored. Add cue still uses the current video time, now with kind `hit_point`, tolerance `0`, importance `medium`, an empty instruction, and a timecode formatted from that time. The bar ruler still projects the same ids and still marks off-map cues. It also marks a cue past the score when unclamped score seconds fall outside the composition.

Suggest calls the new API only from the button handler. Store `pictureSpottingSuggestions` off the composition autosave path. Accept assigns `nextHitId()`, appends, and PUTs. Dismiss drops the session list. At 64 cues, Accept does not PUT.

A Verify control runs the twin against the working composition and shows `landed`, `missed`, or `empty` and `delta_frames` beside the selected cue. It also offers the stored-score route result when a project is open. Neither path writes notes.

Confirm in the browser: author `00:03:42:12`, see it on the video ruler, toggle tolerance, and see the landing status change for a note inside and outside the window. Confirm Suggest does not fire on upload or on tab open, and that Accept adds a cue while Dismiss does not.

LOGGING: `console.info` for suggest and verify with cue count and status counts. `console.warn` with the error code on a failed request. Do not log the brief or the instruction.

Depends on Tasks 3, 4, and 5.

### Task 7: Document spotting cues, verification, and suggestions

Extend `docs/video-scoring.md` with sections for spotting cues, the landing check (including the `00:03:42:12` → `222.5` vector, the attack-only rule, `cue_frame` as `frames_from_zero`, and the past-the-score tick), and the suggest route. State that suggestion is explicit, that it requires a picture and a closed rate, that `ai_agents/` does not import the video or spotting modules, and that cues are not markers or notes.

Add the `spotting_suggest` / `AI_OP_SPOTTING_SUGGEST` / `language_planner` row to the operation table in `docs/ai-runtime.md`.

Update the video scoring rows in `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` so the import ban lists `video_spotting` and `llm_video_spotting`. Do not edit `ROADMAP.md`.

LOGGING: none beyond the behavior the doc says the services already emit.

Depends on Tasks 3, 4, and 6.
