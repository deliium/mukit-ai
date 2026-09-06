import { create } from 'zustand';
import { renderMusicXmlPreview } from '../api/musicApi.js';
import {
  createProject as createProjectRequest,
  deleteProject as deleteProjectRequest,
  duplicateProject as duplicateProjectRequest,
  getProject as getProjectRequest,
  listProjects as listProjectsRequest,
  patchProject as patchProjectRequest,
} from '../api/projectApi.js';
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
import {
  defaultTargetTrackIds,
  normalizeBarRange,
} from '../utils/pianoRollSelection.js';

export const AUTOSAVE_DEBOUNCE_MS = 900;

let autosaveTimer = null;
let autosaveRequestSeq = 0;

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

  aiEditStartBar: null,
  aiEditEndBar: null,
  aiEditTrackMode: 'current',
  aiEditTrackIds: null,
  aiEditInstruction: '',
  aiEditStatus: 'idle',
  aiEditError: '',
  aiEditWarnings: [],

  activeView: 'home',
  currentProjectId: null,
  currentProjectName: '',
  projectList: [],
  projectListStatus: 'idle',
  saveStatus: 'saved',
  saveError: '',
  lastSavedRevision: 'empty',
  generationMeta: null,

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
    if (get().generationStatus === 'loading') {
      console.warn('[musicStore] Duplicate generation blocked', {
        provider: get().selectedProvider,
        model: get().selectedModel,
      });
      return false;
    }
    console.info('[musicStore] LLM generation started', {
      provider: get().selectedProvider,
      model: get().selectedModel,
    });
    set({ generationStatus: 'loading', uiError: '', warnings: [] });
    return true;
  },

  completeGeneration: ({ music, musicxml, warnings = [], provider = null, model = null }) => {
    const { composition } = ensureCompositionNoteIds(music);
    const validation = validateMusicJson(composition);
    const revision = compositionRevisionKey(composition);
    const promptSnapshot = buildPromptSnapshot(get().prompt);
    const generationMeta = {
      provider: provider || get().selectedProvider || null,
      model: model || get().selectedModel || null,
      prompt: promptSnapshot,
    };
    console.debug('[musicStore] LLM generation completed', {
      hasMusic: Boolean(composition),
      schemaVersion: composition?.schema_version || 'legacy',
      canonical: isCanonicalComposition(composition),
      musicXmlLength: musicxml?.length || 0,
      warningCount: warnings.length,
      valid: validation.valid,
      compositionRevision: revision.slice(0, 48),
      provider: generationMeta.provider,
      model: generationMeta.model,
      projectId: get().currentProjectId,
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
      generationMeta,
    });
    markProjectDirty(set, get, revision);
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
    markProjectDirty(set, get, revision);
  },

  resetEditedMusicJson: () => {
    console.debug('[musicStore] Edited music JSON reset');
    set((state) => {
      const music = state.generatedMusicJson;
      const revision = compositionRevisionKey(music);
      return {
        editedMusicJson: music,
        compositionRevision: revision,
        trackControls: buildDefaultTrackControls(music),
        pianoRollTrackId: pickDefaultTrackId(music),
        pianoRollNoteId: null,
        pianoRollEditStatus: 'idle',
        noteEditUndoStack: [],
        noteEditRedoStack: [],
      };
    });
    markProjectDirty(set, get, get().compositionRevision);
  },

  selectPianoRollTrack: (trackId) => {
    const state = get();
    const previous = state.pianoRollTrackId;
    const next = pickDefaultTrackId(state.editedMusicJson, trackId);
    console.info('[musicStore] Piano-roll track selected', { previousTrackId: previous, nextTrackId: next });
    const patch = {
      pianoRollTrackId: next,
      pianoRollNoteId: null,
    };
    if (state.aiEditTrackMode === 'current') {
      patch.aiEditTrackIds = defaultTargetTrackIds(state.editedMusicJson, {
        mode: 'current',
        currentTrackId: next,
      });
    }
    set(patch);
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
    markProjectDirty(set, get, revision);
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
    markProjectDirty(set, get, revision);
    return true;
  },

  setAiEditSelection: ({ startBar, endBar, trackMode, trackIds } = {}) => {
    const composition = get().editedMusicJson;
    const barCount = Number(composition?.bar_count) || 0;
    const normalized = normalizeBarRange(startBar, endBar, barCount || Number.MAX_SAFE_INTEGER);
    if (normalized.startBar === null) {
      console.warn('[musicStore] Invalid AI edit selection ignored', {
        startBar,
        endBar,
        warning: normalized.warning,
      });
      return false;
    }
    if (barCount > 0 && (normalized.startBar > barCount || normalized.endBar > barCount)) {
      console.warn('[musicStore] AI edit selection out of composition bounds', {
        startBar: normalized.startBar,
        endBar: normalized.endBar,
        barCount,
      });
      return false;
    }
    const nextMode = trackMode === 'all' ? 'all' : 'current';
    const nextTrackIds = Array.isArray(trackIds)
      ? trackIds.map(String)
      : defaultTargetTrackIds(composition, {
        mode: nextMode,
        currentTrackId: get().pianoRollTrackId,
      });
    console.info('[musicStore] AI edit selection changed', {
      startBar: normalized.startBar,
      endBar: normalized.endBar,
      trackMode: nextMode,
      trackScopeCount: nextTrackIds.length,
    });
    set({
      aiEditStartBar: normalized.startBar,
      aiEditEndBar: normalized.endBar,
      aiEditTrackMode: nextMode,
      aiEditTrackIds: nextTrackIds,
    });
    return true;
  },

  clearAiEditSelection: () => {
    console.info('[musicStore] AI edit selection cleared');
    set({
      aiEditStartBar: null,
      aiEditEndBar: null,
      aiEditTrackIds: null,
      aiEditTrackMode: 'current',
    });
  },

  setAiEditInstruction: (instruction) => {
    const value = typeof instruction === 'string' ? instruction : '';
    console.debug('[musicStore] AI edit instruction updated', { length: value.trim().length });
    set({ aiEditInstruction: value });
  },

  setAiEditTrackMode: (trackMode) => {
    const nextMode = trackMode === 'all' ? 'all' : 'current';
    const composition = get().editedMusicJson;
    const trackIds = defaultTargetTrackIds(composition, {
      mode: nextMode,
      currentTrackId: get().pianoRollTrackId,
    });
    console.info('[musicStore] AI edit track mode changed', {
      trackMode: nextMode,
      trackScopeCount: trackIds.length,
    });
    set({
      aiEditTrackMode: nextMode,
      aiEditTrackIds: trackIds,
    });
  },

  startAiEdit: () => {
    const state = get();
    if (state.aiEditStatus === 'loading') {
      console.warn('[musicStore] Duplicate AI edit blocked', {
        startBar: state.aiEditStartBar,
        endBar: state.aiEditEndBar,
      });
      return false;
    }
    const instruction = String(state.aiEditInstruction || '').trim();
    if (!state.editedMusicJson || !isCanonicalComposition(state.editedMusicJson)) {
      console.warn('[musicStore] AI edit start rejected; composition missing/invalid');
      return false;
    }
    if (!state.aiEditStartBar || !state.aiEditEndBar) {
      console.warn('[musicStore] AI edit start rejected; no bar selection');
      return false;
    }
    if (!instruction) {
      console.warn('[musicStore] AI edit start rejected; empty instruction');
      return false;
    }
    console.info('[musicStore] AI edit started', {
      startBar: state.aiEditStartBar,
      endBar: state.aiEditEndBar,
      trackMode: state.aiEditTrackMode,
      trackScopeCount: (state.aiEditTrackIds || []).length,
      provider: state.selectedProvider,
      model: state.selectedModel,
    });
    set({
      aiEditStatus: 'loading',
      aiEditError: '',
      aiEditWarnings: [],
    });
    return true;
  },

  failAiEdit: (message) => {
    const safeMessage = message || 'AI region edit failed';
    console.error('[musicStore] AI edit failed', { message: safeMessage });
    set({
      aiEditStatus: 'error',
      aiEditError: safeMessage,
    });
  },

  completeAiEdit: ({ composition, musicxml = '', warnings = [] } = {}) => {
    const state = get();
    const { composition: normalized } = ensureCompositionNoteIds(composition);
    const validation = validateMusicJson(normalized);
    if (!validation.valid || !isCanonicalComposition(normalized)) {
      console.error('[musicStore] AI edit completion rejected invalid composition', {
        message: validation.message,
      });
      set({
        aiEditStatus: 'error',
        aiEditError: validation.message || 'Edited composition failed validation',
      });
      return false;
    }

    const historySnapshot = snapshotNoteEditState(state);
    const revision = compositionRevisionKey(normalized);
    console.info('[musicStore] AI edit applied', {
      startBar: state.aiEditStartBar,
      endBar: state.aiEditEndBar,
      trackScopeCount: (state.aiEditTrackIds || []).length,
      warningCount: warnings.length,
      eventCount: countEvents(normalized),
      compositionRevision: revision.slice(0, 48),
    });
    console.debug('[musicStore] AI edit revision/event counts', {
      previousEventCount: countEvents(state.editedMusicJson),
      nextEventCount: countEvents(normalized),
      undoDepth: Math.min(state.noteEditUndoStack.length + 1, MAX_UNDO_HISTORY),
    });

    set({
      editedMusicJson: normalized,
      generatedMusicJson: normalized,
      musicXml: musicxml || state.musicXml || '',
      compositionRevision: revision,
      trackControls: mergeTrackControls(state.trackControls, normalized),
      pianoRollTrackId: pickDefaultTrackId(normalized, state.pianoRollTrackId),
      pianoRollNoteId: null,
      noteEditUndoStack: [...state.noteEditUndoStack, historySnapshot].slice(-MAX_UNDO_HISTORY),
      noteEditRedoStack: [],
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      aiEditStatus: 'success',
      aiEditError: '',
      aiEditWarnings: Array.isArray(warnings) ? warnings : [],
      pianoRollEditStatus: 'idle',
    });
    console.info('[musicStore] Project autosave-dirty transition after AI edit', {
      projectId: state.currentProjectId,
      revision: revision.slice(0, 48),
    });
    markProjectDirty(set, get, revision);
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

  goHome: async () => {
    console.info('[musicStore] View transition', { activeView: 'home', projectId: get().currentProjectId });
    cancelAutosaveTimer();
    set({ activeView: 'home' });
    await get().loadProjectList();
  },

  loadProjectList: async () => {
    console.debug('[musicStore] Loading project list');
    set({ projectListStatus: 'loading' });
    try {
      const response = await listProjectsRequest();
      const projects = response?.projects || [];
      console.info('[musicStore] Project list loaded', { count: projects.length });
      set({ projectList: projects, projectListStatus: 'success' });
      return projects;
    } catch (error) {
      console.error('[musicStore] Project list failed', { message: error.message });
      set({ projectListStatus: 'error', uiError: error.message });
      throw error;
    }
  },

  createNewProject: async (name = 'Untitled Project') => {
    console.info('[musicStore] Creating project', { nameLength: name.length });
    try {
      const project = await createProjectRequest({ name });
      hydrateProject(set, get, project, { openComposer: true, markSaved: true });
      console.info('[musicStore] Project created and opened', { projectId: project.id });
      return project;
    } catch (error) {
      console.error('[musicStore] Project create failed', { message: error.message });
      set({ uiError: error.message });
      throw error;
    }
  },

  openProject: async (projectId) => {
    console.info('[musicStore] Opening project', { projectId });
    try {
      const project = await getProjectRequest(projectId);
      hydrateProject(set, get, project, { openComposer: true, markSaved: true });
      console.info('[musicStore] Project opened', {
        projectId: project.id,
        hasComposition: Boolean(project.composition),
        compositionMigrated: Boolean(project.composition_migrated),
      });
      return project;
    } catch (error) {
      console.error('[musicStore] Project open failed', { projectId, message: error.message });
      set({ uiError: error.message });
      throw error;
    }
  },

  renameCurrentProject: async (name) => {
    const projectId = get().currentProjectId;
    if (!projectId) {
      console.warn('[musicStore] rename ignored; no open project');
      return null;
    }
    const trimmed = String(name || '').trim();
    if (!trimmed) {
      console.warn('[musicStore] rename rejected empty name', { projectId });
      return null;
    }
    console.info('[musicStore] Renaming project', { projectId, nameLength: trimmed.length });
    set({ currentProjectName: trimmed, saveStatus: 'saving', saveError: '' });
    try {
      const project = await patchProjectRequest(projectId, { name: trimmed });
      set({
        currentProjectName: project.name,
        saveStatus: get().compositionRevision === get().lastSavedRevision ? 'saved' : get().saveStatus,
      });
      // If composition still dirty keep unsaved; rename alone is persisted.
      if (get().compositionRevision === get().lastSavedRevision) {
        set({ saveStatus: 'saved', saveError: '' });
      } else {
        set({ saveStatus: 'unsaved', saveError: '' });
        scheduleAutosave(set, get);
      }
      console.info('[musicStore] Project renamed', { projectId });
      return project;
    } catch (error) {
      console.error('[musicStore] Project rename failed', { projectId, message: error.message });
      set({ saveStatus: 'error', saveError: error.message });
      throw error;
    }
  },

  renameProjectById: async (projectId, name) => {
    const trimmed = String(name || '').trim();
    if (!trimmed) {
      console.warn('[musicStore] renameProjectById rejected empty name', { projectId });
      return null;
    }
    console.info('[musicStore] Renaming project from browser', { projectId, nameLength: trimmed.length });
    try {
      const project = await patchProjectRequest(projectId, { name: trimmed });
      if (get().currentProjectId === projectId) {
        set({ currentProjectName: project.name });
      }
      await get().loadProjectList();
      return project;
    } catch (error) {
      console.error('[musicStore] Browser rename failed', { projectId, message: error.message });
      set({ uiError: error.message });
      throw error;
    }
  },

  duplicateProjectById: async (projectId) => {
    console.info('[musicStore] Duplicating project', { projectId });
    try {
      const project = await duplicateProjectRequest(projectId);
      await get().loadProjectList();
      console.info('[musicStore] Project duplicated', { sourceProjectId: projectId, projectId: project.id });
      return project;
    } catch (error) {
      console.error('[musicStore] Project duplicate failed', { projectId, message: error.message });
      set({ uiError: error.message });
      throw error;
    }
  },

  deleteProjectById: async (projectId) => {
    console.info('[musicStore] Deleting project', { projectId });
    try {
      await deleteProjectRequest(projectId);
      if (get().currentProjectId === projectId) {
        cancelAutosaveTimer();
        console.info('[musicStore] Cleared active project after delete', { projectId });
        set({
          currentProjectId: null,
          currentProjectName: '',
          activeView: 'home',
          generatedMusicJson: null,
          editedMusicJson: null,
          musicXml: '',
          compositionRevision: 'empty',
          lastSavedRevision: 'empty',
          saveStatus: 'saved',
          saveError: '',
          generationMeta: null,
          noteEditUndoStack: [],
          noteEditRedoStack: [],
        });
      }
      await get().loadProjectList();
      console.info('[musicStore] Project deleted', { projectId });
    } catch (error) {
      console.error('[musicStore] Project delete failed', { projectId, message: error.message });
      set({ uiError: error.message });
      throw error;
    }
  },

  saveCurrentProject: async ({ reason = 'manual' } = {}) => {
    const state = get();
    const projectId = state.currentProjectId;
    if (!projectId) {
      console.debug('[musicStore] Save skipped; no open project', { reason });
      return null;
    }
    if (state.compositionRevision === state.lastSavedRevision && reason !== 'manual-force') {
      console.debug('[musicStore] Save skipped; already saved', {
        reason,
        projectId,
        revision: state.compositionRevision.slice(0, 48),
      });
      set({ saveStatus: 'saved', saveError: '' });
      return null;
    }

    const requestId = ++autosaveRequestSeq;
    const revisionAtStart = state.compositionRevision;
    const composition = state.editedMusicJson;
    const payload = {
      composition: composition || undefined,
      clear_composition: !composition,
    };
    if (state.generationMeta) {
      payload.generation = {
        provider: state.generationMeta.provider || null,
        model: state.generationMeta.model || null,
        prompt: state.generationMeta.prompt || null,
      };
    }

    console.info('[musicStore] Saving project', {
      reason,
      projectId,
      requestId,
      eventCount: countEvents(composition),
      revision: revisionAtStart.slice(0, 48),
    });
    console.debug('[musicStore] Save status transition', { from: state.saveStatus, to: 'saving', reason });
    set({ saveStatus: 'saving', saveError: '' });

    try {
      const project = await patchProjectRequest(projectId, payload);
      if (requestId !== autosaveRequestSeq) {
        console.warn('[musicStore] Ignoring stale save response', { requestId, latest: autosaveRequestSeq });
        return project;
      }
      const stillCurrent = get().currentProjectId === projectId;
      if (!stillCurrent) {
        console.warn('[musicStore] Save completed after project closed', { projectId, requestId });
        return project;
      }
      const currentRevision = get().compositionRevision;
      if (currentRevision !== revisionAtStart) {
        console.debug('[musicStore] Save completed but newer edits exist', {
          projectId,
          savedRevision: revisionAtStart.slice(0, 48),
          currentRevision: currentRevision.slice(0, 48),
        });
        set({
          lastSavedRevision: revisionAtStart,
          saveStatus: 'unsaved',
          saveError: '',
        });
        scheduleAutosave(set, get);
        return project;
      }
      console.info('[musicStore] Project saved', {
        reason,
        projectId,
        eventCount: countEvents(composition),
      });
      console.debug('[musicStore] Save status transition', { from: 'saving', to: 'saved', reason });
      set({
        lastSavedRevision: revisionAtStart,
        saveStatus: 'saved',
        saveError: '',
        currentProjectName: project.name || get().currentProjectName,
      });
      return project;
    } catch (error) {
      if (requestId !== autosaveRequestSeq) {
        return null;
      }
      console.error('[musicStore] Project save failed', {
        reason,
        projectId,
        message: error.message,
        status: error.status,
      });
      console.debug('[musicStore] Save status transition', { from: 'saving', to: 'error', reason });
      set({ saveStatus: 'error', saveError: error.message });
      throw error;
    }
  },

  scheduleAutosave: () => {
    scheduleAutosave(set, get);
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
  markProjectDirty(set, get, revision);
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

function cancelAutosaveTimer() {
  if (autosaveTimer) {
    console.debug('[musicStore] Autosave debounce cancelled');
    clearTimeout(autosaveTimer);
    autosaveTimer = null;
  }
}

function markProjectDirty(set, get, revision) {
  const state = get();
  if (!state.currentProjectId) {
    return;
  }
  if (revision === state.lastSavedRevision) {
    set({ saveStatus: 'saved', saveError: '' });
    cancelAutosaveTimer();
    return;
  }
  console.debug('[musicStore] Project marked unsaved', {
    projectId: state.currentProjectId,
    revision: String(revision).slice(0, 48),
  });
  set({ saveStatus: 'unsaved', saveError: '' });
  scheduleAutosave(set, get);
}

function scheduleAutosave(set, get) {
  const state = get();
  if (!state.currentProjectId) {
    console.debug('[musicStore] Autosave skipped; no open project');
    return;
  }
  if (state.compositionRevision === state.lastSavedRevision) {
    console.debug('[musicStore] Autosave skipped; clean revision');
    return;
  }
  cancelAutosaveTimer();
  console.debug('[musicStore] Autosave debounce scheduled', {
    projectId: state.currentProjectId,
    delayMs: AUTOSAVE_DEBOUNCE_MS,
    revision: state.compositionRevision.slice(0, 48),
  });
  autosaveTimer = setTimeout(() => {
    autosaveTimer = null;
    console.debug('[musicStore] Autosave debounce fired', {
      projectId: get().currentProjectId,
    });
    get().saveCurrentProject({ reason: 'autosave' }).catch(() => {
      // error already recorded on saveStatus
    });
  }, AUTOSAVE_DEBOUNCE_MS);
}

function hydrateProject(set, get, project, { openComposer = true, markSaved = true } = {}) {
  const composition = project.composition
    ? ensureCompositionNoteIds(project.composition).composition
    : null;
  const revision = compositionRevisionKey(composition);
  const generationMeta = project.generation_provider || project.generation_model || project.generation_prompt
    ? {
      provider: project.generation_provider || null,
      model: project.generation_model || null,
      prompt: project.generation_prompt || null,
    }
    : null;

  console.debug('[musicStore] Hydrating project into composer state', {
    projectId: project.id,
    hasComposition: Boolean(composition),
    openComposer,
    markSaved,
  });

  cancelAutosaveTimer();
  set({
    currentProjectId: project.id,
    currentProjectName: project.name,
    activeView: openComposer ? 'composer' : get().activeView,
    generatedMusicJson: composition,
    editedMusicJson: composition,
    musicXml: '',
    compositionRevision: revision,
    lastSavedRevision: markSaved ? revision : get().lastSavedRevision,
    saveStatus: markSaved ? 'saved' : 'unsaved',
    saveError: '',
    generationMeta,
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
    uiError: '',
  });
}

function buildPromptSnapshot(prompt) {
  return {
    genre: prompt.genre,
    mood: prompt.mood,
    key: prompt.key || null,
    time_signature: prompt.time_signature,
    tempo_min: Number(prompt.tempo_min),
    tempo_max: Number(prompt.tempo_max),
    instruments: String(prompt.instruments || '')
      .split(',')
      .map((instrument) => instrument.trim())
      .filter(Boolean),
    sections: String(prompt.sections || '')
      .split(',')
      .map((section) => {
        const [type, bars] = section.split(':').map((part) => part.trim());
        return type && bars ? { type, bars: Number(bars) } : null;
      })
      .filter(Boolean),
    complexity: prompt.complexity,
    duration_bars: Number(prompt.duration_bars),
    instructions: prompt.instructions || null,
  };
}
