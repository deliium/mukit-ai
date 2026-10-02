# Explicit preference learning

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`.

A **ballot** is one set of development or arrangement alternatives plus the one the composer Applied. A **choice** is the stored `preference.choice.v1` row. A **pending ballot** is the feature snapshot taken at preview, before any choice. The **ranker** is `preference.ranker.v1`, a 16-weight linear model. It is not a Music Transformer and not a personal adapter. **Collection** writes choices. **Ranking** orders the next ballot. **Manual selection** is the existing candidate radio plus Apply. A **Composer Profile** remains `composer.profile.v1` soft text. A choice does not update it. **Genre** means any style, mood, artist, or composer label. Those strings are not features and are not stored.

`ai_agents/` does not import `preference_store`.

## Two gates

`PREFERENCE_LEARNING_ENABLED` defaults off. Unset, empty, and `0` are off. `1`, `true`, `yes`, and `on` are on. Any other string is off. The user switches `collection_enabled` and `ranking_enabled` also default off.

| Action | When it runs |
| --- | --- |
| Stash a pending ballot | The flag is on and either collection or ranking is on, and at least two candidates yield a feature vector |
| Insert a choice | The flag is on and collection is on, after a successful Apply |
| Rank a later ballot | The flag is on and ranking is on |
| Inspect and reset | Always, including when the flag is off |

A pending ballot is written when ranking is on even if collection is off. In that case `POST /preferences/choices` returns `preference_collection_disabled` and inserts no row. Turning the flag off stops new choices and stops ranking. It does not hide rows already stored. Reset deletes choices, pending ballots, and the ranker. It leaves the settings row.

## Ballot

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

Preview responses stay in generation order. The Develop and Arrange panels reorder the session list when ranking is on and select the first ranked id as a suggestion. That selection does not Apply. Apply still writes the candidate the composer clicks.

## Features

`project_preference_features` reads the pre-L2 `symbolic.features.v1` vector. Each preference element stays in `[0, 1]`. Generation context, `profile_id`, and `profile_strength` are stored on the ballot and are not ranker dimensions.

| Index | Source |
| --- | --- |
| 0 | Range minimum |
| 1 | Range maximum |
| 2 | Range mean |
| 3 | Density |
| 4 | Contour up |
| 5 | Contour down |
| 6 | Contour same |
| 7 | Mean of the eight duration bins |
| 8 | Mean of the eight onset bins |
| 9 | Weighted mean of absolute interval, bins −12..+12, divided by 12 |
| 10 | Melody role |
| 11 | Bass role |
| 12 | Accompaniment role |
| 13 | Mean of the four concurrent bins |
| 14 | Track count |
| 15 | Form position |

Empty interval mass yields index 9 equal to 0. Dividing by 12 keeps that index inside `[0, 1]`.

The ranker starts at zero weights. Each non-chosen sibling adds one step `w ← clip(w + 0.1 * (1 - sigmoid(w·delta)) * delta, -4, 4)`, where `delta` is the chosen vector minus the other vector. Score is the dot product. Ties keep the earlier preview index. A cold ranker returns the request order with `ranking_applied` false.

## Studio

The Profiles tab includes Preference learning under the personal adapter panel. Opening the tab reads settings and choices. It does not enable collection, enable ranking, or record a choice. When `feature_available` is false both toggles are disabled and the panel says the server flag is off. Reset stays available.

`POST /preferences/settings`, `GET/POST /preferences/choices`, `DELETE /preferences/data`, and `POST /preferences/rank` are the HTTP surface. List rows show surface, operation, chosen id, candidate count, and a fingerprint prefix. They do not include feature numbers. A choice document includes the 16-d vectors and no note events.
