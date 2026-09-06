# 🎵 AI Music Composer

A full-stack LLM music composer that generates canonical playable `composition.v1` JSON through a LangChain/LangGraph-backed FastAPI service. The React frontend lets users choose a configured provider/model, edit the returned JSON, render notation from backend MusicXML, and preview canonical note-event playback in the browser.

## 🚀 Features

- **LLM JSON Composition**: Generate structured music JSON with OpenAI or DeepSeek-compatible providers
- **Prompt Controls**: Configure genre, mood, key, meter, tempo range, instruments, sections, complexity, duration, and freeform instructions
- **Editable JSON Workflow**: Review and edit canonical sections, tracks, harmony metadata, timing, and note events
- **Notation And Playback**: Render backend MusicXML with OpenSheetMusicDisplay and play exact multi-track canonical note events with Tone.js (mute/solo/volume, pause/resume, seek-to-start)
- **Deterministic Export**: Download MusicXML and MIDI from the same canonical `tracks[].events[]` used by notation and playback

## 🏗️ Architecture

- **Backend**: FastAPI with Python
- **Frontend**: React with styled-components
- **Music Processing**: music21 library for MusicXML rendering
- **LLM Orchestration**: LangChain/LangGraph with OpenAI-compatible chat providers
- **Frontend State**: Zustand store for API status, LLM models, generation output, notation, playback transport state, and per-track mute/solo/volume

## 📋 Prerequisites

- Python 3.14+
- Node.js 16+
- npm or yarn

## 🛠️ Installation

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

3. Start the React development server:
```bash
npm start
```

The frontend will be available at `http://localhost:3000`

## 🎼 Usage

### LLM JSON Composition

1. Configure `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, or both on the backend.
2. Start the backend and frontend.
3. Choose the provider/model in the LLM JSON Composer panel.
4. Set prompt parameters such as genre, mood, key, time signature, tempo range, instruments, sections, complexity, duration, and freeform instructions.
5. Click "Generate LLM Music JSON".
6. Edit the returned JSON in the browser. Invalid edits show a client-side validation error.
7. Review notation rendered from backend MusicXML.
8. Use Play/Stop to preview canonical note events from the generated or edited JSON.
9. Export MusicXML or MIDI from the edited canonical JSON; notation preview refreshes from the exported MusicXML.

## 🔧 API Endpoints

- `GET /` - API status
- `GET /health` - Health check
- `GET /llm/models` - Return configured LLM provider/model options
- `POST /llm/generate-music-json` - Generate validated music JSON and derived MusicXML
- `POST /export/musicxml` - Render canonical `composition.v1` JSON as a downloadable MusicXML attachment
- `POST /export/midi` - Render canonical `composition.v1` JSON as a downloadable Standard MIDI File attachment

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

`POST /llm/generate-music-json` returns canonical `composition.v1` JSON in `music` plus derived `musicxml`. Legacy LLM output with explicit notes is normalized before returning; harmony-only legacy output is rejected as non-playable.

Example canonical music JSON shape returned in `music`:

```json
{
  "schema_version": "composition.v1",
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
│   │   ├── llm_settings.py      # LLM provider environment settings
│   │   └── services/            # LLM generation, normalization, MusicXML, MIDI-ready mapping
│   ├── requirements.txt
│   └── tests/                   # Backend unit tests
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Header.jsx
│   │   │   ├── MusicGenerator.jsx
│   │   │   ├── NotationViewer.jsx
│   │   │   ├── PlaybackControls.jsx
│   │   │   ├── TrackPlaybackControls.jsx
│   │   │   ├── ExportControls.jsx
│   │   │   └── PromptJsonEditor.jsx
│   │   ├── api/musicApi.js
│   │   ├── store/musicStore.js
│   │   ├── utils/               # Validation, playback events/tracks/engine helpers
│   │   ├── App.jsx
│   │   ├── index.jsx
│   │   └── index.css
│   └── package.json
├── docs/
│   ├── composition-v1.md
│   └── testing.md
└── README.md
```

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
- **Generation bounds**: initial LLM Composition V1 generation rejects requests above 32 bars or 6 non-drum instruments with HTTP `422` and an actionable message.
- **Validation**: Pydantic schema checks plus deterministic integrity validation cover required roles, note density, pitch ranges, timing bounds, and harmony-only rejection. Failed stages repair using structured diagnostics until `max_retries` is exhausted; final failures return HTTP `502` with sanitized detail.
- **Normalization**: legacy LLM output with explicit notes is still migrated to canonical track-local events when encountered; harmony-only legacy output is rejected with an actionable error.
- **Testing**: normal backend tests mock providers. Opt-in real-provider smoke: `RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest tests/test_llm_real_provider_smoke.py` from `backend/`.
- **MusicXML rendering**: canonical note events are converted to deterministic MusicXML with music21 for notation preview and `/export/musicxml`; harmony remains chord-symbol metadata.
- **MIDI export**: `/export/midi` writes a Standard MIDI File via `mido` from the same track-local events, preserving velocity, program, channel, volume, and pan.
- **Frontend preview**: the browser edits canonical JSON, renders MusicXML with OSMD, schedules exact multi-track note events from ticks with Tone.js (no harmony-derived substitutes), exposes mute/solo/volume routing controls, and downloads MusicXML/MIDI exports that share the same `tracks[].events[]`.

## 🔍 Troubleshooting

### Common Issues

1. **No LLM models visible**: Set `OPENAI_API_KEY` or `DEEPSEEK_API_KEY` before starting the backend
2. **Generation returns 503**: No provider key is configured in the backend environment
3. **Invalid LLM JSON**: The staged composer validates and repairs using diagnostic codes; check backend logs for `stage`, diagnostic codes, retry counts, and sanitized provider errors (never API keys)
4. **Notation does not render**: Confirm the response includes `musicxml` and the edited JSON still matches the expected shape
5. **Playback fails**: Browser audio requires a user gesture; click Play directly and check that canonical `tracks[].events[]` contain valid pitches, ticks, durations, and velocities. Use browser console for schedule summaries, instrument fallback warnings, and transport errors.

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
