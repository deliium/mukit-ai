# Implementation Plan: V5 Film Scoring Agent

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-01

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Agents tab review of `film.score.plan.v1` (section bars, tempo strategy, hit status, critic recommendation) and an explicit Commit. The Picture tab stays the cue editor. Preview does not run when the tab opens
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-01 (`/aif-improve`). Commit uses `DurableCommitRequest` and a spine `artifact_role_map`, including an empty motif plan when no motif is requested. The candidate tick 0 is the music-window start; accent checks and the commit origin use that pair. `composition.plan.v1` section types stay inside `SUPPORTED_SECTION_TYPES`, and tempo changes are applied after symbolic generation. Hit alignment uses the same picture-frame floor as `verify_cue_landings`. Vector B is bpm `128` at video `17.875`. Vector C is the one-frame miss at video `16.05`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next film-scoring milestone and does not edit `ROADMAP.md`
- Scope: an explicit preview that returns an inspectable musical plan aligned to stored spotting cues, then an explicit commit of an editable `composition.v2`. Existing V4 agents supply form labels, harmony, motifs, arrangement, orchestration notes, and critique. Bar lengths and tempo stay on a deterministic compiler

## Roadmap Linkage
Milestone: "V5 Film scoring agent"
Rationale: A multi-minute scene needs a structured musical plan on authored spotting cues and an editable `composition.v2`. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A composer can ask for a film score for the current picture, inspect the plan (cue boundaries, tempo strategy, section lengths, harmonic arc, motif appearances, hit alignment, dialogue density), and only then commit an editable canonical Composition.

Ship:

1. A non-playable `film.score.plan.v1` compiled from the stored video timeline, spotting cues, creative brief, Musical Universe motif ids, Composer Profile, instrumentation, and target duration.
2. Tempo adaptation only when a high or critical sync cue cannot land on the beat grid inside its frame tolerance, and only at a section boundary or as the opening tempo. A new tempo is not written at every hit.
3. A fixed call order of existing agents: `creative_director`, `structure_form`, `harmony`, `melody_motif` when motif ids are present, `arrangement`, `orchestration`, `critic`. No new agent id and no single prompt that emits the score.
4. A session preview that returns the plan, the candidate, and the artifact role map. Commit writes that candidate through the existing `multi-agent-apply` CAS and sets the scoring sync origin so tick 0 is the music-window start. Cue rows and video bytes stay unchanged.
5. Mocked-agent tests that record the call order and prove a stub form plan cannot replace the compiled bar counts.

Acceptance: a 180-second scene at `24/1`, `4/4`, and 120 bpm, with three critical hits that already sit on barlines, returns a plan whose sections cover that window, whose `tempo_changes` are empty, and whose dialogue cue is a sparse density region. A second scene moves one integer tempo at a section boundary so a critical hit lands on a beat, and leaves a hit that cannot be met inside the tempo band as `unsatisfiable` without adding a tempo event. Commit produces a `composition.v2` the user can edit. Authoring the plan does not require a network when agents are the in-process fakes.

```text
video.scoring.v1 hit_points + asset duration + closed rate
        ↓
cue snapshot (id, kind, importance, video_seconds, tolerance_frames)
        ↓
film.score.plan.v1   (bars, tempo strategy, density, hit status)
        ↓
composition.plan.v1 → symbolic note body
        ↓
existing agents (brief, form, harmony, motif, arrangement, orchestration, critic)
        ↓
accent repair on high/critical aligned hits
        ↓
preview (committed false) → explicit commit → composition.v2
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. The picture documents stay `video.asset.v1` and `video.scoring.v1`. There is no `video.scoring.v2`. **Film score plan** is `film.score.plan.v1`. It is inspectable and non-playable. **Cue** remains one `hit_points` item from the spotting plan. **Musical Universe motif** is a motif id already stored on the source `composition.v2` `motifs[]`. There is no universe document and no cross-project motif fetch. **Composer Profile** is an optional `composer.profile.v1` id. Soft fragment only. **Instrumentation** is the request instrument list resolved through the arrangement catalog. **Tempo strategy** is the opening bpm plus `tempo_changes` whose ticks are section starts. **Sync cue** is importance `high` or `critical` and kind `hit_point`, `reveal`, `cut`, `action`, or `emotional_cue`. **Soft cue** is importance `low` or `medium`, or any cue that is not a sync cue. **Dialogue density** is a sparse bar span taken from cue kind. **Accent** is one note attack placed so an aligned sync cue can land. **Preview** returns the plan and a candidate. **Commit** is the only write of `tracks[].events[]`. It also stores the new sync origin on `video.scoring.v1`. It does not rewrite cue rows.

Predecessor: `.ai-factory/plans/v5-film-scoring-spotting-hit-points.md`. Do not reopen cue fields, timecode derivation, the landing definition, suggestion preview, or the Picture ruler. Do not add a `MarkerKind`. Do not write cue rows from this agent.

## Approach Evaluation (locked)

### Part A — What the film-scoring agent is

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A tenth agent id with one prompt that returns notes and tempo** | One call | The user asked for the existing specialized agents. A giant prompt invents a second score path. `ai_agents/` cannot import the video modules | **Reject** |
| **B. Fold picture spotting into `POST /ai/agents/workflows/preview` so every spine run scores the picture** | Reuses the spine | Spine preview is general composition. Opening the Agents tab or a normal preview would score without a click | **Reject** |
| **C. A service orchestrator that compiles `film.score.plan.v1`, then calls the registered agents in a fixed order, then returns the plan before any commit** | Agents stay specialized. The plan is inspectable. Video I/O stays in the router | The orchestrator must ignore a stub that returns a 4-bar form | **Accepted** |

### Part B — Where tempo changes go

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One `tempo_changes` entry at every hit `musical_tick`** | Hits land | The user forbade a new tempo at every hit. The grid stops being music. The spotting plan already rejected hit-point time warps | **Reject** |
| **B. Never change tempo; only report misses** | Coherent pulse | A critical cue that is a fraction of a beat off can never be met, so the requirement to support tempo adaptation fails | **Reject** |
| **C. Keep one bpm per section. Change bpm only when a sync cue in that section misses the beat grid inside its frame tolerance. The new bpm is an integer inside `[tempo_min, tempo_max]` and within 12 of the previous bpm. The change tick is the section start. Opening mismatch changes the root `tempo` and writes no `tempo_changes` row. A miss that no legal bpm can fix is `unsatisfiable` and keeps the previous bpm** | Coherence stays. Important cues can still lock | Some hits stay `unsatisfiable` inside a tight band | **Accepted** |

### Part C — Where the plan and the composition live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Store the plan on `video.scoring.v1` or inside `composition.v2`** | One GET | Cue document and playable events pick up a second schema. `extra=forbid` | **Reject** |
| **B. Commit the composition as soon as the agents finish** | Fewer clicks | The user asked to inspect `FilmScorePlan` before committing | **Reject** |
| **C. Preview returns `film.score.plan.v1` plus a candidate and `committed: false`. Commit sends the candidate fingerprint through existing `RevisionOperationType.MULTI_AGENT_APPLY`** | Same CAS as the spine. No Alembic revision. No new operation enum | The client must hold the preview until Commit | **Accepted** |

### Part D — How note events are produced

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Let the orchestrator invent pitches from the plan** | Full score | Violates the rule that notes come from `tracks[].events[]` via trusted realize paths | **Reject** |
| **B. Accents only** | Hits land | A multi-minute scene is almost empty | **Reject** |
| **C. Build a `composition.plan.v1` from the compiled sections and call the existing symbolic composer, then run harmony, motif, and arrangement, then repair accents on aligned sync cues** | Existing note engine. Agents still do their jobs. Hits survive redistribution | Fake mode is the CI path; a missing Music Transformer model uses the same fake engine the hybrid pipeline already uses | **Accepted** |

### Part E — How the caller passes picture data

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `ai_agents/` imports `video_scoring_*`** | Direct | Architecture forbids those imports | **Reject** |
| **B. The tempo compiler reads SQLite itself** | One module | Mixes the pure grid with persistence | **Reject** |
| **C. The router loads the asset and scoring document and passes a cue snapshot plus rate and duration into `services/film_score_workflow.py`. The snapshot type lives in `film_score_schemas.py` and does not import video modules. `ai_agents/` imports neither** | The compiler stays pure. The import ban holds | The router is the only video reader on this path | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Agents | Nine ids in `KNOWN_AGENT_IDS`. Spine order in `backend/app/ai_agents/spine.py`. Registry `get_agent` / `register_agent`. Fakes in `backend/app/ai_agents/fake_agents.py`. `structure_form` stub returns one 4-bar `agent.form_plan.v1` | Call these ids. Do not add a tenth |
| Agent context | `AgentWorkflowContext` slots for brief, structure, harmony, melody, arrangement, orchestration, critique. `AgentRunRequest.selection` already carries bar ranges | Pass selection ranges. Do not add a film slot on the context |
| Non-playable plans | `composition.plan.v1`, `agent.form_plan.v1`, `agent.harmony_plan.v1`, `agent.motif_plan.v1`. Forbidden playable keys in `backend/app/ai_agents/artifact_schemas.py` | Film plan uses the same forbid list |
| Notes | `generate_symbolic_composition` and `fake:symbolic-tiny`. Progressive realize services `reharmonize_candidate`, `motif_apply`, `arrangement_candidate` | Body plus agent refine |
| Tempo | `CompositionV2.tempo` (int 40..240) and `tempo_changes[{tick>0, bpm}]`. `CompiledTimeline` in `backend/app/services/composition_timeline.py`. Bar length via `bar_duration_ticks` | The only tempo integral |
| Cues | `HitPointV1` kind, timecode, tolerance, importance, instruction. Verify route `POST /projects/{id}/video-scoring/spotting/verify`. `verify_cue_landings` | Read-only check after accents. Do not reimplement landing |
| Profile | `resolve_profile_merge` in `backend/app/services/composer_profile_merge.py`. Hard constraints win | Optional `profile_id` |
| Motifs | `CompositionV2MotifDefinition.id` on the score | Musical Universe ids |
| Instruments | Arrangement catalog `instrument_id` resolve | Request `instruments` |
| History | `DurableCommitRequest` plus `RevisionOperationType.MULTI_AGENT_APPLY`. `validate_artifact_role_map` requires `brief`, `harmony_plan`, `motif_plan`, `arrangement_plan`, and `critique` | Commit path. Empty motif plan still fills `motif_plan` |
| Tests | `clear_registry_for_tests`, `register_agent`, `LLM_FAKE_MODE` | Mock pattern |
| Constraints | `GenerationConstraints` hard key, meter, `duration_bars` 1..512, tempo band, instruments | Preview builds these and checks the plan against them |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Film plan document | No `film.score.plan.v1` |
| Tempo compiler | Nothing groups hits into sections or refuses a per-hit tempo |
| Agent sequence for a picture | Spine ignores cues. The form stub ignores the scene length |
| Preview and commit routes | No film-score HTTP |
| Inspect UI | Agents tab has autonomous and spine panels, not a film-score plan |
| Mocked-agent proof | No test records a film-score call order |

### Coupling risks to avoid

1. Importing `video_scoring_schemas`, `video_scoring_settings`, `video_scoring_store`, `video_container_probe`, `video_scoring_map`, `video_spotting`, or `llm_video_spotting` from `ai_agents/`.
2. Adding agent id `film_scoring` or any id outside `KNOWN_AGENT_IDS`.
3. Writing `tempo_changes` at a cue tick that is not a section start.
4. Letting `dialogue` or `music_start` / `music_stop` start or stop Tone, the video element, or the adaptive clock.
5. Letting importance change velocity, mixer state, or hard `GenerationConstraints`.
6. Logging the brief, cue instructions, cue labels in bulk, the profile fragment, video bytes, or note events.
7. Running the preview from upload, GET scoring, picture playback, or Agents-tab mount.
8. Persisting the plan inside `composition_json` or onto cue rows. The commit may update `video_origin_seconds` and `musical_origin_tick` only.
9. Calling `/embeddings/related-motifs` or reading another project's motifs.
10. Replacing a score that already has note events unless `replace_existing` is true.
11. Editing `ROADMAP.md`, adding Alembic, or adding `composition.v5`.

## Scope And Decisions

### In scope
- `film.score.plan.v1` and the preview request DTO.
- Deterministic section, tempo, density, and hit-status compiler.
- Symbolic body from that plan, then the existing agents, then accent repair.
- `POST /projects/{project_id}/film-score/preview` and `POST /projects/{project_id}/film-score/commit`.
- Agents-tab inspect and Commit.
- Mocked-agent tests and the locked tempo vectors.
- Docs named in Task 7.

### Out of scope
- Scene detection, silence detection from audio, shot detection, ffmpeg, and a new media file.
- Changing spotting cue storage, suggestion, or the Picture ruler.
- A new agent id, a revision loop, or autonomous-run stages.
- Transport behavior for `music_start` and `music_stop`.
- Adaptive score, neural render, and mix plan.
- Cross-project Musical Universe search.
- A new Alembic revision, a new revision operation enum, or `composition.v5`.

### Architecture decisions (locked)

**1. Documents**

`FilmCueSnapshot` in `backend/app/film_score_schemas.py`. `extra=forbid`. Fields: `id` (`^hit_[0-9a-f]{8}$`), `kind` (the nine cue kinds), `importance` (`low`, `medium`, `high`, `critical`), `video_seconds` (finite, `>= 0`), `tolerance_frames` (`0..240`). No `instruction`, no `label`, no `timecode`. The router copies those four musical fields off the stored hit. Instruction stays on the scoring document.

`FilmScorePreviewRequest`. `extra=forbid`.

| Field | Rule |
|-------|------|
| `brief` | 1..2000 characters. Required |
| `profile_id` | optional, 1..80, or null |
| `profile_strength` | `off`, `light`, `normal`, `strong`. Default `off` |
| `motif_ids` | 0..16 strings, each 1..120. Default empty |
| `instruments` | 1..16 non-empty strings |
| `key` | optional supported key. Default: source composition key, else `C major` |
| `time_signature` | optional. Default: source composition, else `4/4` |
| `opening_tempo` | optional int 40..240. Default: source tempo if inside the band, else `120` clamped into the band |
| `tempo_min` / `tempo_max` | ints 40..240, `tempo_min <= tempo_max`. Default `96` and `132` when opening is `120`. When `opening_tempo` is set and the band is omitted, the band is `max(40, opening-24)` .. `min(240, opening+24)` |
| `target_duration_seconds` | optional finite `> 0` and `<= 3600`. Omitted uses the asset `duration_seconds` |
| `replace_existing` | bool, default false |

`FilmScorePlanV1` schema version `film.score.plan.v1`. `extra=forbid`. Reject the same top-level playable keys as `composition.plan.v1` (`tracks`, `events`, `notes`, `musicxml`, `midi`, `wav`, `composition`, `music`, `note_events`, `analysis`).

| Field | Rule |
|-------|------|
| `project_id` | string |
| `source_fingerprint` | string |
| `scoring_document_revision` | int `>= 0` |
| `target_duration_seconds` | the window end used for bars |
| `music_start_seconds` / `music_end_seconds` | floats. Tick 0 of the candidate is `music_start_seconds` |
| `sync_origin` | `{video_origin_seconds, musical_origin_tick}`. `video_origin_seconds` equals `music_start_seconds`. `musical_origin_tick` is `0` |
| `time_signature`, `key` | the hard values |
| `root_tempo` | int 40..240 |
| `tempo_min`, `tempo_max` | the band |
| `sections` | 1..32 of `{id, label, start_bar, bar_count, density, tempo_bpm, start_video_seconds, end_video_seconds}` |
| `tempo_strategy` | `{policy: "section_boundary", changes: [{tick, bpm, section_id, reason_code}]}`. `reason_code` is `phrase_fit` only on this list. Cap 8 |
| `harmonic_arc` | 0..32 of `{start_bar, end_bar, key, function}` from the harmony artifact. Empty when the agent returns no chords |
| `motif_appearances` | 0..16 of `{motif_id, section_id}` |
| `hit_alignments` | one row per input cue: `{cue_id, kind, importance, status, target_bar, delta_seconds, tempo_change_added}` |
| `density_regions` | 0..32 of `{cue_id, start_bar, end_bar, density}`. Dialogue uses `sparse` |
| `agent_sequence` | the ids invoked, in order |
| `warnings` | codes only, max 32. Includes `hit_unsatisfiable`, `film_motif_absent`, `harmony_plan_empty`, and `film_origin_not_updated` |
| `committed` | always false on this document |

Hit status is `aligned`, `soft`, `unsatisfiable`, `boundary`, or `silence`. `boundary` is `music_start` and `music_stop`. `silence` is the trailing span after `music_stop` and is not a hit row; the stop cue itself is `boundary`.

Preview response `film.score.preview.v1`: `plan`, `candidate` (`composition.v2` or null), `candidate_fingerprint` (null when candidate is null), `artifact_log` (the `AgentArtifactV1` envelopes the agents returned, plus the empty motif plan when `melody_motif` did not run), `artifact_role_map` (the spine roles `buildArtifactRoleMapFromLog` already expects), `committed: false`, `recommendation` (`approve` or `revise` or null).

Error codes on `FilmScoreError`: `film_score_invalid` (422), `film_duration_invalid` (422), `film_frame_rate_required` (422), `film_asset_missing` (404), `film_motif_missing` (422), `film_instrument_unknown` (422), `film_score_conflict` (409), `film_score_replace_required` (409), `film_agent_failed` (503).

**2. Window, sections, and tempo**

Constants in `backend/app/services/film_score_tempo.py`: `FILM_PHRASE_BARS = 8`, `FILM_PHRASE_MIN_BARS = 4`, `FILM_PHRASE_MAX_BARS = 16`, `FILM_TEMPO_STEP_MAX = 12`, `FILM_DIALOGUE_SECONDS = 2.0`. Not request fields.

Music window:

- Start is the earliest `music_start` `video_seconds`, or `0` when no `music_start` exists.
- End is the earliest of `target_duration_seconds` and the earliest `music_stop` at or after the start. No `music_stop` means the target duration.
- A window shorter than one bar at `tempo_min` is `film_duration_invalid`.

Bar count uses `bar_duration_ticks` and integer bpm. `bar_seconds = beats_per_bar * 60 / bpm`. `bar_count = clamp(round(window_seconds / bar_seconds), 1, 512)`. The composition duration is that many whole bars. The compiler does not emit a partial bar.

Sections: walk the window in phrases of 8 bars. A phrase shorter than 4 bars merges into the previous phrase. A phrase never exceeds 16 bars. Two sync cues fewer than 4 bars apart stay in one section. A new section starts when the next phrase begins, or when the tempo rule below needs a new bpm and the boundary is at least 4 bars after the previous section start. Boundary tick is that section's `start_bar` tick. Bar 1 has tick 0 and is the root tempo, never a `tempo_changes` row (`tick > 0` is already the V2 rule).

Sync set: importance `high` or `critical`, kind in `hit_point`, `reveal`, `cut`, `action`, `emotional_cue`, and `video_seconds` inside the window. Other cues do not move tempo.

The candidate grid treats tick 0 as the music-window start. Accent checks and this compiler pass `video_origin_seconds = music_start_seconds` and `musical_origin_tick = 0`. They do not use the stored origin from the previous score.

For each section, at the incoming bpm, map each sync cue to the nearest beat inside the section (beat length `60 / bpm`, including beat 0 at the section start). Status `aligned` when `abs(beat_frame - cue_frame) <= tolerance_frames`. Both frames use `floor(seconds * numerator / denominator + 1e-9)`, the same floor `verify_cue_landings` uses for a null timecode. The snapshot has `video_seconds` and no timecode, so `cue_frame` is that floor of `video_seconds`. `delta_seconds` stays on the row as the signed seconds between that beat and the cue.

When any sync cue in the section is not `aligned`:

1. Search integer bpms in the intersection of `[tempo_min, tempo_max]` and `[previous-12, previous+12]`.
2. Score each candidate by the worst sync-cue frame delta onto that candidate's beat grid, measured from the section start. Lower is better. Tie-break: smaller absolute distance from the previous bpm, then the lower bpm.
3. Adopt the candidate only when that worst frame delta is strictly smaller than the worst frame delta at the previous bpm.
4. Adopted opening section (bar 1): set `root_tempo`. `tempo_changes` gains no row. `tempo_change_added` is false on those hits. The plan's `root_tempo` is the adopted value.
5. Adopted later section: append one change `{tick: section_start_tick, bpm, section_id, reason_code: "phrase_fit"}`. Hits in that section that become aligned get `tempo_change_added: true`.
6. When no candidate improves the worst delta, keep the previous bpm. Each still-missing sync cue is `unsatisfiable` and `tempo_change_added: false`. Append warning `hit_unsatisfiable`.
7. After the chosen bpm, recompute this and later section video edges with `CompiledTimeline` once the skeleton exists. The compiler's first pass may use the closed bar-seconds formula; the workflow's stored `start_video_seconds` must match `tick_to_seconds` of the candidate. Tests compare those, not a handwritten tick product copied into production code.

Soft cues: `aligned` when inside tolerance, otherwise `soft`. They never set `tempo_change_added` and never add a warning.

`music_start` and `music_stop` rows are `boundary`. They are not sync cues.

Dialogue: kind `dialogue` adds a density region from the cue `video_seconds` for `2.0` seconds, clipped to the next cue and to 8 bars, mapped onto bars, density `sparse`. A dialogue cue does not add a tempo change even when its time is off the grid.

Density on sections: `sparse` when the section overlaps a dialogue region, otherwise `moderate`. The structure agent may label sections. It does not change `bar_count`, `start_bar`, or `tempo_bpm`.

Locked vectors, rate `24/1`, meter `4/4`, `ticks_per_quarter` 480, band `96..132`, step `12`, tolerance `0` unless noted. `bar_seconds` at 120 is `2`. One bar is `1920` ticks.

Vector A — no per-hit tempo. Window `180` seconds, opening `120`. Critical hits at `10`, `40`, and `60` seconds. No dialogue. Result: `bar_count` `90`, `root_tempo` `120`, `tempo_strategy.changes` empty, each of those three hits `aligned` with `tempo_change_added` false.

Vector B — one section-boundary tempo. Window `32` seconds, opening `120`. Critical hit at `10` seconds (`aligned` at 120). Critical hit at `17.875` seconds. Section 2 starts at bar 9, tick `15360`, which is `16.0` seconds at 120. At 120 the local time `1.875` is `3.75` beats; the nearest beat is 3 frames away at `24/1`, so tolerance `0` misses. At `128`, four beats last `4 * 60/128 = 1.875` seconds and `1.875 * 24 = 45` frames, so the hit frame matches. `128` sits inside `96..132` and within 12 of 120. The change is `{tick: 15360, bpm: 128, reason_code: phrase_fit}`. That tick is the section start. First section stays 120. `tempo_change_added` is true only on the `17.875` hit.

Vector C — unsatisfiable. Same window and opening. Critical hit at `16.05` seconds, one frame past the section-2 start. Tolerance `0`. At every integer bpm in `108..132` the nearest beat is still that section start, whose frame is one away, and the next beat is farther. Result: no change row, root stays `120`, status `unsatisfiable`, warning `hit_unsatisfiable`. A local gap of `3.6` seconds (`video_seconds` `19.6`) is not this vector: bpm `116` lands on that same frame.

Vector D — dialogue is density. Window `180`, opening `120`, critical hits on barlines at `10` and `40`, plus a `dialogue` cue at `20` seconds. `tempo_strategy.changes` stays empty. One density region covers the bars that contain `20` through `22` seconds. Those bars are sparse. The dialogue row is `soft` or `aligned` from tolerance only, and `tempo_change_added` is false.

**3. Realization order**

`compile_film_score_plan` runs before any agent. It needs the cue snapshots, the closed rate, the hard tempo band, meter, and the window. It does not call a model.

Then `run_film_score_preview` in `backend/app/services/film_score_workflow.py`:

1. Build hard `GenerationConstraints` from the request. Profile strength `off` skips the profile. Otherwise `resolve_profile_merge` supplies a soft fragment. The fragment does not change key, meter, tempo band, instruments, or `duration_bars`.
2. Resolve every instrument through the arrangement catalog. An unknown id is `film_instrument_unknown`.
3. Every `motif_id` must exist on the source composition. A miss is `film_motif_missing`. Empty `motif_ids` is allowed and adds warning `film_motif_absent`.
4. Build `composition.plan.v1` from the compiled sections. `ComposerFormSection.type` is a `SUPPORTED_SECTION_TYPES` value: the only phrase is `verse`; with several phrases the first is `intro`, the last is `outro`, and the rest are `verse`. Film-plan labels stay on `CompositionV2Section.label`. `ComposerFormPlan.tempo` is `root_tempo`. Instrumentation is the request list. Call `generate_symbolic_composition`. Tests set `LLM_FAKE_MODE` so the backend is `fake:symbolic-tiny`. After generation, copy `tempo_strategy.changes` onto the candidate and keep each section `start_tick` on the tick grid. Section video edges then come from that candidate's `CompiledTimeline`.
5. Call agents through `get_agent(...).run` with a fresh `AgentWorkflowContext` whose source is that symbolic composition. Selection carries `start_bar` and `end_bar` for the music window. Order:

| Step | Agent id | Operation | Notes |
|------|----------|-----------|-------|
| 1 | `creative_director` | `plan` | Brief text is the request brief plus the soft fragment. Cue instructions are not appended |
| 2 | `structure_form` | `plan` | Artifact recorded. Compiler bar counts replace any stub sections before the candidate is built |
| 3 | `harmony` | `propose` | Existing reharm wrapper. `preserve_melody_adapt_harmony` |
| 4 | `melody_motif` | `propose` | Skipped when `motif_ids` is empty. The workflow then appends an empty `agent.motif_plan.v1` so `motif_plan` is present on the role map. One appearance in the first non-sparse section and, when `bar_count >= 16`, one recurrence in a later non-sparse section |
| 5 | `arrangement` | `propose` | Instruments from the request |
| 6 | `orchestration` | `plan` | Artifact only |
| 7 | `critic` | `critique` | One pass. `revision_mode` stays off. A `revise` recommendation does not block preview or commit |

6. `apply_film_score_accents` in `backend/app/services/film_score_accents.py`. For each hit with status `aligned` and importance `high` or `critical`, if `verify_cue_landings` would not report `landed`, insert one note on the first melody-role track (else the first track): pitch is the tonic of the plan key, `duration_ticks` one beat, velocity `96`, `start_tick` the nearest beat tick the plan already chose. Call `verify_cue_landings` with the candidate timeline, `video_origin_seconds = music_start_seconds`, and `musical_origin_tick = 0`. Do not insert accents for `soft`, `unsatisfiable`, `boundary`, or dialogue. Do not insert on sparse dialogue bars. Run this after the agents. If arrangement removes an accent, this step puts it back. The landing result does not copy pitches onto the plan.
7. Build `artifact_role_map` the way `buildArtifactRoleMapFromLog` does. Required roles are `brief`, `harmony_plan`, `motif_plan`, `arrangement_plan`, and `critique`. `revision_plan` stays null because the critic runs once and `revision_mode` stays off.
8. Validate `CompositionV2`. Fingerprint with `composition_edit_fingerprint`.
9. Copy harmony chord functions into `harmonic_arc` from the harmony artifact when present. Empty chords add warning `harmony_plan_empty` and leave `harmonic_arc` empty. Do not invent chords.
10. On agent exception: log the agent id and the error code, return HTTP 503 `film_agent_failed` with the plan and `candidate: null`. Do not write the project. The plan's `artifact_log` still includes whatever envelopes were produced before the failure, and it includes the empty motif plan when motifs were absent.

`agent_sequence` on a full run with motifs is `creative_director`, `structure_form`, `harmony`, `melody_motif`, `arrangement`, `orchestration`, `critic`. Without motifs, `melody_motif` is omitted.

The workflow module may import `video_spotting.verify_cue_landings` and the map only for the accent check. It must not live under `ai_agents/`. `ai_agents/` must not import `film_score_schemas`, `film_score_tempo`, `film_score_workflow`, `film_score_accents`, or the video modules.

**4. HTTP**

Router `backend/app/routers/film_score.py`, mounted beside the other project routers.

| Route | Access | Behavior |
|-------|--------|----------|
| `POST /projects/{project_id}/film-score/preview` | `write_score` | Loads asset, scoring, composition. Missing asset is `film_asset_missing`. Rate that is not closed is `film_frame_rate_required`. Returns `film.score.preview.v1`. Writes nothing |
| `POST /projects/{project_id}/film-score/commit` | `write_score` | Body: `candidate`, `candidate_fingerprint`, `replace_existing`, `expected_document_revision`, `artifact_log`, `artifact_role_map`, and the `DurableCommitRequest` CAS fields `branch_id`, `expected_active_branch_id`, `expected_working_version`, `expected_head_revision_id`, `expected_source_fingerprint`. Fingerprint mismatch is `film_score_conflict`. Source events nonempty and `replace_existing` false is `film_score_replace_required`. A role map missing a required spine role is `film_score_conflict`. Success calls `commit_revision` with `RevisionOperationType.MULTI_AGENT_APPLY` and puts `artifact_role_map` plus the envelopes on `AiProvenance.generation_parameters` |

Preview does not call suggest, the verify route, or project save. Commit does not open the video file for write. Asset SHA-256 stays unchanged.

Before `commit_revision`, the route reads the scoring document and requires `expected_document_revision` to match `plan.scoring_document_revision` (send that revision on the commit body). A mismatch is `film_score_conflict` and the composition is left as it was. After the composition commit succeeds, the route writes `video_origin_seconds = music_start_seconds` and `musical_origin_tick = 0` through the existing scoring update. Hit ids, kinds, timecodes, `video_seconds`, `musical_tick`, tolerances, importance, and instructions are copied unchanged. If that scoring update fails, the response stays `committed: true` and the plan warnings include `film_origin_not_updated`.

When the source composition has note events, preview still returns a candidate. Commit is the call that checks `replace_existing`.

**5. UI**

`frontend/src/components/FilmScorePanel.jsx` on the Agents tab, mounted from `frontend/src/components/MultiAgentPanel.jsx` beside `AutonomousComposerPanel`. Fields: brief, optional profile id and strength, motif id checkboxes from the current composition motifs, instrument text list, optional duration seconds, preview button, plan summary (section label, bars, bpm, density; hit id, status, `tempo_change_added`; warning codes; critic recommendation). Commit stays disabled until a preview fingerprint is in session state.

Session slice `filmScorePreview` in `musicStore.js` is not copied into `composition_json` and is not autosaved. Commit calls the project reload path already used after `multi-agent-apply`. Opening the Agents tab does not POST preview. The Picture tab is not modified.

`frontend/src/utils/filmScorePlan.js` formats the summary from the plan document. It does not recompute tempo.

**6. Logging**

`LOG_LEVEL` controls verbosity. INFO: preview started and finished with project id, cue count, section count, tempo-change count, warning codes, and duration milliseconds; commit finished with project id and the first 12 characters of the fingerprint. DEBUG: section id, start bar, bar count, bpm; hit status counts by status. WARNING: schema and domain error codes; `film_agent_failed` with agent id. ERROR: unexpected failure with the error code. Never log the brief, instructions, cue labels, the profile fragment, video bytes, paths, or note arrays.

## Tasks

### Phase 1: Plan contract and tempo grid
- [x] Task 1: Add the film-score request and plan documents
- [x] Task 2: Compile sections, tempo strategy, and dialogue density

### Phase 2: Agents and composition
- [x] Task 3: Place sync accents and check landings
- [x] Task 4: Run the existing agents and return a candidate

### Phase 3: HTTP and review
- [x] Task 5: Preview and commit over the project
- [x] Task 6: Review the plan and commit from the Agents tab

### Phase 4: Docs
- [x] Task 7: Document the film-scoring agent

## Commit Plan
- **Commit 1** (after tasks 1-2): "feat: plan film-score sections and tempo against spotting cues"
- **Commit 2** (after tasks 3-4): "feat: realize a film-score preview with the existing agents"
- **Commit 3** (after tasks 5-6): "feat: preview and commit a film score from the Agents tab"
- **Commit 4** (after task 7): "docs: describe the film-scoring agent"

## Tasks (detail)

### Task 1: Add the film-score request and plan documents

Add `FilmCueSnapshot`, `FilmScorePreviewRequest`, `FilmScorePlanV1`, the preview response model, and `FilmScoreError` in `backend/app/film_score_schemas.py` using the tables in the decisions. `extra` stays `forbid`. Reject forbidden playable keys with `film_score_invalid`. `committed` on the plan is false. Omitted `motif_ids` is an empty list. Omitted `profile_strength` is `off`.

`backend/tests/test_film_score_schemas.py` accepts a plan with one section, an empty `tempo_strategy.changes` list, and a hit row `aligned`. It rejects a payload that contains `tracks` or `events`, a ninth tempo change, a 33rd section, an unknown hit status, `tempo_min > tempo_max`, and a brief of 2001 characters.

LOGGING: DEBUG on schema rejection with the model name and error code. Do not log the brief. Levels follow `LOG_LEVEL`.

Files: `backend/app/film_score_schemas.py`, `backend/tests/test_film_score_schemas.py`.

### Task 2: Compile sections, tempo strategy, and dialogue density

Add `compile_film_score_plan` in `backend/app/services/film_score_tempo.py`. Inputs are cue snapshots, a closed frame-rate rational, and the request tempo, meter, key, and window. No FastAPI, SQLite, agent, or video-schema import. Use `bar_duration_ticks` for bar length. Use integer bpm only.

`backend/tests/test_film_score_tempo.py` asserts vectors A, B, C, and D from the decisions. Vector B's only change is tick `15360` and bpm `128`. The change list has no tick derived from `17.875` seconds. Frame equality uses `floor(seconds * 24 + 1e-9)`. Vector C at `16.05` seconds adds no change and sets `unsatisfiable`. A `dialogue` cue at an off-beat time adds a sparse region and zero changes. `music_stop` at `30` seconds on a `180` second target ends the window at 30 seconds. A window that cannot hold one bar at `tempo_min` raises `film_duration_invalid`. `sync_origin.musical_origin_tick` is `0` and `video_origin_seconds` equals the window start.

LOGGING: DEBUG one line per section with section id, start bar, bar count, and bpm, plus hit status counts. Do not log labels or instructions. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Place sync accents and check landings

Add `apply_film_score_accents` in `backend/app/services/film_score_accents.py`. It may call `verify_cue_landings` and `CompiledTimeline`. It inserts the tonic attack described in the decisions only for aligned high or critical sync hits that are not inside a sparse dialogue region. It does not delete other notes. The landing call uses the candidate timeline, `video_origin_seconds` equal to the music-window start, and `musical_origin_tick` `0`.

`backend/tests/test_film_score_accents.py` builds a composition for vector A, removes every attack near the 10-second hit, runs the accent helper, and asserts `verify_cue_landings` reports `landed` for that cue under that origin. A sustain that merely covers the frame stays `missed`. An `unsatisfiable` cue gains no new note. A dialogue-sparse bar gains no accent. The helper does not change `tempo` or `tempo_changes`. Passing the stored non-zero origin is not this test: the helper's origin arguments are the window start and tick `0`.

LOGGING: DEBUG accent insertions with cue id, start tick, and status. Do not log pitch lists or the event array. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Run the existing agents and return a candidate

Add `run_film_score_preview` in `backend/app/services/film_score_workflow.py` following the realization order. Build `composition.plan.v1` with `SUPPORTED_SECTION_TYPES` as specified in the decisions, call `generate_symbolic_composition` at `root_tempo`, then copy `tempo_strategy.changes` onto the candidate. Register calls go through `get_agent`. After the agents return, overwrite section bar counts from the compiler when they differ, then run accent repair. When `melody_motif` is skipped, append an empty `AgentMotifPlanV1` and include it in `artifact_log`. Build `artifact_role_map` with `brief`, `harmony_plan`, `motif_plan`, `arrangement_plan`, and `critique`.

`backend/tests/test_film_score_workflow.py` clears the registry and registers `MusicAgent` fakes that record `(agent_id, operation)`. The structure fake returns the stub 4-bar form. The test uses a 180-second vector A snapshot and `LLM_FAKE_MODE`. Assert the call order with motifs is the seven-id sequence, and without motifs omits `melody_motif`. Assert the no-motif `artifact_role_map` still has a `motif_plan` entry whose content type is `agent.motif_plan.v1`. Assert the candidate `bar_count` is `90`. Assert `tempo_changes` is empty. Assert `committed` is false. Assert a fake that raises becomes `film_agent_failed` and a null candidate while the plan is still present. Assert a requested motif id missing from the composition is `film_motif_missing` before any agent runs. Assert profile strength `off` leaves `duration_bars` at the compiled bar count. Assert a multi-phrase plan uses `intro` and `outro` as section types.

Add a test that reads `backend/app/ai_agents` and fails if any file imports `video_scoring`, `video_spotting`, `llm_video_spotting`, or `film_score_`.

LOGGING: INFO preview finished with project id, cue count, section count, tempo-change count, and warning codes. DEBUG the agent id at each call. WARNING `film_agent_failed` with the agent id. Do not log the brief or the soft fragment. Levels follow `LOG_LEVEL`.

Depends on Tasks 2 and 3.

### Task 5: Preview and commit over the project

Add `backend/app/routers/film_score.py` and mount it from `backend/app/main.py`. Preview loads the project composition, the scoring document, and the asset duration. It builds cue snapshots from stored hits and calls `run_film_score_preview`. Commit accepts the `DurableCommitRequest` CAS fields named in the decisions, checks the candidate fingerprint, `replace_existing`, and `expected_document_revision`, then calls `commit_revision` with `RevisionOperationType.MULTI_AGENT_APPLY`. `artifact_role_map` and `artifact_log` go on `AiProvenance.generation_parameters`. After that commit, the route updates `video_origin_seconds` and `musical_origin_tick` and leaves every cue field as stored.

`backend/tests/test_film_score_routes.py` stores a picture whose duration covers 180 seconds and a scoring document with vector A's three hits. The source composition may be short. Preview returns `committed` false, change count 0, a candidate whose `bar_count` is `90`, and a role map that includes `motif_plan`. The stored hit ids, timecodes, and `musical_tick` values, plus the asset SHA-256, match the pre-call snapshot. A second preview is not issued by GET scoring. Commit with the wrong fingerprint returns `film_score_conflict`. Commit when the project already has a note and `replace_existing` is false returns `film_score_replace_required`. Commit with `replace_existing` true persists a `composition.v2` whose events the following GET returns. The following scoring GET has the same hit ids and cue fields, `musical_origin_tick` `0`, and `video_origin_seconds` equal to the window start. A stale `expected_document_revision` returns `film_score_conflict` and leaves the composition fingerprint unchanged. Omitting `motif_plan` from the role map returns `film_score_conflict`. Missing picture returns `film_asset_missing`. A null scoring rate returns `film_frame_rate_required`.

LOGGING: INFO commit finished with project id and the 12-character fingerprint prefix. Do not log the candidate events. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Review the plan and commit from the Agents tab

Add `frontend/src/utils/filmScorePlan.js` and `frontend/src/api/filmScoreApi.js`. Add `FilmScorePanel.jsx` and a `filmScorePreview` session slice. Mount the panel from `frontend/src/components/MultiAgentPanel.jsx` beside `AutonomousComposerPanel`. Preview button sends the request. The summary lists section bars, bpm, density, hit status, and warning codes. Commit stays disabled until `candidate_fingerprint` is set. Commit posts the CAS fields, `artifact_log`, and `artifact_role_map` from the preview. Successful commit reloads the project the same way multi-agent apply already does and clears the session preview.

`frontend/src/utils/filmScorePlan.test.js` covers an empty change list, vector B's single change, a disabled commit when the fingerprint is null, and a sparse dialogue region. The store test, or the same util test if the slice is pure, asserts the preview object is absent from the composition snapshot that autosave would send.

LOGGING: the panel does not log the brief. API failures surface the error code on the panel.

Depends on Task 5.

### Task 7: Document the film-scoring agent

Add `docs/film-scoring.md` with the documents, the preview/commit split, the tempo rules, the agent order, the sync-origin pair written on commit, and the statement that cue rows and video bytes stay put. Link it from `docs/multi-agent.md` and `docs/video-scoring.md` in a short pointer. Add the router, services, and panel to `AGENTS.md` and the architecture dependency note in `.ai-factory/ARCHITECTURE.md`. State that `ai_agents/` does not import the film-score or video modules.

Do not document a new agent id. Do not change `ROADMAP.md`.

Depends on Tasks 5 and 6.
