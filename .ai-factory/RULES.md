# Project Rules

> Short, actionable axioms for this project. Loaded automatically by `/aif-implement` and quality gates. Area conventions live under `rules/`; more specific rules win over these axioms.

## Rules

- Treat `composition.v2` `tracks[].events[]` as the only playable note source; never synthesize notes from harmony, markers, sections, or analysis metadata.
- Keep `composition.v1` as migration/parser input only after V2 rollout; do not add a V2-to-V1 downgrade path.
- Prefer extending `routers/` and cohesive `services/` modules over growing unrelated logic in `main.py`.
- Never log API keys, full freeform prompts, or raw MusicXML/MIDI/WAV payloads; log sanitized codes, counts, and ids only.
- Decompose unrelated shell/git steps instead of chaining them with `&&` when a mid-chain failure would obscure the failing command.
- Backend runtime verbosity must stay controllable via `LOG_LEVEL` without code changes; keep frontend diagnostics removable through existing build/runtime logging policy.
