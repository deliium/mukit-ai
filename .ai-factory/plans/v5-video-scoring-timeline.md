# Implementation Plan: V5 Video Scoring Timeline

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-30

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Picture tab in the studio (video element, timecode, bar ruler, composition markers, hit points) plus the existing piano-roll playback cursor
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-09-30 (`/aif-improve`). The probe seeks over `mdat` so a trailing `moov` still parses. Explicit frame-rate fields stay null until a closed rate is chosen. Replace writes the new file before deleting the old one and keeps an explicit rate. The map query takes one selector. `playbackSeconds` stores score seconds. Picture play uses the Tone pause path. Hit points are off-map past one tick. `.env.example` lists the video caps. Nginx limits only the upload location
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next picture milestone and does not edit `ROADMAP.md`
- Scope: one immutable video asset per project, a `video.scoring.v1` sync document, and a single session clock that keeps the composition cursor on the same instant as video timecode and musical bars

## Roadmap Linkage
Milestone: "V5 Video scoring timeline"
Rationale: Film scoring needs a project video asset and a tempo-aware map onto composition bars. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A composer can upload one video onto a project, see duration, frame rate, audio presence, and resolution, set the project frame rate and timecode, and play that video while the composition cursor stays on the same instant as the video timecode and the musical bar.

Ship:

1. A read-only import of one video file as a project asset. The stored bytes are never rewritten.
2. Metadata: duration, frame rate, whether an audio track is present, and pixel resolution.
3. Explicit project frame rate and timecode settings, separate from the probed snapshot.
4. A Picture tab with a video player, a timecode readout, a musical bar ruler, existing composition markers, and authored hit points.
5. A pure map between video time and musical ticks that uses the composition timeline, including tempo changes.
6. Fixture-video tests that build a tiny ISO-BMFF file in process and probe it. No ffmpeg and no checked-in binary.

Acceptance: with a stored picture and a composition that has a tempo change, playing the video moves the piano-roll playback cursor to the tick and bar returned by the map, and the timecode readout matches that video time under the explicit frame rate. Changing frame rate, start timecode, sync origin, or hit points does not change the asset SHA-256. Deleting the project removes the file. `composition.v2` note events are not rewritten by upload, probe, settings, or playback.

```text
upload (read bytes once) → probe boxes → immutable file + video.asset.v1
        ↓
video.scoring.v1  (explicit fps, timecode, sync origin, hit points)
        ↓
CompiledTimeline.tick_to_seconds / seconds_to_tick
        ↓
Picture tab clock  ↔  playback cursor (one leader at a time)
composition.v2 markers (read) and hit points (scoring doc) on the bar ruler
```

**Terminology lock:** Product generation is **V5**. The asset document is `video.asset.v1`. The sync document is `video.scoring.v1`. There is no `composition.v5`. **Probe** reads container boxes and returns metadata. It does not transcode, demux to a new media file, or rewrite the upload. **Explicit frame rate** is the rational the composer sets for timecode. **Probed frame rate** is the snapshot taken at import. **Start timecode** is the `HH:MM:SS:FF` label of video time 0. **Sync origin** is the single pair `(video_seconds, musical_tick)` that places the picture on the score. **Hit point** is a labeled pair stored on the scoring document. It is not a `composition.v2` marker and not a note. **Marker** stays `CompositionV2Marker` (`rehearsal` or `text`). The Picture tab projects those markers through the map. **Leader** is whichever clock may write `playbackSeconds`: the video element or the Tone transport, never both. **Picture playback** means the `<video>` element is playing. It does not mean a neural render or an HTMLAudio recovery source.

Predecessor: none for picture. Do not reopen audio-alignment decisions (`audio.alignment.v1` stays the recovery-source map; source playhead stays `sourcePlayheadTick`). Do not reopen adaptive-engine decisions (no `/adaptive/*` changes, no client SDK changes). This plan does not add a `MarkerKind`, does not write `tracks[].events[]`, and does not add ffmpeg, ffprobe, or a Compose media profile.

## Approach Evaluation (locked)

### Part A — Where picture bytes live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Bytes inside `projects.composition_json` or a new column on `projects`** | One row | Blows SQLite, mixes media into the score, breaks `extra=forbid` on V2 if fps is stuffed into the composition | **Reject** |
| **B. Reuse `audio_recovery_assets` and Bind** | Tables exist | Recovery is WAV-in, notes-out. A silent or picture-led asset would widen that CHECK and imply transcription | **Reject** |
| **C. New asset table plus `VIDEO_ASSET_ROOT`, same shape as recovery: UUID path, sha256 prefix, project-delete GC** | Source stays a file. Score JSON stays notes. Root refusal already exists | One more migration | **Accepted** |

### Part B — How duration, frame rate, audio presence, and resolution are read

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require ffprobe/ffmpeg** | Handles every container | Not in requirements or images. Fixture tests would skip in CI | **Reject** |
| **B. Trust client-supplied metadata** | No parser | The browser can lie. Acceptance needs a server fixture probe | **Reject** |
| **C. Bounded read-only ISO-BMFF (`mp4`/`mov`) parser, fixture built in pytest** | CI has no binary tool. Parser never writes the file | WebM and MPEG-TS wait. Unsupported brands return a typed error | **Accepted** |

### Part C — What the video-time ↔ tick map is

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `tick * 60 / rootTempo / ticks_per_quarter`** | Short | Wrong after `tempo_changes`. Tone already uses compiled seconds, not Transport BPM, for note times | **Reject** |
| **B. Piecewise warp through every hit point** | Spotting list becomes the tempo | Two tempo maps. The cursor jumps when a hit point is edited | **Reject** |
| **C. One sync origin, then `CompiledTimeline.tick_to_seconds` / `seconds_to_tick`** | Matches playback. Tempo changes stay in the score. Hit points stay labels | A hit point may sit off the line; the UI shows that, it does not bend time | **Accepted** |

### Part D — Where hit points and frame rate are stored

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New `MarkerKind` values and fps fields on `composition.v2`** | Markers already export to MIDI | `extra=forbid`. Hit points and SMPTE are not notes, rehearsal marks, or conductor events. MIDI import already rejects SMPTE division | **Reject** |
| **B. Session-only Zustand state** | No migration | Restart drops the spotting list and the frame rate | **Reject** |
| **C. `video.scoring.v1` JSON beside the asset. Markers stay on the composition and are only projected** | Explicit settings survive restart. Upload does not edit the score | Two documents to load | **Accepted** |

### Part E — Which clock moves the composition cursor

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A third playhead, copying `sourcePlayheadTick`** | Aligns with recovery audition | Acceptance asks for the composition cursor, which `PianoRollOverlayLayer` hides while `playbackStatus === 'idle'` | **Reject** |
| **B. Let Tone and `<video>` both write `playbackSeconds`** | Both "play" | They drift. The 100 ms Tone poll overwrites the video clock | **Reject** |
| **C. One leader. Picture play pauses Tone and writes the cursor from `timeupdate`. Tone play pauses the video and seeks it to the mapped time** | One instant. The existing cursor component is enough | Hearing the score while the picture plays is a follow-up, not this plan | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Musical time | `CompiledTimeline.tick_to_seconds` / `seconds_to_tick` / `bar_at_tick` in `backend/app/services/composition_timeline.py`. JS twins in `frontend/src/utils/compositionTimeline.js` | The only tick conversion. Do not add a second tempo integral |
| Cursor | `playbackSeconds` in `musicStore`, poll in `PlaybackControls`, `PlaybackCursor` in `PianoRollOverlayLayer.jsx` | Picture leader writes the same fields. Extend the idle hide so a picture leader still draws the cursor |
| Markers | `CompositionV2Marker`, kinds `rehearsal` and `text` | Read-only projection. No schema change |
| Asset pattern | `audio_recovery_store.py`: UUID relpath, 16-hex sha256 prefix, `reject_storage_root`, `cleanup_project_audio_recovery` from `delete_project` | Copy the shape. New table, new root, new cleanup call |
| Upload bound | `read_upload_bounded` in `backend/app/audio_upload.py` | Same reader. Map overflow to HTTP 413 |
| Tabs | `TABS` in `ComposerWorkspace.jsx`, `requestComposerTab` | One `picture` tab |
| Tests | pytest `tmp_path` + Alembic; frontend `node --test` on `src/utils/*.test.js` | Fixture builder in pytest. Mapping tests in pytest and `node:test` |
| Alembic head | `20260928_0018_adaptive_scores.py` revises `20260926_0017` | Next revision `20260930_0019` |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Picture bytes | No video asset table, root, or route |
| Probe | No ISO-BMFF reader. ffmpeg is not installed |
| Scoring document | No frame rate, start timecode, sync origin, or hit points |
| Cursor leadership | Tone is the only writer of `playbackSeconds` while playing. Idle hides the playback cursor |
| Fixture video | No mp4/mov builder |
| Upload body limit | `location /projects` in `frontend/nginx.conf` has no `client_max_body_size`, so nginx defaults to 1 MiB |

### Coupling risks to avoid

1. Writing fps, timecode, hit points, or asset ids into `composition.v2` or `tracks[].events[]`.
2. Rewriting, transcoding, or demuxing the stored video. Probe opens the file read-only.
3. Putting `VIDEO_ASSET_ROOT` on `DATASET_ROOT` or on `PROJECT_DB_PATH`.
4. Importing video modules from `ai_agents/`.
5. Reusing `sourcePlayheadTick`, `sourceSeekRequest`, or `audio.alignment.v1` for picture time.
6. Calling ffprobe, ffmpeg, or a neural/media sidecar.
7. Logging video bytes, full paths, or original filenames beyond a basename.
8. Letting Tone's position timer and the video `timeupdate` both assign `playbackSeconds`.
9. Editing `ROADMAP.md` or adding `MarkerKind` values.
10. Choosing hit-point times with `random`. Ids are a counter or a UUID, not a musical choice.
11. Raising `client_max_body_size` on the whole `location /projects` block. Only the video upload location grows.

## Scope And Decisions

### In scope
- `video.asset.v1` and `video.scoring.v1` schemas, a read-only MP4/MOV probe, one asset per project, scoring settings and hit points, map helpers, Picture tab, cursor leadership, fixture tests, the nginx upload location in Task 10, and the docs named in Task 9.

### Out of scope
- WebM, MXF, transport streams, and image sequences.
- ffmpeg, ffprobe, proxies, thumbnails, waveforms, and extracted audio files.
- Burned-in timecode OCR and MIDI SMPTE (import continues to reject SMPTE division).
- A new marker kind, note generation, or hit-point playback.
- Playing the composition through Tone at the same time as the video element. Leaders swap. They do not run together.
- Changing `audio.alignment.v1`, recovery Bind, adaptive scores, or the adaptive engine API.
- `composition.v5`. `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

`video.asset.v1` is the probe snapshot plus storage identity. Fields:

| Field | Rule |
|-------|------|
| `schema_version` | `video.asset.v1` |
| `asset_id` | `^vid_[0-9a-f]{8}$` |
| `project_id` | existing project |
| `container` | `mp4` or `mov` |
| `content_type` | `video/mp4` or `video/quicktime` |
| `byte_size` | size of the stored file |
| `sha256_prefix` | first 16 hex chars of SHA-256 |
| `duration_seconds` | finite, `> 0`, from `mvhd` timescale and duration |
| `frame_rate_numerator` / `frame_rate_denominator` | probed rational; see probe |
| `has_audio` | bool |
| `width` / `height` | positive ints from the video `tkhd` |
| `created_at` | ISO timestamp |

`video.scoring.v1` is the editable sync document. One row per project. Fields:

| Field | Rule |
|-------|------|
| `schema_version` | `video.scoring.v1` |
| `project_id` | |
| `asset_id` | the current asset, or null when no picture |
| `frame_rate_numerator` / `frame_rate_denominator` | null, or one of the closed rates below. Null until a probed rate snaps or the user picks one |
| `frame_rate_source` | null, `probed`, or `explicit` |
| `timecode_mode` | `non_drop` or `drop_frame` |
| `start_timecode` | `HH:MM:SS:FF`, default `00:00:00:00` |
| `video_origin_seconds` | finite, `>= 0`, default `0` |
| `musical_origin_tick` | int, `>= 0`, default `0`, `<= duration_ticks` |
| `hit_points` | at most 64 |
| `document_revision` | int. A GET with no row returns `0` and does not insert. The first successful PUT stores revision `1` |

Hit point: `id` (`^hit_[0-9a-f]{8}$`), `label` (1..80 chars), `video_seconds` (finite, `>= 0`), `musical_tick` (int, `>= 0`). Both numbers are stored. The map does not rewrite them.

Closed explicit frame rates:

| Rational | Nominal frames | Drop-frame |
|----------|----------------|------------|
| `24/1` | 24 | refused |
| `25/1` | 25 | refused |
| `30/1` | 30 | refused |
| `24000/1001` | 24 | refused |
| `30000/1001` | 30 | allowed |

`drop_frame` with any rate other than `30000/1001` is error `video_drop_frame_unsupported`.

**2. Map**

Pure functions in `backend/app/services/video_scoring_map.py`. No SQLite, FastAPI, or file I/O.

```text
score_seconds = tick_to_seconds(musical_origin_tick) + (video_seconds - video_origin_seconds)
video_seconds = video_origin_seconds + (score_seconds - tick_to_seconds(musical_origin_tick))
tick = seconds_to_tick(score_seconds)   # existing timeline
bar = bar_at_tick(clamped tick)
```

Clamp video seconds to `[0, duration_seconds]` and ticks to `[0, duration_ticks]` at the public boundary. Return a warning code `video_time_clamped` or `musical_time_clamped` when clamping happens. Do not clamp inside the pure inverse used by tests that pass in-range values.

Timecode at video time `t`:

```text
frames_from_zero = floor(t * numerator / denominator + 1e-9)
display_frames = frames_from_zero + frames(start_timecode)
```

Non-drop: `HH:MM:SS:FF` with nominal frames. Drop-frame (30 nominal, `30000/1001` only): skip displayed frames `:00` and `:01` at the start of each minute except every tenth minute. The formatter is total-frame based. It does not accumulate float seconds per frame.

Frontend twins live in `frontend/src/utils/videoScoringMap.js` and must match the backend vectors, including one composition that changes tempo.

**3. Probe**

`probe_iso_bmff(path) -> VideoAssetProbe` in `backend/app/services/video_container_probe.py`. Open read-only. Walk top-level boxes by their declared size and seek over `mdat` (and any other non-`moov` payload) without reading sample bytes. Parse `moov` wherever it sits, including after `mdat`. If the size chain never reaches a `moov`, or a box size runs past EOF, return `video_moov_not_found`. Do not read the media payload of a multi-gigabyte file.

Require brands `isom`, `mp41`, `mp42`, `iso2`, or `qt  ` in `ftyp`. `container` is `mov` when the major brand is `qt  `, otherwise `mp4`.

Read `mvhd` and `tkhd` version 0 (32-bit times) and version 1 (64-bit times). Ignore `elst`. From `mvhd`: duration seconds. The handler is `trak/mdia/hdlr`. From the first video trak (`hdlr` == `vide`): `tkhd` width/height (16.16 fixed, truncated toward zero), and frame rate from `stbl/stts` as `sample_count * mdhd_timescale / media_duration`, reduced to a rational. Snap to a closed rate when relative error is under `0.01`. If it does not snap, store the reduced rational on the asset and leave the scoring frame-rate fields null until the user picks a closed rate. Timecode and the map endpoint then return `video_frame_rate_required`. `has_audio` is true when any trak has `hdlr` `soun`. No sample decoder. No writes.

Refusal codes: `video_container_unsupported`, `video_moov_not_found`, `video_missing_video_track`, `video_duration_invalid`, `video_resolution_invalid`.

**4. Fixture**

`backend/tests/fixtures/video/iso_bmff.py` writes a minimal file: `ftyp`, `moov`, `mvhd`, one `vide` trak (`tkhd`, `mdhd`, `hdlr`, `stts`, `stsd`) and an optional `soun` trak. No `mdat` payload is required. Parameters: duration, timescale, sample count, width, height, audio flag. Tests cover:

- 24/1, 2.0 s, 320×180, no audio
- 30000/1001 snapped, with audio
- unsupported brand → typed error
- missing video trak → typed error
- `mdat` before `moov`, with the file hash unchanged after probe

The builder is the fixture. Do not commit `.mp4` bytes.

**5. Storage**

| Piece | Choice |
|-------|--------|
| Root | `VIDEO_ASSET_ROOT`, default `{PROJECT_DB_PATH parent}/video_assets`. `reject_storage_root` before use |
| Bytes | `{root}/{project_id}/{asset_id}.mp4` or `.mov`. Write only to a new path |
| Upload cap | `VIDEO_ASSET_MAX_UPLOAD_BYTES`, default 256 MiB, clamp 1 MiB–1 GiB |
| Count | one asset per project |
| Table `video_assets` | `asset_id`, `project_id` FK ON DELETE CASCADE, probe columns, `relpath`, `created_at`. UNIQUE `project_id` |
| Table `video_scoring` | `project_id` PK FK ON DELETE CASCADE, `body_json`, `document_revision`, timestamps |

Replace: probe the temp upload, write the new file, commit the row, then delete the previous file. If the probe or the commit fails, delete the temp and leave the previous asset and its file. Never open the stored file for write. If `frame_rate_source` is `explicit`, keep that rate across replace. If it is `probed` and the new probe snaps to a closed rate, update the scoring rate from the new snapshot. A probe that does not snap leaves an existing explicit rate untouched and clears a probed rate back to null.

`delete_project` calls `cleanup_project_video_assets` after the row delete, same warn-only style as recovery. SQLite cascade already drops the metadata.

**6. HTTP**

Mounted from `main.py` as a small router. Paths stay under `/projects`, which the Vite dev server already proxies. Collaboration uses `enforce_current` in `backend/app/routers/collaboration_guard.py`: `write_score` for POST, PUT, and DELETE; `read` for GET. Flag off returns before a membership lookup.

| Route | Behavior |
|-------|----------|
| `POST /projects/{project_id}/video-asset` | multipart field `file`. 201 `video.asset.v1` plus scoring summary |
| `GET /projects/{project_id}/video-asset` | 200 asset or 404 `video_asset_missing` |
| `GET /projects/{project_id}/video-asset/media` | `FileResponse` of the original bytes. `Content-Length` is `byte_size`. A `Range` request returns 206 for that range. The file bytes stay unchanged |
| `DELETE /projects/{project_id}/video-asset` | 204. Removes the file, sets `asset_id` to null, and clears `hit_points`. Frame rate, timecode, and sync origin remain |
| `GET /projects/{project_id}/video-scoring` | 200 `video.scoring.v1`. No row yet returns defaults with `document_revision` 0 and does not insert |
| `PUT /projects/{project_id}/video-scoring` | settings, origin, hit points. Requires `expected_document_revision`. Expected `0` inserts revision `1`. 409 `video_scoring_conflict` on mismatch. Does not read the media file |
| `GET /projects/{project_id}/video-scoring/map` | exactly one of `video_seconds`, `tick`, or `bar`. `bar` is 1-based and maps through `bar_start_tick`. Returns `{video_seconds, tick, bar, timecode}` where `video_seconds` is picture time and the tick's timeline seconds are score seconds |

413 on upload overflow. 422 on schema and probe errors, on zero or several map selectors, and on `video_frame_rate_required`. 507 is not used; the cap is the single-file limit. HTTP handlers do not follow a path that writes the asset. The production body limit for this POST is Task 10.

**7. Picture tab and cursor**

New tab `{ id: 'picture', label: 'Picture' }` in `ComposerWorkspace.jsx`. Panel `VideoScoringPanel.jsx`:

- `<video>` `src` is the media route. `controls` on. No canvas capture, no `MediaRecorder`.
- Timecode `<output>` from the explicit rate and the current video time.
- Settings form: closed frame-rate select, drop-frame checkbox (disabled unless 29.97), start timecode, video origin seconds, musical origin tick. Save calls PUT.
- Bar ruler from `compileTimeline`. Marker ticks from the loaded composition. Hit points from the scoring document. A hit point is off-map when its stored tick and the mapped tick differ by more than one tick. That label is visible and does not move the origin.
- Add hit point uses the current video time and the mapped tick. Delete removes that id. Cap 64.

`playbackSeconds` is score seconds from the map (`score_seconds_from_video`), which `secondsToPlaybackPosition` already converts with the composition timeline. It is not `video.currentTime`.

Store slice `pictureSyncStatus`: `idle` or `playing`. `pictureSeekRequest` is `{ id, seconds, reason }`, the same shape as `sourceSeekRequest`. Only `VideoScoringPanel` applies it to its `<video>`. Each `timeupdate` and `seeked` calls `applyPictureTime` with those score seconds and the bar. `PianoRollOverlayLayer` draws `PlaybackCursor` when `playbackStatus !== 'idle'` or `pictureSyncStatus === 'playing'`.

Leadership: starting picture playback uses the existing Tone pause path so `clearPositionTimer` runs and `playbackStatus` returns to `idle`, then sets `pictureSyncStatus` to `playing`. The `startPositionTimer` interval returns before `setPlaybackPosition` while that status is `playing`. Starting Tone playback sets the picture leader idle, pauses the video element, and publishes `pictureSeekRequest` with `currentTime` equal to the mapped video seconds. `sourcePlayheadTick` stays the recovery clock. Browser APIs added under `frontend/src/store` or `frontend/src/utils` go through `globalThis`.

The panel does not autosave picture state into `composition_json`.

**8. Logging**

`LOG_LEVEL` controls verbosity. INFO: asset id, project id, container, byte size, duration seconds, snapped frame rate, width, height, `has_audio`, probe error code, scoring revision, leader changes (`picture` or `tone`). DEBUG: map inputs and outputs (seconds, tick, bar, timecode). WARNING: cleanup failure, clamp warning, conflict. Never log bytes, absolute paths, full filenames (basename only), hit-point label lists in bulk, or composition events.

## Tasks

### Phase 1: Contracts and pure time
- [x] Task 1: Add `video.asset.v1` and `video.scoring.v1` schemas
- [x] Task 2: Add the sync map and timecode formatter

### Phase 2: Fixture probe and storage
- [x] Task 3: Add the ISO-BMFF fixture builder and read-only probe
- [x] Task 4: Add settings, tables, store, and project-delete cleanup

### Phase 3: HTTP
- [x] Task 5: Add video asset and scoring routes
- [x] Task 10: Limit the nginx body size on the video upload location

### Phase 4: Studio clock
- [x] Task 6: Add frontend map twins and store leadership
- [x] Task 7: Add the Picture tab
- [x] Task 8: Drive the composition cursor from the picture leader

### Phase 5: Docs
- [x] Task 9: Document the picture contract

## Commit Plan
- **Commit 1** (after tasks 1-2): "feat: add video scoring time map"
- **Commit 2** (after tasks 3-4): "feat: store an immutable project video asset"
- **Commit 3** (after tasks 5 and 10): "feat: add video scoring HTTP routes"
- **Commit 4** (after tasks 6-8): "feat: sync the composition cursor to picture timecode"
- **Commit 5** (after task 9): "docs: describe the video scoring timeline"

## Tasks (detail)

### Task 1: Add `video.asset.v1` and `video.scoring.v1` schemas

Create `backend/app/video_scoring_schemas.py` with Pydantic models for both documents, the hit-point model, the closed frame-rate set, and a typed `VideoScoringError` (`code`, `message`) with the codes listed in the decisions. `extra` keys are rejected. `frame_rate_numerator`, `frame_rate_denominator`, and `frame_rate_source` are null together, or a closed rate with source `probed` or `explicit`. Frame-rate validation refuses drop-frame unless the rate is `30000/1001`. A null rate with `timecode_mode` `drop_frame` is `video_drop_frame_unsupported`. Id patterns are `vid_[0-9a-f]{8}` and `hit_[0-9a-f]{8}`. At most 64 hit points. Labels are 1..80 characters. `document_revision` 0 is valid on the GET-default model. Persisted rows use revision `>= 1`.

`backend/tests/test_video_scoring_schemas.py` accepts a golden pair, accepts a scoring document with all three frame-rate fields null, and rejects an unknown frame rate, drop-frame at 24 fps, a source without a rate, a nested hit point, and an extra key.

LOGGING: DEBUG when a model rejects a payload, including the error code and not the file bytes. No INFO on success beyond the existing app logs. Levels follow `LOG_LEVEL`.

Files: `backend/app/video_scoring_schemas.py`, `backend/tests/test_video_scoring_schemas.py`.

### Task 2: Add the sync map and timecode formatter

Create `backend/app/services/video_scoring_map.py`. Functions: `score_seconds_from_video`, `video_seconds_from_score`, `map_video_to_music`, `map_tick_to_video`, `map_bar_to_video`, `format_timecode`, `parse_timecode`. Mapping calls `CompiledTimeline` from `composition_timeline.py`. Timecode uses integer frames as specified. Drop-frame follows the minute rule in the decisions.

`backend/tests/test_video_scoring_map.py` uses a composition with a tempo change (root tempo, then one `tempo_changes` entry). Assert the tick at a video time after the change is not the constant-BPM tick. Round-trip an in-range video time to tick and back within one tick. Assert clamp warning codes. Assert `00:00:00:00` at t=0 with start `00:00:00:00`. Assert a known drop-frame value at the first dropped minute for `30000/1001`. Assert `video_drop_frame_unsupported` is not raised by the formatter when the mode is non-drop.

LOGGING: DEBUG each public map call with video seconds, tick, bar, and timecode. No composition events.

Depends on Task 1.

### Task 3: Add the ISO-BMFF fixture builder and read-only probe

Create `backend/tests/fixtures/video/iso_bmff.py` and `backend/app/services/video_container_probe.py`. The probe implements the box rules in the decisions. It opens with mode `rb` only. It seeks over `mdat` by box size and parses version 0 and version 1 `mvhd`/`tkhd`. The builder can emit either version and can place a sized `mdat` before `moov`.

`backend/tests/test_video_container_probe.py` builds the 24 fps silent fixture and the 29.97 fixture with an audio trak, parses them from a temp file, and checks duration, snapped rate, width, height, and `has_audio`. Hash the file before and after probe and require equality. Cases: bad brand, video trak absent, no `moov` in the size chain, and `mdat` before `moov` still yields the same duration and rate. One case uses version-1 headers.

LOGGING: INFO one line per probe with container, byte size, duration, rate, resolution, and `has_audio` or the error code. Never log the path or the byte string.

Depends on Task 1.

### Task 4: Add settings, tables, store, and project-delete cleanup

Create `backend/app/video_scoring_settings.py` (`VIDEO_ASSET_ROOT`, `VIDEO_ASSET_MAX_UPLOAD_BYTES`). `load` applies the clamps and calls `reject_storage_root`.

Add `backend/app/db/alembic/versions/20260930_0019_video_scoring.py`, `down_revision` `20260928_0018`, tables as specified.

Create `backend/app/services/video_scoring_store.py`: write bytes to a new UUID path, sha256 prefix of 16 hex chars, insert asset, read/write scoring JSON with revision CAS, `cleanup_project_video_assets`. Replace writes the new file, commits the new row, then deletes the previous path. A failed probe deletes only the temp. An `explicit` frame rate survives replace. A `probed` rate updates only when the new probe snaps. Wire cleanup from `delete_project` in `backend/app/services/project_store.py` next to the recovery cleanup, lazy import, warn on failure, no raise.

`backend/tests/test_video_scoring_store.py` follows `test_audio_recovery_store.py`: `tmp_path`, env roots, Alembic upgrade. Assert one asset, replace keeps a new id and removes the old path only after the new row commits, an explicit rate survives replace, scoring PUT with a stale revision returns the conflict code, the stored file hash is unchanged after PUT, and `delete_project` removes the directory. A root equal to `DATASET_ROOT` is rejected. GET with no scoring row is revision 0 and leaves the table empty until PUT.

LOGGING: INFO asset id, project id, byte size, revision. WARNING cleanup failures with the project id and exception class. Never log absolute paths or bytes.

Depends on Tasks 1 and 3.

### Task 5: Add video asset and scoring routes

Create `backend/app/routers/video_scoring.py` and register it in `backend/app/main.py`. Routes match the decision table. POST, PUT, and DELETE call `enforce_current(..., "write_score")`. GET calls `enforce_current(..., "read")`. Multipart uses `read_upload_bounded`. Probe failures and schema failures become 422. Missing project becomes the existing project 404 shape. GET scoring with no row returns revision 0. PUT with expected `0` inserts. The map endpoint requires exactly one of `video_seconds`, `tick`, or `bar`, loads the composition via the existing project store, compiles the timeline, and calls the pure map. `bar` uses `bar_start_tick`. It does not save the composition. A null explicit frame rate returns `video_frame_rate_required`. Media uses `FileResponse`.

`backend/tests/test_video_scoring_routes.py` uploads both fixtures through the TestClient, reads media back, compares SHA-256 to the upload, requests a `Range` and expects 206 with those bytes, PUTs a new frame rate and one hit point, GETs the map for a tick past the tempo change, and checks the asset hash again. A second selector on the map is 422. DELETE then GET asset is 404, and GET scoring still has the explicit frame rate with empty hit points. A second upload replaces the asset id. GET scoring before any upload is revision 0.

LOGGING: INFO method, path template, project id, status, asset id. WARNING 409 and 422 with the error code. No body bytes.

Depends on Tasks 2 and 4.

### Task 6: Add frontend map twins and store leadership

Add `frontend/src/utils/videoScoringMap.js` and `videoScoringMap.test.js`. Vectors match Task 2, including drop-frame and the tempo-change composition expressed with the JS timeline helper.

Extend `frontend/src/store/musicStore.js` with `pictureSyncStatus`, `pictureSeekRequest`, `pictureScoring` (the last GET), and actions `setPictureLeader`, `applyPictureTime`, `requestPictureSeek`, `clearPicture`. `applyPictureTime` writes `playbackSeconds` as score seconds from `score_seconds_from_video` and writes `playbackBar`. `requestPictureSeek` bumps a module sequence the way `sourceSeekRequest` does. Any browser API in this slice uses `globalThis`. Document that this slice is session-only and is not part of project autosave.

`frontend/src/api/videoScoringApi.js` wraps the routes. Keep it out of any barrel that pulls the studio into unrelated tests.

LOGGING: DEBUG in the map helper when `localStorage` debug is not required; the helper stays pure and silent. The store action does not log. API failures return the error code to the panel, which logs a single `console.warn` with the code and not the response body.

Depends on Task 2. Can land after Task 5 for the API, but the pure file does not need HTTP.

### Task 7: Add the Picture tab

Add `frontend/src/components/VideoScoringPanel.jsx` and a `picture` entry on `TABS` in `ComposerWorkspace.jsx`. Show empty state when GET asset is 404. After upload, show duration, frame rate, audio presence, and resolution from `video.asset.v1`. Form and hit-point editor match the decisions. Bar ruler renders composition markers and hit points. A hit point is labeled off-map when its stored tick and the mapped tick differ by more than one tick. Save and add/delete call PUT and require the revision from the last GET.

Do not start Tone. Do not write the composition.

LOGGING: `console.info` on upload success with asset id, duration, and resolution. `console.warn` on API error codes. No file contents.

Depends on Tasks 5 and 6.

### Task 8: Drive the composition cursor from the picture leader

In `VideoScoringPanel.jsx`, on video `play`, call the existing pause path so `clearPositionTimer` runs and `playbackStatus` becomes `idle`, then set `pictureSyncStatus` to `playing`. On `timeupdate` and `seeked`, call `applyPictureTime` with score seconds and the bar. On `pause`/`ended`, set `pictureSyncStatus` to `idle`. The panel applies `pictureSeekRequest` to its element only, the way `AudioRecoveryPanel.jsx` applies `sourceSeekRequest`.

In `startPositionTimer` inside `PlaybackControls.jsx`, the interval callback returns before `setPlaybackPosition` while `pictureSyncStatus === 'playing'`. When the user starts Tone playback, set the picture leader idle and call `requestPictureSeek` with the mapped video seconds.

In `PianoRollOverlayLayer.jsx`, show `PlaybackCursor` during picture playback as well as Tone playback. The cursor still derives its tick from `playbackSeconds` via `secondsToPlaybackPosition`. Leave `sourcePlayheadTick` unchanged.

`frontend/src/utils/videoScoringClock.test.js` covers the leader switch: a picture time maps to score seconds, and a Tone seek request carries the inverse video time. This file is pure. Do not add a Playwright spec that needs a decoded H.264 sample. Use `globalThis` for any browser API added in `frontend/src/utils`.

LOGGING: `console.info` when the leader changes, with the value `picture` or `tone`. No currentTime spam at INFO. DEBUG is not available in the browser helper; do not log every `timeupdate`.

Depends on Tasks 6 and 7.

### Task 9: Document the picture contract

Add `docs/video-scoring.md`: documents, probe limits, closed frame rates, drop-frame, sync origin, hit points versus markers, one-leader rule, immutability, env vars, and error codes. State that fixture videos are generated in tests and are not a playable sample.

Update the Documentation table and the key-entry rows in `AGENTS.md`. Add the router, schemas, settings, store, and probe to the folder sketch and the feature table in `.ai-factory/ARCHITECTURE.md`. Add the `ai_agents/` import ban for `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, and `video_scoring_map`. Note the session slice next to the other ephemeral slices. Add a short testing pointer in `docs/testing.md` for the fixture builder and the route test. Add `VIDEO_ASSET_ROOT` and `VIDEO_ASSET_MAX_UPLOAD_BYTES` to `.env.example` with the defaults and clamps from Task 4.

Do not edit `ROADMAP.md`. Do not document ffmpeg.

LOGGING: docs only. No new logs.

Depends on Tasks 5 and 8.

### Task 10: Limit the nginx body size on the video upload location

In `frontend/nginx.conf`, add a regex location for `^/projects/[^/]+/video-asset$` only. Set `client_max_body_size` to the same default cap as `VIDEO_ASSET_MAX_UPLOAD_BYTES` (256m). Proxy that location to the backend the same way `location /projects` does. Leave `location /projects` itself unchanged so composition JSON saves stay small. `/video-asset/media` keeps using `location /projects`.

Do not add a Vite proxy prefix. `/projects` is already proxied in `frontend/vite.config.js`.

LOGGING: nginx config only. No new application logs.

Depends on Task 5.
