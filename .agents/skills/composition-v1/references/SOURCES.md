# Composition V1 Reference Pointers

- Spec narrative: `docs/composition-v1.md`
- Persistence: `docs/project-persistence.md`
- Backend schema: `backend/app/schemas.py` (`Composition`, note events, LLM request/response models)
- Validator / normalizer: `backend/app/services/composition_validator.py`, `composition_normalizer.py`
- Staged composer: `backend/app/services/llm_music_generator.py`, `composition_planner.py`
- Region patch: `backend/app/services/composition_region_patch.py`, `llm_composition_editor.py`
- Frontend validation: `frontend/src/utils/musicJsonValidation.js`
