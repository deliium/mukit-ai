# Testing

## Backend

Run backend unit tests from the `backend/` directory:

```bash
../.venv/bin/python -m pytest
```

The LLM tests mock provider behavior and do not call OpenAI or DeepSeek APIs.

## Frontend Smoke Checks

No frontend test runner is configured yet. Use these manual checks after `npm run build` and during local development.

1. Start the backend on port `8888` and the frontend dev server on port `3000`.
2. Confirm the system status shows API connected.
3. With no `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`, confirm the LLM composer shows the provider configuration message.
4. With one provider key configured, confirm the provider/model selector shows one option.
5. With both provider keys configured, confirm both provider/model options appear and selection changes are retained.
6. Generate LLM music JSON with a mocked or real configured provider and confirm the editable JSON appears.
7. Edit the JSON to an invalid shape and confirm a validation error appears.
8. Reset the editor and confirm the generated JSON is restored.
9. Confirm notation renders from backend MusicXML.
10. Click Play and Stop to confirm playback starts only after the user gesture and stops cleanly.
11. Resize to a mobile viewport and confirm controls remain usable without horizontal page overflow.

## Frontend Build

Run from `frontend/`:

```bash
npm run build
```

The OSMD/Tone.js bundle can trigger Vite's large chunk warning; that warning is expected until code splitting is added.
