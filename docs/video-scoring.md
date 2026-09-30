# Video scoring timeline

A project holds one immutable picture and a separate sync document. `composition.v2` note events stay untouched by upload, probe, settings, and playback. There is no `composition.v5`.

`audio.alignment.v1` remains the recovery-source map. Picture time is not `sourcePlayheadTick`.

## Documents

`video.asset.v1` is the probe snapshot plus storage identity: container (`mp4` or `mov`), content type, byte size, SHA-256 prefix, duration, probed frame-rate rational, audio presence, width, height, and timestamps. Asset ids match `vid_` plus 8 hex characters.

`video.scoring.v1` is the editable sync document, one row per project. Fields: asset id (null when no picture), explicit frame rate, `frame_rate_source` (`probed` or `explicit`, or null), `timecode_mode`, `start_timecode`, `video_origin_seconds`, `musical_origin_tick`, at most 64 hit points, and `document_revision`.

A GET with no scoring row returns revision `0` and does not insert. The first successful PUT or the first upload stores revision `1`. PUT sends `expected_document_revision`. A mismatch is `409` `video_scoring_conflict`.

A hit point is `{id, label, video_seconds, musical_tick}`. The id matches `hit_` plus 8 hex characters. Both times are stored. The map does not rewrite them. A hit point is not a `CompositionV2Marker` and not a note. Markers stay `rehearsal` or `text` on the composition. The Picture tab projects those markers through the map.

## Probe

`probe_iso_bmff` opens the file read-only, walks top-level boxes by declared size, and seeks over `mdat` and other non-`moov` payloads. A trailing `moov` still parses. It does not transcode, demux to a new file, or rewrite the upload. Brands `isom`, `mp41`, `mp42`, `iso2`, and `qt  ` are accepted. `container` is `mov` when the major brand is `qt  `, otherwise `mp4`.

Duration comes from `mvhd` timescale and duration (version 0 and version 1). Width and height come from the first `vide` track `tkhd` (16.16 fixed, truncated toward zero). Frame rate is `sample_count * mdhd_timescale / media_duration`, reduced. It snaps to a closed rate when relative error is under `0.01`. If it does not snap, the asset keeps the reduced rational and the scoring frame-rate fields stay null until the composer picks a closed rate. `has_audio` is true when any track handler is `soun`. Edit lists are ignored. There is no sample decoder.

Refusal codes: `video_container_unsupported`, `video_moov_not_found`, `video_missing_video_track`, `video_duration_invalid`, `video_resolution_invalid`.

Fixture videos are built in process by `backend/tests/fixtures/video/iso_bmff.py`. They are not a playable sample and are not checked in as `.mp4` bytes.

## Frame rate and timecode

The explicit frame rate is the rational the composer sets for timecode. The probed frame rate is the import snapshot. Start timecode is the `HH:MM:SS:FF` label of video time 0. Default is `00:00:00:00`.

Closed rates:

| Rational | Nominal frames | Drop-frame |
|----------|----------------|------------|
| `24/1` | 24 | refused |
| `25/1` | 25 | refused |
| `30/1` | 30 | refused |
| `24000/1001` | 24 | refused |
| `30000/1001` | 30 | allowed |

`drop_frame` with any other rate, including a null rate, is `video_drop_frame_unsupported`. The formatter is total-frame based (`floor(t * numerator / denominator + 1e-9)` plus frames of the start timecode). Drop-frame skips displayed frames `:00` and `:01` at the start of each minute except every tenth minute. At `30000/1001`, frame count 1800 displays `00:01:00:02`. Timecode and the map endpoint return `video_frame_rate_required` until a closed rate is set.

Replace keeps an explicit rate. If the source is `probed` and the new probe snaps, the scoring rate updates from the new snapshot. A probe that does not snap leaves an explicit rate and clears a probed rate to null (drop-frame is cleared with it so the document stays valid).

## Sync origin and the map

The sync origin is the pair `(video_origin_seconds, musical_origin_tick)`. Score seconds are `tick_to_seconds(musical_origin_tick) + (video_seconds - video_origin_seconds)`. The inverse solves for video seconds. Musical ticks and bars then use `CompiledTimeline.seconds_to_tick` and `bar_at_tick`, including tempo changes. Hit points do not warp time. A hit point is off-map when its stored tick and the mapped tick differ by more than one tick. The label stays visible. The origin does not move.

Public map helpers clamp video seconds to the asset duration and ticks to the composition, and return `video_time_clamped` or `musical_time_clamped`. The pure inverse used for in-range values does not clamp.

`GET /projects/{project_id}/video-scoring/map` takes exactly one of `video_seconds`, `tick`, or `bar`. Two selectors are `video_map_selector_invalid`.

## Storage

`VIDEO_ASSET_ROOT` defaults to `{PROJECT_DB_PATH parent}/video_assets`. The loader refuses `DATASET_ROOT` and `PROJECT_DB_PATH`. Bytes live at `{root}/{project_id}/{asset_id}.mp4` or `.mov`. Upload writes a temp file, probes it, writes the new path, commits the row, then deletes the previous file. A failed probe or commit deletes the temp and leaves the previous asset. The stored file is never opened for write.

`VIDEO_ASSET_MAX_UPLOAD_BYTES` defaults to 256 MiB and clamps to 1 MiB–1 GiB. Overflow is HTTP 413 `video_upload_too_large`. Nginx sets `client_max_body_size 256m` only on `^/projects/[^/]+/video-asset$`. `location /projects` stays small, including `/video-asset/media`.

Deleting the asset removes the file, sets `asset_id` to null, and clears hit points. Frame rate, timecode, and sync origin remain. Deleting the project removes the file directory after the row delete (warn-only if the filesystem cleanup fails). SQLite cascade drops the metadata.

Changing frame rate, start timecode, sync origin, or hit points does not change the asset SHA-256.

## HTTP

Mounted under `/projects` (the Vite dev server already proxies that prefix). Collaboration uses `write_score` for POST, PUT, and DELETE, and `read` for GET. With the flag off, the guard returns before a membership lookup.

| Route | Behavior |
|-------|----------|
| `POST /projects/{id}/video-asset` | multipart field `file`. 201 `{asset, scoring}` |
| `GET /projects/{id}/video-asset` | 200 asset, or 404 `video_asset_missing` |
| `GET /projects/{id}/video-asset/media` | original bytes. `Content-Length` is `byte_size`. `Range` returns 206 |
| `DELETE /projects/{id}/video-asset` | 204 |
| `GET /projects/{id}/video-scoring` | 200 document, revision 0 when no row |
| `PUT /projects/{id}/video-scoring` | CAS update |
| `GET /projects/{id}/video-scoring/map` | one selector |

Other codes: `video_scoring_invalid`, `video_frame_rate_unsupported`, `video_frame_rate_incomplete`, `video_timecode_invalid`, `video_composition_missing`, `video_musical_origin_outside`.

## Picture clock

The Picture tab shows the video element, a timecode readout, a bar ruler, composition markers, and hit points. One leader may write `playbackSeconds`, which stores score seconds. Picture play pauses Tone and writes the cursor from `timeupdate`. Tone play pauses the video and seeks it to the mapped time. They do not run together. The piano-roll playback cursor stays visible while `pictureSyncStatus` is `playing`, including when Tone status is idle.

`pictureScoring`, `pictureSyncStatus`, and `pictureSeekRequest` are a session slice. They are not project autosave.

`ai_agents/` must not import `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, or `video_scoring_map`.
