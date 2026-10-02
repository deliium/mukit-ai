# Implementation Plan: V5 Explicit Preference Learning

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-02

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: a Preference learning section under the existing Profiles tab, plus rank order on the Develop and Arrange candidate lists. Opening the tab reads settings and choices. It does not enable collection, enable ranking, or record a choice. Ranking reorders the list and does not Apply
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-02 (`/aif-improve`). Feature index 9 is the absolute-interval mean divided by 12, so every element stays in `[0, 1]`. A pending ballot is stashed when collection or ranking is effectively on. Choice insert still requires both collection gates. `startDevelopmentPreview` and `startArrangementPreview` rank from Zustand flags that survive tab unmount, and they call preference routes only when those flags are true. Preview requests carry `active_project_id`, `profile_id`, and `profile_strength`. A candidate whose feature extraction raises is omitted from the stash
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: an explicit choice among development or arrangement candidates becomes one stored ballot and one update of a linear pairwise ranker, only when preference learning is enabled. The ranker orders a later ballot. The working `composition.v2` changes only through the existing Apply control

## Roadmap Linkage
Milestone: "V5 Explicit preference learning"
Rationale: Prior choices among generated alternatives should order the next ballot, while the composer can still pick any candidate and can see or delete the data. The milestone is the first unchecked item in `.ai-factory/ROADMAP.md`. This plan appends that unchecked item because the request asked to add it. Implementation does not edit `ROADMAP.md`.

## Goal

Learn which generated alternative a user prefers from an explicit ballot, without treating genre or other prompt metadata as the preference. A ballot looks like this:

```text
context
candidate A
candidate B ← chosen
candidate C
```

Ship:

1. Non-playable documents `preference.settings.v1`, `preference.pending_ballot.v1`, `preference.choice.v1`, `preference.features.v1`, `preference.ranker.v1`, and `preference.ranking.v1`.
2. A deployment flag `PREFERENCE_LEARNING_ENABLED` (default off) and a user setting that also defaults off. New choices are written only when both are on.
3. Inspect and reset of the stored choices and the ranker weights.
4. A `CandidateRanker` abstraction and one linear pairwise implementation that updates from the chosen-minus-other feature difference.
5. Feature vectors taken from `symbolic.features.v1`. Generation context and the active Composer Profile id are stored on the ballot and are not ranker dimensions.
6. Ranking on the next development or arrangement preview. Apply stays the existing manual control.

Acceptance: at `4/4`, 120 bpm, and 480 ticks per quarter, three one-bar fixtures differ by rhythm. Sparse is one `C4` at tick `0` lasting `1920`. Middle is `C4 E4 G4 C5` at ticks `0, 480, 960, 1440`, each lasting `480`. Dense is eight notes `C4 D4 E4 F4 G4 A4 B4 C5` at ticks `0, 240, 480, 720, 960, 1200, 1440, 1680`, each lasting `240`. With both gates on, recording Dense as the choice makes a later ballot of the same three rhythms, transposed to `F4`, rank Dense first, then Middle, then Sparse. Selecting Sparse after that ranking and applying it writes the Sparse pitches. The Dense candidate stays unapplied. With the flag off, the same record call writes no row. Reset leaves zero choices and a later rank returns the original order.

```text
effective collection OR effective ranking
        │
        ▼
preview (development | arrangement, 2..4 candidates)
        │  extract preference.features.v1 from each composition.v2
        ▼
preference.pending_ballot.v1
        │  explicit choice only when both collection gates are on
        ▼
preference.choice.v1  →  linear pairwise update  →  preference.ranker.v1
        │
        ▼
POST /preferences/rank   reorders the next ballot; Apply is unchanged
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. A **ballot** is one set of alternatives plus the one the user chose. A **choice** is the stored `preference.choice.v1` row. A **pending ballot** is the feature snapshot taken at preview, before any choice. The **ranker** is `preference.ranker.v1`, a weight vector, not a Music Transformer and not a personal adapter. **Collection** writes choices. **Ranking** orders candidates with those weights. **Manual selection** is the existing candidate radio plus Apply. A **Composer Profile** remains `composer.profile.v1` soft text. This plan does not update it. **Genre** means any style, mood, artist, or composer label. Those strings are not features and are not stored.

Predecessor: `.ai-factory/plans/v2-context-aware-composition-development.md` and `.ai-factory/plans/v2-ai-assisted-arrangement-orchestration.md` for the 1–4 candidate previews. `.ai-factory/plans/v4-composer-profiles.md` stays the soft-preference path. `.ai-factory/plans/v5-personal-symbolic-composer-adaptation.md` trains an adapter on owned scores and is not a choice log.

## Approach Evaluation (locked)

### Part A — When a choice is collected

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Record every preview click, including audition** | More rows | Audition is not a preference. Accidental clicks would train the model | **Reject** |
| **B. Infer preference from whichever score is later saved** | No extra control | Autosave and unrelated edits would look like choices. Single-candidate generate has no siblings to compare | **Reject** |
| **C. After a successful development or arrangement Apply, store the pending ballot for that surface with the applied `candidate_id` marked chosen. A preview with fewer than two candidates stores nothing** | Matches the ballot in the request. Apply is already explicit. Siblings are the alternatives that were actually offered | The preview must stash features, because Apply does not resend the rejected compositions | **Accepted** |

### Part B — Where collection is allowed

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always collect** | Nothing to enable | The user required an opt-in | **Reject** |
| **B. A user checkbox only** | Visible in the studio | A deployment cannot turn the feature off without deleting the checkbox | **Reject** |
| **C. `PREFERENCE_LEARNING_ENABLED` defaults off, using the same truthy set as `COLLABORATION_ENABLED`. The user setting `collection_enabled` also defaults off. A choice is inserted only when both are on. Ranking uses weights only when the flag is on and `ranking_enabled` is on. Inspect and reset still work when the flag is off** | The process default collects nothing. The Profiles tab is a second explicit opt-in. Turning the flag off stops new training and stops ranking without hiding data the user already has | Two switches must both be on before the first row appears | **Accepted** |

### Part C — What the model is allowed to see

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Store genre, mood, or style strings and rank by those labels** | Easy to read | The user forbade treating preference as that kind of metadata. Two scores in the same key would look identical for the wrong reason | **Reject** |
| **B. Call `analyze_composition` and persist `composition.analysis.v1`, plus the 81-d embedding and the profile bands** | Uses every named input | The analysis orchestrator is a sidecar with an advisory path. Storing the report or the profile bands puts a soft-preference document into the ranker. Profile bands are the same for every candidate in one ballot, so they cannot change pairwise order | **Reject** |
| **C. Project a fixed 16-d vector from the pre-L2 `symbolic.features.v1` vector via `extract_symbolic_features_v1`. Store generation context and `profile_id` / `profile_strength` on the choice. Keep them out of the weight vector** | The features come from note events already used for embeddings. A transposed repeat of the same rhythm keeps the density ordering, which is the evaluation. No advisory LLM call | The ranker cannot see a full analysis report. That report stays a separate sidecar | **Accepted** |

### Part D — Which ranker to ship first

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Fine-tune the Music Transformer or a personal LoRA on chosen scores** | Uses an existing trainer | This is not candidate ranking. It would change a generator and would train on notes. The personal-composer plan already owns adapters | **Reject** |
| **B. Add `torch` and fit Bradley-Terry with a batch solver** | A familiar ranking loss | Core tests do not require torch. A batch solver is more machinery than the first model needs | **Reject** |
| **C. A `CandidateRanker` protocol with one `LinearPairwiseRanker`. Weights start at zero. Each non-chosen sibling adds one update `w ← clip(w + lr * (1 - sigmoid(w·delta)) * delta)` where `delta` is chosen features minus other features. Score is the dot product. Ties break by original preview index** | No new package. One choice is enough to prefer Dense over Sparse on the next ballot. A later model can implement the same protocol | The surface is linear. It will not learn interactions between dimensions | **Accepted** |

### Part E — How ranking meets the studio

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reorder inside `run_composition_development_preview` and auto-apply the top candidate** | One round trip | The generator service would import SQLite. Auto-apply writes `composition.v2` from the model | **Reject** |
| **B. Put `preference_rank` on `DevelopmentCandidate` and `ArrangementCandidate`** | The list arrives sorted | Those models are `extra=forbid` and already stable. A rank field mixes a session preference into the candidate document | **Reject** |
| **C. The preview route stashes features only. `startDevelopmentPreview` and `startArrangementPreview` call `POST /preferences/rank` when the Zustand ranking flag is true, reorder the session list, and select the first ranked id as a suggestion. Apply still sends the selected composition through the existing path** | Preview services stay free of the preference store. The Profiles panel can unmount without dropping the flags. Existing preview tests that leave the flags false keep a single axios URL. A failed rank call leaves preview order in place | The client makes one extra request when ranking is on | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Development ballot | `POST /composition/development/preview` in `composition_development_preview_route` calls `run_composition_development_preview`. `candidate_count` is 1–4. Each success is a `DevelopmentCandidate` with `candidate_id`, `candidate_fingerprint`, `edit_source_fingerprint`, and a full `composition`. Response order is success order. `startDevelopmentPreview` sets `developmentSelectedCandidateId` from `response.candidates[0]`. The request already accepts `active_project_id` with length 1..80 and does not accept profile fields. `musicStore.js` does not send `active_project_id` today | Stash after this route returns when two or more extractable candidates remain. Do not sort inside the service. Send `active_project_id`, `profile_id`, and `profile_strength` from the store |
| Arrangement ballot | `composition_arrangement_preview_route` calls `run_composition_arrangement_preview`. Successes are `ArrangementCandidate` values with the same id and fingerprint fields. Failures are `rejected_attempts` and have no composition. `CompositionArrangementPreviewRequest` has no project or profile fields. `startArrangementPreview` also selects `candidates[0]` | Add optional `active_project_id`, `profile_id`, and `profile_strength`. Stash only candidates whose features extract. A rejected attempt is not a ballot entry |
| Apply | `applySelectedDevelopmentCandidate` and `applySelectedArrangementCandidate` in `frontend/src/store/musicStore.js` write the selected full `composition.v2` and may store `ai.candidate_id` on the revision | After that write succeeds, POST the choice with the applied id. A preference error does not roll back the composition |
| Features | `extract_symbolic_features_v1` in `backend/app/embeddings/features.py` returns the pre-L2 81-d vector. Layout offsets: range at 12, density at `PC+RANGE+DUR+ONSET`, contour at the interval block, roles after concurrent bins | The preference projector reads this vector. It does not call `POST /embeddings/compute` and does not write the embedding cache |
| Profiles | `composer.profile.v1` and `ProfileStrength` (`off`, `light`, `normal`, `strong`). The store keeps `composerProfileId` and `composerProfileStrength`. Generate stores `composer_profile_id` and `profile_strength` in provenance. Profile ids are `prof_` plus 16 hex characters | Copy `profile_id` and `profile_strength` from the preview body onto the ballot context. Do not read profile bands |
| Opt-in flag pattern | `collaboration_enabled` in `backend/app/collaboration_settings.py`. Truthy set `1`, `true`, `yes`, `on`. Unset, empty, and `0` are off. Any other string is off, logged by code, and the raw value is never logged | Copy that parser for `PREFERENCE_LEARNING_ENABLED` |
| Latest migration | `20261002_0022` in `backend/app/db/alembic/versions/20261002_0022_personal_composers.py`, `down_revision` `20261002_0021` | Next revision `20261002_0023`, `down_revision` `20261002_0022` |
| Agent boundary | `backend/tests/test_ai_agents_architecture.py` forbids store imports from `ai_agents/` | Add `from app.services.preference_store` |
| Profiles UI | Profiles tab in `frontend/src/components/ComposerWorkspace.jsx` renders `ComposerProfilesPanel` then `PersonalComposerPanel` | Render `PreferenceLearningPanel` under `PersonalComposerPanel` |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Choice row | Apply stores the winning id on a revision and drops the siblings |
| Opt-in | No flag or user setting gates preference data |
| Ranker | No protocol, weights, or ordered candidate response |
| Inspect and reset | No list or delete for this data |
| Rank display | `startDevelopmentPreview` and `startArrangementPreview` select index 0 in generation order. The existing development store test asserts that preview axios hits only `/composition/development/preview` |

### Coupling risks to avoid

1. Writing `tracks[].events[]` from rank, record, inspect, reset, or settings changes.
2. Updating `composer.profile.v1`, a personal adapter, or `DATASET_ROOT` from a choice.
3. Calling `analyze_composition`, an LLM, or the Music Transformer from the feature projector or the ranker.
4. Importing `preference_store` from `ai_agents/`, or importing FastAPI or `project_store` from the ranker module.
5. Logging prompts, genre or mood strings, note arrays, profile bodies, full fingerprints, or the 81-d embedding. Log `snapshot_fingerprint_log_prefix` only.
6. Treating single-candidate generate, revision-pass audition, or the film-score preview as a ballot. Those surfaces do not offer a sibling set today.
7. Auto-applying the top-ranked candidate.
8. Adding `composition.v5`, a new workspace tab, `torch`, or a new agent id.
9. Editing `ROADMAP.md` during implementation.
10. Inserting a choice when the flag is off or `collection_enabled` is false. A pending ballot is written only when effective collection or effective ranking is on. Reset may delete rows while the flag is off.

## Scope And Decisions

### In scope
- Settings, pending ballot, choice, feature, ranker, and ranking documents.
- Tables, the env flag, inspect, and reset.
- The 16-d projector and the linear pairwise ranker.
- Stash on development and arrangement preview when effective collection or effective ranking is on and at least two candidates yield a feature vector.
- Choice record after a successful Apply, and `POST /preferences/rank`.
- Profiles-tab controls and list reordering on Develop and Arrange.
- Evaluation tests on the Sparse / Middle / Dense vector and on the transposed repeat.

### Out of scope
- Training or ranking from generate, revision-loop `pass_candidates`, or film-score preview. Those paths do not present a multi-candidate ballot.
- Fitting the model on `composition.analysis.v1` reports, profile bands, or prompt text.
- Changing Apply, audition, or the candidate fingerprint formula.
- A neural ranker, a second implementation of `CandidateRanker`, or an online update other than the locked linear step.
- Rewriting Composer Profiles from observed choices.
- `ROADMAP.md` during implementation.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/preference_schemas.py`. `extra=forbid`. This module does not import FastAPI, torch, stores, video, film, agent, or embedding modules. A payload that contains any of these keys anywhere is `preference_forbidden_payload`: `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`, `genre`, `genres`, `mood`, `style`, `styles`, `artist`, `artist_name`, `composer`, `composer_name`, `prompt`, `embedding`, `analysis`, `analysis_report`, `vector`. The allowed feature field is `feature_vector`, and the scan matches key names exactly.

`PreferenceSettingsV1`. Schema version `preference.settings.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `preference.settings.v1` |
| `collection_enabled` | bool, default false |
| `ranking_enabled` | bool, default false |
| `updated_at` | optional server timestamp |

`PreferenceContextV1`.

| Field | Rule |
|-------|------|
| `surface` | `development` or `arrangement` |
| `operation` | string, 1..64 |
| `project_id` | optional string, 1..80. Same bound as development `active_project_id` |
| `source_fingerprint` | string, 16..128 |
| `request_digest` | `^[0-9a-f]{64}$` |
| `profile_id` | optional `^prof_[0-9a-f]{16}$` |
| `profile_strength` | optional `off`, `light`, `normal`, `strong` |

`request_digest` is SHA-256 of canonical JSON with keys `surface`, `operation`, `source_fingerprint`, `candidate_ids` sorted, `profile_id`, and `profile_strength`. The digest input contains no prompt and no note events.

`PreferenceCandidateFeaturesV1`. `candidate_id` 16..128, `candidate_fingerprint` 16..128, `original_index` int 0..3, `feature_vector` length exactly 16. Each element is a finite float in `[0, 1]`. `chosen` is a bool and appears only on a stored choice, not on a pending ballot.

`PreferencePendingBallotV1`. Schema version `preference.pending_ballot.v1`. Context plus `candidates` length 2..4. Candidate ids are unique. No `chosen` field.

`PreferenceChoiceV1`. Schema version `preference.choice.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `preference.choice.v1` |
| `id` | `^pref_[0-9a-f]{16}$` |
| `context` | `PreferenceContextV1` |
| `candidates` | length 2..4 of feature rows with `chosen` |
| `chosen_candidate_id` | must equal the single candidate whose `chosen` is true |
| `created_at` | server timestamp |

`PreferenceRankerV1`. Schema version `preference.ranker.v1`. `feature_schema` literal `preference.features.v1`. `dims` literal 16. `weights` length 16, each clipped to `[-4, 4]`. `learning_rate` literal `0.1`. `pair_count` int `>= 0`. `updated_at` server timestamp.

`PreferenceRankingV1`. Schema version `preference.ranking.v1`. `ranking_applied` bool. `ordered_candidate_ids` length 1..4. `scores` length equal to that list. When `ranking_applied` is false, `ordered_candidate_ids` is the request order and every score is `0`.

**2. Feature vector**

`project_preference_features` in `backend/app/services/preference_features.py` calls `extract_symbolic_features_v1` on the candidate `CompositionV2` and reads the pre-L2 vector. It does not L2-normalize the 81-d vector and does not import `composition_analysis`.

| Index | Source |
|-------|--------|
| 0 | range min, offset 12 |
| 1 | range max, offset 13 |
| 2 | range mean, offset 14 |
| 3 | density scalar |
| 4 | contour up |
| 5 | contour down |
| 6 | contour same |
| 7 | mean of the 8 duration bins |
| 8 | mean of the 8 onset bins |
| 9 | weighted mean of absolute interval, bins `-12..+12`, divided by 12 |
| 10 | melody role |
| 11 | bass role |
| 12 | accompaniment role |
| 13 | mean of the 4 concurrent bins |
| 14 | track count |
| 15 | form position |

Empty interval mass yields index 9 equal to `0`. Dividing by 12 keeps index 9 inside `[0, 1]` with the other fifteen components. Section-type one-hots are not copied. The function returns exactly 16 floats. A candidate that raises during extraction is omitted. If fewer than two candidates remain, the caller writes no pending row.

**3. Ranker**

`backend/app/services/preference_ranker.py` defines `CandidateRanker` with `rank(items, model) -> PreferenceRankingV1` and `update(model, choice) -> PreferenceRankerV1`. `LinearPairwiseRanker` is the only implementation. The module imports schemas and the standard library. It does not import SQLite, FastAPI, embeddings, or stores.

Update, for each candidate with `chosen` false:

```text
delta = chosen.feature_vector - other.feature_vector
p = 1 / (1 + exp(-(w · delta)))
w = clip(w + 0.1 * (1 - p) * delta, -4, 4)
```

`pair_count` increases by one per sibling. A choice with zero siblings does not reach this function because the schema requires at least two candidates. `rank` sorts by score descending, then `original_index` ascending. `model is None` or `pair_count == 0` returns `ranking_applied` false and the input order.

**4. Tables**

`backend/app/db/alembic/versions/20261002_0023_preference_learning.py`. `down_revision` is `20261002_0022`.

| Table | Columns | Rule |
|-------|---------|------|
| `preference_settings` | `id` integer primary key, `body_json`, `updated_at` | At most one row, `id = 1`. Migration inserts nothing |
| `preference_pending` | `surface` text primary key, `body_json`, `updated_at` | One pending ballot per surface. A new stash replaces that surface |
| `preference_choices` | `id` text primary key, `surface` text, `created_at` text, `body_json` | Cap 200. The 201st insert raises `preference_choice_limit` and writes nothing, including the ranker |
| `preference_ranker` | `id` integer primary key, `body_json`, `updated_at` | At most one row, `id = 1`. Created on the first successful choice |

No foreign keys. `body_json` is the document above. The store is `backend/app/services/preference_store.py`. It does not import `project_store`, `composer_profile_store`, or `personal_composer_store`.

**5. Gates**

`preference_learning_enabled` in `backend/app/preference_settings.py` parses `PREFERENCE_LEARNING_ENABLED`. Unrecognized values log WARNING code `preference_flag_unrecognized` and return false. The raw value is never logged.

Effective collection is flag and `collection_enabled`. Effective ranking is flag and `ranking_enabled`. `GET /preferences/settings` returns the stored booleans plus `feature_available`. It does not insert a row. When no row exists the booleans are false.

`PUT /preferences/settings` with either boolean true while the flag is off returns `preference_learning_disabled` (HTTP 409) and leaves the stored row unchanged. `PUT` with both false is allowed while the flag is off, so the user can turn the stored switches off. `DELETE /preferences/data` deletes choices, the ranker row, and pending ballots while the flag is on or off. It does not delete the settings row.

**6. HTTP**

Router `backend/app/routers/preferences.py`, registered in `backend/app/main.py` beside `composer_profiles_router`.

| Method | Path | Effect |
|--------|------|--------|
| `GET` | `/preferences/settings` | Reads. No insert |
| `PUT` | `/preferences/settings` | Writes the two booleans under the gate in decision 5 |
| `GET` | `/preferences/choices` | Summaries, newest first, limit query default 20 and max 50. Each item has `id`, `surface`, `operation`, `chosen_candidate_id`, `candidate_count`, `created_at`, and `source_fingerprint_prefix` (12 hex). No `feature_vector` on the list |
| `GET` | `/preferences/choices/{choice_id}` | One choice document, including `feature_vector`, and no composition |
| `DELETE` | `/preferences/data` | Reset |
| `POST` | `/preferences/choices` | Body is `surface` plus `chosen_candidate_id`. Loads the pending ballot for that surface. The chosen id must be on it. Inserts the choice and updates the ranker in one transaction, then deletes that pending row |
| `POST` | `/preferences/rank` | Body is `surface` plus `candidate_ids` length 1..4. When effective ranking is off, or no ranker exists, return `ranking_applied` false and the request order. When on, score the stashed features for those ids. An id missing from the pending ballot is `preference_ballot_missing` |

`POST /preferences/choices` when the flag is off returns `preference_learning_disabled` (HTTP 409) and inserts nothing. When the flag is on and `collection_enabled` is false, it returns `preference_collection_disabled` (HTTP 409) and inserts nothing. A missing pending row is `preference_ballot_missing` (HTTP 404). None of these responses change a project row.

When `COLLABORATION_ENABLED` is on and the pending ballot has `project_id`, `POST /preferences/choices` and `POST /preferences/rank` call `enforce_current(project_id, "read")` before the write or the score. There is no new collaboration action. When the flag is off, `authorize_project` returns before the membership lookup, matching `collaboration_guard.py`.

Preview stash lives in `backend/app/services/preference_capture.py`. `composition_development_preview_route` and `composition_arrangement_preview_route` call it after a successful preview response is built, and before the response is returned. The helper writes when effective collection is on or effective ranking is on, and at least two candidates produce a feature vector. When both effective gates are off it returns without writing. It copies `active_project_id` into `project_id`, and copies `profile_id` and `profile_strength` when the request carries them. It replaces the pending row for that surface only. It does not reorder `candidates`. A capture exception is logged WARNING `preference_capture_failed` and the preview response is still returned. A per-candidate extraction failure omits that candidate and does not fail the preview.

`preference_capture` imports the feature projector and the store. The development and arrangement services do not import it.

**7. UI**

`frontend/src/components/PreferenceLearningPanel.jsx` mounts under `PersonalComposerPanel` on the Profiles tab. On mount it `GET`s settings and choices and writes `preferenceCollectionEnabled`, `preferenceRankingEnabled`, and `preferenceFeatureAvailable` into the Zustand store. Those fields survive leaving the tab. Opening the tab does not `PUT`. `feature_available` false disables both toggles and shows that the server flag is off. A successful `PUT` updates the same store fields. Reset stays enabled and calls `DELETE /preferences/data`, then reloads the list. The list shows surface, operation, chosen id, candidate count, and fingerprint prefix. No feature numbers in the list.

`frontend/src/utils/preferenceRanking.js` exports `orderCandidatesByRanking(candidates, ranking)`. It returns a new array. Unknown ids stay at the end in their original order. `ranking_applied` false returns the input array copy.

`startDevelopmentPreview` and `startArrangementPreview` send `active_project_id` from `currentProjectId` when it is set, plus `profile_id` and `profile_strength` from `composerProfileId` and `composerProfileStrength`. After the preview response arrives, and only when `preferenceRankingEnabled` is true, they call `POST /preferences/rank`, then re-check `requestId` and the base revision. A stale response is ignored. A current response replaces the session candidate array with the ordered copy and sets the selected id to `ordered_candidate_ids[0]`. That selection is a suggestion. The existing radios still call `selectDevelopmentCandidate` and `selectArrangementCandidate`. Neither preview action calls Apply. A rank error leaves the preview order and surfaces the server `code`. When the ranking flag is false, the preview actions do not call preference routes, so the existing development store test still sees only `/composition/development/preview`.

Apply handlers in `musicStore.js` call `POST /preferences/choices` only after the composition commit succeeds and only when `preferenceCollectionEnabled` is true. The request body is the surface and the applied candidate id. A preference error leaves the committed composition in place and records the code on the session status string those panels already use for request failures. It does not clear the new working score. When the collection flag is false, apply does not call that route.

`frontend/src/api/preferenceApi.js` holds the HTTP helpers. The panel does not `console.log` choice bodies or feature vectors.

**8. Evaluation**

`backend/tests/test_preference_ranker.py` builds the three fixtures from the acceptance, asserts density index 3 is ordered Dense > Middle > Sparse, fits one choice of Dense, and ranks the same rhythms transposed so the lowest pitch is `F4`. Order is the Dense transposition, then Middle, then Sparse. A second fit that chooses Sparse moves Sparse ahead of Dense on a third ballot with those same rhythms. Identical `CompositionV2` documents produce identical vectors. A payload containing `genre` raises `preference_forbidden_payload`.

`backend/tests/test_preference_collection.py` covers flag off, user collection off, flag on plus collection on, inspect list without `feature_vector`, detail with `feature_vector` and without note keys, reset to zero choices, and the 201st choice. It also asserts the project `working_fingerprint` is unchanged by record, rank, and reset.

`backend/tests/test_preference_preview_rank.py` runs a fake development preview with `candidate_count` 3 while both gates are on, asserts one pending row and a preview `candidates` array still in success order, then ranks and asserts the HTTP order. It also stashes when ranking is on and collection is off, and it inserts no choice in that case. Applying the last-ranked id through the existing apply helper is asserted at the store test layer: `frontend/src/utils/preferenceRanking.test.js` plus a new case in `frontend/src/store/musicStore.development.test.js`. That case sets the Zustand flags, stubs preference URLs separately from the preview URL, commits the clicked candidate id after a reorder, and leaves the applied composition in place when the choice POST returns 409. The existing preview test leaves both flags false and still asserts a single preview URL.

## Tasks

### Phase 1: Documents and the ranker
- [x] Task 1: Add the preference documents
- [x] Task 2: Project features and rank candidates with the linear model

### Phase 2: Opt-in storage
- [x] Task 3: Store settings, choices, and the ranker under both gates
- [x] Task 4: Serve inspect, reset, record, and rank

### Phase 3: Preview ballots and the studio
- [x] Task 5: Stash development and arrangement ballots without reordering them
- [x] Task 6: Inspect and reset in Profiles, and rank lists without applying

### Phase 4: Docs
- [x] Task 7: Document opt-in preference learning

## Commit Plan
- **Commit 1** (after tasks 1-2): "feat: rank candidates with a linear pairwise preference model"
- **Commit 2** (after tasks 3-4): "feat: collect preference choices only when learning is enabled"
- **Commit 3** (after tasks 5-6): "feat: order development and arrangement candidates from prior choices"
- **Commit 4** (after task 7): "docs: describe opt-in preference learning from candidate choices"

## Tasks (detail)

### Task 1: Add the preference documents

Add the models, the forbidden-key scan, and `PreferenceLearningError` in `backend/app/preference_schemas.py`. Codes: `preference_forbidden_payload`, `preference_invalid`, `preference_learning_disabled`, `preference_collection_disabled`, `preference_ballot_missing`, `preference_choice_limit`, `preference_not_found`. Include `http_status` and `map_preference_error_to_http`. Statuses are 422, 409, 404, and 422 in the code order above, with `preference_not_found` at 404.

`backend/tests/test_preference_schemas.py` accepts a choice whose candidates are Sparse, Middle, and Dense with Dense chosen, and a `project_id` of length 80. It rejects a second `chosen` flag, a `feature_vector` of the wrong length, a feature element above `1` or below `0`, a non-finite element, the key `genre`, the key `events`, and a surface other than `development` or `arrangement`.

LOGGING: DEBUG on schema rejection with the model name and the error code. Do not log fingerprints, feature values, or the raw payload. Levels follow `LOG_LEVEL`.

Files: `backend/app/preference_schemas.py`, `backend/tests/test_preference_schemas.py`.

### Task 2: Project features and rank candidates with the linear model

Add `preference_features.py` and `preference_ranker.py` as specified. The evaluation test is `backend/tests/test_preference_ranker.py` and covers the acceptance fixtures, the `F4` transposition, the second update that prefers Sparse, identical scores mapping to identical vectors, and the cold-start rank that returns input order with `ranking_applied` false.

Also assert index 3 increases from Sparse to Middle to Dense on the untransposed fixtures, and that index 9 is the absolute-interval mean divided by 12 and is at most `1`. Do not import `analyze_composition` in the feature module.

LOGGING: DEBUG `preference features projected` with `candidate_id` and `dims`. DEBUG `preference ranker updated` with `pair_count` and `surface` only when the caller passes a surface string into a log helper on the ranker; the pure `update` function itself does not log weights. INFO is not required in the pure functions. Do not log the weight vector at INFO. Levels follow `LOG_LEVEL`.

Files: `backend/app/services/preference_features.py`, `backend/app/services/preference_ranker.py`, `backend/tests/test_preference_ranker.py`.

Depends on Task 1.

### Task 3: Store settings, choices, and the ranker under both gates

Add `preference_settings.py`, the Alembic revision, and `preference_store.py`. `get_settings` returns defaults when the table has no row. `put_settings` refuses a true boolean when the flag is off. `record_choice` inserts the choice and the updated ranker on one connection and then deletes that surface from `preference_pending`. A failure rolls back both. `reset_preference_data` deletes choices, pending, and the ranker row. `stash_pending` replaces one surface. `list_choices` returns summaries only.

`backend/tests/test_preference_store.py` uses a temporary `PROJECT_DB_PATH`. It checks flag off, collection off, a successful Dense choice, reset, the cap at 200, and a migration upgrade from `20261002_0022` with downgrade. `backend/tests/test_preference_migration.py` can be that migration test if the store file is better kept to the gate behavior. Update `backend/tests/test_ai_agents_architecture.py` so the ban includes `from app.services.preference_store`.

LOGGING: INFO on stash, record, and reset with `surface`, `choice_id` when present, `candidate_count`, and `snapshot_fingerprint_log_prefix` of `source_fingerprint`. WARNING on disabled gates and on `preference_choice_limit` with the code only. Do not log `body_json`, weights, or feature vectors. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Serve inspect, reset, record, and rank

Add `backend/app/routers/preferences.py` and register it. Map errors through `map_preference_error_to_http`. `GET /preferences/choices` omits `feature_vector`. `GET /preferences/choices/{choice_id}` includes it and omits note keys. Rank with an empty ranker returns `ranking_applied` false. Collaboration-on tests call `enforce_current` with `read` when `project_id` is set, and they do not add an action.

`backend/tests/test_preference_collection.py` is the HTTP evaluation: flag off inserts nothing, user opt-in off inserts nothing, both on records Dense and lists one summary, detail has 16 floats and no `pitch`, reset returns an empty list, and a following rank is `ranking_applied` false. Before and after those calls, a seeded project `working_fingerprint` is unchanged.

LOGGING: INFO each route with method, path template, status, and `duration_ms`. DEBUG the choice id on record. Do not log feature vectors or fingerprints beyond the prefix already stored in the summary response. Levels follow `LOG_LEVEL`.

Depends on Task 3.

### Task 5: Stash development and arrangement ballots without reordering them

Add `preference_capture.py`. Call it from `composition_development_preview_route` and `composition_arrangement_preview_route` after the preview object is complete. Pass the validated compositions, ids, fingerprints, original indexes, surface, operation, `active_project_id`, `profile_id`, and `profile_strength`. Add those three optional fields to `CompositionArrangementPreviewRequest` with the same bounds as the development `active_project_id` and the profile id pattern. Add optional `profile_id` and `profile_strength` to `CompositionDevelopmentPreviewRequest`. The helper computes `request_digest` and the 16-d vectors. It writes when effective collection is on or effective ranking is on, and at least two candidates produce a vector. A candidate that raises during extraction is omitted. Exceptions outside that per-candidate omission do not change the HTTP status of a successful preview.

`backend/tests/test_preference_preview_rank.py` uses `LLM_FAKE_MODE=1` and `candidate_count` 3 for development. It asserts the pending row exists, the response candidate ids are still success order, and the pending context stores the posted `active_project_id` and `profile_id`. Arrangement with one success writes no pending row. A preview while the flag is off leaves the pending table empty. Ranking on with collection off writes one pending row and `POST /preferences/choices` returns `preference_collection_disabled` with a choice count of 0. A candidate that fails extraction is absent from the stash, and the preview response still lists it.

LOGGING: INFO `preference ballot stashed` with `surface` and `candidate_count`. WARNING `preference_capture_failed` with the error code. Do not log compositions. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Inspect and reset in Profiles, and rank lists without applying

Add `preferenceApi.js`, `preferenceRanking.js`, `PreferenceLearningPanel.jsx`, and the store fields `preferenceCollectionEnabled`, `preferenceRankingEnabled`, and `preferenceFeatureAvailable`, all defaulting false. Mount the panel from `ComposerWorkspace.jsx` under `PersonalComposerPanel`. The panel GET writes those fields. A successful PUT writes them again. `startDevelopmentPreview` and `startArrangementPreview` send the project and profile fields, then rank only when `preferenceRankingEnabled` is true, after the existing stale-response check, and select `ordered_candidate_ids[0]` without calling Apply. Apply posts the choice only when `preferenceCollectionEnabled` is true, after the composition commit succeeds, and ignores a preference failure for the working score.

`frontend/src/utils/preferenceRanking.test.js` reorders three ids and leaves the array unchanged when `ranking_applied` is false. Extend `frontend/src/store/musicStore.development.test.js` with a case that sets the ranking and collection flags, stubs `/preferences/rank` and `/preferences/choices` by URL, commits the clicked candidate after the reorder, and leaves that composition in place when the choice POST returns 409. The existing preview test leaves both flags false and still asserts that the only axios URL is `/composition/development/preview`. Do not add a component test file.

LOGGING: the panel does not `console.log` choice payloads. API helpers may `console.debug` the route code on failure. Do not log feature vectors.

Depends on Task 5.

### Task 7: Document opt-in preference learning

Add `docs/preference-learning.md` with the terminology lock, the two collection gates, the rule that a pending ballot is also written when ranking is on, the ballot diagram, the 16-d feature table with index 9 divided by 12, the rule that ranking selects a suggestion and does not Apply, and the statement that genre labels are not features. Link it from `AGENTS.md`, `docs/CODEBASE_MAP.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` beside the composer-profile lines. State that `ai_agents/` does not import `preference_store`, and that a choice does not update `composer.profile.v1`. Do not edit `ROADMAP.md`.

LOGGING: none. This task does not add runtime logs.

Depends on Tasks 4 and 6.
