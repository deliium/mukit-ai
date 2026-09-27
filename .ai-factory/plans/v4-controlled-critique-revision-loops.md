# Implementation Plan: Controlled AI Critique → Revision → Re-evaluation Loops

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: turn the shipped spine `max_revisions` knob + Critic Evaluation Engine into a **bounded, inspectable critique → RevisionPlan → targeted agents → CompositionPatch → validate → re-critique** loop with explicit pass modes, stopping conditions, cancellation, cost accounting, and failure-safe last-valid candidates — **without** unbounded self-reflection, auto-commit on approve, inventing `composition.v4`, or regressing Analysis / workspace / `multi-agent-apply` CAS
- Parent plans:
  - `.ai-factory/plans/v4-multi-agent-music-architecture.md` (spine + `max_revisions` default 0; revise re-entry at harmony; explicitly out-of-scoped autonomous Critic→auto-repair commit loops)
  - `.ai-factory/plans/v4-music-critic-evaluation-engine.md` (**shipped** — structured findings, strata policy, climax check, `POST /critique/evaluate`, Critique UI)
  - `.ai-factory/plans/v4-shared-musical-workspace-artifact-graph.md` (**shipped** — typed promote for critique / revision_plan roles)

## Roadmap Linkage
Milestone: "V4 multi-agent music architecture"
Rationale: Parent milestone shipped registry/spine/workspace/critic evaluation; this follow-on closes the remaining product gap — **controlled automatic musical improvement** with hard pass caps, stopping conditions, targeted patches, inspectable per-pass history, and compare of initial vs revised candidates — while keeping Apply as the only durable mutation path.

## Goal

Allow the system to improve a generated composition automatically through a **bounded** Critic → revise → re-evaluate loop, without uncontrolled infinite agent loops.

Ship:

1. Explicit revision-pass modes: **Fast** (max 1), **Balanced** (max 2), **Thorough** (max 3) — never unbounded.
2. Stopping conditions: hard requirements satisfied · critique improvement below threshold · maximum passes reached · resource/token/time budget exhausted · user cancellation.
3. Revision targeting of identified ranges/tracks where practical; preserve already-good material.
4. Every revision pass emits inspectable artifacts: critique · revision plan · patch · validation result.
5. Cost/resource accounting when provider usage is available.
6. Cancellation support that leaves the last valid candidate intact.
7. Failed revision must not destroy the last valid candidate.
8. User-visible compare of initial generation vs revised generation(s).

```text
Composition candidate (working_draft)
  → Critic (EvaluationEngine + optional model)
  → if revise + budget remaining:
        RevisionPlan (targets + ranges/tracks + stop criteria)
        → targeted agent(s) only
        → CompositionPatch + progressive realize
        → validation (integrity + optional constraints)
        → new candidate revision (or keep last_valid on failure)
        → Critic again
  → stop: approve | hard_ok | delta_below_threshold | max_passes | budget | cancel | exhausted
  → session revision_history[] (inspectable) + final candidate
  → Apply still only via multi-agent-apply CAS
```

**Terminology lock:** Loop = **session preview controller**, not a durable commit loop. Critic still never mutates V2. Canonical playable schema stays **`composition.v2`**. `agent.critique.v1` / `agent.revision_plan.v1` / `agent.composition_patch.v1` remain non-playable artifacts. Unbounded “reflect until perfect” is forbidden. Default product posture remains `revision_mode=off` / `max_passes=0` unless the user opts into Fast/Balanced/Thorough.

## Approach Evaluation (locked)

### Part A — Where the loop controller lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only bump `max_revisions` + keep full-spine re-entry** | Tiny diff | No stopping deltas, no pass artifacts, no targeted ranges, no last_valid, no cancel/cost | **Insufficient** |
| **B. Inflate CriticAgent to self-call revise agents** | Localized | Critic must not mutate; couples judgment to realize; untestable HTTP reuse | **Reject** |
| **C. New `ai_agents/revision_loop.py` controller + thin workflow/API wiring** | Matches spine purity; unit-testable; keeps Critic read-only | New module | **Accepted** |
| **D. New FastAPI microservice / background job queue** | Isolation | Contradicts monolith / V4 in-process agents; harder cancel UX | **Reject** |
| **E. Client-only loop calling `/critique/evaluate` + agents** | Easy FE | Untrusted stop policy; racey; duplicates backend accounting | **Reject** as sole control plane |

### Part B — Pass limits / modes

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Raw `max_revisions` only (0–8)** | Already shipped | Users can set high caps; no named product modes | **Keep as escape hatch** under modes |
| **B. Named modes Fast/Balanced/Thorough → hard caps 1/2/3** | Matches AC; prevents accidental unbounded | Need clear mapping + docs | **Accepted** |
| **C. Soft “until approve” with no hard cap** | “Quality” | Violates req #2 | **Reject** |
| **D. Mode caps + absolute hard ceiling (≤3 product; ≤8 API escape)** | Defense in depth | Slight API complexity | **Accepted** — product modes clamp ≤3; raw API still ≤8 but docs warn; UI never exposes >Thorough |

### Part C — Stopping conditions

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only Critic approve OR max_revisions exhausted** | Status quo | Misses delta/budget/cancel/hard-ok | **Insufficient** |
| **B. Multi-condition stop policy with stable reason codes** | Meets req #3; testable | Needs metric digests | **Accepted** |
| **C. LLM decides when to stop** | Flexible | Unbounded / non-deterministic; taste≠correctness | **Reject** |

**Locked stop reasons** (`revision_stop_reason`):

| Code | Meaning |
|------|---------|
| `critic_approve` | Critic recommendation `approve` |
| `hard_requirements_satisfied` | No hard_constraint errors remain (even if stylistic/subjective remain) when policy so configured |
| `improvement_below_threshold` | Critique score / hard+technical finding count delta vs prior pass &lt; threshold |
| `max_passes_reached` | Completed allowed passes without approve (distinct from exhausted mid-revise) |
| `resource_budget_exhausted` | Token/time/cost class budget hit |
| `cancelled` | User / disconnect cancellation |
| `revise_exhausted` | Critic still `revise` after last allowed pass (maps existing `workflow_revise_exhausted`) |
| `validation_failed_kept_last_valid` | Patch/validation failed; candidate rolled back; loop stops or continues per policy — **never** keeps invalid draft |

### Part D — Targeting ranges / preserving good material

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always re-run full harmony→melody→arrangement spine** | Simple (today) | Rewrites good sections | **Reject** as sole path |
| **B. RevisionPlan carries finding-derived `affected_range` / `affected_tracks` + agent target list; realize via region/motif/reharm/arrangement scoped ops** | Meets req #4–5 | Needs agent selection + preserve checks | **Accepted** |
| **C. Always regenerate full composition** | Easy | Destroys AC “unchanged sections intact” | **Reject** |

### Part E — Per-pass inspectable artifacts

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Flat `artifact_log` only** | Already exists | Hard to group by pass; compare UX weak | **Insufficient** |
| **B. `revision_pass_record.v1` session envelope linking critique / plan / patch / validation per pass_index** | Clear history; promote-friendly | New content type or workflow DTO | **Accepted** (session DTO first; optional workspace role later) |
| **C. Persist every pass as project revision automatically** | Strong history | Auto-commits; violates preview-first | **Reject** |

### Part F — Cost accounting & cancellation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Ignore cost / cancel** | Fast | Fails req #7–8 | **Reject** |
| **B. Accumulate optional usage when adapters expose it; wall-clock + pass budgets; cooperative cancel checks between agents** | Practical; CI with fake zeros | Providers often omit usage | **Accepted** — record `available` / `unavailable`; never invent dollars |
| **C. Hard kill mid-agent with partial draft apply** | Responsive | Can corrupt last_valid | **Reject** — cancel between agents / after failed validate; always restore `last_valid_candidate` |

### Part G — Compare initial vs revised

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only final candidate** | Status quo | Fails AC compare | **Reject** |
| **B. Session `revision_history` with fingerprints + reuse Versions compare / audition patterns** | Matches Develop/Versions UX | FE work | **Accepted** |
| **C. Auto-create named branches per pass** | Durable compare | Noise; auto-mutate history | **Defer** — optional Apply-as-branch remains user action |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Spine workflow | `ai_agents/workflow.py` `run_spine_workflow(..., max_revisions=0)`; revise re-enters at `harmony` | Base loop skeleton; replace blind full re-entry with targeted revision controller |
| Critic + EvaluationEngine | `CriticAgent` → `composition_critique.evaluate_composition`; findings + strata + climax | Source of revise/approve + range/track evidence |
| `agent.revision_plan.v1` | Thin: `revise_targets`, `stop_criteria`, comment | Extend with ranges/tracks/pass metadata (backward-compatible) |
| `agent.composition_patch.v1` | Realize-service ref + op_refs; forbids embedded score | Emit per targeted realize; never embed V2 |
| Progressive realize | `progressive_realize.py` trusted realize wrappers + integrity validate | Sole draft mutation path |
| Workspace roles | `critique`, `revision_plan` promote-on-Apply | Keep; optional pass-record promote later |
| FE MultiAgentPanel | Preview/Apply; `max_revisions: 0` hardcoded | Modes + history + cancel + compare |
| Versions compare/audition | `compositionVersionComparison`, `playbackSource`, ProjectVersionsPanel | Pattern for session candidate compare |
| Fake agents / `LLM_FAKE_MODE` | Deterministic spine CI | Fake revise loops with scripted Critic recommendations |
| Errors | `WorkflowReviseExhaustedError` / `workflow_revise_exhausted` | Keep; add cancel / budget / validation-kept codes |
| Resource hints | `AgentResourceHints.estimated_cost_class` | Seed accounting; extend with usage accumulators |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Named revision modes Fast/Balanced/Thorough | Map → max_passes 1/2/3; UI + API |
| Stop policy beyond approve/exhausted | Hard-ok, improvement delta, budget, cancel |
| Targeted revise from findings | RevisionPlan ranges/tracks; agent subset selection |
| Preserve-good-material checks | Fingerprint outside affected ranges; fail closed |
| Per-pass record DTO | critique + plan + patch + validation + candidate fingerprint |
| last_valid_candidate semantics | On failed realize/validate/cancel mid-pass |
| Cost/usage accumulator | Optional tokens/latency; unavailable when provider silent |
| Cancellation | Cooperative between agents; HTTP disconnect / FE abort |
| Session revision history + FE compare | Initial vs pass N audition/compare |
| Deterministic mocked loop tests | Stopping, failure, cancel, climax dual-pass AC |

### Coupling risks to avoid

1. Unbounded loops (“while revise and quality low”).
2. Auto-commit / auto-Apply on Critic approve.
3. Critic or evaluation engine mutating V2.
4. Full-spine rewrite of unaffected bars/tracks when ranges are known.
5. Keeping an invalid working draft after failed validation.
6. Treating subjective/stylistic findings as hard stop blockers by default.
7. `ai_agents/` importing workspace / SQLite.
8. Logging full critiques, prompts, event arrays, or usage secrets at INFO.
9. Inventing `composition.v4` or embedding playable scores in patches.
10. Growing loop logic into `main.py` — prefer `ai_agents/` + `routers/ai_agents.py` + thin services helpers.

## Scope And Decisions

### In scope
- Revision modes + hard pass caps.
- `revision_loop` controller with multi-condition stop policy.
- Extended RevisionPlan targeting (ranges/tracks + agent ids).
- Targeted agent invocation + CompositionPatch + validation + last_valid rollback.
- Per-pass inspectable session records + workflow response history.
- Optional cost/usage accounting + wall-clock budget.
- Cancellation (API + FE).
- FE MultiAgentPanel: mode select, cancel, pass history, compare/audition initial vs revised.
- Deterministic tests (stopping, failure, cancel, mocked multi-pass including weak-climax dual revise).
- Docs updates (`docs/multi-agent.md`, critique cross-links, AGENTS.md entry if needed).

### Out of scope
- Auto-commit durable revisions per pass.
- Unbounded or LLM-decided loop length.
- Expanding to full nine-agent revise graph (may select among spine agents only).
- Training new quality models.
- Changing FluidSynth / Tone.js / export fidelity.
- Replacing EvaluationEngine strata policy (reuse shipped Critic).
- Playwright mega-journey (API + unit + store tests suffice; optional e2e later).
- Billing/currency conversion — only provider usage units + cost_class hints.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP POST /ai/agents/workflows/preview
        ↓  revision_mode | max_passes | budgets | cancel token
ai_agents/revision_loop.py   (controller; no SQLite)
        ↓
├─ CriticAgent / evaluate_composition (read-only)
├─ RevisionPlan builder (from findings)
├─ targeted spine agents (harmony / melody_motif / arrangement only as needed)
├─ progressive_realize + composition_validator
└─ stop policy + usage accumulator
        ↓
WorkflowPreviewResult + revision_history[] + last_valid_candidate
        ↓
[Apply only] multi-agent-apply CAS (existing)
```

**2. Mode mapping**

| Mode | `max_passes` | Default budgets (tunable constants) |
|------|--------------|-------------------------------------|
| `off` | 0 | n/a (single Critic at end of spine only) |
| `fast` | 1 | tight time/token |
| `balanced` | 2 | medium |
| `thorough` | 3 | larger but still hard-capped |

`max_revisions` request field remains for compatibility but **must be clamped** by mode when mode is set; product UI only exposes modes.

**3. Pass index semantics**

- Pass 0 = initial spine generation ending in first Critic (candidate_0 = initial generation).
- Pass k (1..max_passes) = one revise cycle (plan → agents → patch → validate → Critic).
- AC “revised twice” = `thorough` or `balanced` with Critic still revise after pass 1 → complete pass 2.

**4. RevisionPlan extension (backward-compatible)**

Keep existing fields; add optional:

- `pass_index: int`
- `affected_ranges: list[{start_bar,end_bar}]` (from findings; max small)
- `affected_tracks: list[str]`
- `target_agent_ids: list[str]` (subset of spine revise agents)
- `preserve_outside_targets: bool = true`
- `stop_criteria` already exists — populate with policy codes

**5. Targeting policy**

- Union finding `affected_range` / `affected_tracks` for hard+technical findings driving revise (and optional technical when `revise_on_technical`).
- Map reason codes → default agents: harmony codes → `harmony`; melody/motif/density/climax → `melody_motif` and/or `arrangement`; unknown → conservative `{harmony, melody_motif}` but still scoped by ranges when present.
- Progressive realize must prefer existing region/motif/reharm/arrangement scoped paths; assert unchanged fingerprint for events outside targets when `preserve_outside_targets` (tests).

**6. last_valid_candidate**

- Snapshot fingerprintable V2 after every successful validate.
- On realize/validate failure: restore last_valid into working_draft; record validation artifact `status=failed`; stop or continue based on `on_failure=keep_last_valid_and_stop` (default stop).
- On cancel: restore last_valid if mid-pass draft dirty; set stop_reason `cancelled`.

**7. Improvement threshold**

- Define bounded critique digest score: e.g. `100*hard_errors + 10*technical_warnings` (stylistic/subjective excluded by default).
- If `prev_score - new_score < improvement_min_delta` (default 1) after a completed revise pass → stop `improvement_below_threshold`.
- Configurable constant in one settings module (`revision_loop_settings.py`).

**8. Cost / resource accounting**

- Accumulator fields: `latency_ms_total`, optional `prompt_tokens`, `completion_tokens`, `estimated_cost_class_peak`, `usage_status: available|partial|unavailable`.
- Pull from agent provenance / runtime adapters when present; fake mode returns zeros with `available`.
- Budgets: `max_wall_ms`, optional `max_prompt_tokens`; hit → `resource_budget_exhausted`.

**9. Cancellation**

- Request body/header or workflow param `cancel_check` / use Starlette `Request.is_disconnected` polled between agents and between passes.
- FE: AbortController on preview; store `revisionLoopStatus: running|cancelled|…`.
- Never leave partial invalid draft.

**10. Logging**

- INFO: workflow_id, revision_mode, pass_index, stop_reason, max_passes, revise_count, duration_ms, usage_status, candidate fingerprint prefixes, targeted agent ids.
- DEBUG: finding codes, affected bars, score deltas, validation status.
- Never INFO: full findings prose, prompts, keys, event arrays, full pass payloads.

**11. Frontend**

- MultiAgentPanel: mode selector (Off/Fast/Balanced/Thorough), Cancel, pass list with critique recommendation + fingerprint, Compare/Audition initial vs selected pass (reuse playback source exclusivity).
- Do not auto-Apply.

**12. Acceptance criterion (locked)**

Fixture: composition with weak climax (`climax_lacks_contrast` or hard/technical driver configured for revise in test via `revise_on_technical` / injected hard finding — **product stylistic climax alone does not force revise**; for AC use a deterministic FakeCritic / test hook that recommends revise twice on weak climax while preserve checks ensure unaffected sections’ event fingerprints stay intact). History shows pass 0 + pass 1 + pass 2 candidates inspectable.

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat(ai-agents): add revision loop schemas, modes, and stop policy`
- **Commit 2** (after tasks 4–7): `feat(ai-agents): implement targeted critique revision loop with last_valid and budgets`
- **Commit 3** (after tasks 8–10): `feat(api): expose revision modes, cancellation, and pass history on workflow preview`
- **Commit 4** (after tasks 11–13): `feat(frontend): multi-agent revision modes, cancel, and candidate compare`
- **Commit 5** (after tasks 14–16): `test(docs): revision loop stopping/cancel tests and multi-agent docs`

## Tasks

### Phase 1: Contracts & policy

- [x] Task 1: Revision loop schemas and modes
  Deliverable: Pydantic DTOs for `RevisionMode` (`off|fast|balanced|thorough`), `RevisionStopReason`, `RevisionPassRecordV1` (pass_index, critique artifact ref/payload digest, revision_plan, composition_patch, validation_result, candidate_fingerprint, score_digest, usage_delta, stop_eligible_reasons), `RevisionLoopUsage`, extended `AgentRevisionPlanV1` optional targeting fields (backward-compatible). Validation caps; reject embedded playable scores. Unit tests for mode→max_passes mapping and schema forbid extras.
  LOGGING: DEBUG schema reject codes only; no payload dumps.
  Files: `backend/app/ai_agents/artifact_schemas.py` and/or new `revision_loop_schemas.py`, `backend/app/ai_agents/schemas.py` (workflow request fields), `backend/tests/test_revision_loop_schemas.py`.
  Depends on: none.

- [x] Task 2: Revision loop settings + stop policy
  Deliverable: `revision_loop_settings.py` (env-tunable thresholds: improvement_min_delta, max_wall_ms per mode, token caps, on_failure policy). Pure function `evaluate_stop_conditions(...)` returning stop_reason or `None` to continue. Unit tests for each stop code including hard_requirements_satisfied vs stylistic-only remaining.
  LOGGING: INFO stop_reason + pass_index + score_before/after (numeric only).
  Files: `backend/app/revision_loop_settings.py` (new), `backend/app/ai_agents/revision_stop_policy.py` (new), tests `test_revision_stop_policy.py`.
  Depends on: Task 1.

- [x] Task 3: RevisionPlan builder from critique findings
  Deliverable: Pure builder mapping Critic findings → `AgentRevisionPlanV1` with revise_targets / target_agent_ids / affected_ranges / affected_tracks / stop_criteria; ignore subjective-only; include technical only when policy says so. Tests with multi-finding fixtures (climax bars + track ids).
  LOGGING: INFO target_agent_ids + range count; DEBUG bar ranges.
  Files: `backend/app/ai_agents/revision_plan_builder.py` (new), extend `typed_emit.revision_plan_payload`, tests.
  Depends on: Task 1.

### Phase 2: Controller core

- [x] Task 4: `revision_loop` controller skeleton
  Deliverable: `ai_agents/revision_loop.py` orchestrating: initial spine (or accept pre-built candidate) → Critic → while revise and not stopped → build plan → run targeted agents → realize patches → validate → snapshot last_valid → Critic. Hard-cap passes via mode. Never writes SQLite. Integrate with / replace revise branch in `workflow.py` without breaking `max_revisions=0` default behavior.
  LOGGING: INFO mode, pass_index, agents run, duration_ms; DEBUG sequence.
  Files: `backend/app/ai_agents/revision_loop.py`, `workflow.py`, tests `test_revision_loop_controller.py` (approve path, max_passes).
  Depends on: Task 1, Task 2, Task 3.

- [x] Task 5: Targeted realize + preserve-outside-targets
  Deliverable: Wire targeted agents to emit `agent.composition_patch.v1` and update working_draft only via progressive_realize trusted services; when ranges/tracks present, assert or compute that events outside targets remain byte/fingerprint-stable (helper). If agent cannot honor range, fail validation rather than silently rewriting whole score.
  LOGGING: INFO realize_service + preserve_ok; WARN on preserve violation.
  Files: `progressive_realize.py` (helpers as needed), harmony/melody/arrangement agents (selection/params), `services/composition_revision_preserve.py` (optional helper), tests.
  Depends on: Task 3, Task 4.

- [x] Task 6: last_valid candidate + validation failure behavior
  Deliverable: Maintain `last_valid_candidate` + fingerprint; on patch/validate failure restore it; append pass record with validation failed; default stop `validation_failed_kept_last_valid`. Tests prove invalid draft never returned as `candidate`.
  LOGGING: WARN validation failure codes; INFO restored fingerprint prefix.
  Files: `revision_loop.py`, tests `test_revision_loop_failure_rollback.py`.
  Depends on: Task 4, Task 5.

- [x] Task 7: Usage accounting + wall/token budgets
  Deliverable: Accumulator updated from agent stages/provenance when tokens/latency exist; fake agents contribute deterministic zeros/`available`; budget checks invoke stop `resource_budget_exhausted`. Tests with injected usage and tiny budgets.
  LOGGING: INFO usage_status + latency_ms_total + token totals when available.
  Files: `revision_loop.py`, optional `revision_usage.py`, fake_agents hooks, tests.
  Depends on: Task 4.

### Phase 3: API, cancellation, FE

- [x] Task 8: Workflow API — modes, history, stop_reason
  Deliverable: Extend `POST /ai/agents/workflows/preview` body with `revision_mode`, optional budgets, keep compatible `max_revisions`; response includes `revision_history`, `stop_reason`, `last_valid_fingerprint`, `usage`. Map new errors to HTTP. Default mode `off` preserves today’s behavior.
  LOGGING: INFO route mode + stop_reason + pass_count; never log full history payloads at INFO.
  Files: `backend/app/routers/ai_agents.py`, workflow result DTO, `backend/tests/test_ai_agents_routes.py`, `test_revision_loop_routes.py`.
  Depends on: Task 4, Task 6, Task 7.

- [x] Task 9: Cancellation support
  Deliverable: Cooperative cancel between passes/agents; HTTP disconnect and/or explicit cancel flag; stop_reason `cancelled`; last_valid preserved. Unit tests simulate cancel after pass 1 mid-pass.
  LOGGING: INFO cancelled at pass_index.
  Files: `revision_loop.py`, `routers/ai_agents.py`, tests `test_revision_loop_cancel.py`.
  Depends on: Task 4, Task 6, Task 8.

- [x] Task 10: Artifact promote / role map compatibility
  Deliverable: Ensure final Apply still promotes critique (+ optional revision_plan); session pass records do not require new Alembic. If pass records are session-only, document that; optional include latest revision_plan in role map. Architecture gate: `ai_agents/` still cannot import workspace.
  LOGGING: INFO promote role keys unchanged.
  Files: `artifact_role_map.py` (if needed), acceptance tests extend.
  Depends on: Task 1, Task 8.

- [x] Task 11: Frontend API + store for revision loop
  Deliverable: `previewMultiAgentWorkflow` sends `revision_mode`; store holds `revisionHistory`, `revisionStopReason`, `revisionLoopStatus`; AbortController cancel; discard clears history. Unit tests for normalize helpers.
  LOGGING: FE debug under `VITE_LOG_LEVEL`; no full artifact dumps at info.
  Files: `frontend/src/api/musicApi.js`, `musicStore.js`, utils + unit tests.
  Depends on: Task 8, Task 9.

- [x] Task 12: MultiAgentPanel UX — modes, cancel, compare
  Deliverable: Mode selector Off/Fast/Balanced/Thorough; Cancel while running; list passes (initial + revisions) with recommendation + fingerprint prefix; Compare/Audition selected vs working using existing playback-source exclusivity patterns (mirror development/version audition). Apply still fingerprint-gated single candidate (final/last_valid).
  LOGGING: debug mode changes / audition toggles.
  Files: `MultiAgentPanel.jsx`, optional small compare helper, CSS consistent with panel.
  Depends on: Task 11.

### Phase 4: Acceptance, hardening, docs

- [x] Task 13: Deterministic mocked multi-pass loop tests
  Deliverable: FakeCritic / fake agents scripted to: (a) approve immediately; (b) revise exactly twice then approve; (c) revise forever → stop max_passes / revise_exhausted; (d) improvement_below_threshold; (e) validation failure keeps last_valid; (f) cancel. Assert per-pass artifacts present (critique, plan, patch, validation).
  LOGGING: caplog assertions for stop_reason / pass_index.
  Files: `backend/tests/test_revision_loop_mocked.py`, fake agent hooks.
  Depends on: Task 4–9.

- [x] Task 14: Weak-climax dual-revision acceptance test
  Deliverable: Fixture composition with weak climax; loop mode Balanced/Thorough with test policy forcing revise from climax finding (test-only revise driver or injected hard/technical twin — document clearly so product stylistic policy stays intact); assert two automatic revision passes; unchanged sections’ events remain intact (preserve helper); all iterations visible in `revision_history`.
  LOGGING: INFO pass fingerprints prefixes.
  Files: `backend/tests/test_revision_loop_climax_acceptance.py`, fixture JSON if needed.
  Depends on: Task 5, Task 13.

- [x] Task 15: Architecture / logging safety gates
  Deliverable: Tests: no unbounded while without cap; candidate never invalid; Critic path still non-mutating; subjective≠auto-revise default; INFO logs omit prompts/full findings; `ai_agents/` import gate green.
  LOGGING: as above.
  Files: extend `test_ai_agents_architecture.py`, revision loop tests.
  Depends on: Task 13, Task 14.

- [x] Task 16: Documentation checkpoint
  Deliverable: Update `docs/multi-agent.md` with revision modes, stop reasons, targeting/preserve rules, cancellation, usage accounting, compare UX; cross-link `docs/composition-critique.md` (Critic still read-only; loop is workflow controller). Update `AGENTS.md` entry points if new modules added. `.env.example` for new `REVISION_LOOP_*` knobs only if introduced.
  LOGGING: document INFO/DEBUG policy accurately.
  Files: `docs/multi-agent.md`, `docs/composition-critique.md`, `AGENTS.md`, optional `.env.example`.
  Depends on: Task 12, Task 14, Task 15.

## Implementation notes for `/aif-implement`

- Prefer extending `workflow.py` call sites to delegate into `revision_loop.py` rather than growing CriticAgent.
- Keep default `revision_mode=off` / `max_revisions=0` so existing CI spine tests stay green.
- Reuse EvaluationEngine recommendation policy — do **not** make stylistic climax auto-revise in production; use explicit test hooks for the dual-pass AC.
- Preserve helpers should compare event subsets by track_id + tick/pitch identity outside ranges — avoid full-score equality when targeted edits occurred.
- Frontend: follow existing MultiAgent / Develop audition patterns; do not add useMemo/useCallback noise unless repo pattern requires.
- Docs policy `yes` → mandatory `/aif-docs` checkpoint at implement completion.

## Non-goals reminder

- No infinite self-reflection.
- No auto-Apply / auto-commit of revised candidates.
- No `composition.v4`.
- No replacing Analysis tab with loop UI.
- No agents writing SQLite.

## Definition of Done

- Fast/Balanced/Thorough modes enforce max 1/2/3 passes; no unbounded path in product UI/API defaults.
- Stop reasons implemented and tested: approve, hard_ok, improvement threshold, max passes, budget, cancel, validation-failed-kept-last-valid, revise_exhausted.
- Targeted revisions preserve unaffected material in tests.
- Each pass exposes critique, revision plan, patch, validation in session history.
- Usage accounting present with `available|partial|unavailable`.
- Cancellation leaves last valid candidate.
- FE can compare initial vs revised generations.
- Weak-climax dual-revision acceptance test green.
- Docs updated; architecture import gates green.
