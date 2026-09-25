# Mix-plan audio fixtures

`builders.py` writes short deterministic stereo tones (bass 110 Hz, piano 440 Hz, strings 220 Hz) with the stdlib `wave` module. Tests use these bytes, or the fake neural stem WAVs, to pin sha256 before and after apply.

The tones are not a mastering reference. Fake mix bounce (`MIX_PLAN_FAKE_MODE=1`) does not claim timbral quality; it only proves a new mix path while source files stay unchanged.
