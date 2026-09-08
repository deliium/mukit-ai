import { create } from 'zustand';
import { analyzeComposition, AnalysisApiError, importMidi, importMusicXml, renderMusicXmlPreview } from '../api/musicApi.js';
import {
  createProject as createProjectRequest,
  deleteProject as deleteProjectRequest,
  duplicateProject as duplicateProjectRequest,
  getProject as getProjectRequest,
  listProjects as listProjectsRequest,
  patchProject as patchProjectRequest,
} from '../api/projectApi.js';
import {
  ANALYSIS_DEBOUNCE_MS,
  AnalysisScopeError,
  analysisRequestKeysEqual,
  analysisWarningCodes,
  buildAnalysisRequestKey,
  buildAnalysisRequestScope,
  deriveAnalysisFreshness,
  fingerprintLogPrefix,
  normalizeAnalysisScopeKind,
  recoverAnalysisSectionKey,
  revisionLogPrefix,
  sanitizeScopeForLog,
} from '../utils/compositionAnalysis.js';
import { prepareCompositionForStore, tryPrepareCompositionForStore } from '../utils/compositionVersion.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { compositionRevisionKey, notationRevisionKey } from '../utils/playbackPosition.js';
import {
  MAX_UNDO_HISTORY,
  SNAP_VALUES,
  applyTieChain as applyTrackTieChain,
  countV2FeatureSummary,
  createTrackNote,
  deleteTrackNote,
  ensureCompositionNoteIds,
  pickDefaultTrackId,
  removeTieChain as removeTrackTieChain,
  sanitizeNoteSummary,
  toggleNoteArticulation as toggleTrackNoteArticulation,
  updateTrackNote,
} from '../utils/pianoRollEvents.js';
import {
  defaultTargetTrackIds,
  normalizeBarRange,
} from '../utils/pianoRollSelection.js';
import { projectPersistRevisionKey } from '../utils/projectPersistRevision.js';

export const AUTOSAVE_DEBOUNCE_MS = 900;
export { ANALYSIS_DEBOUNCE_MS };

function isManualSaveReason(reason) {
  return reason === 'manual' || reason === 'manual-force';
}

let autosaveTimer = null;
let autosaveRequestSeq = 0;

let analysisRequestSeq = 0;
let analysisDebounceTimer = null;
let analysisInFlightKey = null;

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
  notationRevision: 'empty',
  uiError: '',
  warnings: [],
  pianoRollTrackId: null,
  pianoRollNoteId: null,
  pianoRollNoteIds: [],
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
  lastSavedPersistRevision: 'empty',
  generationMeta: null,

  importStatus: 'idle',
  importError: '',
  importReport: null,
  notationReport: null,

  analysisScope: 'composition',
  analysisSelectedSectionKey: null,
  analysisResult: null,
  analysisResultKey: null,
  analysisAttemptKey: null,
  analysisStatus: 'idle',
  analysisError: '',
  analysisWarnings: [],
  analysisTabVisible: false,

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
    syncGenerationMetaFromPrompt(set, get, { reason: 'prompt-edit' });
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
    const { composition: withIds } = ensureCompositionNoteIds(music);
    const composition = prepareCompositionForStore(withIds);
    const validation = validateMusicJson(composition);
    const revision = compositionRevisionKey(composition);
    const notationRev = notationRevisionKey(composition);
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
    cancelAnalysisLifecycle();
    set({
      generatedMusicJson: composition,
      editedMusicJson: composition,
      musicXml: musicxml || '',
      warnings,
      generationStatus: 'success',
      uiError: '',
      compositionRevision: revision,
      notationRevision: notationRev,
      trackControls: buildDefaultTrackControls(composition),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      pianoRollTrackId: pickDefaultTrackId(composition),
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
      pianoRollEditStatus: 'idle',
      pianoRollNotationStatus: 'idle',
      pianoRollNotationError: '',
      noteEditUndoStack: [],
      noteEditRedoStack: [],
      generationMeta,
      ...clearedAnalysisState(),
    });
    markProjectDirty(set, get);
  },

  failGeneration: (message) => {
    console.error('[musicStore] LLM generation failed', { message });
    set({ generationStatus: 'error', uiError: message });
  },

  startImport: ({ format = null } = {}) => {
    if (get().importStatus === 'loading') {
      console.warn('[musicStore] Duplicate import blocked', { format });
      return false;
    }
    console.info('[musicStore] Composition import started', { format });
    set({
      importStatus: 'loading',
      importError: '',
      importReport: null,
      notationReport: null,
      uiError: '',
    });
    return true;
  },

  completeImport: ({
    composition,
    musicxml = '',
    import_report: importReport = null,
    notation_report: notationReport = null,
  }) => {
    const { composition: withIds } = ensureCompositionNoteIds(composition);
    const nextComposition = prepareCompositionForStore(withIds);
    const validation = validateMusicJson(nextComposition);
    if (!validation.valid || !isCanonicalComposition(nextComposition)) {
      const message = validation.message || 'Imported composition is invalid';
      console.error('[musicStore] Import rejected after validation', { message });
      set({
        importStatus: 'error',
        importError: message,
      });
      return false;
    }

    // Supersede in-flight saves / timers before replacing workspace music.
    cancelAutosaveTimer();
    autosaveRequestSeq += 1;

    const revision = compositionRevisionKey(nextComposition);
    const notationRev = notationRevisionKey(nextComposition);
    const eventCount = countEvents(nextComposition);
    const featureSummary = countV2FeatureSummary(nextComposition);
    console.info('[musicStore] Composition import completed', {
      schemaVersion: nextComposition.schema_version,
      trackCount: nextComposition.tracks?.length || 0,
      eventCount,
      barCount: nextComposition.bar_count || 0,
      importStatus: importReport?.status || null,
      importIssueCount: importReport?.issues?.length || 0,
      projectId: get().currentProjectId,
      ...featureSummary,
    });
    console.debug('[musicStore] Import state installed', {
      compositionRevision: revision.slice(0, 48),
      musicXmlLength: musicxml?.length || 0,
      hasNotationReport: Boolean(notationReport),
    });

    cancelAnalysisLifecycle();
    set({
      generatedMusicJson: nextComposition,
      editedMusicJson: nextComposition,
      musicXml: musicxml || '',
      warnings: [],
      generationStatus: 'idle',
      uiError: '',
      compositionRevision: revision,
      notationRevision: notationRev,
      trackControls: buildDefaultTrackControls(nextComposition),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      pianoRollTrackId: pickDefaultTrackId(nextComposition),
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
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
      generationMeta: null,
      importStatus: 'success',
      importError: '',
      importReport: importReport || null,
      notationReport: notationReport || null,
      ...clearedAnalysisState(),
    });
    markProjectDirty(set, get);
    return true;
  },

  failImport: (message, { code = null } = {}) => {
    console.error('[musicStore] Composition import failed', {
      message,
      code,
    });
    set({
      importStatus: 'error',
      importError: message || 'Import failed',
    });
  },

  createImportedProject: async (file, { format = 'midi' } = {}) => {
    const started = get().startImport({ format });
    if (!started) {
      return null;
    }
    console.debug('[musicStore] Import-as-project parse started', {
      format,
      byteCount: typeof file?.size === 'number' ? file.size : null,
    });
    try {
      const imported = format === 'musicxml'
        ? await importMusicXml(file)
        : await importMidi(file);
      const name = defaultImportedProjectName(file);
      const project = await createProjectRequest({
        name,
        composition: imported.composition,
      });
      cancelAutosaveTimer();
      autosaveRequestSeq += 1;
      hydrateProject(set, get, project, { openComposer: true, markSaved: true });
      set({
        musicXml: imported.musicxml || '',
        generationMeta: null,
        importStatus: 'success',
        importError: '',
        importReport: imported.import_report || null,
        notationReport: imported.notation_report || null,
        uiError: '',
      });
      console.info('[musicStore] Imported project created', {
        projectId: project.id,
        format,
        trackCount: imported.composition?.tracks?.length || 0,
        eventCount: countEvents(imported.composition),
        importStatus: imported.import_report?.status || null,
      });
      return {
        project,
        import_report: imported.import_report || null,
        notation_report: imported.notation_report || null,
      };
    } catch (error) {
      const message = error.message || 'Import failed';
      console.error('[musicStore] Import-as-project failed before/during create', {
        format,
        code: error.code || null,
        status: error.status || null,
        message,
      });
      get().failImport(message, { code: error.code || null });
      throw error;
    }
  },

  setAnalysisScope: (scopeKind) => {
    const next = normalizeAnalysisScopeKind(scopeKind);
    const state = get();
    if (state.analysisScope === next) {
      return;
    }
    console.debug('[musicStore] Analysis scope changed', {
      previous: state.analysisScope,
      next,
    });
    const patch = { analysisScope: next };
    if (next === 'section' && !state.analysisSelectedSectionKey) {
      patch.analysisSelectedSectionKey = recoverAnalysisSectionKey(state.editedMusicJson, null);
    }
    set(patch);
    scheduleAnalysisRequest(get, { reason: 'scope-change' });
  },

  selectAnalysisSection: (sectionKey) => {
    const recovered = recoverAnalysisSectionKey(get().editedMusicJson, sectionKey);
    console.debug('[musicStore] Analysis section selected', {
      requested: sectionKey || null,
      recovered,
    });
    set({
      analysisSelectedSectionKey: recovered,
      analysisScope: 'section',
    });
    scheduleAnalysisRequest(get, { reason: 'section-change' });
  },

  setAnalysisTabVisible: (visible) => {
    const next = Boolean(visible);
    const previous = get().analysisTabVisible;
    if (previous === next) {
      return;
    }
    console.debug('[musicStore] Analysis tab visibility changed', { visible: next });
    set({ analysisTabVisible: next });
    if (next) {
      scheduleAnalysisRequest(get, { reason: 'tab-visible' });
    } else {
      cancelAnalysisDebounce();
    }
  },

  getAnalysisFreshness: () => {
    const state = get();
    const desired = buildDesiredAnalysisRequestKey(state);
    return deriveAnalysisFreshness({
      analysisResult: state.analysisResult,
      analysisResultKey: state.analysisResultKey,
      desiredRequestKey: desired,
      analysisStatus: state.analysisStatus,
    });
  },

  requestAnalysis: async ({ force = false, reason = 'request' } = {}) => {
    return runAnalysisRequest(set, get, { force, reason });
  },

  refreshAnalysis: async () => {
    console.info('[musicStore] Analysis refresh requested');
    cancelAnalysisDebounce();
    return runAnalysisRequest(set, get, { force: true, reason: 'refresh' });
  },

  retryAnalysis: async () => {
    console.info('[musicStore] Analysis retry requested');
    cancelAnalysisDebounce();
    return runAnalysisRequest(set, get, { force: true, reason: 'retry' });
  },

  resetAnalysis: () => {
    console.debug('[musicStore] Analysis state reset');
    cancelAnalysisLifecycle();
    set(clearedAnalysisState({ preserveScope: true }));
  },

  setEditedMusicJson: (editedMusicJson) => {
    const normalized = editedMusicJson
      ? coerceEditableComposition(editedMusicJson)
      : editedMusicJson;
    const validation = normalized ? validateMusicJson(normalized) : { valid: false };
    const previous = get().editedMusicJson;
    const previousEventCount = countEvents(previous);
    const nextEventCount = countEvents(normalized);
    const revision = compositionRevisionKey(normalized);
    const notationRev = notationRevisionKey(normalized);
    const staleNotation = notationStalePatch(get().notationRevision, notationRev);
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
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: mergeTrackControls(get().trackControls, normalized),
      pianoRollTrackId: nextTrackId,
      pianoRollNoteId: noteStillExists(normalized, nextTrackId, get().pianoRollNoteId)
        ? get().pianoRollNoteId
        : null,
      pianoRollNoteIds: filterExistingNoteIds(normalized, nextTrackId, get().pianoRollNoteIds),
      noteEditUndoStack: [],
      noteEditRedoStack: [],
      analysisSelectedSectionKey: recoverAnalysisSectionKey(normalized, get().analysisSelectedSectionKey),
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'edited-json' });
  },

  resetEditedMusicJson: () => {
    console.debug('[musicStore] Edited music JSON reset');
    set((state) => {
      const music = state.generatedMusicJson;
      const revision = compositionRevisionKey(music);
      const notationRev = notationRevisionKey(music);
      const staleNotation = notationStalePatch(state.notationRevision, notationRev);
      return {
        editedMusicJson: music,
        compositionRevision: revision,
        notationRevision: notationRev,
        ...staleNotation,
        trackControls: buildDefaultTrackControls(music),
        pianoRollTrackId: pickDefaultTrackId(music),
        pianoRollNoteId: null,
        pianoRollNoteIds: [],
        pianoRollEditStatus: 'idle',
        noteEditUndoStack: [],
        noteEditRedoStack: [],
        analysisSelectedSectionKey: recoverAnalysisSectionKey(music, state.analysisSelectedSectionKey),
      };
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'reset-edited' });
  },

  selectPianoRollTrack: (trackId) => {
    const state = get();
    const previous = state.pianoRollTrackId;
    const next = pickDefaultTrackId(state.editedMusicJson, trackId);
    console.info('[musicStore] Piano-roll track selected', { previousTrackId: previous, nextTrackId: next });
    const patch = {
      pianoRollTrackId: next,
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
    };
    if (state.aiEditTrackMode === 'current') {
      patch.aiEditTrackIds = defaultTargetTrackIds(state.editedMusicJson, {
        mode: 'current',
        currentTrackId: next,
      });
    }
    set(patch);
    if (get().analysisScope === 'track' && previous !== next) {
      scheduleAnalysisRequest(get, { reason: 'track-change' });
    }
  },

  selectPianoRollNote: (noteId, options = {}) => {
    const extend = Boolean(options.extend);
    if (extend && noteId) {
      const current = get().pianoRollNoteIds || [];
      const exists = current.some((id) => String(id) === String(noteId));
      const nextIds = exists
        ? current.filter((id) => String(id) !== String(noteId))
        : [...current, noteId];
      console.info('[musicStore] Piano-roll note selection toggled', {
        trackId: get().pianoRollTrackId,
        noteId,
        selectedCount: nextIds.length,
      });
      set({
        pianoRollNoteIds: nextIds,
        pianoRollNoteId: noteId,
      });
      return;
    }
    console.info('[musicStore] Piano-roll note selected', {
      trackId: get().pianoRollTrackId,
      noteId,
      selectedCount: noteId ? 1 : 0,
    });
    set({
      pianoRollNoteId: noteId || null,
      pianoRollNoteIds: noteId ? [noteId] : [],
    });
  },

  toggleNoteArticulation: (trackId, noteId, articulation) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] toggleNoteArticulation rejected non-canonical composition');
        return null;
      }
      const result = toggleTrackNoteArticulation(current, trackId, noteId, articulation);
      if (!result.note) {
        console.warn('[musicStore] toggleNoteArticulation rejected', {
          trackId,
          noteId,
          articulation,
          message: result.warning,
        });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] toggleNoteArticulation failed validation', { message: validation.message });
        return null;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: result.note.id,
        selectedNoteIds: state.pianoRollNoteIds?.includes(result.note.id)
          ? state.pianoRollNoteIds
          : [result.note.id],
        action: 'articulation',
        noteSummary: sanitizeNoteSummary(result.note),
        featureCounts: countV2FeatureSummary(result.composition),
      });
      console.info('[musicStore] toggleNoteArticulation applied', {
        trackId,
        noteId,
        articulation,
        articulations: result.note.articulations,
      });
      return result.note;
    } catch (error) {
      console.error('[musicStore] toggleNoteArticulation unexpected failure', {
        trackId,
        noteId,
        articulation,
        message: error.message,
      });
      return null;
    }
  },

  applyTieChain: (trackId, noteIds) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] applyTieChain rejected non-canonical composition');
        return false;
      }
      const ids = Array.isArray(noteIds) && noteIds.length ? noteIds : state.pianoRollNoteIds;
      const result = applyTrackTieChain(current, trackId, ids);
      if (!result.notes?.length) {
        console.warn('[musicStore] applyTieChain rejected incompatible selection', {
          trackId,
          noteIds: ids,
          message: result.warning,
        });
        return false;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] applyTieChain failed validation', { message: validation.message });
        return false;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: result.notes[0]?.id ?? null,
        selectedNoteIds: result.notes.map((note) => note.id),
        action: 'tie-apply',
        noteSummary: {
          groupId: result.groupId,
          noteCount: result.notes.length,
          tickRange: {
            start: result.notes[0]?.start_tick,
            end: result.notes[result.notes.length - 1]?.start_tick
              + result.notes[result.notes.length - 1]?.duration_ticks,
          },
        },
        featureCounts: countV2FeatureSummary(result.composition),
      });
      console.info('[musicStore] applyTieChain applied', {
        trackId,
        groupId: result.groupId,
        noteCount: result.notes.length,
      });
      return true;
    } catch (error) {
      console.error('[musicStore] applyTieChain unexpected failure', {
        trackId,
        message: error.message,
      });
      return false;
    }
  },

  removeTieChain: (trackId, noteIds) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        console.warn('[musicStore] removeTieChain rejected non-canonical composition');
        return false;
      }
      const ids = Array.isArray(noteIds) && noteIds.length ? noteIds : state.pianoRollNoteIds;
      const result = removeTrackTieChain(current, trackId, ids);
      if (!result.clearedCount) {
        console.warn('[musicStore] removeTieChain rejected', {
          trackId,
          noteIds: ids,
          message: result.warning,
        });
        return false;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        console.warn('[musicStore] removeTieChain failed validation', { message: validation.message });
        return false;
      }
      applyNoteEdit(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: state.pianoRollNoteId,
        selectedNoteIds: ids,
        action: 'tie-remove',
        noteSummary: { clearedCount: result.clearedCount },
        featureCounts: countV2FeatureSummary(result.composition),
      });
      console.info('[musicStore] removeTieChain applied', {
        trackId,
        clearedCount: result.clearedCount,
      });
      return true;
    } catch (error) {
      console.error('[musicStore] removeTieChain unexpected failure', {
        trackId,
        message: error.message,
      });
      return false;
    }
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
    const notationRev = notationRevisionKey(previous.editedMusicJson);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    console.info('[musicStore] undoNoteEdit applied', {
      trackId: previous.pianoRollTrackId,
      noteId: previous.pianoRollNoteId,
      eventCount: countEvents(previous.editedMusicJson),
      compositionRevision: revision.slice(0, 48),
    });
    set({
      editedMusicJson: previous.editedMusicJson,
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: mergeTrackControls(state.trackControls, previous.editedMusicJson),
      pianoRollTrackId: previous.pianoRollTrackId,
      pianoRollNoteId: previous.pianoRollNoteId,
      pianoRollNoteIds: previous.pianoRollNoteIds || [],
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      analysisSelectedSectionKey: recoverAnalysisSectionKey(
        previous.editedMusicJson,
        state.analysisSelectedSectionKey,
      ),
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'undo' });
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
    const notationRev = notationRevisionKey(next.editedMusicJson);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    console.info('[musicStore] redoNoteEdit applied', {
      trackId: next.pianoRollTrackId,
      noteId: next.pianoRollNoteId,
      eventCount: countEvents(next.editedMusicJson),
      compositionRevision: revision.slice(0, 48),
    });
    set({
      editedMusicJson: next.editedMusicJson,
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: mergeTrackControls(state.trackControls, next.editedMusicJson),
      pianoRollTrackId: next.pianoRollTrackId,
      pianoRollNoteId: next.pianoRollNoteId,
      pianoRollNoteIds: next.pianoRollNoteIds || [],
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      analysisSelectedSectionKey: recoverAnalysisSectionKey(
        next.editedMusicJson,
        state.analysisSelectedSectionKey,
      ),
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'redo' });
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
    const prepared = prepareCompositionForStore(ensureCompositionNoteIds(composition).composition);
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
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
    const revision = compositionRevisionKey(prepared);
    const notationRev = notationRevisionKey(prepared);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    console.info('[musicStore] AI edit applied', {
      startBar: state.aiEditStartBar,
      endBar: state.aiEditEndBar,
      trackScopeCount: (state.aiEditTrackIds || []).length,
      warningCount: warnings.length,
      eventCount: countEvents(prepared),
      compositionRevision: revision.slice(0, 48),
    });
    console.debug('[musicStore] AI edit revision/event counts', {
      previousEventCount: countEvents(state.editedMusicJson),
      nextEventCount: countEvents(prepared),
      undoDepth: Math.min(state.noteEditUndoStack.length + 1, MAX_UNDO_HISTORY),
    });

    set({
      editedMusicJson: prepared,
      generatedMusicJson: prepared,
      musicXml: musicxml || state.musicXml || '',
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: mergeTrackControls(state.trackControls, prepared),
      pianoRollTrackId: pickDefaultTrackId(prepared, state.pianoRollTrackId),
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
      noteEditUndoStack: [...state.noteEditUndoStack, historySnapshot].slice(-MAX_UNDO_HISTORY),
      noteEditRedoStack: [],
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      aiEditStatus: 'success',
      aiEditError: '',
      aiEditWarnings: Array.isArray(warnings) ? warnings : [],
      pianoRollEditStatus: 'idle',
      analysisSelectedSectionKey: recoverAnalysisSectionKey(prepared, state.analysisSelectedSectionKey),
    });
    console.info('[musicStore] Project autosave-dirty transition after AI edit', {
      projectId: state.currentProjectId,
      revision: revision.slice(0, 48),
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'ai-edit' });
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
        pianoRollNotationError: validation.message || 'Canonical composition required for notation refresh',
      });
      return null;
    }

    const eventCount = countEvents(composition);
    const revision = get().notationRevision;
    console.debug('[musicStore] MusicXML preview refresh started', {
      schemaVersion: composition.schema_version,
      eventCount,
      notationRevision: revision.slice(0, 48),
    });
    set({ pianoRollNotationStatus: 'loading', pianoRollNotationError: '' });
    try {
      const preview = await renderMusicXmlPreview(composition);
      const musicxml = typeof preview === 'string' ? preview : preview.musicxml;
      const projectionWarnings = typeof preview === 'string' ? [] : (preview.warnings || []);
      // Ignore stale responses if another edit landed meanwhile
      if (get().notationRevision !== revision) {
        console.warn('[musicStore] MusicXML preview result discarded; composition changed', {
          requestedRevision: revision.slice(0, 48),
          currentRevision: get().notationRevision.slice(0, 48),
        });
        return null;
      }
      console.debug('[musicStore] MusicXML preview refresh completed', {
        schemaVersion: composition.schema_version,
        eventCount,
        musicXmlLength: musicxml?.length || 0,
        notationRevision: revision.slice(0, 48),
        projectionIssueCount: projectionWarnings.length,
      });
      set({
        musicXml: musicxml || '',
        pianoRollNotationStatus: 'success',
        pianoRollNotationError: projectionWarnings.length
          ? projectionWarnings.join('; ')
          : '',
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
      const persistRevision = projectPersistRevisionKey(get().editedMusicJson, get().generationMeta);
      const isClean = persistRevision === get().lastSavedPersistRevision;
      set({
        currentProjectName: project.name,
        saveStatus: isClean ? 'saved' : 'unsaved',
        saveError: '',
      });
      if (!isClean) {
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
        cancelAnalysisLifecycle();
        console.info('[musicStore] Cleared active project after delete', { projectId });
        set({
          currentProjectId: null,
          currentProjectName: '',
          activeView: 'home',
          generatedMusicJson: null,
          editedMusicJson: null,
          musicXml: '',
          compositionRevision: 'empty',
          notationRevision: 'empty',
          lastSavedPersistRevision: 'empty',
          saveStatus: 'saved',
          saveError: '',
          generationMeta: null,
          importStatus: 'idle',
          importError: '',
          importReport: null,
          notationReport: null,
          noteEditUndoStack: [],
          noteEditRedoStack: [],
          ...clearedAnalysisState(),
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
    const hasGenerationMeta = Boolean(state.generationMeta);
    const generationForSave = hasGenerationMeta
      ? {
        provider: state.generationMeta.provider || state.selectedProvider || null,
        model: state.generationMeta.model || state.selectedModel || null,
        prompt: buildPromptSnapshot(state.prompt),
      }
      : null;
    const persistRevisionAtStart = projectPersistRevisionKey(state.editedMusicJson, generationForSave);
    const fingerprintDirty = persistRevisionAtStart !== state.lastSavedPersistRevision;
    const eventCount = countEvents(state.editedMusicJson);

    if (!projectId) {
      console.debug('[FIX] Save skipped; no open project', {
        reason,
        fingerprintDirty,
        eventCount,
      });
      if (isManualSaveReason(reason)) {
        set({
          saveStatus: 'error',
          saveError: 'No project open to save',
        });
      }
      return null;
    }

    const validation = validateMusicJson(state.editedMusicJson);
    if (state.editedMusicJson && !validation.valid) {
      console.warn('[musicStore] Save blocked by invalid composition JSON', {
        reason,
        projectId,
        message: validation.message,
      });
      set({
        saveStatus: 'unsaved',
        saveError: validation.message || 'Composition JSON is invalid',
      });
      return null;
    }

    // Autosave stays gated on persist fingerprint; manual Save always PATCHes.
    if (!fingerprintDirty && !isManualSaveReason(reason)) {
      console.debug('[FIX] Save skipped; persist fingerprint clean', {
        reason,
        projectId,
        fingerprintDirty,
        eventCount,
        persistRevision: persistRevisionAtStart.slice(0, 48),
      });
      set({ saveStatus: 'saved', saveError: '' });
      return null;
    }

    console.debug('[FIX] Save starting', {
      reason,
      projectId,
      fingerprintDirty,
      eventCount,
      persistRevision: persistRevisionAtStart.slice(0, 48),
    });

    const requestId = ++autosaveRequestSeq;
    const composition = state.editedMusicJson;
    const payload = {
      composition: composition || undefined,
      clear_composition: !composition,
      ...(hasGenerationMeta
        ? { generation: generationForSave }
        : { clear_generation: true }),
    };

    console.info('[musicStore] Saving project', {
      reason,
      projectId,
      requestId,
      eventCount,
      persistRevision: persistRevisionAtStart.slice(0, 48),
      generationProvider: generationForSave?.provider || null,
      generationModel: generationForSave?.model || null,
      clearGeneration: !hasGenerationMeta,
      promptGenre: generationForSave?.prompt?.genre || null,
      promptMood: generationForSave?.prompt?.mood || null,
    });
    console.debug('[FIX] Save generation payload', {
      reason,
      projectId,
      hasGenerationMeta,
      promptGenre: generationForSave?.prompt?.genre || null,
      promptMood: generationForSave?.prompt?.mood || null,
      promptKeyLength: String(generationForSave?.prompt?.key || '').length,
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
      // Keep in-memory generationMeta aligned with what we just persisted.
      set({ generationMeta: generationForSave });
      const currentPersistRevision = projectPersistRevisionKey(get().editedMusicJson, get().generationMeta);
      if (currentPersistRevision !== persistRevisionAtStart) {
        console.debug('[FIX] Save completed but newer persist fingerprint exists', {
          projectId,
          savedPersistRevision: persistRevisionAtStart.slice(0, 48),
          currentPersistRevision: currentPersistRevision.slice(0, 48),
          eventCount: countEvents(get().editedMusicJson),
        });
        set({
          lastSavedPersistRevision: persistRevisionAtStart,
          saveStatus: 'unsaved',
          saveError: '',
        });
        scheduleAutosave(set, get);
        return project;
      }
      console.info('[musicStore] Project saved', {
        reason,
        projectId,
        eventCount,
      });
      console.debug('[FIX] Save success', {
        reason,
        projectId,
        eventCount,
        persistRevision: persistRevisionAtStart.slice(0, 48),
        promptGenre: generationForSave?.prompt?.genre || null,
        promptMood: generationForSave?.prompt?.mood || null,
      });
      console.debug('[musicStore] Save status transition', { from: 'saving', to: 'saved', reason });
      set({
        lastSavedPersistRevision: persistRevisionAtStart,
        saveStatus: 'saved',
        saveError: '',
        currentProjectName: project.name || get().currentProjectName,
      });
      return project;
    } catch (error) {
      if (requestId !== autosaveRequestSeq) {
        return null;
      }
      console.error('[FIX] Save failed', {
        reason,
        projectId,
        eventCount,
        message: error.message,
        status: error.status,
      });
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

// Expose store for Playwright E2E assertions (event counts, playback status).
if (typeof window !== 'undefined') {
  window.__MUKIT_MUSIC_STORE__ = useMusicStore;
}

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
    pianoRollNoteIds: state.pianoRollNoteIds || [],
  };
}

function filterExistingNoteIds(composition, trackId, noteIds) {
  if (!trackId || !Array.isArray(noteIds) || !noteIds.length) {
    return [];
  }
  const track = composition?.tracks?.find((item) => String(item.id) === String(trackId));
  if (!track || !Array.isArray(track.events)) {
    return [];
  }
  const existing = new Set(track.events.map((event) => String(event.id)));
  return noteIds.filter((id) => existing.has(String(id)));
}

function applyNoteEdit(set, get, {
  nextComposition,
  selectedTrackId,
  selectedNoteId,
  selectedNoteIds = null,
  action,
  noteSummary,
  featureCounts = null,
  skipHistory = false,
  historySnapshot = null,
}) {
  const state = get();
  const revision = compositionRevisionKey(nextComposition);
  const notationRev = notationRevisionKey(nextComposition);
  const staleNotation = notationStalePatch(state.notationRevision, notationRev);
  const resolvedNoteIds = selectedNoteIds ?? (selectedNoteId ? [selectedNoteId] : []);
  console.info('[musicStore] Note edit applied', {
    action,
    trackId: selectedTrackId,
    noteId: selectedNoteId,
    selectedIds: resolvedNoteIds,
    note: noteSummary,
    skipHistory,
  });
  console.debug('[musicStore] Note edit revision/event counts', {
    previousEventCount: countEvents(state.editedMusicJson),
    nextEventCount: countEvents(nextComposition),
    compositionRevision: revision.slice(0, 48),
    featureCounts: featureCounts || countV2FeatureSummary(nextComposition),
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
    notationRevision: notationRev,
    ...staleNotation,
    trackControls: mergeTrackControls(state.trackControls, nextComposition),
    pianoRollTrackId: selectedTrackId,
    pianoRollNoteId: selectedNoteId,
    pianoRollNoteIds: resolvedNoteIds,
    pianoRollEditStatus: 'idle',
    noteEditUndoStack,
    noteEditRedoStack,
    analysisSelectedSectionKey: recoverAnalysisSectionKey(
      nextComposition,
      state.analysisSelectedSectionKey,
    ),
  });
  markProjectDirty(set, get);
  scheduleAnalysisRequest(get, { reason: 'note-edit' });
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

function markProjectDirty(set, get) {
  const state = get();
  if (!state.currentProjectId) {
    return;
  }
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  if (persistRevision === state.lastSavedPersistRevision) {
    console.debug('[FIX] markProjectDirty clean', {
      projectId: state.currentProjectId,
      eventCount: countEvents(state.editedMusicJson),
      persistRevision: persistRevision.slice(0, 48),
    });
    set({ saveStatus: 'saved', saveError: '' });
    cancelAutosaveTimer();
    return;
  }
  console.debug('[FIX] markProjectDirty unsaved', {
    projectId: state.currentProjectId,
    eventCount: countEvents(state.editedMusicJson),
    persistRevision: String(persistRevision).slice(0, 48),
  });
  console.debug('[musicStore] Project marked unsaved', {
    projectId: state.currentProjectId,
    persistRevision: String(persistRevision).slice(0, 48),
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
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  if (persistRevision === state.lastSavedPersistRevision) {
    console.debug('[FIX] Autosave skipped; persist fingerprint clean', {
      projectId: state.currentProjectId,
      eventCount: countEvents(state.editedMusicJson),
    });
    console.debug('[musicStore] Autosave skipped; clean revision');
    return;
  }
  cancelAutosaveTimer();
  console.debug('[musicStore] Autosave debounce scheduled', {
    projectId: state.currentProjectId,
    delayMs: AUTOSAVE_DEBOUNCE_MS,
    persistRevision: persistRevision.slice(0, 48),
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
  const rawComposition = project.composition
    ? ensureCompositionNoteIds(project.composition).composition
    : null;
  const composition = rawComposition ? prepareCompositionForStore(rawComposition) : null;
  const revision = compositionRevisionKey(composition);
  const notationRev = notationRevisionKey(composition);
  const hasStoredGeneration = Boolean(
    project.generation_provider
    || project.generation_model
    || project.generation_prompt,
  );
  const restoredPrompt = promptFromGenerationSnapshot(project.generation_prompt) || { ...initialPrompt };
  // Retain null provenance for imported / never-generated projects.
  const generationMeta = hasStoredGeneration
    ? {
      provider: project.generation_provider || null,
      model: project.generation_model || null,
      prompt: buildPromptSnapshot(restoredPrompt),
    }
    : null;
  const persistRevision = projectPersistRevisionKey(composition, generationMeta);

  console.debug('[musicStore] Hydrating project into composer state', {
    projectId: project.id,
    hasComposition: Boolean(composition),
    hasStoredGeneration,
    openComposer,
    markSaved,
    persistRevision: persistRevision.slice(0, 48),
    restoredPromptGenre: restoredPrompt.genre || null,
    restoredPromptMood: restoredPrompt.mood || null,
  });
  console.debug('[FIX] Hydrate prompt from generation_prompt', {
    projectId: project.id,
    hasGenerationPrompt: Boolean(project.generation_prompt),
    genre: restoredPrompt.genre || null,
    mood: restoredPrompt.mood || null,
  });

  cancelAutosaveTimer();
  cancelAnalysisLifecycle();
  set({
    currentProjectId: project.id,
    currentProjectName: project.name,
    activeView: openComposer ? 'composer' : get().activeView,
    generatedMusicJson: composition,
    editedMusicJson: composition,
    musicXml: '',
    compositionRevision: revision,
    notationRevision: notationRev,
    lastSavedPersistRevision: markSaved ? persistRevision : get().lastSavedPersistRevision,
    saveStatus: markSaved ? 'saved' : 'unsaved',
    saveError: '',
    generationMeta,
    prompt: restoredPrompt,
    trackControls: buildDefaultTrackControls(composition),
    playbackStatus: 'idle',
    playbackSeconds: 0,
    playbackBar: 1,
    pianoRollTrackId: pickDefaultTrackId(composition),
    pianoRollNoteId: null,
    pianoRollNoteIds: [],
    pianoRollEditStatus: 'idle',
    pianoRollNotationStatus: 'idle',
    pianoRollNotationError: '',
    noteEditUndoStack: [],
    noteEditRedoStack: [],
    uiError: '',
    ...clearedAnalysisState(),
  });
}

function syncGenerationMetaFromPrompt(set, get, { reason = 'prompt-edit' } = {}) {
  const state = get();
  if (!state.currentProjectId || !state.generationMeta) {
    // Do not fabricate generation metadata for imported / never-generated projects.
    return;
  }
  const nextMeta = {
    provider: state.generationMeta.provider || state.selectedProvider || null,
    model: state.generationMeta.model || state.selectedModel || null,
    prompt: buildPromptSnapshot(state.prompt),
  };
  const prev = state.generationMeta;
  const unchanged = Boolean(
    prev
    && prev.provider === nextMeta.provider
    && prev.model === nextMeta.model
    && JSON.stringify(prev.prompt) === JSON.stringify(nextMeta.prompt),
  );
  if (unchanged) {
    return;
  }
  console.debug('[FIX] Synced generationMeta from live prompt', {
    reason,
    projectId: state.currentProjectId,
    genre: nextMeta.prompt?.genre || null,
    mood: nextMeta.prompt?.mood || null,
  });
  set({ generationMeta: nextMeta });
  markProjectDirty(set, get);
}

function defaultImportedProjectName(file) {
  const raw = String(file?.name || 'Imported Project').replace(/\\/g, '/').split('/').pop();
  const withoutExt = raw.replace(/\.(mid|midi|musicxml|xml|mxl)$/i, '');
  const trimmed = withoutExt.trim() || 'Imported Project';
  return trimmed.slice(0, 200);
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

function promptFromGenerationSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') {
    return null;
  }
  const instruments = Array.isArray(snapshot.instruments)
    ? snapshot.instruments.join(',')
    : String(snapshot.instruments || '');
  const sections = Array.isArray(snapshot.sections)
    ? snapshot.sections
      .map((section) => {
        if (!section || typeof section !== 'object') {
          return null;
        }
        const type = section.type;
        const bars = section.bars;
        return type && bars != null ? `${type}:${bars}` : null;
      })
      .filter(Boolean)
      .join(',')
    : String(snapshot.sections || '');
  return {
    genre: snapshot.genre ?? '',
    mood: snapshot.mood ?? '',
    key: snapshot.key ?? '',
    time_signature: snapshot.time_signature ?? '4/4',
    tempo_min: snapshot.tempo_min ?? 80,
    tempo_max: snapshot.tempo_max ?? 120,
    instruments,
    sections,
    complexity: snapshot.complexity ?? 'moderate',
    duration_bars: snapshot.duration_bars ?? 20,
    instructions: snapshot.instructions ?? '',
  };
}

function coerceEditableComposition(raw) {
  const withIds = ensureCompositionNoteIds(raw).composition;
  return tryPrepareCompositionForStore(withIds) ?? withIds;
}

function notationStalePatch(previousNotationRevision, nextNotationRevision) {
  if (previousNotationRevision === nextNotationRevision) {
    return {};
  }
  return {
    musicXml: '',
    pianoRollNotationStatus: 'idle',
    pianoRollNotationError: '',
  };
}

function clearedAnalysisState({ preserveScope = false } = {}) {
  const base = {
    analysisResult: null,
    analysisResultKey: null,
    analysisAttemptKey: null,
    analysisStatus: 'idle',
    analysisError: '',
    analysisWarnings: [],
  };
  if (preserveScope) {
    return base;
  }
  return {
    ...base,
    analysisScope: 'composition',
    analysisSelectedSectionKey: null,
    analysisTabVisible: false,
  };
}

function cancelAnalysisDebounce() {
  if (analysisDebounceTimer) {
    clearTimeout(analysisDebounceTimer);
    analysisDebounceTimer = null;
  }
}

function cancelAnalysisLifecycle() {
  cancelAnalysisDebounce();
  analysisRequestSeq += 1;
  analysisInFlightKey = null;
}

function buildDesiredAnalysisRequestKey(state) {
  try {
    const scope = buildAnalysisRequestScope({
      analysisScope: state.analysisScope,
      composition: state.editedMusicJson,
      sectionKey: state.analysisSelectedSectionKey,
      trackId: state.pianoRollTrackId,
    });
    return buildAnalysisRequestKey({
      compositionRevision: state.compositionRevision,
      scope,
    });
  } catch (error) {
    console.debug('[musicStore] Analysis request key unavailable', {
      code: error.code || null,
      message: error.message,
      scopeKind: state.analysisScope,
    });
    return null;
  }
}

function scheduleAnalysisRequest(get, { reason = 'schedule', force = false } = {}) {
  const state = get();
  if (!force && !state.analysisTabVisible) {
    return;
  }
  cancelAnalysisDebounce();
  console.debug('[musicStore] Analysis debounce scheduled', {
    reason,
    force,
    scopeKind: state.analysisScope,
    revisionPrefix: revisionLogPrefix(state.compositionRevision),
  });
  analysisDebounceTimer = setTimeout(() => {
    analysisDebounceTimer = null;
    const store = useMusicStore.getState();
    store.requestAnalysis({ force, reason: `${reason}:debounced` }).catch(() => {
      // error already recorded on analysisStatus
    });
  }, ANALYSIS_DEBOUNCE_MS);
}

async function runAnalysisRequest(set, get, { force = false, reason = 'request' } = {}) {
  const state = get();
  if (!force && !state.analysisTabVisible) {
    console.debug('[musicStore] Analysis request skipped; tab not visible', { reason });
    return null;
  }

  const composition = state.editedMusicJson;
  if (!composition || !isCanonicalComposition(composition)) {
    const message = 'Canonical composition.v2 is required for analysis';
    console.warn('[musicStore] Analysis request blocked', { reason, message });
    set({
      analysisStatus: 'error',
      analysisError: message,
      analysisAttemptKey: null,
    });
    return null;
  }

  let scope;
  try {
    scope = buildAnalysisRequestScope({
      analysisScope: state.analysisScope,
      composition,
      sectionKey: state.analysisSelectedSectionKey,
      trackId: state.pianoRollTrackId,
    });
  } catch (error) {
    const message = error.message || 'Invalid analysis scope';
    console.warn('[musicStore] Analysis scope rejected locally', {
      reason,
      code: error.code || null,
      message,
    });
    set({
      analysisStatus: 'error',
      analysisError: message,
      analysisAttemptKey: null,
    });
    return null;
  }

  const requestKey = buildAnalysisRequestKey({
    compositionRevision: state.compositionRevision,
    scope,
  });

  if (
    !force
    && state.analysisStatus === 'success'
    && analysisRequestKeysEqual(state.analysisResultKey, requestKey)
    && state.analysisResult
  ) {
    console.debug('[musicStore] Analysis reused current result', {
      reason,
      scope: sanitizeScopeForLog(scope),
      revisionPrefix: revisionLogPrefix(state.compositionRevision),
    });
    return state.analysisResult;
  }

  if (!force && analysisInFlightKey && analysisRequestKeysEqual(analysisInFlightKey, requestKey)) {
    console.debug('[musicStore] Analysis deduped identical in-flight request', {
      reason,
      scope: sanitizeScopeForLog(scope),
      revisionPrefix: revisionLogPrefix(state.compositionRevision),
    });
    return null;
  }

  const sequence = ++analysisRequestSeq;
  analysisInFlightKey = requestKey;
  const eventCount = countEvents(composition);
  console.debug('[musicStore] Analysis request started', {
    reason,
    sequence,
    force,
    scope: sanitizeScopeForLog(scope),
    revisionPrefix: revisionLogPrefix(state.compositionRevision),
    trackCount: composition.tracks?.length || 0,
    sectionCount: composition.sections?.length || 0,
    eventCount,
  });

  set({
    analysisStatus: 'loading',
    analysisError: '',
    analysisAttemptKey: requestKey,
    // Retain stale result/warnings during refresh.
  });

  try {
    const report = await analyzeComposition(composition, scope);
    const latest = get();
    if (sequence !== analysisRequestSeq) {
      console.debug('[musicStore] Analysis response discarded (stale sequence)', {
        sequence,
        currentSequence: analysisRequestSeq,
        scope: sanitizeScopeForLog(scope),
      });
      return null;
    }
    let desiredKey = null;
    try {
      desiredKey = buildAnalysisRequestKey({
        compositionRevision: latest.compositionRevision,
        scope: buildAnalysisRequestScope({
          analysisScope: latest.analysisScope,
          composition: latest.editedMusicJson,
          sectionKey: latest.analysisSelectedSectionKey,
          trackId: latest.pianoRollTrackId,
        }),
      });
    } catch {
      desiredKey = null;
    }
    if (!desiredKey || !analysisRequestKeysEqual(requestKey, desiredKey)) {
      console.debug('[musicStore] Analysis response discarded (request key mismatch)', {
        sequence,
        scope: sanitizeScopeForLog(scope),
        revisionPrefix: revisionLogPrefix(latest.compositionRevision),
      });
      if (analysisInFlightKey === requestKey) {
        analysisInFlightKey = null;
      }
      return null;
    }

    const warningCodes = analysisWarningCodes(report.warnings);
    console.info('[musicStore] Analysis result accepted', {
      sequence,
      status: report.status,
      algorithmVersion: report.algorithm_version,
      scopeKind: report.resolved_scope?.kind || null,
      warningCount: report.warnings.length,
      fingerprintPrefix: fingerprintLogPrefix(report.source_fingerprint),
      revisionPrefix: revisionLogPrefix(latest.compositionRevision),
    });
    if (warningCodes.length) {
      console.warn('[musicStore] Analysis warning codes', {
        warningCodes: warningCodes.slice(0, 32),
      });
    }

    if (analysisInFlightKey === requestKey) {
      analysisInFlightKey = null;
    }
    set({
      analysisResult: report,
      analysisResultKey: requestKey,
      analysisAttemptKey: requestKey,
      analysisStatus: 'success',
      analysisError: '',
      analysisWarnings: report.warnings,
    });
    return report;
  } catch (error) {
    if (sequence !== analysisRequestSeq) {
      console.debug('[musicStore] Analysis error discarded (stale sequence)', {
        sequence,
        currentSequence: analysisRequestSeq,
      });
      return null;
    }
    if (analysisInFlightKey === requestKey) {
      analysisInFlightKey = null;
    }
    const code = error instanceof AnalysisApiError || error instanceof AnalysisScopeError
      ? error.code
      : null;
    const message = error.message || 'Composition analysis failed';
    console.warn('[musicStore] Analysis request failed', {
      sequence,
      code,
      message,
      status: error.status || null,
      scope: sanitizeScopeForLog(scope),
    });
    set({
      analysisStatus: 'error',
      analysisError: message,
      analysisAttemptKey: requestKey,
      // Retain previous successful result for stale display.
    });
    return null;
  }
}
