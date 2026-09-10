# Third-party notices

This file separates **application package licenses** from **audio content licenses**.

> **Project license (owner note):** Root `LICENSE` currently ships CC0 1.0 text while `README.md` historically claimed MIT. Resolve that inconsistency separately; do not treat either claim as settled based on this notices file alone.

## Application packages

Runtime and build dependencies (FastAPI, React, Tone.js, music21, etc.) are declared in:

- `backend/requirements*.txt` / project Python environment
- `frontend/package.json`

Consult each package’s SPDX license as published by its upstream maintainers. Tone.js is used for browser transport, synthesis, sampling, and effects; it is not a sound library.

## Audio content

### Server WAV export (FluidSynth)

Docker/local WAV export may use the FluidR3 GM SoundFont (`fluid-soundfont-gm`). That SoundFont is **server-side only** and is **not** the browser instrument pack. Follow the FluidR3 / package license distributed by your OS or container image.

### Browser playback packs

| Pack | Location | License / provenance |
|------|----------|----------------------|
| `dev-tone-fixtures-v1` | `frontend/public/audio/dev-tone-fixtures-v1/` | Project-owned synthetic WAV fixtures generated for tests. See `provenance/NOTICE.md` and digests in `manifest.json`. |
| `core-acoustic-v1` | *(not shipped until licensed)* | Must include exact upstream license text, attribution, conversion records, content hashes, and redistribution approval before release. |

Browser packs must remain same-origin (or explicitly allowlisted with integrity). Do not conflate browser samples with FluidR3/FluidSynth assets.

## Release gate

Every shipped binary sample must have:

1. Recorded digests in the pack manifest
2. Upstream license text beside the pack
3. Maintainer redistribution approval

Omit any unverified sample.
