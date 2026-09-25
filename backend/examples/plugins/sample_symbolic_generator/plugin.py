"""Deterministic two-bar composition.v2. Ignores the request body."""

from app.plugin_sdk import PluginContext

REGISTER_CALLS = 0


class SampleSymbolicGenerator:
    def compose(self, ctx: PluginContext, request):
        del request
        ctx.logger.debug("sample_symbolic_generator compose")
        return {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 3840,
            "bar_count": 2,
            "harmony": [],
            "sections": [
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                }
            ],
            "tracks": [
                {
                    "id": "melody",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ],
        }


def register(ctx: PluginContext) -> SampleSymbolicGenerator:
    global REGISTER_CALLS
    REGISTER_CALLS += 1
    del ctx
    return SampleSymbolicGenerator()
