# 🎵 AI Music Composer

A full-stack LLM music composer that generates structured music JSON through a LangChain/LangGraph-backed FastAPI service. The React frontend lets users choose a configured provider/model, edit the returned JSON, render notation from backend MusicXML, and preview simple chord playback in the browser.

## 🚀 Features

- **LLM JSON Composition**: Generate structured music JSON with OpenAI or DeepSeek-compatible providers
- **Prompt Controls**: Configure genre, mood, key, meter, tempo range, instruments, sections, complexity, duration, and freeform instructions
- **Editable JSON Workflow**: Review and edit generated sections, tracks, harmony, tempo, key, and meter
- **Notation And Playback**: Render backend MusicXML with OpenSheetMusicDisplay and preview chords with Tone.js

## 🏗️ Architecture

- **Backend**: FastAPI with Python
- **Frontend**: React with styled-components
- **Music Processing**: music21 library for MusicXML rendering
- **LLM Orchestration**: LangChain/LangGraph with OpenAI-compatible chat providers
- **Frontend State**: Zustand store for API status, LLM models, generation output, notation, and playback state

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
8. Use Play/Stop to preview simple chord playback from the generated or edited JSON.

## 🔧 API Endpoints

- `GET /` - API status
- `GET /health` - Health check
- `GET /llm/models` - Return configured LLM provider/model options
- `POST /llm/generate-music-json` - Generate validated music JSON and derived MusicXML

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

Example music JSON shape returned in `music`:

```json
{
  "tempo": 92,
  "key": "C minor",
  "time_signature": "4/4",
  "sections": [{ "type": "intro", "bars": 4 }],
  "tracks": [{ "instrument": "piano", "role": "harmony" }],
  "harmony": [{ "bar": 1, "chord": "Cm" }]
}
```

## 📁 Project Structure

```
mukit-ai/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application
│   │   ├── schemas.py           # Pydantic models
│   │   ├── llm_settings.py      # LLM provider environment settings
│   │   └── services/            # LLM generation and MusicXML rendering
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
│   │   │   └── PromptJsonEditor.jsx
│   │   ├── api/musicApi.js
│   │   ├── store/musicStore.js
│   │   ├── App.jsx
│   │   ├── index.jsx
│   │   └── index.css
│   └── package.json
├── docs/
│   └── testing.md
└── README.md
```

## ✅ Testing

Backend tests:

```bash
cd backend
../.venv/bin/python -m pytest
```

Frontend build:

```bash
cd frontend
npm run build
```

Frontend manual smoke checks are documented in `docs/testing.md`.

## Logging And Secret Handling

Backend LLM settings and generation code use Python `logging` and intentionally log provider names, model names, request lifecycle events, validation retry counts, and sanitized error details. API key values are never logged. Frontend API/store code uses `console.debug`, `console.warn`, and `console.error` for request intent, state transitions, JSON validation, notation rendering, and playback events without logging secrets.

## 🧠 Generation Architecture

- **Model discovery**: `GET /llm/models` exposes only providers with configured API keys.
- **Prompt orchestration**: the backend builds a strict JSON-only prompt and runs it through a LangGraph flow.
- **Validation**: Pydantic validates tempo, key, meter, sections, tracks, and harmony before any response is returned.
- **MusicXML rendering**: validated JSON is converted to deterministic MusicXML with music21 for notation preview.
- **Frontend preview**: the browser edits JSON, renders MusicXML with OSMD, and schedules simple chord playback with Tone.js.

## 🔍 Troubleshooting

### Common Issues

1. **No LLM models visible**: Set `OPENAI_API_KEY` or `DEEPSEEK_API_KEY` before starting the backend
2. **Generation returns 503**: No provider key is configured in the backend environment
3. **Invalid LLM JSON**: The backend validates model output and retries once by default; check backend logs for sanitized validation details
4. **Notation does not render**: Confirm the response includes `musicxml` and the edited JSON still matches the expected shape
5. **Playback fails**: Browser audio requires a user gesture; click Play directly and check that harmony entries contain supported chord names

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
