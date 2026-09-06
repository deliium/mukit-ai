import { create } from 'zustand';
import { renderMusicXmlPreview } from '../api/musicApi.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { compositionRevisionKey } from '../utils/playbackPosition.js';
import {
  MAX_UNDO_HISTORY,
  SNAP_VALUES,
  createTrackNote,
  deleteTrackNote,
  ensureCompositionNoteIds,
  pickDefaultTrackId,
  sanitizeNoteSummary,
  updateTrackNote,
} from '../utils/pianoRollEvents.js';

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

const DEFAULT_PIANO_ROLL_ZOOM = 0.05;
const MIN_PIANO_ROLL_ZOOM = 0.01;
const MAX_PIANO_ROLL_ZOOM = 0.25;

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
  pianoRollTrackId: null,
  pianoRollNoteId: null,
  pianoRollSnap: '1/8',
  pianoRollZoom: DEFAULT_PIANO_ROLL_ZOOM,
  pianoRollEditStatus: 'idle',
  pianoRollNotationStatus: 'idle',
  pianoRollNotationError: '',
  noteEditUndoStack: [],
  noteEditRedoStack: [],

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
    const { composition } = ensureCompositionNoteIds(music);
    const validation = validateMusicJson(composition);
    const revision = compositionRevisionKey(composition);
    console.debug('[musicStore] LLM generation completed', {
      hasMusic: Boolean(composition),
      schemaVersion: composition?.schema_version || 'legacy',
      canonical: isCanonicalComposition(composition),
      musicXmlLength: musicxml?.length || 0,
      warningCount: warnings.length,
      valid: validation.valid,
      compositionRevision: revision.slice(0, 48),
    });
    if (!validation.valid) {
      console.error('[musicStore] Generated music JSON failed validation', { message: validation.message });
    }
    set({
      generatedMusicJson: composition,
      editedMusicJson: composition,
      musicXml: musicxml || '',
      warnings,
      generationStatus: 'success',
      uiError: '',
      compositionRevision: revision,
      trackControls: buildDefaultTrackControls(composition),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      pianoRollTrackId: pickDefaultTrackId(composition),
      pianoRollNoteId: null,
      pianoRollEditStatus: 'idle',
      pianoRollNotationStatus: 'idle',
      pianoRollNotationError: '',
      noteEditUndoStack: [],
      noteEditRedoStack: [],
    });
  },

  failGeneration: (message) => {
    console.error('[musicStore] LLM generation failed', { message });
    set({ generationStatus: 'error', uiError: message });
  },

  setEditedMusicJson: (editedMusicJson) => {
    const normalized = editedMusicJson
      ? ensureCompositionNoteIds(editedMusicJson).composition
      : editedMusicJson;
    const validation = normalized ? validateMusicJson(normalized) : { valid: false };
    const previous = get().editedMusicJson;
    const previousEventCount = countEvents(previous);
    const nextEventCount = countEvents(normalized);
    const revision = compositionRevisionKey(normalized);
    const previousTrackId = get().pianoRollTrackId;
    const nextTrackId = pickDefaultTrackId(normalized, previousTrackId);
    if (previousTrackId && nextTrackId && previousTrackId !== nextTrackId) {
      console.warn('[musicStore] Stale piano-roll track recovered after JSON edit', {
        previousTrackId,
        nextTrackId,
      });
    }
    console.debug('[musicStore] Edited music JSON changed', {
      hasJson: Boolean(normalized),
      schemaVersion: normalized?.schema_version || 'legacy',
      canonical: isCanonicalComposition(normalized),
      valid: validation.valid,
      previousEventCount,
      nextEventCount,
      compositionRevision: revision.slice(0, 48),
    });
    if (!validation.valid && normalized) {
      console.warn('[musicStore] Invalid edited JSON may prevent playback', { message: validation.message });
    }
    set({
      editedMusicJson: normalized,
      compositionRevision: revision,
      trackControls: mergeTrackControls(get().trackControls, normalized),
      pianoRollTrackId: nextTrackId,
      pianoRollNoteId: noteStillExists(normalized, nextTrackId, get().pianoRollNoteId)
        ? get().pianoRollNoteId
        : null,
      noteEditUndoStack: [],
      noteEditRedoStack: [],
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
        pianoRollTrackId: pickDefaultTrackId(music),
        pianoRollNoteId: null,
        pianoRollEditStatus: 'idle',
        noteEditUndoStack: [],
        noteEditRedoStack: [],
      };
    });
  },

  selectPianoRollTrack: (trackId) => {
    const state = get();
    const previous = state.pianoRollTrackId;
    const next = pickDefaultTrackId(state.editedMusicJson, trackId);
    console.info('[musicStore] Piano-roll track selected', { previousTrackId: previous, nextTrackId: next });
    set({
      pianoRollTrackId: next,
      pianoRollNoteId: null,
    });
  },

  selectPianoRollNote: (noteId) => {
    console.info('[musicStore] Piano-roll note selected', {
      trackId: get().pianoRollTrackId,
      noteId,
    });
    set({ pianoRollNoteId: noteId || null });
  },

  setPianoRollSnap: (snapValue) => {
    if (!SNAP_VALUES.includes(snapValue)) {
      console.warn('[musicStore] Rejected invalid piano-roll snap', { snapValue });
      return;
    }
    console.info('[musicStore] Piano-roll snap changed', { snapValue });
    set({ pianoRollSnap: snapValue });
  },

  setPianoRollZoom: (zoom) => {
    const value = Number(zoom);
    if (!Number.isFinite(value) || value < MIN_PIANO_ROLL_ZOOM || value > MAX_PIANO_ROLL_ZOOM) {
      console.warn('[musicStore] Rejected invalid piano-roll zoom', { zoom });
      return;
    }
    console.info('[musicStore] Piano-roll zoom changed', { zoom: value });
    console.debug('[musicStore] Piano-roll pixels-per-tick', { pixelsPerTick: value });
    set({ pianoRollZoom: value });
  },

  createNote: (trackId, noteDraft) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] createNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const result = createTrackNote(current, trackId, noteDraft);
      if (!result.note) {
        console.warn('[musicStore] createNote rejected', { trackId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] createNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: result.note.id,
        action: 'create',
        noteSummary: sanitizeNoteSummary(result.note),
      });
      return result.note;
    } catch (error) {
      console.error('[musicStore] createNote unexpected failure', { trackId, message: error.message });
      set({ pianoRollEditStatus: 'error' });
      return null;
    }
  },

  updateNote: (trackId, noteId, patch, options = {}) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] updateNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const before = findNote(current, trackId, noteId);
      const result = updateTrackNote(current, trackId, noteId, patch);
      if (!result.note) {
        console.warn('[musicStore] updateNote rejected', { trackId, noteId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] updateNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: result.note.id,
        action: 'update',
        noteSummary: {
          old: sanitizeNoteSummary(before),
          next: sanitizeNoteSummary(result.note),
        },
        skipHistory: Boolean(options.skipHistory),
        historySnapshot: options.historySnapshot || null,
      });
      return result.note;
    } catch (error) {
      console.error('[musicStore] updateNote unexpected failure', {
        trackId,
        noteId,
        message: error.message,
      });
      set({ pianoRollEditStatus: 'error' });
      return null;
    }
  },

  deleteNote: (trackId, noteId) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!noteId) {
        console.warn('[musicStore] deleteNote ignored with no selection', { trackId });
        return false;
      }
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] deleteNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      const result = deleteTrackNote(current, trackId, noteId);
      if (!result.deleted) {
        console.warn('[musicStore] deleteNote rejected', { trackId, noteId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] deleteNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: null,
        action: 'delete',
        noteSummary: sanitizeNoteSummary(result.deleted),
      });
      return true;
    } catch (error) {
      console.error('[musicStore] deleteNote unexpected failure', {
        trackId,
        noteId,
        message: error.message,
      });
      set({ pianoRollEditStatus: 'error' });
      return false;
    }
  },

  undoNoteEdit: () => {
    const state = get();
    if (!state.noteEditUndoStack.length) {
      console.warn('[musicStore] undoNoteEdit ignored; stack empty');
      return false;
    }
    const previous = state.noteEditUndoStack[state.noteEditUndoStack.length - 1];
    const currentSnapshot = snapshotNoteEditState(state);
    const nextUndo = state.noteEditUndoStack.slice(0, -1);
    const nextRedo = [...state.noteEditRedoStack, currentSnapshot].slice(-MAX_UNDO_HISTORY);
    const revision = compositionRevisionKey(previous.editedMusicJson);
    console.info('[musicStore] undoNoteEdit applied', {
      trackId: previous.pianoRollTrackId,
      noteId: previous.pianoRollNoteId,
      eventCount: countEvents(previous.editedMusicJson),
      compositionRevision: revision.slice(0, 48),
    });
    set({
      editedMusicJson: previous.editedMusicJson,
      compositionRevision: revision,
      trackControls: mergeTrackControls(state.trackControls, previous.editedMusicJson),
      pianoRollTrackId: previous.pianoRollTrackId,
      pianoRollNoteId: previous.pianoRollNoteId,
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
    });
    return true;
  },

  redoNoteEdit: () => {
    const state = get();
    if (!state.noteEditRedoStack.length) {
      console.warn('[musicStore] redoNoteEdit ignored; stack empty');
      return false;
    }
    const next = state.noteEditRedoStack[state.noteEditRedoStack.length - 1];
    const currentSnapshot = snapshotNoteEditState(state);
    const nextRedo = state.noteEditRedoStack.slice(0, -1);
    const nextUndo = [...state.noteEditUndoStack, currentSnapshot].slice(-MAX_UNDO_HISTORY);
    const revision = compositionRevisionKey(next.editedMusicJson);
    console.info('[musicStore] redoNoteEdit applied', {
      trackId: next.pianoRollTrackId,
      noteId: next.pianoRollNoteId,
      eventCount: countEvents(next.editedMusicJson),
      compositionRevision: revision.slice(0, 48),
    });
    set({
      editedMusicJson: next.editedMusicJson,
      compositionRevision: revision,
      trackControls: mergeTrackControls(state.trackControls, next.editedMusicJson),
      pianoRollTrackId: next.pianoRollTrackId,
      pianoRollNoteId: next.pianoRollNoteId,
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
    });
    return true;
  },

  refreshMusicXmlFromEditedComposition: async () => {
    const composition = get().editedMusicJson;
    const validation = composition ? validateMusicJson(composition) : { valid: false, message: 'No composition' };
    if (!composition || !validation.valid || !isCanonicalComposition(composition)) {
      console.warn('[musicStore] MusicXML preview refresh skipped', {
        valid: validation.valid,
        message: validation.message,
        canonical: isCanonicalComposition(composition),
      });
      set({
        pianoRollNotationStatus: 'error',
        pianoRollNotationError: validation.message || 'Canonical composition.v1 required for notation refresh',
      });
      return null;
    }

    const eventCount = countEvents(composition);
    const revision = get().compositionRevision;
    console.debug('[musicStore] MusicXML preview refresh started', {
      schemaVersion: composition.schema_version,
      eventCount,
      compositionRevision: revision.slice(0, 48),
    });
    set({ pianoRollNotationStatus: 'loading', pianoRollNotationError: '' });
    try {
      const musicxml = await renderMusicXmlPreview(composition);
      // Ignore stale responses if another edit landed meanwhile
      if (get().compositionRevision !== revision) {
        console.warn('[musicStore] MusicXML preview result discarded; composition changed', {
          requestedRevision: revision.slice(0, 48),
          currentRevision: get().compositionRevision.slice(0, 48),
        });
        return null;
      }
      console.debug('[musicStore] MusicXML preview refresh completed', {
        schemaVersion: composition.schema_version,
        eventCount,
        musicXmlLength: musicxml?.length || 0,
        compositionRevision: revision.slice(0, 48),
      });
      set({
        musicXml: musicxml || '',
        pianoRollNotationStatus: 'success',
        pianoRollNotationError: '',
      });
      return musicxml;
    } catch (error) {
      console.error('[musicStore] MusicXML preview refresh failed', {
        message: error.message,
        eventCount,
      });
      set({
        pianoRollNotationStatus: 'error',
        pianoRollNotationError: error.message || 'Notation refresh failed',
      });
      return null;
    }
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

function snapshotNoteEditState(state) {
  return {
    editedMusicJson: state.editedMusicJson,
    pianoRollTrackId: state.pianoRollTrackId,
    pianoRollNoteId: state.pianoRollNoteId,
  };
}

function applyNoteEdit(set, get, {
  nextComposition,
  selectedTrackId,
  selectedNoteId,
  action,
  noteSummary,
  skipHistory = false,
  historySnapshot = null,
}) {
  const state = get();
  const revision = compositionRevisionKey(nextComposition);
  console.info('[musicStore] Note edit applied', {
    action,
    trackId: selectedTrackId,
    noteId: selectedNoteId,
    note: noteSummary,
    skipHistory,
  });
  console.debug('[musicStore] Note edit revision/event counts', {
    previousEventCount: countEvents(state.editedMusicJson),
    nextEventCount: countEvents(nextComposition),
    compositionRevision: revision.slice(0, 48),
    undoDepth: skipHistory
      ? state.noteEditUndoStack.length
      : Math.min(state.noteEditUndoStack.length + 1, MAX_UNDO_HISTORY),
  });

  let noteEditUndoStack = state.noteEditUndoStack;
  let noteEditRedoStack = state.noteEditRedoStack;
  if (!skipHistory) {
    const snapshot = historySnapshot || snapshotNoteEditState(state);
    noteEditUndoStack = [...state.noteEditUndoStack, snapshot].slice(-MAX_UNDO_HISTORY);
    noteEditRedoStack = [];
  }

  set({
    editedMusicJson: nextComposition,
    compositionRevision: revision,
    trackControls: mergeTrackControls(state.trackControls, nextComposition),
    pianoRollTrackId: selectedTrackId,
    pianoRollNoteId: selectedNoteId,
    pianoRollEditStatus: 'idle',
    noteEditUndoStack,
    noteEditRedoStack,
  });
}

function findNote(composition, trackId, noteId) {
  const track = composition?.tracks?.find((item) => String(item.id) === String(trackId));
  if (!track || !Array.isArray(track.events)) {
    return null;
  }
  return track.events.find((event) => String(event.id) === String(noteId)) || null;
}

function noteStillExists(composition, trackId, noteId) {
  if (!noteId || !trackId) {
    return false;
  }
  return Boolean(findNote(composition, trackId, noteId));
}
