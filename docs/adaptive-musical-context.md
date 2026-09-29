# Adaptive musical context

An external application posts a flat sample. Mapping rules project that sample onto a closed musical view. Smoothing and hysteresis then emit the existing adaptive playback commands. The stored graph stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`. There is no `composition.v5` and no `adaptive.score.v2`.

The context session does not copy note events and does not call an LLM. Restart drops the mapping and the samples.

## Documents

| Schema | Role |
|--------|------|
| `adaptive.context.external.v1` | One flat sample: an optional source label and a map of scalars |
| `adaptive.context.mapping.v1` | Bindings and rules stored on the session, not on the score |
| `adaptive.musical_context.v1` | The closed view after mapping and smoothing |

`values` holds at most 64 keys. A key matches `^[A-Za-z][A-Za-z0-9_.]{0,63}$`. A value is a finite number, a boolean, a short string, or a list of short strings. Nested objects, nulls, and lists of numbers are rejected. Keys named `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, or `composition_json` are rejected as embedded note material. Absent keys do not clear a normalized slot.

`musical_state_id` is a state id on `adaptive.score.v1`. The snapshot field `state` is an application label such as `combat`. A binding may copy an external string into `state`. Only a rule may copy a normalized value into `musical_state_id`.

## Held state

Each numeric band has an enter threshold, an exit threshold, and a consecutive-sample dwell. The default gap is `0.05` (`ADAPTIVE_CONTEXT_MIN_HYSTERESIS_GAP`). A high band claims enter when the smoothed value is at least `enter`, and claims release when it is at most `exit`. Values inside the open band do neither. Wall-clock time is not used.

The locked example is the flap fixture:

```text
baseline_state_id: state-exploration
binding bind-danger: external_key danger, slot danger, transform identity, smooth_alpha 1
rule rule-combat: numeric_band, slot danger, polarity high
  enter 0.65, exit 0.35, min_dwell_samples 3, target_state_id state-combat, priority 10
intensity: slot danger, emit_epsilon 0.02
```

Twenty samples that alternate danger `0.49` and `0.51` stay on `state-exploration` and emit no `request_state`. Three samples at `0.7` move the held state to `state-combat`. When playback is already running on that fixture, the playback clock follows `state-combat` without a score write. The same sample list returns the same snapshots. `context_id` may differ across two fresh sessions.

Numeric slots use an exponential moving average, then clamp `tension`, `health`, `danger`, and `intensity` to `0..1`. `set_intensity` is emitted when the clamped source moves by at least `emit_epsilon`, and again on the sample that changes musical state. Command order is `set_intensity`, then `set_flags`, then `request_state`.

## HTTP

Prefix: `/projects/{project_id}/adaptive-scores`. Permission: `read`.

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/{score_id}/context` | Start. Body: `expected_document_revision` and `mapping`. Snapshot with `sample_index` 0 |
| `GET` | `/{score_id}/context` | Current snapshot, or `204` |
| `POST` | `/{score_id}/context/samples` | One external sample. Returns the snapshot |
| `DELETE` | `/{score_id}/context` | Clear. `204` |

A missing session on sample post is `404` `context_not_running`. A baseline or target that is not on the score is `422` `dangling_state_ref` and creates no session. A wrong `schema_version` is `422` `unsupported_schema_version` and names the context document, not `adaptive.score.v1`.

Start does not start playback. A sample while playback is absent still steps the context clock and adds warning `playback_not_running`. When playback is running, the service calls the existing playback commands in order. It does not send `stop`, `advance`, or `observe`.

## Logging

Each successful start, sample, and stop logs one INFO line with:

- `adaptive_musical_context`
- `project_id`
- `score_id`
- `context_id`
- `sample_index`
- `musical_state_id`
- `state_changed`
- `emitted_ops` (op names only)
- `warning_codes`
- `sample_count`
- `state_change_count`
- `intensity_emit_count`
- `rejected_sample_count`

DEBUG may add `dwell_rule_id`, `dwell_count`, `ignored_key_count`, `binding_count`, and the smoothed `danger`, `health`, `tension`, and `intensity` numbers. ERROR logs `code` only.

These fields are not logged: `source_id`, `observed_at_ms`, the `values` object, external keys, narrative tags, location, character ids, custom categorical strings, raw unclamped inputs, `body_json`, and note data.

## Caps

`backend/app/adaptive_musical_context_settings.py` reads integer caps and the hysteresis gap. An invalid env value logs WARNING with `setting` and `invalid: true`, then keeps the default. Thresholds for Combat versus Exploration live on the mapping.

| Env | Default |
|-----|---------|
| `ADAPTIVE_CONTEXT_MAX_BINDINGS` | 32 |
| `ADAPTIVE_CONTEXT_MAX_RULES` | 32 |
| `ADAPTIVE_CONTEXT_MAX_VALUES` | 64 |
| `ADAPTIVE_CONTEXT_MAX_TAGS` | 32 |
| `ADAPTIVE_CONTEXT_MAX_CHARACTERS` | 32 |
| `ADAPTIVE_CONTEXT_MAX_CUSTOM` | 32 |
| `ADAPTIVE_CONTEXT_DEFAULT_DWELL` | 3 |
| `ADAPTIVE_CONTEXT_MAX_DWELL` | 64 |
| `ADAPTIVE_CONTEXT_MIN_HYSTERESIS_GAP` | 0.05 |

## Adaptive tab

The readout under Playback has **Arm example**, **Flap**, and **Sustain**. Those buttons post the locked mapping and the locked sample lists. The visible held state, intensity, dwell, and warning codes come from the response. The panel does not select the authoring card and does not move Tone by itself.

## Non-goals

- No game client and no nested application schema. The caller flattens its own objects.
- No SQLite table and no Alembic revision for samples or mappings.
- No new playback command, no variant selection, and no note generation.
- `ai_agents/` does not import `adaptive_musical_context_schemas`, `adaptive_musical_context_settings`, `adaptive_musical_context`, `adaptive_musical_context_service`, or `adaptive_musical_context_runtime`. Agents that need a snapshot use HTTP.

See also [adaptive-score.md](adaptive-score.md).
