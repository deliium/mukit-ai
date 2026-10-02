# Derived material dependency graph

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. The stored row is `musical.dependency.edge.v1`. The assembled read model is `musical.dependency.graph.v1`. The impact read is `musical.dependency.impact.v1`. The offer is `musical.dependency.update_offer.v1`. None of these is a score.

## Terminology

**Upstream** is the material an edge depends on. **Downstream** is the derived asset.

**Fresh** means the recorded upstream fingerprint still matches the live upstream. **Stale** means that fingerprint differs. **Missing** means the upstream target cannot be resolved. **Downstream of stale** means this edge's own fingerprint still matches, and a walk reaches it from a stale edge.

A **targeted update** is an offer for one edge, or an edge-row refresh. It is not a batch rewrite of notes, WAVs, or LLM output.

**Theme**, **variant**, and **usage** keep the meanings in [Musical universe](musical-universe.md). A variation label on a `variation_of` edge is the variant label, such as Exploration variation. Film-score `hit_points` are not graph nodes.

## Six dependency types

| Type | Upstream | Downstream |
|------|----------|------------|
| `motif_derived_from` | motif occurrence or theme | motif occurrence |
| `arrangement_of` | revision or motif occurrence | revision |
| `variation_of` | theme or revision | motif occurrence or revision |
| `rendered_from` | project | neural render or stem set |
| `transcribed_from` | audio asset | project |
| `reference_conditioned_by` | project | a different project |

Edges store fingerprints and ids. They do not store pitches, event arrays, relative cells, or WAV bytes. A payload that contains `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, or `composition_json` is `embedded_note_material`.

A new edge whose upstream already reaches the new downstream is `dependency_cycle`. The stored graph is a directed acyclic graph. Theme A can have several variations. A variation cannot become the parent of Theme A.

## Capture

Writers record the edge on the same SQLite connection as their own commit:

- Universe reuse records one `variation_of` from the theme to the destination motif.
- A motif apply, an arrangement apply, and a `vary_section` development apply record their edges inside history commit and apply-as-branch.
- A completed neural mix job or stem set records `rendered_from`. A second completion of the same stem set updates that edge.
- Recovery bind records `transcribed_from` with sha256 of the source payload.
- A revision whose generation parameters name another project records `reference_conditioned_by`.

There is no client route that inserts an edge.

## Impact

An upstream change does not rewrite dependents. `GET /musical-universes/{universe_id}/themes/{theme_id}/dependents` computes `fresh`, `stale`, `missing`, and `downstream_of_stale` from live fingerprints.

`arrangement_of` and a revision-rooted `variation_of` stay `fresh` while that revision row exists.

`POST /musical-dependency/edges/{edge_id}/accept-current` and `record-refresh` update the edge fingerprint only. `record-refresh` returns `dependency_refresh_conflict` when the observed downstream hash does not match. The reuse button remains the only note writer for a theme variation. The neural route remains the only render writer.

## Universe tab

The Universe tab draws the graph in an SVG under the theme list. Opening the tab loads the graph. It does not rewrite notes, start a render, or call reuse. Status `stale` shows **Out of date**. Status `downstream_of_stale` shows **May need regeneration**. **Review update** selects the existing reuse form. **Accept current** refreshes one edge and reloads the graph.

`ai_agents/` does not import `musical_dependency_store`.
