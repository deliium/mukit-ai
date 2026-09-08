# 🎵 AI Music Composer

A full-stack LLM music composer that generates and edits canonical playable `composition.v2` JSON through a LangChain/LangGraph-backed FastAPI service. Import MIDI or MusicXML into the same V2 workspace. The React frontend lets users choose a configured provider/model, edit notes on a piano roll or in JSON, render notation from backend MusicXML, and preview canonical note-event playback in the browser. V1 remains accepted as migration input.

## 🚀 Features

- **LLM JSON Composition**: Generate structured music JSON with OpenAI or DeepSeek-compatible providers
- **MIDI / MusicXML Import**: Upload `.mid`/`.midi`, `.musicxml`/`.xml`, or `.mxl` into strict `composition.v2` with session import warnings; no LLM required
- **Local Project Persistence**: Create/open/rename/duplicate/delete projects backed by SQLite; debounced autosave keeps edited compositions across Docker restarts
- **Prompt Controls**: Configure genre, mood, key, meter, tempo range, instruments, sections, complexity, duration, and freeform instructions
- **Editable JSON Workflow**: Review and edit canonical sections, tracks, harmony metadata, timing, and note events
- **Piano-Roll Editor**: Create, select, drag/transpose, resize, and delete notes on `tracks[].events[]` with snap/zoom, track focus, context tracks, and note-edit undo/redo; shares the same `editedMusicJson` as the JSON editor
- **Notation And Playback**: Render backend MusicXML with OpenSheetMusicDisplay and play exact multi-track canonical note events with Tone.js (mute/solo/volume, pause/resume, seek-to-start, piano-roll playback cursor)
- **Composition Analysis**: Deterministic `composition.analysis.v1` sidecar for tonal context, inferred harmony, phrases/density, and stable warnings over current V2 (Analysis tab; optional bounded advisory context for LLM edit/repair — not persisted, not required for import/playback)
- **Deterministic Export**: Download MusicXML, MIDI, and server-rendered WAV from the same canonical `tracks[].events[]`; export responses include projection status headers when approximations apply

## 🏗️ Architecture

- **Backend**: FastAPI with Python
- **Frontend**: React with styled-components
- **Persistence**: SQLite project store (`PROJECT_DB_PATH`) with numbered SQL migrations; Docker named volume `mukit_project_data`
- **Music Processing**: music21 library for MusicXML rendering
- **LLM Orchestration**: LangChain/LangGraph with OpenAI-compatible chat providers
- **Frontend State**: Zustand store for API status, LLM models, project browser/save status, generation output, piano-roll edit state, notation, playback transport state, per-track mute/solo/volume, and derived analysis report cache

## Prerequisites

- **Recommended (V1):** Docker + Docker Compose
- For demos/tests without API spend: `LLM_FAKE_MODE=1` (no OpenAI/DeepSeek keys required)
- For real LLM generation: one provider API key (`OPENAI_API_KEY` and/or `DEEPSEEK_API_KEY`)
- Optional host-local: Python 3.14+, Node.js 20+, npm

## First run (Docker) — production-local V1

1. Copy `.env.example` → `.env`
2. Either:
   - **Credit-free demo/tests:** set `LLM_FAKE_MODE=1` (and optionally `DEFAULT_LLM_PROVIDER=fake`), or
   - **Real providers:** set at least one of `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` (models/timeouts in `.env.example`)
3. `docker compose up --build`
4. Open **http://localhost:3000** (nginx SPA; API proxied same-origin). Backend also on **http://localhost:8888** for debugging.

Secrets stay in `.env` / Compose and are passed **only to the backend**. Frontend never receives API keys. Fake mode never opens network sockets to OpenAI/DeepSeek.

### V1 workflow

1. **Projects** → New Project (or Open), or **Import** a MIDI/MusicXML file (no LLM required)
2. Select a configured model (Fake deterministic, or a real provider) when generating or AI-editing
3. Generate 16–32 bar multi-track composition (or work from the imported V2)
4. Play (Tone.js), view notation (OSMD), edit notes on the piano roll
5. Open the **Analysis** tab for deterministic tonality/harmony/density/warnings on the current V2 (no LLM required)
6. AI region edit (select bars → instruction → Regenerate Selection)
7. Undo note edits if needed → Save
7. Export MusicXML / MIDI / WAV
8. `docker compose restart` → reopen the same project (named volume keeps SQLite)

- Named volume `mukit_project_data` persists SQLite at `/data/projects.db`. Prefer `docker compose restart` or `down` without `-v`.
- Both services use `restart: unless-stopped` and healthchecks (`GET /health` on backend; HTTP on frontend).
- Hot-reload override (optional): `docker compose -f docker-compose.yml -f compose.dev.yml up --build`
- Logging: set `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` (default `INFO`). Never expect keys/prompts/raw MusicXML/MIDI/WAV or upload bytes in logs.
- Import limits: `IMPORT_*` in `.env.example` (default upload 5 MiB). Details: [docs/import.md](docs/import.md).
- Acceptance commands: see `docs/testing.md` (pytest, Playwright, Docker persistence script).

## Installation (host-local optional)

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Optional: configure LLM providers:
```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_MODEL="deepseek-chat"
export DEFAULT_LLM_PROVIDER="openai"
export LLM_REQUEST_TIMEOUT_SECONDS="60"
export LLM_TEMPERATURE="0.7"
# Optional local SQLite path (default: backend/data/projects.db)
export PROJECT_DB_PATH="/absolute/path/to/projects.db"
```

If no provider key is configured, `/llm/models` returns an empty list and LLM generation returns `503` with a clear message.

5. Start the FastAPI server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888
```

The API will be available at `http://localhost:8888`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the Vite development server:
```bash
npm run dev
```

The frontend will be available at `http://localhost:3000` (proxies `/health`, `/ready`, `/llm`, `/export`, `/projects`, `/imports`, `/analysis` to the backend).

## 🎼 Usage

### Local projects

1. Start the backend and frontend (or `docker compose up`).
2. On the Projects home screen, create a project, open an existing one, or **Import** MIDI/MusicXML.
3. Generate or edit music, and confirm the save chip reaches **Saved**.
4. Restart with `docker compose restart` and reopen the project — edits should match.
5. Avoid `docker compose down -v` unless you intend to wipe the `mukit_project_data` volume.

Details: [docs/project-persistence.md](docs/project-persistence.md).

### Import MIDI or MusicXML

1. From Projects (or an open composer), choose Import MIDI or Import MusicXML / MXL.
2. On success the workspace installs strict `composition.v2` with regenerated notation MusicXML (never the uploaded source). Session import warnings summarize defaults, quantization, and omissions.
3. Importing into an open project asks for replace confirmation; a failed import leaves the current composition unchanged.
4. Play, edit, save, export, and AI region edit use the same canonical path as generated scores. Import does not require an LLM key.

Formats, limits, issue codes, and security: [docs/import.md](docs/import.md).

### Composition analysis

1. Open a generated or imported project with valid `composition.v2`.
2. Select the composer **Analysis** tab.
3. Choose whole-composition, a section, or the current piano-roll track; the UI posts the current edited V2 to `POST /analysis/composition`.
4. Review declared vs inferred tonality/harmony, density, phrases, and stable warning codes. Edit notes while the tab is open to see stale → debounced refresh.
5. Analysis is deterministic native Python (no LLM). Region edit / targeted generation repair may receive a bounded advisory summary only; hard constraints and canonical events remain authoritative.

Contract, scopes, and warning codes: [docs/composition-analysis.md](docs/composition-analysis.md).

### LLM JSON Composition

1. Configure `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or both on the backend.
2. Start the backend and frontend.
3. Open or create a project, then choose the provider/model in the LLM JSON Composer panel.
4. Set prompt parameters such as genre, mood, key, time signature, tempo range, instruments, sections, complexity, duration, and freeform instructions.
5. Click "Generate LLM Music JSON".
6. Edit notes on the piano roll (or in the JSON editor). Invalid edits show a client-side validation error. Piano-roll undo/redo covers note edits only. Changes autosave when a project is open.
7. Optionally Shift+drag bars on the piano roll, enter an instruction, and use **Regenerate Selection / AI Edit** to change only the selected region. Failures leave the current composition unchanged; success supports undo/redo.
8. Review notation rendered from backend MusicXML; piano-roll and AI edits refresh notation via `POST /export/musicxml/preview` after a short debounce.
9. Use Play/Stop to preview canonical note events from the generated or edited JSON; the piano-roll cursor follows playback position.
10. Export MusicXML or MIDI from the edited canonical JSON; notation preview refreshes from the exported MusicXML.

## 🔧 API Endpoints

- `GET /` - API status
- `GET /health` - Liveness probe
- `GET /ready` - Readiness (DB openable; LLM provider names/count; WAV deps booleans — no secrets)
- `GET /llm/models` - Return configured LLM provider/model options
- `POST /llm/generate-music-json` - Generate validated music JSON and derived MusicXML
- `POST /llm/edit-composition-region` - Apply a validated `replace_region` AI edit to selected bars/tracks
- `GET /projects` - List local project summaries
- `POST /projects` - Create a local project
- `GET /projects/{id}` - Open a project (migrates stored composition to `composition.v2` when needed)
- `PATCH /projects/{id}` - Rename and/or save composition + generation metadata
- `POST /projects/{id}/duplicate` - Duplicate a project
- `DELETE /projects/{id}` - Delete a project
- `POST /export/musicxml` - Render canonical composition JSON (V1 or V2 input) as a downloadable MusicXML attachment
- `POST /export/musicxml/preview` - Render MusicXML text for notation refresh without a download header
- `POST /export/midi` - Render canonical composition JSON as a downloadable Standard MIDI File attachment
- `POST /export/wav` - Render canonical composition JSON as a downloadable WAV via FluidSynth (reuses MIDI note content)
- `POST /imports/midi` - Multipart MIDI → strict `composition.v2` + regenerated MusicXML + `import_report`
- `POST /imports/musicxml` - Multipart MusicXML/MXL → same response shape (content-detected)
- `POST /analysis/composition` - Deterministic `composition.analysis.v1` sidecar for a composition/section/track scope (complete current V2 body; not persisted)

Example LLM request:

```json
{
  "selection": { "provider": "openai", "model": "gpt-4o-mini" },
  "options": { "max_retries": 1 },
  "prompt": {
    "genre": "ambient",
    "mood": "cinematic",
    "tempo_min": 80,
    "tempo_max": 120,
    "key": "C minor",
    "time_signature": "4/4",
    "instruments": ["piano", "bass", "strings"],
    "sections": [{ "type": "intro", "bars": 4 }, { "type": "verse", "bars": 8 }],
    "complexity": "moderate",
    "duration_bars": 12,
    "instructions": "Use a sparse, moody progression."
  }
}
```

`POST /llm/generate-music-json` returns canonical `composition.v2` JSON in `music`, derived `musicxml`, human-readable `warnings`, and optional structured `validation` (constraint status, errors/warnings, repair attempts, tonality, instrumentation satisfaction/duplicates, ordered repair actions). Request bodies may still send V1 compositions for edit/migration paths; responses are always V2 after normalization. Hard/soft prompt constraints: [docs/composition-v1.md](docs/composition-v1.md). V2 timeline/expression: [docs/composition-v2.md](docs/composition-v2.md).

`POST /llm/edit-composition-region` accepts an existing composition, bar/track selection, and instruction, then returns a validated `replace_region` `patch`, the applied `composition`, and preview `musicxml`. Outside-region notes and metadata stay unchanged unless the request explicitly expands scope. Invalid provider patches return `502` without mutating the input composition.

Example canonical music JSON shape returned in `music` (abbreviated; see docs for expression/timeline fields):

```json
{
  "schema_version": "composition.v2",
  "tempo": 92,
  "key": "C minor",
  "time_signature": "4/4",
  "ticks_per_quarter": 480,
  "duration_ticks": 1920,
  "bar_count": 1,
  "sections": [
    { "type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1920 }
  ],
  "tracks": [
    {
      "id": "piano-1",
      "name": "Piano",
      "instrument": "piano",
      "role": "harmony",
      "midi_program": 0,
      "channel": 1,
      "is_drum": false,
      "volume": 100,
      "pan": 0,
      "events": [
        { "type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 84 },
        { "type": "note", "pitch": "Eb4", "start_tick": 0, "duration_ticks": 480, "velocity": 80 },
        { "type": "note", "pitch": "G4", "start_tick": 0, "duration_ticks": 480, "velocity": 80 }
      ]
    }
  ],
  "harmony": [{ "bar": 1, "chord": "Cm" }]
}
```

`ticks_per_quarter`, `start_tick`, and `duration_ticks` are integer canonical timing fields measured from composition start. Polyphony is represented by multiple note events with the same `start_tick` or overlapping durations. Rests are implicit empty tick ranges. `harmony` is contextual metadata for chord symbols and analysis; canonical rendering and playback do not use harmony to invent audible notes.

## 📁 Project Structure

```
mukit-ai/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application
│   │   ├── schemas.py           # Pydantic models
│   │   ├── import_schemas.py    # Import DTOs / issue codes
│   │   ├── import_settings.py   # IMPORT_* limits
│   │   ├── analysis_schemas.py  # composition.analysis.v1 DTOs / warning codes
│   │   ├── llm_settings.py      # LLM provider environment settings
│   │   ├── routers/             # projects, imports, analysis
│   │   └── services/            # LLM, import, analysis, normalization, MusicXML, MIDI/WAV
│   ├── requirements.txt
│   └── tests/                   # Backend unit tests
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/          # Generator, analysis, piano roll, notation, playback, export controls
│   │   ├── api/musicApi.js
│   │   ├── store/               # Zustand music store (edits, undo, playback, notation, analysis)
│   │   ├── utils/               # Validation, piano-roll geometry, playback, analysis helpers
│   │   ├── App.jsx
│   │   ├── index.jsx
│   │   └── index.css
│   └── package.json
├── docs/
│   ├── composition-v2.md
│   ├── composition-analysis.md
│   ├── import.md
│   ├── composition-v1.md
│   ├── project-persistence.md
│   └── testing.md
└── README.md
```

## Documentation

| Guide | Description |
|-------|-------------|
| [Composition V2](docs/composition-v2.md) | Operational canonical contract, migration, export fidelity |
| [Composition Analysis](docs/composition-analysis.md) | Deterministic sidecar, scopes, warnings, Analysis tab |
| [MIDI / MusicXML import](docs/import.md) | Ingestion mappings, limits, issue codes, security |
| [Composition V1](docs/composition-v1.md) | V1 compatibility, staged generation, region editing |
| [Project persistence](docs/project-persistence.md) | SQLite projects, migrate-on-open, autosave |
| [Testing](docs/testing.md) | Backend/frontend tests, fixtures, acceptance scripts |
| [Codebase map](docs/CODEBASE_MAP.md) | Architecture navigation map |

## ✅ Testing

Backend tests:

```bash
cd backend
../.venv/bin/python -m pytest
```

Frontend tests and build:

```bash
cd frontend
npm test
npm run build
```

Additional manual smoke checks are documented in `docs/testing.md`.

## Logging And Secret Handling

Backend LLM settings, normalization, rendering, and MIDI-ready mapping use Python `logging` and intentionally log provider names, model names, schema version, normalization path, validation retry counts, event counts, timing summaries, and sanitized error details. API key values are never logged. Frontend API/store/validator/playback code uses `console.debug`, `console.info`, `console.warn`, and `console.error` for request intent, state transitions, JSON validation, instrument strategy/fallback, mute/solo gains, transport lifecycle, notation rendering, and playback scheduling without logging secrets or full raw composition payloads.

## 🧠 Generation Architecture

- **Model discovery**: `GET /llm/models` exposes only providers with configured API keys.
- **Prompt orchestration**: the backend runs a multi-stage LangGraph composer (form → harmony → melody → bass → accompaniment → assemble → validate → repair) that emits canonical note events rather than one full-score blob.
- **Generation bounds**: initial LLM Composition V1 generation rejects requests above 32 bars or 6 non-drum instruments with HTTP `422` and an actionable message. Hard musical parameters are constraint-checked through staged generation; contradictory tonal centers fail with structured `502` diagnostics.
- **Validation**: Pydantic schema checks plus deterministic integrity validation cover required roles, note density, pitch ranges, timing bounds, and harmony-only rejection. Failed stages repair using structured diagnostics until `max_retries` is exhausted; final failures return HTTP `502` with sanitized detail.
- **Normalization**: legacy and V1 input migrates to operational `composition.v2`; harmony-only legacy output is rejected with an actionable error.
- **Testing**: normal backend tests mock providers. Opt-in real-provider smoke: `RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest tests/test_llm_real_provider_smoke.py` from `backend/`.
- **MusicXML rendering**: canonical note events are converted to deterministic MusicXML with music21 for notation preview and `/export/musicxml`; harmony remains chord-symbol metadata.
- **MIDI export**: `/export/midi` writes a Standard MIDI File via `mido` from the same track-local events, preserving velocity, program, channel, volume, and pan.
- **WAV export**: `/export/wav` synthesizes PCM with the FluidSynth CLI from those MIDI bytes (Docker installs `fluidsynth` + `fluid-soundfont-gm` / FluidR3_GM). Browser Tone.js remains interactive preview only.
- **Frontend preview**: the browser edits canonical JSON via piano roll or JSON editor, renders MusicXML with OSMD (including debounced preview refresh), schedules exact multi-track note events from ticks with Tone.js (no harmony-derived substitutes), exposes mute/solo/volume routing controls, and downloads MusicXML/MIDI/WAV exports that share the same `tracks[].events[]`.

## 🔍 Troubleshooting

### Common Issues

1. **No LLM models visible**: Set `OPENAI_API_KEY` or `DEEPSEEK_API_KEY` before starting the backend (import still works without keys)
2. **Generation returns 503**: No provider key is configured in the backend environment
3. **Invalid LLM JSON**: The staged composer validates and repairs using diagnostic codes; check backend logs for `stage`, diagnostic codes, retry counts, and sanitized provider errors (never API keys)
4. **Notation does not render**: Confirm the response includes `musicxml` and the edited JSON still matches the expected shape. After import, notation is regenerated from V2 — uploaded XML is never shown directly
5. **Playback fails**: Browser audio requires a user gesture; click Play directly and check that canonical `tracks[].events[]` contain valid pitches, ticks, durations, and velocities. Use browser console for schedule summaries, instrument fallback warnings, and transport errors.
6. **WAV export returns 503**: Install FluidSynth and a SoundFont locally, or use Docker (`fluidsynth` + `fluid-soundfont-gm`). Set `COMPOSITION_WAV_SOUNDFONT` to the `.sf2` path (default `/usr/share/sounds/sf2/FluidR3_GM.sf2`). Check logs for missing binary/SoundFont basename.
7. **Import returns 413/415/422**: Check `IMPORT_*` limits, content type vs endpoint (`/imports/midi` vs `/imports/musicxml`), and import report/error `code` in the JSON body. Malformed or hostile files never mutate an open composition. See [docs/import.md](docs/import.md).

### Performance Tips

- Keep prompt instructions specific and concise.
- Use narrower tempo and section constraints for more predictable output.
- Add frontend code splitting before production deployment if bundle size becomes a concern.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- [music21](https://web.mit.edu/music21/) for MusicXML rendering
- [FastAPI](https://fastapi.tiangolo.com/) for the backend API
- [React](https://reactjs.org/) for the frontend interface
- [LangChain](https://www.langchain.com/) and [LangGraph](https://www.langchain.com/langgraph) for LLM orchestration
- [OpenSheetMusicDisplay](https://opensheetmusicdisplay.org/) for notation rendering
- [Tone.js](https://tonejs.github.io/) for browser audio playback

## 🔮 Future Enhancements

- Richer multi-instrument arrangement controls
- Code-split notation/playback bundles
- Advanced music theory constraints
- Style transfer between different musical genres
- Collaborative composition features
- Mobile app version

---

**Happy Composing! 🎵**
