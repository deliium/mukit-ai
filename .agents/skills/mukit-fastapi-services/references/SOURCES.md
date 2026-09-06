# Backend Layout Pointers

- Entry / composition routes: `backend/app/main.py`
- Projects router: `backend/app/routers/projects.py`
- Settings: `backend/app/llm_settings.py`
- DB: `backend/app/db/connection.py`, `backend/app/db/migrations/`
- Services: `backend/app/services/`
- Tests: `backend/tests/`
- Run locally: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8888` from `backend/`
