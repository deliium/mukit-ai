"""Note and track counts for a composition mapping. Does not mutate the input."""

from app.plugin_sdk import PluginContext


class DeterministicAnalyzer:
    def analyze(self, ctx: PluginContext, composition):
        tracks = composition.get("tracks") if isinstance(composition, dict) else None
        track_rows = tracks if isinstance(tracks, list) else []
        note_count = 0
        for track in track_rows:
            events = track.get("events") if isinstance(track, dict) else None
            if not isinstance(events, list):
                continue
            note_count += sum(
                1 for event in events if isinstance(event, dict) and event.get("type", "note") == "note"
            )
        track_count = len(track_rows)
        ctx.logger.debug(
            "deterministic analyzer counts",
            extra={"note_count": note_count, "track_count": track_count},
        )
        minimum = ctx.config.get("min_notes", 0)
        if isinstance(minimum, int) and note_count < minimum:
            return {"note_count": note_count, "track_count": track_count}
        return {"note_count": note_count, "track_count": track_count}


def register(ctx: PluginContext) -> DeterministicAnalyzer:
    return DeterministicAnalyzer()
