# Film score adaptation

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. Picture documents stay `video.asset.v1` and `video.scoring.v1`. The full generation path stays `film.score.plan.v1`. This page is the local repair.

A composer who already has a score declares how the picture timing changed, inspects `film.score.adaptation.v1`, and only then commits. Preview does not run when the Agents tab opens, when the Picture tab saves cues, or when a picture file is replaced. The Picture tab stays the cue editor. The previous timeline is a request field. It is not stored in SQLite.

## Documents

`film.score.adaptation.v1` is inspectable and non-playable. It carries one strategy per edit, hit changes, preserved flags, event counts, and warning codes. It has no note events.

`FilmTimelineSnapshot` on the preview request is the previous picture: duration, frame rate, origin, and `FilmCueSnapshot` rows. Cue labels and instructions are not copied.

An edit is `delete_span` (a closed picture interval), `insert_span` (seconds inserted at a time), or `move_hit` (one cue moves, duration stays). The service checks that those edits explain the stored asset duration and cue times within one frame, using `floor(seconds * frame_rate + 1e-9)`.

Preview is `POST /projects/{id}/film-score/adapt/preview`. It returns the proposal and a candidate with `committed: false`. Commit is `POST /projects/{id}/film-score/adapt/commit`. It recomputes the candidate and writes it as revision operation `film-score-adapt-apply` only when the fingerprint matches. Commit does not rewrite cue rows, video bytes, `video_origin_seconds`, or `musical_origin_tick`. The response includes `film_origin_unchanged`.

## Strategy order

The compiler chooses one strategy. The user does not. Cheapest first:

1. `unchanged`
2. `local_tempo`
3. `transition_shorten` or `transition_extend`
4. `phrase_contract` or `phrase_extend`
5. `bar_remove` or `bar_insert`
6. `silence_insert`
7. `targeted_regenerate`

`local_tempo` is chosen from the raw second delta before any half-bar is removed. It is illegal for `move_hit`. A legal bpm stays within 12 of the section bpm. The change tick is the section start. A later section restores the previous bpm at its start.

A span that covers every bar, or that needs more than 32 bars, is `film_adapt_span_too_large`. That refusal does not call the film-score agent and does not invent a replacement score.

## Identity

An unchanged event keeps `id`, `pitch`, `start_tick`, `duration_ticks`, and `velocity`. A shifted event keeps those fields except `start_tick`, which moves by one tick delta after the edit point. A note that crosses a removed span is truncated in place and is not one of the four counts; the warning is `note_truncated`.

`targeted_regenerate` moves the nearest melody attack in one bar onto the cue tick and keeps its pitch and velocity. Motif-linked event ids are not replaced. If that bar has no movable melody attack, `film_score_adapt.py` inserts one tonic note (`{key}4`, velocity 96, one beat) and sets `melody_protected`. It does not call `apply_film_score_accents`.

## Vectors

At `24/1`, `4/4`, 120 bpm, and 480 ticks per quarter:

- Deleting video seconds `[16, 22)` from a 32-bar score uses `phrase_contract` only, removes bars 9–11, and shifts later notes by `-5760` ticks. Earlier notes stay identical. `targeted_regenerate` is absent.
- Deleting `[16, 17)` uses `local_tempo` at 128 bpm on the section that starts at tick `15360` and restores 120 at tick `30720`. Every note stays identical. The later cue moves from 36 seconds to 35 on a 63-second picture. Section math is `8 * 4 * 60 / 15 = 128`.
- Moving a cue from 6 seconds to 6.25 seconds uses `targeted_regenerate` on bar 4. `local_tempo` is not considered.

`ai_agents/` does not import `film_score_adapt`. `film_score_adapt.py` does not import `film_score_accents`.

## Frontend

`FilmScoreAdaptPanel` sits under the film-score panel on the Agents tab. Remember reads `getVideoAsset` and `pictureScoring` and drops label and instruction. The summary lists each edit id, strategy, bar range, bar delta, the four event counts, each cue id with change and status, and warning codes. Commit stays disabled until `candidate_fingerprint` is set. `filmAdaptBaseline` and `filmAdaptPreview` are not project autosave.

## See also

- [Film scoring](./film-scoring.md)
- [Video scoring](./video-scoring.md)
