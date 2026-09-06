import { create } from 'zustand';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { compositionRevisionKey } from '../utils/playbackPosition.js';

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
  playbackSeconds: 0,
  playbackBar: 1,
  trackControls: {},
  compositionRevision: 'empty',
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
    const validation = validateMusicJson(music);
    const revision = compositionRevisionKey(music);
    console.debug('[musicStore] LLM generation completed', {
      hasMusic: Boolean(music),
      schemaVersion: music?.schema_version || 'legacy',
      canonical: isCanonicalComposition(music),
      musicXmlLength: musicxml?.length || 0,
      warningCount: warnings.length,
      valid: validation.valid,
      compositionRevision: revision.slice(0, 48),
    });
    if (!validation.valid) {
      console.error('[musicStore] Generated music JSON failed validation', { message: validation.message });
    }
    set({
      generatedMusicJson: music,
      editedMusicJson: music,
      musicXml: musicxml || '',
      warnings,
      generationStatus: 'success',
      uiError: '',
      compositionRevision: revision,
      trackControls: buildDefaultTrackControls(music),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
  },

  failGeneration: (message) => {
    console.error('[musicStore] LLM generation failed', { message });
    set({ generationStatus: 'error', uiError: message });
  },

  setEditedMusicJson: (editedMusicJson) => {
    const validation = editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false };
    const previous = get().editedMusicJson;
    const previousEventCount = countEvents(previous);
    const nextEventCount = countEvents(editedMusicJson);
    const revision = compositionRevisionKey(editedMusicJson);
    console.debug('[musicStore] Edited music JSON changed', {
      hasJson: Boolean(editedMusicJson),
      schemaVersion: editedMusicJson?.schema_version || 'legacy',
      canonical: isCanonicalComposition(editedMusicJson),
      valid: validation.valid,
      previousEventCount,
      nextEventCount,
      compositionRevision: revision.slice(0, 48),
    });
    if (!validation.valid && editedMusicJson) {
      console.warn('[musicStore] Invalid edited JSON may prevent playback', { message: validation.message });
    }
    set({
      editedMusicJson,
      compositionRevision: revision,
      trackControls: mergeTrackControls(get().trackControls, editedMusicJson),
    });
  },

  resetEditedMusicJson: () => {
    console.debug('[musicStore] Edited music JSON reset');
    set((state) => {
      const music = state.generatedMusicJson;
      return {
        editedMusicJson: music,
        compositionRevision: compositionRevisionKey(music),
        trackControls: buildDefaultTrackControls(music),
      };
    });
  },

  setMusicXml: (musicXml) => {
    console.debug('[musicStore] MusicXML changed', { musicXmlLength: musicXml?.length || 0 });
    set({ musicXml: musicXml || '' });
  },

  setPlaybackStatus: (playbackStatus) => {
    console.debug('[musicStore] Playback status changed', { playbackStatus });
    set({ playbackStatus });
  },

  setPlaybackPosition: ({ seconds = 0, bar = 1 } = {}) => {
    set({
      playbackSeconds: Number(seconds) || 0,
      playbackBar: Number(bar) || 1,
    });
  },

  syncTrackControlsFromComposition: (musicJson) => {
    set((state) => ({
      trackControls: mergeTrackControls(state.trackControls, musicJson),
    }));
  },

  toggleTrackMute: (trackId) => {
    set((state) => {
      const current = state.trackControls[trackId] || defaultControl();
      const next = {
        ...state.trackControls,
        [trackId]: { ...current, muted: !current.muted },
      };
      console.info('[musicStore] Track mute toggled', { trackId, muted: next[trackId].muted });
      return { trackControls: next };
    });
  },

  toggleTrackSolo: (trackId) => {
    set((state) => {
      const current = state.trackControls[trackId] || defaultControl();
      const next = {
        ...state.trackControls,
        [trackId]: { ...current, solo: !current.solo },
      };
      console.info('[musicStore] Track solo toggled', { trackId, solo: next[trackId].solo });
      return { trackControls: next };
    });
  },

  setTrackVolume: (trackId, volumeMidi) => {
    const clamped = Math.max(0, Math.min(127, Number(volumeMidi) || 0));
    set((state) => {
      const current = state.trackControls[trackId] || defaultControl();
      const next = {
        ...state.trackControls,
        [trackId]: { ...current, volumeMidi: clamped },
      };
      console.info('[musicStore] Track volume changed', { trackId, volumeMidi: clamped });
      return { trackControls: next };
    });
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

function defaultControl(volumeMidi = 100) {
  return {
    muted: false,
    solo: false,
    volumeMidi,
  };
}

function buildDefaultTrackControls(musicJson) {
  if (!isCanonicalComposition(musicJson) || !Array.isArray(musicJson.tracks)) {
    return {};
  }
  const controls = {};
  musicJson.tracks.forEach((track) => {
    const trackId = String(track.id);
    const volume = Number(track.volume);
    controls[trackId] = defaultControl(Number.isFinite(volume) ? volume : 100);
  });
  return controls;
}

function mergeTrackControls(existing, musicJson) {
  const defaults = buildDefaultTrackControls(musicJson);
  const merged = {};
  Object.keys(defaults).forEach((trackId) => {
    merged[trackId] = {
      ...defaults[trackId],
      ...(existing[trackId] || {}),
      volumeMidi: existing[trackId]?.volumeMidi ?? defaults[trackId].volumeMidi,
    };
  });
  return merged;
}

function countEvents(musicJson) {
  if (!musicJson || !Array.isArray(musicJson.tracks)) {
    return Array.isArray(musicJson?.notes) ? musicJson.notes.length : 0;
  }
  return musicJson.tracks.reduce((count, track) => count + (Array.isArray(track.events) ? track.events.length : 0), 0);
}
