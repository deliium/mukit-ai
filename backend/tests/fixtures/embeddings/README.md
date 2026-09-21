# Embedding evaluation fixtures

`musical_quality_claim: false`

These Composition V2 snippets exercise the handcrafted profile
`symbolic.features.v1`. Cosine similarity measures **distributional /
structural affinity** (rhythm/texture/pitch-class shape), not aesthetic
quality or artist identity.

| File | Role |
|------|------|
| `similar_rhythm_a.json` | Query: quarter-note ascending arpeggio |
| `similar_rhythm_b.json` | Same rhythm, transposed pitches — expected nearest neighbor of A |
| `dense_texture_c.json` | Sustained chord pads — expected farther from A than B |
| `motif_scope.json` | Motif definition + occurrence event refs |

Expected order under cosine: **B nearer to A than C**.
