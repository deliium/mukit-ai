# Project Skill Context — aif-verify

Project-specific verification overrides for Mukit AI.

## Required checks

- Treat `.ai-factory/references/CONTEXT-GATES-AND-OWNERSHIP.md` and `.ai-factory/references/GATE-RESULT-CONTRACT.md` as required inputs for `/aif-verify` context gates and machine output contract.
- Always verify the canonical playback rule: `composition.v2` audible output must come only from `tracks[].events[]`; harmony metadata must remain non-audible.
- For harmony/reharmonization work, include acceptance evidence for bars 9-12 preview/apply safety (non-mutating preview, atomic apply, exact melody preservation under `preserve_melody_adapt_harmony`, undo restores source).
- Ensure verification reports mention whether import behavior still keeps `harmony: []` for raw MIDI/MusicXML ingestion.

## Reporting preferences

- Keep human report concise and actionable.
- Emit one final `aif-gate-result` block as the canonical machine result.
- Use `WARN` for context drift/missing optional artifacts and `ERROR` only for blockers.
