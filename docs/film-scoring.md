# Film scoring agent

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. Picture documents stay `video.asset.v1` and `video.scoring.v1`. There is no `video.scoring.v2` and no new agent id.

A composer asks for a film score for the current picture, inspects `film.score.plan.v1`, and only then commits an editable composition. Opening the Agents tab does not run a preview. The Picture tab stays the cue editor.

## Documents

`film.score.plan.v1` is inspectable and non-playable. It carries the music window, section bars, the opening tempo, `tempo_strategy.changes`, dialogue density, hit alignment status, and the agent ids that ran. It is not stored on the composition or on cue rows.

Preview (`POST /projects/{id}/film-score/preview`) returns `film.score.preview.v1`: the plan, a candidate, `candidate_fingerprint`, `artifact_log`, `artifact_role_map`, `committed: false`, and the critic recommendation. It writes nothing.

Commit (`POST /projects/{id}/film-score/commit`) is the only write of `tracks[].events[]` on this path. It uses the existing `multi-agent-apply` revision CAS. `artifact_role_map` and `artifact_log` travel on generation parameters. After that write it sets `video_origin_seconds` to the music-window start and `musical_origin_tick` to `0`. Cue rows and video bytes stay as stored.

## Tempo

Bar lengths and tempo stay on a deterministic compiler. A tempo change is an integer bpm inside the request band and within 12 of the previous bpm. Its tick is a section start. The reason code is `phrase_fit`. The compiler adds a change only when a high or critical sync cue in that section cannot land on the beat grid inside its frame tolerance, and a legal bpm makes the worst frame delta strictly smaller. An opening mismatch changes the root tempo and writes no `tempo_changes` row. A miss that no legal bpm can fix stays `unsatisfiable` and keeps the previous bpm. Soft cues and dialogue do not move tempo. Dialogue is a sparse density region.

## Agents

The orchestrator calls the registered agents in this order: `creative_director` (`plan`), `structure_form` (`plan`), `harmony` (`propose`), `melody_motif` (`propose`) when motif ids are present, `arrangement` (`propose`), `orchestration` (`propose`), `critic` (`critique`). `orchestration` uses `propose` because that is the operation the registered agent accepts; the artifact is still `agent.orchestration_plan.v1`. When no motif is requested, the log still includes an empty `agent.motif_plan.v1`. A stub form plan cannot replace the compiled bar counts. Accent repair runs after the agents and does not change tempo.

`ai_agents/` does not import the film-score modules or the video modules. The film-score router loads the asset and the scoring document and passes a cue snapshot into the workflow.

## Frontend

The Agents tab `FilmScorePanel` takes a brief, an optional profile id and strength, motif checkboxes from the current composition, an instrument list, and an optional target duration. The summary lists each section label, bars, bpm, and density, plus each hit id, status, and whether a tempo change was added, the warning codes, and the critic recommendation. Commit stays disabled until `candidate_fingerprint` is set, and it sends `plan.scoring_document_revision`. The session preview is not part of project autosave.

## See also

- [Multi-agent](./multi-agent.md)
- [Video scoring](./video-scoring.md)
- [Film score adaptation](./film-score-adaptation.md) — local repair after a declared picture edit; it does not replace this generation preview
