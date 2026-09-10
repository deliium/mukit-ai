# Dev tone fixtures pack (`dev-tone-fixtures-v1`)

These WAV files are **project-generated synthetic tones** for deterministic browser
playback tests and offline fallback exercises. They are **not** third-party
acoustic recordings and must not be described as VCSL/CC0 sample content.

## Production pack gate

A redistributable `core-acoustic-v1` piano/bass/strings pack may replace this
pack only after each sample records:

- source URL and creator
- exact license text
- source hash, conversion history, output hash
- attribution obligations
- redistribution approval

Until that gate passes, browser playback uses these fixtures when loaded, or
deterministic Tone synth presets when no pack is installed / a profile fails.

## Technology note

Sampled voices use `Tone.Sampler` (not `smplr`) so scheduling stays on the
existing Tone.Transport absolute-seconds path.
