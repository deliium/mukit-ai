import { create } from 'zustand';

const initialPrompt = {
  genre: 'ambient',
  mood: 'cinematic',
  key: '',
  time_signature: '4/4',
  tempo_min: 80,
  tempo_max: 120,
  instruments: 'piano,bass,strings',
  sections: 'intro:4,verse:8,chorus:8',
  complexity: 'moderate',
  duration_bars: 20,
  instructions: '',
};

export const useMusicStore = create((set, get) => ({
  apiStatus: 'checking',
  llmModelsLoaded: false,
  availableLlmModels: [],
  selectedProvider: '',
  selectedModel: '',
  prompt: initialPrompt,
  generatedMusicJson: null,
  editedMusicJson: null,
  musicXml: '',
  generationStatus: 'idle',
  playbackStatus: 'idle',
  uiError: '',
  warnings: [],

  setApiStatus: (apiStatus) => {
    console.debug('[musicStore] API status changed', { apiStatus });
    set({ apiStatus });
  },

  setAvailableLlmModels: (models, defaults = {}) => {
    const selected = selectModel(models, defaults, get());
    console.debug('[musicStore] LLM models loaded', {
      modelCount: models.length,
      selectedProvider: selected.selectedProvider,
      selectedModel: selected.selectedModel,
    });
    set({
      availableLlmModels: models,
      llmModelsLoaded: true,
      ...selected,
    });
  },

  setSelectedLlmModel: (provider, model) => {
    console.debug('[musicStore] LLM model selected', { provider, model });
    set({ selectedProvider: provider, selectedModel: model });
  },

  updatePrompt: (name, value) => {
    console.debug('[musicStore] Prompt field changed', { name });
    set((state) => ({
      prompt: {
        ...state.prompt,
        [name]: value,
      },
    }));
  },

  startGeneration: () => {
    console.debug('[musicStore] LLM generation started');
    set({ generationStatus: 'loading', uiError: '', warnings: [] });
  },

  completeGeneration: ({ music, musicxml, warnings = [] }) => {
    console.debug('[musicStore] LLM generation completed', {
      hasMusic: Boolean(music),
      musicXmlLength: musicxml?.length || 0,
      warningCount: warnings.length,
    });
    set({
      generatedMusicJson: music,
      editedMusicJson: music,
      musicXml: musicxml || '',
      warnings,
      generationStatus: 'success',
      uiError: '',
    });
  },

  failGeneration: (message) => {
    console.error('[musicStore] LLM generation failed', { message });
    set({ generationStatus: 'error', uiError: message });
  },

  setEditedMusicJson: (editedMusicJson) => {
    console.debug('[musicStore] Edited music JSON changed', { hasJson: Boolean(editedMusicJson) });
    set({ editedMusicJson });
  },

  resetEditedMusicJson: () => {
    console.debug('[musicStore] Edited music JSON reset');
    set((state) => ({ editedMusicJson: state.generatedMusicJson }));
  },

  setMusicXml: (musicXml) => {
    console.debug('[musicStore] MusicXML changed', { musicXmlLength: musicXml?.length || 0 });
    set({ musicXml: musicXml || '' });
  },

  setPlaybackStatus: (playbackStatus) => {
    console.debug('[musicStore] Playback status changed', { playbackStatus });
    set({ playbackStatus });
  },

  setUiError: (uiError) => {
    if (uiError) {
      console.error('[musicStore] UI error set', { uiError });
    }
    set({ uiError });
  },
}));

function selectModel(models, defaults, state) {
  if (!models.length) {
    return { selectedProvider: '', selectedModel: '' };
  }

  const existing = models.find(
    (model) => model.provider === state.selectedProvider && model.model === state.selectedModel,
  );
  if (existing) {
    return { selectedProvider: existing.provider, selectedModel: existing.model };
  }

  const defaultModel = models.find(
    (model) => model.provider === defaults.defaultProvider || model.is_default,
  );
  const selected = defaultModel || models[0];
  return { selectedProvider: selected.provider, selectedModel: selected.model };
}
