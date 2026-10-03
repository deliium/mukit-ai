# Content provenance

Durable, inspectable derivation lineage for composition revisions, imports,
AI operations, neural renders/stems, and mix-plan revisions — without claiming
cryptographic Content Credentials where only Mukit-internal metadata exists.

Playable scores stay **`composition.v2`**. There is no `composition.v5`.
Provenance documents never store note events, PCM, or prompts.

## Terminology

| Term | Meaning |
|------|---------|
| **Provenance record** | Durable `content.provenance.record.v1` DAG node (ids, operation, models, parents, trust class) |
| **Provenance chain** | `content.provenance.chain.v1` assembled walk from a leaf toward roots |
| **Provenance manifest** | Exportable `content.provenance.manifest.v1` (records + honesty + digest) |
| **Trust class** | `mukit_internal` (default), `c2pa_signed` (only after real non-fake credential attach), `unavailable` (missing parent) |
| **C2PA / Content Credentials** | Optional egress projection behind `CONTENT_CREDENTIALS_ENABLED` (default off) |

## Honesty (Part K)

```
honesty.cryptographic = c2pa.attached ∧ ¬c2pa.fake_mode ∧ any(record.trust_class == c2pa_signed)
```

Under `CONTENT_CREDENTIALS_FAKE`, provenance **records** stay `mukit_internal`; only
`content.credentials.status.v1` may set `fake_mode: true`. The UI label
“Cryptographically signed” appears only when `honesty.cryptographic === true`.

## Capture (soft-fail)

Capture runs beside existing writers (`commit_revision`, neural complete,
`apply_mix`, recovery bind, theme reuse, pack clear). Soft-fail wrapper catches
cycle/cap/`ContentProvenanceError`, logs WARNING, and returns — the primary
writer still commits. This intentionally diverges from
`musical.dependency.edge.v1` cycle **hard-fail** and from
[rights governance](rights-governance.md) permission gates (also **hard-fail** —
derivation lineage ≠ permission-to-use).

Idempotent upsert key: `(artifact_kind, artifact_id, operation)`.
Manifest/chain **downloads never insert** records.

Import durability: SPA `completeImport` → `commit_revision` with
`operation_type=import` (maps to `import_midi` / `import_musicxml`). No durable
`import_session` table in ship-1. Transcription stamps `transcription_apply`
only when `user_action=audio_transcribe` is present.

## Relation to other graphs

- `generation.provenance.v1` remains nested in revision summaries; may appear
  compactly on AI records.
- `musical.dependency.edge.v1` `rendered_from` stays the musical **stale** edge.
- Provenance is the richer AI/human lineage with trust labels.
- [Rights governance](rights-governance.md) (`rights.registry.entry.v1`,
  `model.data.provenance.manifest.v1`) is permission-to-use for train/reference —
  complementary, not replaced by this DAG.

## HTTP

| Method | Path | Notes |
|--------|------|-------|
| GET | `/content-provenance/status` | `provenance_enabled`, credentials flags — **not** on `/ready` |
| GET | `…/artifacts/{kind}/{id}/chain` | Chain for UI |
| GET | `…/artifacts/{kind}/{id}/manifest` | JSON manifest |
| GET | `…/artifacts/{kind}/{id}/manifest/download` | Attachment; read-only |
| POST | `…/artifacts/{kind}/{id}/credentials` | Optional C2PA; disabled → `content_credentials_disabled` |

Supported credential media (ship-1): neural WAV + mix-plan WAV. MIDI/MusicXML
are manifest-only.

## Env

```
# CONTENT_PROVENANCE_MAX_RECORDS_PER_PROJECT=4000
# CONTENT_PROVENANCE_MANIFEST_MAX_RECORDS=64
# CONTENT_PROVENANCE_CHAIN_MAX_DEPTH=32
# CONTENT_CREDENTIALS_ENABLED=0
# CONTENT_CREDENTIALS_FAKE=0
# CONTENT_CREDENTIALS_ROOT=   # default: <dir of PROJECT_DB_PATH>/content_credentials
```

`CONTENT_CREDENTIALS_ROOT` refuses `DATASET_ROOT` and `PROJECT_DB_PATH` via
`reject_storage_root`.

## UI

Focused surfaces inside **Neural** (selected complete render / stem set),
**Mix Assist** (head mix revision), and **Versions** (selected revision).
Opening a panel never auto-signs, never fan-out-fetches every job, and never
rewrites WAV/V2.

## Agent boundary

`ai_agents/` must not import `content_provenance_store`,
`content_credentials`, or `content_credentials_settings`.

## Acceptance (fake)

With `NEURAL_AUDIO_FAKE_MODE=1`: create project → commit revision → enqueue mix
render with `project_id` + `source_revision_id` → chain/manifest for
`neural_render/{id}` walks to that revision with `honesty.cryptographic=false`.
No stub AI ops; no real LLM/neural weights or signing keys.
