# Musical universe

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. The shared document is `musical.universe.v1`. It is not a score.

## Terminology

A **project group** is the set of `musical_universe_members` rows for one universe. There is no folder table.

An **entity** is a story identity. Its `kind` is `character`, `location`, `faction`, `concept`, `relationship`, or `narrative_theme`. A **narrative theme** is that entity kind, not a musical statement.

A **theme** is a musical statement, such as Theme A, bound to one canonical motif occurrence. A **variant** is one declared transformation of that theme. A **usage** is one committed placement of a variant into a member project.

**Canonical material** is `tracks[].events[]` on a member `composition.v2`, addressed by `project_id`, `motif_id`, and `occurrence_id`.

**Mechanical reuse** uses `repeat`, `transpose`, `inversion`, `augmentation`, `diminution`, or `sequence`. Creative motif operations stay on `POST /motifs/apply`.

Film-score `hit_points` stay cues on the picture document. They are not universe entities. Film-score `motif_ids` still mean motifs that already exist on that one score. Universe preview does not fetch motifs from other projects.

## References

The universe stores the identity, the variant, and the usage. It does not store pitches, event arrays, or relative cells. Bind checks motif, harmony, and track references against member compositions. A body or command that contains `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, or `composition_json` is rejected with `embedded_note_material`.

Creating a theme does not copy notes. The theme keeps a source fingerprint so a later reuse can warn when the source score has drifted.

A motif definition may carry `musical_universe_id`, `theme_id`, and `variant_id` together. Existing motifs omit all three and still validate.

## Membership

`musical_universes` holds the document. `musical_universe_members` uses `project_id` as its primary key, so one project belongs to at most one universe. Several projects may name the same universe.

Deleting a project removes that membership and leaves the universe and the other projects. Deleting the universe removes memberships and leaves the scores.

## Reuse

`POST /musical-universes/{universe_id}/themes/{theme_id}/reuse` loads both stored scores, realizes one mechanical transform, and commits the destination score together with the usage row on one SQLite connection. The request carries destination branch fields and `expected_universe_revision`. It does not carry a composition body.

The destination notes land in `tracks[].events[]`. A cross-project placement creates a motif definition whose only occurrence is `original` for that score, and pins the universe, theme, and variant. The variant and the usage store the operation. Same-project reuse appends a related occurrence beside the original.

A branch mismatch returns `universe_destination_conflict`. A universe revision mismatch returns `musical_universe_conflict`. Either miss leaves the destination score and the usage unwritten.

The Universe tab lists entities, Theme A, variants, and usage, then asks for an explicit reuse into a member project. Opening the tab loads the stored universe. It does not write notes. Reuse stays disabled until the open project is saved.

Soundtrack / production **asset packs** create several member projects from one franchise brief and bind Theme A automatically on generate. See [Asset packs](asset-packs.md).

## See also

- [Asset packs](asset-packs.md) — brief → AssetPackPlan → multi-project generate
- [Derived material graph](derived-material-graph.md) — theme reuse edges and impact
- [Composer profiles](composer-profiles.md) — soft conditioning across pack slots

