import { create } from 'zustand';
import {
  analyzeComposition,
  AnalysisApiError,
  applyMotif,
  ArrangementApiError,
  DevelopmentApiError,
  importMidi,
  importMusicXml,
  loadArrangementInstruments,
  MotifApiError,
  previewCompositionArrangement,
  previewCompositionDevelopment,
  previewReharmonization,
  ReharmonizeApiError,
  REHARMONIZE_CONTENT_POLICIES,
  REHARMONIZE_ENGINES,
  REHARMONIZE_OPERATIONS,
  renderMusicXmlPreview,
} from '../api/musicApi.js';
import { createAppLogger } from '../utils/appLogger.js';
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
  compositionSourceFingerprint,
  deriveAnalysisFreshness,
  fingerprintLogPrefix,
  normalizeAnalysisScopeKind,
  projectDetectedMotifUsages,
  recoverAnalysisSectionKey,
  revisionLogPrefix,
  sanitizeScopeForLog,
} from '../utils/compositionAnalysis.js';
import {
  applyMotifReconciliation,
  nextMotifLabel,
  projectMotifUsagesForDisplay,
  validateMotifAuthoringSelection,
  validateMotifDefinitions,
} from '../utils/compositionMotifs.js';
import { prepareCompositionForStore, tryPrepareCompositionForStore } from '../utils/compositionVersion.js';
import {
  applyHarmonyAdd,
  applyHarmonyMove,
  applyHarmonyRemove,
  applyHarmonyReplace,
  applyHarmonyResize,
  verifyReharmonizationCandidate,
} from '../utils/compositionHarmony.js';
import {
  DEVELOPMENT_INTENTS,
  DEVELOPMENT_OPERATIONS,
  DEVELOPMENT_SECTION_TYPES,
  VARIATION_STRENGTHS,
  editFingerprintLogPrefix,
  findDevelopmentCandidateById,
  resolveDevelopmentDefaults,
  verifyDevelopmentCandidate,
} from '../utils/compositionCandidates.js';
import {
  ARRANGEMENT_MAX_CANDIDATE_COUNT,
  ARRANGEMENT_MAX_INSTRUCTION_CHARS,
  ARRANGEMENT_MIN_CANDIDATE_COUNT,
  ARRANGEMENT_OPERATIONS,
  ARRANGEMENT_RANGE_ADJUSTMENTS,
  editFingerprintLogPrefix as arrangementFingerprintPrefix,
  findArrangementCandidateById,
  getCachedArrangementCatalog,
  verifyArrangementCandidateForApply,
} from '../utils/compositionArrangementCandidates.js';
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

const MOTIF_CREATIVE_OPERATIONS = new Set([
  'rhythmic_variation',
  'melodic_variation',
  'answer',
  'counterphrase',
]);

export const DEFAULT_MOTIF_OPERATION = 'repeat';
export const DEFAULT_MOTIF_VARIATION_STRENGTH = 0.5;

const initialMotifUiState = {
  motifSelectedMotifId: null,
  motifSelectedOccurrenceId: null,
  motifHighlightedUsageKey: null,
  motifDestinationSectionId: null,
  motifDestinationTrackId: null,
  motifDestinationStartBar: null,
  motifDestinationStartTick: null,
  motifOperation: DEFAULT_MOTIF_OPERATION,
  motifOperationParams: {},
  motifVariationStrength: DEFAULT_MOTIF_VARIATION_STRENGTH,
  motifApplyStatus: 'idle',
  motifApplyError: '',
  motifApplyWarnings: [],
  motifReconcileWarnings: [],
};

export const DEFAULT_REHARMONIZE_OPERATION = 'increase_tension';
export const DEFAULT_REHARMONIZE_CONTENT_POLICY = 'preserve_melody_adapt_harmony';
export const DEFAULT_REHARMONIZE_ENGINE = 'deterministic';

const initialHarmonyUiState = {
  harmonySelectionStartBar: null,
  harmonySelectionEndBar: null,
  harmonySelectedSpanStartTick: null,
};

const initialReharmonizePreviewState = {
  reharmonizeStatus: 'idle',
  reharmonizeError: '',
  reharmonizeWarnings: [],
  reharmonizeRequestId: 0,
  reharmonizeBaseFingerprint: null,
  reharmonizeBaseRevision: null,
  reharmonizeProposalFingerprint: null,
  reharmonizeCandidate: null,
  reharmonizeHarmonyChanges: [],
  reharmonizeTrackChanges: [],
  reharmonizePreservation: [],
  reharmonizeCompatibility: null,
  reharmonizeProvider: null,
  reharmonizeModel: null,
  reharmonizeStartTick: null,
  reharmonizeEndTick: null,
  reharmonizeActiveKey: null,
  reharmonizeRecommendedTargetTrackIds: [],
  reharmonizeOperation: DEFAULT_REHARMONIZE_OPERATION,
  reharmonizeContentPolicy: DEFAULT_REHARMONIZE_CONTENT_POLICY,
  reharmonizeEngine: DEFAULT_REHARMONIZE_ENGINE,
  reharmonizeInstruction: '',
  reharmonizeTargetTrackIds: [],
  reharmonizeAllowModulation: false,
  reharmonizeTargetKey: '',
  reharmonizeTargetChord: '',
};

export const DEFAULT_DEVELOPMENT_OPERATION = 'continue';
export const DEFAULT_DEVELOPMENT_INTENT = 'continue';
export const DEFAULT_VARIATION_STRENGTH = 'balanced';

export const DEFAULT_ARRANGEMENT_OPERATION = 'change_instrumentation';
export const DEFAULT_ARRANGEMENT_RANGE_ADJUSTMENT = 'reject';
export const ARRANGEMENT_AUDITION_SOURCE = 'source';
export const ARRANGEMENT_AUDITION_CANDIDATE = 'candidate';

const arrangementLogger = createAppLogger('musicStore.arrangement');

const initialDevelopmentControlsState = {
  developmentOperation: DEFAULT_DEVELOPMENT_OPERATION,
  developmentIntent: DEFAULT_DEVELOPMENT_INTENT,
  developmentStrength: DEFAULT_VARIATION_STRENGTH,
  developmentOutputBars: 8,
  developmentCandidateCount: 1,
  developmentInstruction: '',
  developmentTargetSectionType: 'verse',
  developmentTargetSectionLabel: '',
  developmentAllowModulation: false,
  developmentSourceStartBar: null,
  developmentSourceEndBar: null,
  developmentSourceSectionKey: null,
};

const initialDevelopmentPreviewState = {
  ...initialDevelopmentControlsState,
  developmentStatus: 'idle',
  developmentError: '',
  developmentWarnings: [],
  developmentRequestId: 0,
  developmentBaseRevision: null,
  developmentEditSourceFingerprint: null,
  developmentCandidates: [],
  developmentSelectedCandidateId: null,
  developmentAuditionActive: false,
  developmentProvider: null,
  developmentModel: null,
};

const initialArrangementControlsState = {
  arrangementOperation: DEFAULT_ARRANGEMENT_OPERATION,
  arrangementSourceTrackIds: [],
  arrangementProtectedTrackIds: [],
  arrangementInstrumentationBefore: [],
  arrangementInstrumentationAfter: [],
  arrangementAllowUnlistedAfter: false,
  arrangementPreserveMelody: true,
  arrangementPreserveHarmony: true,
  arrangementRangeAdjustment: DEFAULT_ARRANGEMENT_RANGE_ADJUSTMENT,
  arrangementCandidateCount: 1,
  arrangementInstruction: '',
};

const initialArrangementPreviewState = {
  ...initialArrangementControlsState,
  arrangementCatalog: null,
  arrangementCatalogStatus: 'idle',
  arrangementCatalogError: '',
  arrangementCatalogFingerprint: null,
  arrangementStatus: 'idle',
  arrangementError: '',
  arrangementStaleReason: null,
  arrangementWarnings: [],
  arrangementRequestId: 0,
  arrangementBaseRevision: null,
  arrangementEditSourceFingerprint: null,
  arrangementResponseCatalogFingerprint: null,
  arrangementControlsFingerprint: null,
  arrangementCandidates: [],
  arrangementRejectedAttempts: [],
  arrangementSelectedCandidateId: null,
  arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
  arrangementCandidateTrackControls: {},
  arrangementProvider: null,
  arrangementModel: null,
};

function isManualSaveReason(reason) {
  return reason === 'manual' || reason === 'manual-force';
}

let autosaveTimer = null;
let autosaveRequestSeq = 0;

let analysisRequestSeq = 0;
let analysisDebounceTimer = null;
let analysisInFlightKey = null;
let reharmonizeRequestSeq = 0;
let developmentRequestSeq = 0;
let arrangementRequestSeq = 0;
let arrangementCatalogRequestSeq = 0;

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

  ...initialHarmonyUiState,
  ...initialReharmonizePreviewState,
  ...initialDevelopmentPreviewState,
  ...initialArrangementPreviewState,
  composerTabRequest: null,
  composerTabRequestSeq: 0,
  ...initialMotifUiState,

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
      ...clearedMotifUiState(),
      ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
      ...initialHarmonyUiState,
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
      ...clearedMotifUiState(),
      ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
      ...initialHarmonyUiState,
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
      ...clearedMotifUiState(),
      ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
      ...initialHarmonyUiState,
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
        ...clearedMotifUiState(),
        ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
        ...initialHarmonyUiState,
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
      const reconciled = applyMotifReconciliation(result.composition, [noteId]);
      const validation = validateMusicJson(reconciled.composition);
      if (!validation.valid) {
        console.warn('[musicStore] deleteNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      applyNoteEdit(set, get, {
        nextComposition: reconciled.composition,
        selectedTrackId: trackId,
        selectedNoteId: null,
        action: 'delete',
        noteSummary: sanitizeNoteSummary(result.deleted),
        statePatch: reconcileMotifUiAfterCompositionChange(get(), reconciled.composition, {
          reconcileWarnings: reconciled.warnings,
        }),
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
      trackControls: previous.trackControls
        ? { ...previous.trackControls }
        : mergeTrackControls(state.trackControls, previous.editedMusicJson),
      pianoRollTrackId: previous.pianoRollTrackId,
      pianoRollNoteId: previous.pianoRollNoteId,
      pianoRollNoteIds: previous.pianoRollNoteIds || [],
      harmonySelectionStartBar: previous.harmonySelectionStartBar ?? state.harmonySelectionStartBar,
      harmonySelectionEndBar: previous.harmonySelectionEndBar ?? state.harmonySelectionEndBar,
      harmonySelectedSpanStartTick: previous.harmonySelectedSpanStartTick ?? null,
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      analysisSelectedSectionKey: recoverAnalysisSectionKey(
        previous.editedMusicJson,
        state.analysisSelectedSectionKey,
      ),
      ...reconcileMotifUiAfterCompositionChange(state, previous.editedMusicJson),
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
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
      trackControls: next.trackControls
        ? { ...next.trackControls }
        : mergeTrackControls(state.trackControls, next.editedMusicJson),
      pianoRollTrackId: next.pianoRollTrackId,
      pianoRollNoteId: next.pianoRollNoteId,
      pianoRollNoteIds: next.pianoRollNoteIds || [],
      harmonySelectionStartBar: next.harmonySelectionStartBar ?? state.harmonySelectionStartBar,
      harmonySelectionEndBar: next.harmonySelectionEndBar ?? state.harmonySelectionEndBar,
      harmonySelectedSpanStartTick: next.harmonySelectedSpanStartTick ?? null,
      noteEditUndoStack: nextUndo,
      noteEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      analysisSelectedSectionKey: recoverAnalysisSectionKey(
        next.editedMusicJson,
        state.analysisSelectedSectionKey,
      ),
      ...reconcileMotifUiAfterCompositionChange(state, next.editedMusicJson),
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
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
      ...clearedMotifUiState(),
      ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
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
          ...clearedMotifUiState(),
          ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
          ...initialHarmonyUiState,
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

  selectMotif: (motifId) => {
    const normalizedId = typeof motifId === 'string' && motifId.trim() ? motifId.trim() : null;
    const composition = get().editedMusicJson;
    const motif = normalizedId
      ? (composition?.motifs || []).find((item) => item.id === normalizedId)
      : null;
    const original = motif?.occurrences?.find((item) => item.relationship === 'original') || null;
    console.debug('[musicStore] Motif selected', {
      motifId: normalizedId,
      occurrenceId: original?.id || null,
    });
    set({
      motifSelectedMotifId: motif?.id || null,
      motifSelectedOccurrenceId: original?.id || null,
      motifHighlightedUsageKey: motif && original
        ? `canonical:${motif.id}:${original.id}`
        : null,
    });
  },

  renameMotif: (motifId, label) => {
    const state = get();
    const current = state.editedMusicJson;
    const trimmedLabel = String(label || '').trim();
    if (!motifId || !trimmedLabel || !isCanonicalComposition(current)) {
      console.warn('[musicStore] renameMotif rejected', { motifId, labelLength: trimmedLabel.length });
      return false;
    }
    const motifs = Array.isArray(current.motifs) ? current.motifs : [];
    const index = motifs.findIndex((item) => item.id === motifId);
    if (index < 0) {
      console.warn('[musicStore] renameMotif motif not found', { motifId });
      return false;
    }
    if (motifs.some((item, itemIndex) => itemIndex !== index && item.label === trimmedLabel)) {
      console.warn('[musicStore] renameMotif duplicate label', { motifId, label: trimmedLabel });
      return false;
    }
    const nextMotifs = motifs.map((item, itemIndex) => (
      itemIndex === index ? { ...item, label: trimmedLabel } : item
    ));
    const nextComposition = { ...current, motifs: nextMotifs };
    const motifValidation = validateMotifDefinitions(nextComposition);
    if (!motifValidation.valid) {
      console.warn('[musicStore] renameMotif failed motif validation', { message: motifValidation.message });
      return false;
    }
    const validation = validateMusicJson(nextComposition);
    if (!validation.valid) {
      console.warn('[musicStore] renameMotif failed validation', { message: validation.message });
      return false;
    }
    applyNoteEdit(set, get, {
      nextComposition,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      action: 'motif-rename',
      noteSummary: { motifId, label: trimmedLabel },
    });
    console.info('[musicStore] Motif renamed', { motifId, label: trimmedLabel });
    return true;
  },

  deleteMotif: (motifId) => {
    const state = get();
    const current = state.editedMusicJson;
    if (!motifId || !isCanonicalComposition(current)) {
      console.warn('[musicStore] deleteMotif rejected', { motifId });
      return false;
    }
    const motifs = Array.isArray(current.motifs) ? current.motifs : [];
    if (!motifs.some((item) => item.id === motifId)) {
      console.warn('[musicStore] deleteMotif motif not found', { motifId });
      return false;
    }
    const nextComposition = {
      ...current,
      motifs: motifs.filter((item) => item.id !== motifId),
    };
    const validation = validateMusicJson(nextComposition);
    if (!validation.valid) {
      console.warn('[musicStore] deleteMotif failed validation', { message: validation.message });
      return false;
    }
    applyNoteEdit(set, get, {
      nextComposition,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      action: 'motif-delete',
      noteSummary: { motifId },
      statePatch: reconcileMotifUiAfterCompositionChange(state, nextComposition, {
        clearedSelection: state.motifSelectedMotifId === motifId,
      }),
    });
    console.info('[musicStore] Motif deleted', { motifId });
    return true;
  },

  markMotifFromSelection: ({ label = null } = {}) => {
    const state = get();
    const current = state.editedMusicJson;
    if (!isCanonicalComposition(current)) {
      console.warn('[musicStore] markMotifFromSelection rejected non-canonical composition');
      return { ok: false, message: 'Canonical composition required', code: 'motif_invalid_composition' };
    }
    const selection = validateMotifAuthoringSelection(current, {
      trackId: state.pianoRollTrackId,
      eventIds: state.pianoRollNoteIds,
    });
    if (!selection.valid) {
      console.warn('[musicStore] markMotifFromSelection rejected', {
        code: selection.code || null,
        message: selection.message,
      });
      return { ok: false, message: selection.message, code: selection.code || null };
    }
    const existingMotifs = Array.isArray(current.motifs) ? current.motifs : [];
    const motifId = createMotifEntityId('motif');
    const occurrenceId = createMotifEntityId('occ');
    const motifLabel = String(label || '').trim() || nextMotifLabel(existingMotifs);
    const nextComposition = {
      ...current,
      motifs: [
        ...existingMotifs,
        {
          id: motifId,
          label: motifLabel,
          occurrences: [{
            id: occurrenceId,
            track_id: selection.trackId,
            event_ids: selection.eventIds,
            relationship: 'original',
          }],
        },
      ],
    };
    const motifValidation = validateMotifDefinitions(nextComposition);
    if (!motifValidation.valid) {
      console.warn('[musicStore] markMotifFromSelection failed motif validation', {
        message: motifValidation.message,
      });
      return { ok: false, message: motifValidation.message, code: 'motif_invalid_definition' };
    }
    const validation = validateMusicJson(nextComposition);
    if (!validation.valid) {
      console.warn('[musicStore] markMotifFromSelection failed validation', { message: validation.message });
      return { ok: false, message: validation.message, code: 'motif_invalid_composition' };
    }
    applyNoteEdit(set, get, {
      nextComposition,
      selectedTrackId: selection.trackId,
      selectedNoteId: selection.eventIds[0] || null,
      selectedNoteIds: selection.eventIds,
      action: 'motif-mark',
      noteSummary: {
        motifId,
        occurrenceId,
        label: motifLabel,
        eventCount: selection.eventIds.length,
      },
      statePatch: {
        motifSelectedMotifId: motifId,
        motifSelectedOccurrenceId: occurrenceId,
        motifHighlightedUsageKey: `canonical:${motifId}:${occurrenceId}`,
        motifApplyStatus: 'idle',
        motifApplyError: '',
        motifApplyWarnings: [],
        motifReconcileWarnings: [],
      },
    });
    console.info('[musicStore] Motif marked from selection', {
      motifId,
      occurrenceId,
      label: motifLabel,
      eventCount: selection.eventIds.length,
    });
    return {
      ok: true,
      motifId,
      occurrenceId,
      label: motifLabel,
      eventCount: selection.eventIds.length,
    };
  },

  getMotifUsages: () => {
    const state = get();
    const freshness = state.getAnalysisFreshness();
    const currentFingerprint = freshness.isCurrent
      ? state.analysisResult?.source_fingerprint
      : null;
    const detected = state.analysisResult
      ? projectDetectedMotifUsages(state.editedMusicJson, state.analysisResult, {
        currentFingerprint,
      })
      : [];
    return projectMotifUsagesForDisplay(state.editedMusicJson, {
      analysisReport: state.analysisResult,
      detectedUsages: detected,
      includeDetected: Boolean(state.analysisResult),
    });
  },

  selectMotifUsage: ({
    usageKey = null,
    motifId = null,
    occurrenceId = null,
    trackId = null,
    eventIds = null,
  } = {}) => {
    const resolvedTrackId = trackId || get().pianoRollTrackId;
    const resolvedEventIds = Array.isArray(eventIds)
      ? eventIds.map(String)
      : (get().pianoRollNoteIds || []);
    console.debug('[musicStore] Motif usage selected', {
      usageKey: usageKey || null,
      motifId: motifId || null,
      occurrenceId: occurrenceId || null,
      trackId: resolvedTrackId,
      eventCount: resolvedEventIds.length,
    });
    set({
      motifHighlightedUsageKey: usageKey || null,
      motifSelectedMotifId: motifId || get().motifSelectedMotifId,
      motifSelectedOccurrenceId: occurrenceId || get().motifSelectedOccurrenceId,
      pianoRollTrackId: pickDefaultTrackId(get().editedMusicJson, resolvedTrackId),
      pianoRollNoteIds: resolvedEventIds,
      pianoRollNoteId: resolvedEventIds[0] || null,
    });
  },

  navigateMotifUsage: (direction = 1) => {
    const state = get();
    const usages = state.getMotifUsages();
    if (!usages.length) {
      console.warn('[musicStore] navigateMotifUsage ignored; no usages');
      return false;
    }
    const delta = direction === 'prev' ? -1 : (direction === 'next' ? 1 : Number(direction));
    if (!Number.isFinite(delta) || delta === 0) {
      return false;
    }
    const currentIndex = usages.findIndex((item) => item.key === state.motifHighlightedUsageKey);
    const startIndex = currentIndex >= 0 ? currentIndex : 0;
    const nextIndex = (startIndex + delta + usages.length) % usages.length;
    const usage = usages[nextIndex];
    state.selectMotifUsage({
      usageKey: usage.key,
      motifId: usage.motifId,
      occurrenceId: usage.occurrenceId,
      trackId: usage.trackId,
      eventIds: usage.eventIds,
    });
    console.info('[musicStore] Motif usage navigated', {
      direction: delta,
      usageKey: usage.key,
      index: nextIndex,
      total: usages.length,
    });
    return true;
  },

  configureMotifDestination: (updates = {}) => {
    const patch = {};
    if (Object.prototype.hasOwnProperty.call(updates, 'sectionId')) {
      const sectionId = updates.sectionId;
      patch.motifDestinationSectionId = typeof sectionId === 'string' && sectionId.trim()
        ? sectionId.trim()
        : null;
    }
    if (Object.prototype.hasOwnProperty.call(updates, 'trackId')) {
      const trackId = updates.trackId;
      patch.motifDestinationTrackId = typeof trackId === 'string' && trackId.trim()
        ? trackId.trim()
        : null;
    }
    if (Object.prototype.hasOwnProperty.call(updates, 'startBar')) {
      const bar = Number(updates.startBar);
      patch.motifDestinationStartBar = Number.isInteger(bar) && bar >= 1 ? bar : null;
      // Bar-only updates must not leave a stale absolute tick (Number(null) === 0).
      if (!Object.prototype.hasOwnProperty.call(updates, 'startTick')) {
        patch.motifDestinationStartTick = null;
      }
    }
    if (Object.prototype.hasOwnProperty.call(updates, 'startTick')) {
      const tick = updates.startTick;
      if (tick == null || tick === '') {
        patch.motifDestinationStartTick = null;
      } else {
        const numeric = Number(tick);
        patch.motifDestinationStartTick = Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
      }
    }
    console.debug('[musicStore] Motif destination configured', {
      sectionId: patch.motifDestinationSectionId ?? get().motifDestinationSectionId,
      trackId: patch.motifDestinationTrackId ?? get().motifDestinationTrackId,
      startBar: patch.motifDestinationStartBar ?? get().motifDestinationStartBar,
      startTick: Object.prototype.hasOwnProperty.call(patch, 'motifDestinationStartTick')
        ? patch.motifDestinationStartTick
        : get().motifDestinationStartTick,
    });
    set(patch);
  },

  configureMotifTransformation: ({
    operation = null,
    operationParams = null,
    variationStrength = null,
  } = {}) => {
    const patch = {};
    if (typeof operation === 'string' && operation.trim()) {
      patch.motifOperation = operation.trim();
    }
    if (operationParams && typeof operationParams === 'object' && !Array.isArray(operationParams)) {
      patch.motifOperationParams = { ...operationParams };
    }
    if (variationStrength != null) {
      const strength = Number(variationStrength);
      if (Number.isFinite(strength)) {
        patch.motifVariationStrength = Math.max(0, Math.min(1, strength));
      }
    }
    console.debug('[musicStore] Motif transformation configured', {
      operation: patch.motifOperation ?? get().motifOperation,
      variationStrength: patch.motifVariationStrength ?? get().motifVariationStrength,
      paramKeys: Object.keys(patch.motifOperationParams ?? get().motifOperationParams ?? {}),
    });
    set(patch);
  },

  resetMotifUiState: () => {
    console.debug('[musicStore] Motif UI state reset');
    set(clearedMotifUiState());
  },

  startMotifApply: () => {
    const state = get();
    if (state.motifApplyStatus === 'loading') {
      console.warn('[musicStore] Duplicate motif apply blocked', {
        motifId: state.motifSelectedMotifId,
        operation: state.motifOperation,
      });
      return false;
    }
    if (!state.editedMusicJson || !isCanonicalComposition(state.editedMusicJson)) {
      console.warn('[musicStore] Motif apply start rejected; composition missing/invalid');
      return false;
    }
    if (!state.motifSelectedMotifId || !state.motifSelectedOccurrenceId) {
      console.warn('[musicStore] Motif apply start rejected; no source motif/occurrence');
      return false;
    }
    if (!state.motifDestinationTrackId || !state.motifDestinationStartBar) {
      console.warn('[musicStore] Motif apply start rejected; destination incomplete');
      return false;
    }
    const payloadError = validateMotifApplyRequest(state);
    if (payloadError) {
      console.warn('[musicStore] Motif apply start rejected', { message: payloadError });
      set({
        motifApplyStatus: 'error',
        motifApplyError: payloadError,
      });
      return false;
    }
    console.info('[musicStore] Motif apply started', {
      motifId: state.motifSelectedMotifId,
      occurrenceId: state.motifSelectedOccurrenceId,
      operation: state.motifOperation,
      destinationTrackId: state.motifDestinationTrackId,
      destinationStartBar: state.motifDestinationStartBar,
    });
    set({
      motifApplyStatus: 'loading',
      motifApplyError: '',
      motifApplyWarnings: [],
    });
    return true;
  },

  failMotifApply: (message, { code = null } = {}) => {
    const safeMessage = message || 'Motif apply failed';
    console.error('[musicStore] Motif apply failed', { message: safeMessage, code });
    set({
      motifApplyStatus: 'error',
      motifApplyError: safeMessage,
      motifApplyWarnings: [],
    });
  },

  completeMotifApply: ({
    composition,
    musicxml = '',
    warnings = [],
    result = null,
  } = {}) => {
    const state = get();
    const prepared = prepareCompositionForStore(ensureCompositionNoteIds(composition).composition);
    const validation = validateMusicJson(prepared);
    const motifValidation = validateMotifDefinitions(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared) || !motifValidation.valid) {
      const message = validation.message || motifValidation.message || 'Motif apply result failed validation';
      console.error('[musicStore] Motif apply completion rejected invalid composition', { message });
      set({
        motifApplyStatus: 'error',
        motifApplyError: message,
      });
      return false;
    }

    const historySnapshot = snapshotNoteEditState(state);
    const revision = compositionRevisionKey(prepared);
    const notationRev = notationRevisionKey(prepared);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    const newOccurrenceId = result?.new_occurrence_id || null;
    console.info('[musicStore] Motif apply completed', {
      motifId: result?.motif_id || state.motifSelectedMotifId,
      sourceOccurrenceId: result?.source_occurrence_id || state.motifSelectedOccurrenceId,
      newOccurrenceId,
      operation: result?.relationship || state.motifOperation,
      warningCount: warnings.length,
      eventCount: countEvents(prepared),
      compositionRevision: revision.slice(0, 48),
    });

    set({
      editedMusicJson: prepared,
      musicXml: musicxml || state.musicXml || '',
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: mergeTrackControls(state.trackControls, prepared),
      pianoRollTrackId: pickDefaultTrackId(prepared, result?.destination_track_id || state.motifDestinationTrackId),
      pianoRollNoteId: null,
      pianoRollNoteIds: Array.isArray(result?.created_event_ids) ? result.created_event_ids : [],
      noteEditUndoStack: [...state.noteEditUndoStack, historySnapshot].slice(-MAX_UNDO_HISTORY),
      noteEditRedoStack: [],
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      motifApplyStatus: 'success',
      motifApplyError: '',
      motifApplyWarnings: Array.isArray(warnings) ? warnings : [],
      motifSelectedMotifId: result?.motif_id || state.motifSelectedMotifId,
      motifSelectedOccurrenceId: newOccurrenceId || state.motifSelectedOccurrenceId,
      motifHighlightedUsageKey: result?.motif_id && newOccurrenceId
        ? `canonical:${result.motif_id}:${newOccurrenceId}`
        : state.motifHighlightedUsageKey,
      analysisSelectedSectionKey: recoverAnalysisSectionKey(prepared, state.analysisSelectedSectionKey),
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    });
    markProjectDirty(set, get);
    scheduleAnalysisRequest(get, { reason: 'motif-apply' });
    return true;
  },

  applyMotifTransformation: async () => {
    const started = get().startMotifApply();
    if (!started) {
      return false;
    }
    const payload = buildMotifApplyPayload(get());
    try {
      const response = await applyMotif(payload);
      return get().completeMotifApply({
        composition: response.composition,
        musicxml: response.musicxml,
        warnings: response.warnings,
        result: response.result,
      });
    } catch (error) {
      const message = error instanceof MotifApiError
        ? error.message
        : (error.message || 'Motif apply failed');
      get().failMotifApply(message, { code: error.code || null });
      return false;
    }
  },

  setHarmonySelection: (startBar, endBar) => {
    const start = Number(startBar);
    const end = Number(endBar);
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
      console.warn('[musicStore] setHarmonySelection rejected invalid bars', { startBar, endBar });
      return false;
    }
    console.debug('[musicStore] Harmony selection set', { startBar: start, endBar: end });
    set({
      harmonySelectionStartBar: start,
      harmonySelectionEndBar: end,
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    });
    return true;
  },

  setHarmonySelectedSpan: (startTick) => {
    const tick = startTick == null ? null : Number(startTick);
    set({
      harmonySelectedSpanStartTick: Number.isInteger(tick) ? tick : null,
    });
  },

  setReharmonizeControls: (patch = {}) => {
    const next = {};
    if (patch.operation != null) {
      if (!REHARMONIZE_OPERATIONS.includes(patch.operation)) {
        return false;
      }
      next.reharmonizeOperation = patch.operation;
    }
    if (patch.contentPolicy != null) {
      if (!REHARMONIZE_CONTENT_POLICIES.includes(patch.contentPolicy)) {
        return false;
      }
      next.reharmonizeContentPolicy = patch.contentPolicy;
    }
    if (patch.engine != null) {
      if (!REHARMONIZE_ENGINES.includes(patch.engine)) {
        return false;
      }
      next.reharmonizeEngine = patch.engine;
    }
    if (patch.instruction != null) {
      next.reharmonizeInstruction = String(patch.instruction).slice(0, 500);
    }
    if (Array.isArray(patch.targetTrackIds)) {
      next.reharmonizeTargetTrackIds = patch.targetTrackIds
        .map((id) => String(id).trim())
        .filter(Boolean);
    }
    if (patch.allowModulation != null) {
      next.reharmonizeAllowModulation = Boolean(patch.allowModulation);
    }
    if (patch.targetKey != null) {
      next.reharmonizeTargetKey = String(patch.targetKey);
    }
    if (patch.targetChord != null) {
      next.reharmonizeTargetChord = String(patch.targetChord);
    }
    set({
      ...next,
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    });
    return true;
  },

  commitHarmonyComposition: (nextComposition, { action = 'harmony-edit', skipHistory = false } = {}) => {
    const state = get();
    const prepared = prepareCompositionForStore(nextComposition);
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
      console.warn('[musicStore] Harmony edit rejected validation', { action, message: validation.message });
      return false;
    }
    applyCompositionEdit(set, get, {
      nextComposition: prepared,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      action,
      noteSummary: null,
      skipHistory,
      statePatch: {
        ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
      },
    });
    return true;
  },

  addHarmonySpan: (span) => {
    const current = get().editedMusicJson;
    if (!isCanonicalComposition(current)) {
      return false;
    }
    try {
      return get().commitHarmonyComposition(applyHarmonyAdd(current, span), { action: 'harmony-add' });
    } catch (error) {
      console.warn('[musicStore] harmony-add failed', { code: error.code, message: error.message });
      return false;
    }
  },

  replaceHarmonyRange: (payload) => {
    const current = get().editedMusicJson;
    if (!isCanonicalComposition(current)) {
      return false;
    }
    try {
      return get().commitHarmonyComposition(applyHarmonyReplace(current, payload), {
        action: 'harmony-replace',
      });
    } catch (error) {
      console.warn('[musicStore] harmony-replace failed', { code: error.code, message: error.message });
      return false;
    }
  },

  removeHarmonyRange: (payload) => {
    const current = get().editedMusicJson;
    if (!isCanonicalComposition(current)) {
      return false;
    }
    try {
      return get().commitHarmonyComposition(applyHarmonyRemove(current, payload), {
        action: 'harmony-remove',
      });
    } catch (error) {
      console.warn('[musicStore] harmony-remove failed', { code: error.code, message: error.message });
      return false;
    }
  },

  moveHarmonySpan: (payload, { skipHistory = false } = {}) => {
    const current = get().editedMusicJson;
    if (!isCanonicalComposition(current)) {
      return false;
    }
    try {
      return get().commitHarmonyComposition(applyHarmonyMove(current, payload), {
        action: 'harmony-move',
        skipHistory,
      });
    } catch (error) {
      console.warn('[musicStore] harmony-move failed', { code: error.code, message: error.message });
      return false;
    }
  },

  resizeHarmonySpan: (payload, { skipHistory = false } = {}) => {
    const current = get().editedMusicJson;
    if (!isCanonicalComposition(current)) {
      return false;
    }
    try {
      return get().commitHarmonyComposition(applyHarmonyResize(current, payload), {
        action: 'harmony-resize',
        skipHistory,
      });
    } catch (error) {
      console.warn('[musicStore] harmony-resize failed', { code: error.code, message: error.message });
      return false;
    }
  },

  discardReharmonizePreview: () => {
    console.info('[musicStore] Reharmonize preview discarded');
    set({
      ...clearedReharmonizePreviewState({ preserveControls: true }),
    });
  },

  requestComposerTab: (tabId) => {
    if (typeof tabId !== 'string' || !tabId.trim()) {
      return;
    }
    set((state) => ({
      composerTabRequest: tabId.trim(),
      composerTabRequestSeq: (state.composerTabRequestSeq || 0) + 1,
    }));
  },

  syncDevelopmentDefaultsFromComposition: () => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      return;
    }
    const defaults = resolveDevelopmentDefaults(composition, {
      operation: state.developmentOperation,
      aiEditStartBar: state.aiEditStartBar,
      aiEditEndBar: state.aiEditEndBar,
    });
    set({
      developmentSourceStartBar: defaults.sourceStartBar,
      developmentSourceEndBar: defaults.sourceEndBar,
      developmentSourceSectionKey: defaults.sourceSectionKey,
      developmentOutputBars: defaults.outputBars ?? state.developmentOutputBars,
      developmentTargetSectionType: defaults.targetSectionType ?? state.developmentTargetSectionType,
    });
  },

  setDevelopmentControls: (patch = {}) => {
    const next = {};
    if (patch.operation != null) {
      if (!DEVELOPMENT_OPERATIONS.includes(patch.operation)) {
        return false;
      }
      next.developmentOperation = patch.operation;
    }
    if (patch.intent != null) {
      if (!DEVELOPMENT_INTENTS.includes(patch.intent)) {
        return false;
      }
      next.developmentIntent = patch.intent;
    }
    if (patch.strength != null) {
      if (!VARIATION_STRENGTHS.includes(patch.strength)) {
        return false;
      }
      next.developmentStrength = patch.strength;
    }
    if (patch.outputBars != null) {
      const bars = Number(patch.outputBars);
      if (!Number.isInteger(bars) || bars < 1 || bars > 64) {
        return false;
      }
      next.developmentOutputBars = bars;
    }
    if (patch.candidateCount != null) {
      const count = Number(patch.candidateCount);
      if (!Number.isInteger(count) || count < 1 || count > 4) {
        return false;
      }
      next.developmentCandidateCount = count;
    }
    if (patch.instruction != null) {
      next.developmentInstruction = String(patch.instruction).slice(0, 500);
    }
    if (patch.targetSectionType != null) {
      if (patch.targetSectionType !== '' && !DEVELOPMENT_SECTION_TYPES.includes(patch.targetSectionType)) {
        return false;
      }
      next.developmentTargetSectionType = patch.targetSectionType || null;
    }
    if (patch.targetSectionLabel != null) {
      next.developmentTargetSectionLabel = String(patch.targetSectionLabel).slice(0, 80);
    }
    if (patch.allowModulation != null) {
      next.developmentAllowModulation = Boolean(patch.allowModulation);
    }
    if (patch.sourceStartBar != null || patch.sourceEndBar != null) {
      const start = Number(patch.sourceStartBar ?? get().developmentSourceStartBar);
      const end = Number(patch.sourceEndBar ?? get().developmentSourceEndBar);
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
        return false;
      }
      next.developmentSourceStartBar = start;
      next.developmentSourceEndBar = end;
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'sourceSectionKey')) {
      next.developmentSourceSectionKey = patch.sourceSectionKey || null;
    }
    set({
      ...next,
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    });
    return true;
  },

  openDevelopWithAiSelection: () => {
    const state = get();
    const defaults = resolveDevelopmentDefaults(state.editedMusicJson, {
      operation: 'vary_section',
      aiEditStartBar: state.aiEditStartBar,
      aiEditEndBar: state.aiEditEndBar,
    });
    set({
      developmentOperation: 'vary_section',
      developmentSourceStartBar: defaults.sourceStartBar,
      developmentSourceEndBar: defaults.sourceEndBar,
      developmentSourceSectionKey: defaults.sourceSectionKey,
      developmentOutputBars: null,
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
      composerTabRequest: 'develop',
      composerTabRequestSeq: (state.composerTabRequestSeq || 0) + 1,
    });
    console.info('[musicStore] Opened Develop tab with AI selection', {
      startBar: defaults.sourceStartBar,
      endBar: defaults.sourceEndBar,
    });
  },

  selectDevelopmentCandidate: (candidateId) => {
    const state = get();
    const candidate = findDevelopmentCandidateById(state.developmentCandidates, candidateId);
    if (!candidate) {
      console.warn('[musicStore] Development candidate selection ignored', {
        candidateIdSuffix: String(candidateId || '').slice(-8),
      });
      return false;
    }
    console.info('[musicStore] Development candidate selected', {
      candidateIdSuffix: candidateId.slice(-8),
      barCount: candidate.composition?.bar_count,
    });
    set({
      developmentSelectedCandidateId: candidateId,
      developmentAuditionActive: false,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  setDevelopmentAuditionActive: (active) => {
    const enabled = Boolean(active);
    const state = get();
    if (enabled && !findDevelopmentCandidateById(state.developmentCandidates, state.developmentSelectedCandidateId)) {
      return false;
    }
    console.info('[musicStore] Development audition toggled', {
      active: enabled,
      candidateIdSuffix: String(state.developmentSelectedCandidateId || '').slice(-8),
    });
    set({
      developmentAuditionActive: enabled,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  discardDevelopmentCandidates: () => {
    console.info('[musicStore] Development candidates discarded');
    set({
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
    });
  },

  startDevelopmentPreview: async () => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      set({
        developmentStatus: 'error',
        developmentError: 'Canonical composition.v2 required for development',
      });
      return false;
    }

    developmentRequestSeq += 1;
    const requestId = developmentRequestSeq;
    const baseRevision = state.compositionRevision;
    const operation = state.developmentOperation;
    const hasBars = Number.isInteger(state.developmentSourceStartBar)
      && Number.isInteger(state.developmentSourceEndBar)
      && state.developmentSourceStartBar >= 1
      && state.developmentSourceEndBar >= state.developmentSourceStartBar;
    if (operation === 'vary_section' && !hasBars) {
      set({
        developmentStatus: 'error',
        developmentError: 'Select a bar range before varying a section',
      });
      return false;
    }
    const source = hasBars
      ? {
          start_bar: state.developmentSourceStartBar,
          end_bar: state.developmentSourceEndBar,
        }
      : null;

    console.info('[musicStore] Development preview started', {
      requestId,
      operation,
      intent: state.developmentIntent,
      strength: state.developmentStrength,
      candidateCount: state.developmentCandidateCount,
      outputBars: state.developmentOutputBars,
      sourceStartBar: state.developmentSourceStartBar,
      sourceEndBar: state.developmentSourceEndBar,
    });

    set({
      developmentStatus: 'loading',
      developmentError: '',
      developmentWarnings: [],
      developmentRequestId: requestId,
      developmentBaseRevision: baseRevision,
      developmentCandidates: [],
      developmentSelectedCandidateId: null,
      developmentAuditionActive: false,
      developmentEditSourceFingerprint: null,
    });

    try {
      const response = await previewCompositionDevelopment({
        composition,
        operation,
        source,
        output_bars: operation === 'vary_section' ? null : state.developmentOutputBars,
        target_section_type: operation === 'add_section' ? state.developmentTargetSectionType : null,
        target_section_label: operation === 'add_section'
          ? (state.developmentTargetSectionLabel || null)
          : null,
        development_intent: state.developmentIntent,
        variation_strength: state.developmentStrength,
        candidate_count: state.developmentCandidateCount,
        allow_modulation: state.developmentAllowModulation,
        instruction: state.developmentInstruction || null,
        selection: {
          provider: state.selectedProvider || null,
          model: state.selectedModel || null,
        },
      });

      const latest = get();
      if (requestId !== latest.developmentRequestId) {
        console.warn('[musicStore] Ignoring stale development preview response', { requestId });
        return false;
      }
      if (latest.compositionRevision !== baseRevision) {
        set({
          developmentStatus: 'stale',
          developmentError: 'Composition changed while preview was loading',
          developmentCandidates: [],
          developmentSelectedCandidateId: null,
          developmentAuditionActive: false,
        });
        return false;
      }

      const selectedId = response.candidates[0]?.candidate_id || null;
      set({
        developmentStatus: 'ready',
        developmentError: '',
        developmentWarnings: response.warning_codes || [],
        developmentEditSourceFingerprint: response.edit_source_fingerprint,
        developmentCandidates: response.candidates,
        developmentSelectedCandidateId: selectedId,
        developmentAuditionActive: false,
        developmentProvider: response.provider || null,
        developmentModel: response.model || null,
      });
      console.info('[musicStore] Development preview ready', {
        requestId,
        returnedCandidateCount: response.candidates.length,
        editSourcePrefix: editFingerprintLogPrefix(response.edit_source_fingerprint),
        warningCodeCount: (response.warning_codes || []).length,
      });
      return true;
    } catch (error) {
      if (requestId !== get().developmentRequestId) {
        return false;
      }
      const message = error instanceof DevelopmentApiError
        ? error.message
        : (error.message || 'Development preview failed');
      console.error('[musicStore] Development preview failed', {
        requestId,
        code: error.code || null,
        status: error.status || null,
      });
      set({
        developmentStatus: 'error',
        developmentError: message,
        developmentCandidates: [],
        developmentSelectedCandidateId: null,
        developmentAuditionActive: false,
      });
      return false;
    }
  },

  applySelectedDevelopmentCandidate: async () => {
    const state = get();
    if (state.developmentStatus !== 'ready') {
      return false;
    }
    const candidate = findDevelopmentCandidateById(
      state.developmentCandidates,
      state.developmentSelectedCandidateId,
    );
    if (!candidate) {
      set({
        developmentStatus: 'error',
        developmentError: 'Select a candidate before applying',
      });
      return false;
    }
    if (state.compositionRevision !== state.developmentBaseRevision) {
      set({
        developmentStatus: 'stale',
        developmentError: 'Base composition changed; request a new preview',
        developmentCandidates: [],
        developmentSelectedCandidateId: null,
        developmentAuditionActive: false,
      });
      return false;
    }

    const verification = await verifyDevelopmentCandidate({
      baseComposition: state.editedMusicJson,
      candidate,
      operation: state.developmentOperation,
      responseSourceFingerprint: state.developmentEditSourceFingerprint,
    });
    if (!verification.ok) {
      console.warn('[musicStore] Development apply blocked by verification', {
        failureCodes: verification.failures.map((item) => item.code).slice(0, 8),
        candidateIdSuffix: candidate.candidate_id.slice(-8),
      });
      set({
        developmentStatus: 'error',
        developmentError: 'Candidate failed fingerprint or preservation checks',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(candidate.composition);
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
      set({
        developmentStatus: 'error',
        developmentError: validation.message || 'Candidate composition invalid',
      });
      return false;
    }

    console.info('[musicStore] Development candidate apply', {
      operation: state.developmentOperation,
      candidateIdSuffix: candidate.candidate_id.slice(-8),
      editSourcePrefix: editFingerprintLogPrefix(verification.localSourceFingerprint),
      barCount: prepared.bar_count,
    });

    applyCompositionEdit(set, get, {
      nextComposition: prepared,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      action: 'development-apply',
      noteSummary: null,
      statePatch: {
        ...clearedDevelopmentPreviewState({ preserveControls: true }),
        developmentAuditionActive: false,
      },
    });
    return true;
  },

  loadArrangementCatalog: async ({ forceRefresh = false } = {}) => {
    arrangementCatalogRequestSeq += 1;
    const requestId = arrangementCatalogRequestSeq;
    const cached = !forceRefresh ? getCachedArrangementCatalog() : null;
    if (cached) {
      set({
        arrangementCatalog: cached,
        arrangementCatalogStatus: 'ready',
        arrangementCatalogError: '',
        arrangementCatalogFingerprint: cached.fingerprint || null,
      });
      markArrangementStaleIfCatalogChanged(set, get, cached.fingerprint);
      return cached;
    }

    set({
      arrangementCatalogStatus: 'loading',
      arrangementCatalogError: '',
    });
    arrangementLogger.info('Arrangement catalog load started', {
      forceRefresh: Boolean(forceRefresh),
      requestId,
    });
    try {
      const catalog = await loadArrangementInstruments({ forceRefresh });
      if (requestId !== arrangementCatalogRequestSeq) {
        arrangementLogger.debug('Arrangement catalog response ignored (superseded)', { requestId });
        return null;
      }
      set({
        arrangementCatalog: catalog,
        arrangementCatalogStatus: 'ready',
        arrangementCatalogError: '',
        arrangementCatalogFingerprint: catalog.fingerprint || null,
      });
      markArrangementStaleIfCatalogChanged(set, get, catalog.fingerprint);
      arrangementLogger.info('Arrangement catalog ready', {
        requestId,
        instrumentCount: catalog.instruments?.length || 0,
        fingerprintPrefix: arrangementFingerprintPrefix(catalog.fingerprint),
      });
      return catalog;
    } catch (error) {
      if (requestId !== arrangementCatalogRequestSeq) {
        return null;
      }
      const message = error instanceof ArrangementApiError
        ? error.message
        : (error.message || 'Arrangement catalog unavailable');
      arrangementLogger.error('Arrangement catalog load failed', {
        requestId,
        code: error.code || null,
        status: error.status || null,
      });
      set({
        arrangementCatalogStatus: 'error',
        arrangementCatalogError: message,
      });
      return null;
    }
  },

  setArrangementControls: (patch = {}) => {
    const state = get();
    const next = {};
    if (patch.operation != null) {
      if (!ARRANGEMENT_OPERATIONS.includes(patch.operation)) {
        return false;
      }
      next.arrangementOperation = patch.operation;
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'sourceTrackIds')) {
      if (!Array.isArray(patch.sourceTrackIds)) {
        return false;
      }
      next.arrangementSourceTrackIds = uniqueStringIds(patch.sourceTrackIds);
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'protectedTrackIds')) {
      if (!Array.isArray(patch.protectedTrackIds)) {
        return false;
      }
      next.arrangementProtectedTrackIds = uniqueStringIds(patch.protectedTrackIds);
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'instrumentationBefore')) {
      if (!Array.isArray(patch.instrumentationBefore)) {
        return false;
      }
      next.arrangementInstrumentationBefore = patch.instrumentationBefore;
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'instrumentationAfter')) {
      if (!Array.isArray(patch.instrumentationAfter)) {
        return false;
      }
      next.arrangementInstrumentationAfter = patch.instrumentationAfter;
    }
    if (patch.allowUnlistedAfter != null) {
      next.arrangementAllowUnlistedAfter = Boolean(patch.allowUnlistedAfter);
    }
    if (patch.preserveMelody != null) {
      next.arrangementPreserveMelody = Boolean(patch.preserveMelody);
    }
    if (patch.preserveHarmony != null) {
      next.arrangementPreserveHarmony = Boolean(patch.preserveHarmony);
    }
    if (patch.rangeAdjustment != null) {
      if (!ARRANGEMENT_RANGE_ADJUSTMENTS.includes(patch.rangeAdjustment)) {
        return false;
      }
      next.arrangementRangeAdjustment = patch.rangeAdjustment;
    }
    if (patch.candidateCount != null) {
      const count = Number(patch.candidateCount);
      if (
        !Number.isInteger(count)
        || count < ARRANGEMENT_MIN_CANDIDATE_COUNT
        || count > ARRANGEMENT_MAX_CANDIDATE_COUNT
      ) {
        return false;
      }
      next.arrangementCandidateCount = count;
    }
    if (patch.instruction != null) {
      next.arrangementInstruction = String(patch.instruction)
        .slice(0, ARRANGEMENT_MAX_INSTRUCTION_CHARS);
    }

    const merged = { ...state, ...next };
    const controlsFp = arrangementControlsFingerprint(merged);
    const hasPreview = state.arrangementStatus === 'ready'
      || state.arrangementStatus === 'stale'
      || (Array.isArray(state.arrangementCandidates) && state.arrangementCandidates.length > 0);
    const settingsChanged = hasPreview
      && state.arrangementControlsFingerprint
      && controlsFp !== state.arrangementControlsFingerprint;

    if (settingsChanged) {
      arrangementLogger.debug('Arrangement preview marked stale (settings changed)', {
        operation: merged.arrangementOperation,
        previousStatus: state.arrangementStatus,
      });
      set({
        ...next,
        arrangementStatus: 'stale',
        arrangementStaleReason: 'settings_changed',
        arrangementError: 'Arrangement settings changed; request a new preview',
        arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      });
      return true;
    }

    set(next);
    return true;
  },

  selectArrangementCandidate: (candidateId) => {
    const state = get();
    const candidate = findArrangementCandidateById(state.arrangementCandidates, candidateId);
    if (!candidate) {
      arrangementLogger.warn('Arrangement candidate selection ignored', {
        candidateIdSuffix: String(candidateId || '').slice(-8),
      });
      return false;
    }
    arrangementLogger.info('Arrangement candidate selected', {
      operation: state.arrangementOperation,
      candidateIdSuffix: candidateId.slice(-8),
      fingerprintPrefix: arrangementFingerprintPrefix(candidate.candidate_fingerprint),
      revision: String(state.arrangementBaseRevision || '').slice(0, 48),
    });
    set({
      arrangementSelectedCandidateId: candidateId,
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      arrangementCandidateTrackControls: buildDefaultTrackControls(candidate.composition),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  setArrangementAuditionMode: (mode) => {
    const nextMode = mode === ARRANGEMENT_AUDITION_CANDIDATE
      ? ARRANGEMENT_AUDITION_CANDIDATE
      : ARRANGEMENT_AUDITION_SOURCE;
    const state = get();
    if (nextMode === ARRANGEMENT_AUDITION_CANDIDATE) {
      const candidate = findArrangementCandidateById(
        state.arrangementCandidates,
        state.arrangementSelectedCandidateId,
      );
      if (!candidate || state.arrangementStatus !== 'ready') {
        return false;
      }
      const nextControls = Object.keys(state.arrangementCandidateTrackControls || {}).length
        ? state.arrangementCandidateTrackControls
        : buildDefaultTrackControls(candidate.composition);
      arrangementLogger.info('Arrangement audition mode changed', {
        mode: nextMode,
        operation: state.arrangementOperation,
        candidateIdSuffix: String(state.arrangementSelectedCandidateId || '').slice(-8),
      });
      set({
        arrangementAuditionMode: nextMode,
        arrangementCandidateTrackControls: nextControls,
        developmentAuditionActive: false,
        playbackStatus: 'idle',
        playbackSeconds: 0,
        playbackBar: 1,
      });
      return true;
    }
    arrangementLogger.info('Arrangement audition mode changed', {
      mode: nextMode,
      operation: state.arrangementOperation,
      candidateIdSuffix: String(state.arrangementSelectedCandidateId || '').slice(-8),
    });
    set({
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  syncArrangementCandidateTrackControls: (musicJson) => {
    set((state) => ({
      arrangementCandidateTrackControls: mergeTrackControls(
        state.arrangementCandidateTrackControls,
        musicJson,
      ),
    }));
  },

  toggleArrangementCandidateMute: (trackId) => {
    set((state) => {
      const current = state.arrangementCandidateTrackControls[trackId] || defaultControl();
      return {
        arrangementCandidateTrackControls: {
          ...state.arrangementCandidateTrackControls,
          [trackId]: { ...current, muted: !current.muted },
        },
      };
    });
  },

  toggleArrangementCandidateSolo: (trackId) => {
    set((state) => {
      const current = state.arrangementCandidateTrackControls[trackId] || defaultControl();
      return {
        arrangementCandidateTrackControls: {
          ...state.arrangementCandidateTrackControls,
          [trackId]: { ...current, solo: !current.solo },
        },
      };
    });
  },

  setArrangementCandidateVolume: (trackId, volumeMidi) => {
    const clamped = Math.max(0, Math.min(127, Number(volumeMidi) || 0));
    set((state) => {
      const current = state.arrangementCandidateTrackControls[trackId] || defaultControl();
      return {
        arrangementCandidateTrackControls: {
          ...state.arrangementCandidateTrackControls,
          [trackId]: { ...current, volumeMidi: clamped },
        },
      };
    });
  },

  discardArrangementCandidates: () => {
    arrangementRequestSeq += 1;
    arrangementLogger.info('Arrangement candidates discarded', {
      operation: get().arrangementOperation,
      revision: String(get().arrangementBaseRevision || '').slice(0, 48),
      candidateCount: get().arrangementCandidates?.length || 0,
    });
    set({
      ...clearedArrangementPreviewState({ preserveControls: true }),
      arrangementRequestId: arrangementRequestSeq,
    });
  },

  startArrangementPreview: async () => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      set({
        arrangementStatus: 'error',
        arrangementError: 'Canonical composition.v2 required for arrangement',
        arrangementStaleReason: null,
      });
      return false;
    }

    arrangementRequestSeq += 1;
    const requestId = arrangementRequestSeq;
    const baseRevision = state.compositionRevision;
    const controlsFp = arrangementControlsFingerprint(state);
    const catalog = state.arrangementCatalog || getCachedArrangementCatalog();

    arrangementLogger.info('Arrangement preview started', {
      requestId,
      operation: state.arrangementOperation,
      revision: String(baseRevision || '').slice(0, 48),
      sourceCount: state.arrangementSourceTrackIds.length,
      protectedCount: state.arrangementProtectedTrackIds.length,
      candidateCount: state.arrangementCandidateCount,
      catalogPrefix: arrangementFingerprintPrefix(
        catalog?.fingerprint || state.arrangementCatalogFingerprint,
      ),
    });

    set({
      arrangementStatus: 'loading',
      arrangementError: '',
      arrangementStaleReason: null,
      arrangementWarnings: [],
      arrangementRequestId: requestId,
      arrangementBaseRevision: baseRevision,
      arrangementControlsFingerprint: controlsFp,
      arrangementCandidates: [],
      arrangementRejectedAttempts: [],
      arrangementSelectedCandidateId: null,
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      arrangementCandidateTrackControls: {},
      arrangementEditSourceFingerprint: null,
      arrangementResponseCatalogFingerprint: null,
    });

    try {
      const response = await previewCompositionArrangement({
        composition,
        operation: state.arrangementOperation,
        source_track_ids: state.arrangementSourceTrackIds,
        protected_track_ids: state.arrangementProtectedTrackIds,
        instrumentation: {
          before: state.arrangementInstrumentationBefore,
          after: state.arrangementInstrumentationAfter,
        },
        allow_unlisted_after: state.arrangementAllowUnlistedAfter,
        preserve_melody: state.arrangementPreserveMelody,
        preserve_harmony: state.arrangementPreserveHarmony,
        range_adjustment: state.arrangementRangeAdjustment,
        candidate_count: state.arrangementCandidateCount,
        instruction: state.arrangementInstruction || null,
        selection: {
          provider: state.selectedProvider || null,
          model: state.selectedModel || null,
        },
      });

      const latest = get();
      if (requestId !== latest.arrangementRequestId) {
        arrangementLogger.debug('Ignoring superseded arrangement preview response', {
          requestId,
          latestRequestId: latest.arrangementRequestId,
        });
        return false;
      }
      if (latest.compositionRevision !== baseRevision) {
        arrangementLogger.debug('Arrangement preview stale (source revision changed)', {
          requestId,
        });
        set({
          arrangementStatus: 'stale',
          arrangementStaleReason: 'source_changed',
          arrangementError: 'Composition changed while preview was loading',
          arrangementCandidates: [],
          arrangementRejectedAttempts: [],
          arrangementSelectedCandidateId: null,
          arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
          arrangementCandidateTrackControls: {},
        });
        return false;
      }

      const selectedId = response.candidates[0]?.candidate_id || null;
      const selectedCandidate = findArrangementCandidateById(response.candidates, selectedId);
      set({
        arrangementStatus: 'ready',
        arrangementError: '',
        arrangementStaleReason: null,
        arrangementWarnings: response.warning_codes || [],
        arrangementEditSourceFingerprint: response.edit_source_fingerprint,
        arrangementResponseCatalogFingerprint: response.catalog_fingerprint || null,
        arrangementCandidates: response.candidates,
        arrangementRejectedAttempts: response.rejected_attempts || [],
        arrangementSelectedCandidateId: selectedId,
        arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
        arrangementCandidateTrackControls: selectedCandidate
          ? buildDefaultTrackControls(selectedCandidate.composition)
          : {},
        arrangementProvider: response.provider || null,
        arrangementModel: response.model || null,
        arrangementControlsFingerprint: controlsFp,
      });
      arrangementLogger.info('Arrangement preview ready', {
        requestId,
        operation: response.operation,
        revision: String(baseRevision || '').slice(0, 48),
        returnedCandidateCount: response.candidates.length,
        rejectedCount: (response.rejected_attempts || []).length,
        editSourcePrefix: arrangementFingerprintPrefix(response.edit_source_fingerprint),
        catalogPrefix: arrangementFingerprintPrefix(response.catalog_fingerprint),
        status: 'ready',
      });
      return true;
    } catch (error) {
      if (requestId !== get().arrangementRequestId) {
        return false;
      }
      const message = error instanceof ArrangementApiError
        ? error.message
        : (error.message || 'Arrangement preview failed');
      arrangementLogger.error('Arrangement preview failed', {
        requestId,
        code: error.code || null,
        status: error.status || null,
      });
      set({
        arrangementStatus: 'error',
        arrangementError: message,
        arrangementStaleReason: null,
        arrangementCandidates: [],
        arrangementRejectedAttempts: [],
        arrangementSelectedCandidateId: null,
        arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
        arrangementCandidateTrackControls: {},
      });
      return false;
    }
  },

  applySelectedArrangementCandidate: async () => {
    const state = get();
    if (state.arrangementStatus !== 'ready') {
      return false;
    }
    const candidate = findArrangementCandidateById(
      state.arrangementCandidates,
      state.arrangementSelectedCandidateId,
    );
    if (!candidate) {
      set({
        arrangementStatus: 'error',
        arrangementError: 'Select a candidate before applying',
      });
      return false;
    }
    if (state.compositionRevision !== state.arrangementBaseRevision) {
      arrangementLogger.debug('Arrangement apply blocked; base revision stale', {
        candidateIdSuffix: candidate.candidate_id.slice(-8),
      });
      set({
        arrangementStatus: 'stale',
        arrangementStaleReason: 'source_changed',
        arrangementError: 'Base composition changed; request a new preview',
        arrangementCandidates: [],
        arrangementRejectedAttempts: [],
        arrangementSelectedCandidateId: null,
        arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
        arrangementCandidateTrackControls: {},
      });
      return false;
    }

    const catalog = state.arrangementCatalog || getCachedArrangementCatalog();
    if (
      state.arrangementResponseCatalogFingerprint
      && catalog?.fingerprint
      && catalog.fingerprint !== state.arrangementResponseCatalogFingerprint
    ) {
      arrangementLogger.debug('Arrangement apply blocked; catalog fingerprint stale', {
        candidateIdSuffix: candidate.candidate_id.slice(-8),
      });
      set({
        arrangementStatus: 'stale',
        arrangementStaleReason: 'catalog_changed',
        arrangementError: 'Instrument catalog changed; request a new preview',
      });
      return false;
    }

    const request = {
      operation: state.arrangementOperation,
      source_track_ids: state.arrangementSourceTrackIds,
      protected_track_ids: state.arrangementProtectedTrackIds,
      instrumentation: {
        before: state.arrangementInstrumentationBefore,
        after: state.arrangementInstrumentationAfter,
      },
      preserve_melody: state.arrangementPreserveMelody,
      preserve_harmony: state.arrangementPreserveHarmony,
      range_adjustment: state.arrangementRangeAdjustment,
    };

    const verification = await verifyArrangementCandidateForApply(
      state.editedMusicJson,
      candidate,
      {
        catalog,
        request,
        responseSourceFingerprint: state.arrangementEditSourceFingerprint,
      },
    );
    if (!verification.ok) {
      const failureCodes = verification.failures.map((item) => item.code).slice(0, 12);
      arrangementLogger.warn('Arrangement apply blocked by verification', {
        failureCodes,
        failureCount: verification.failures.length,
        candidateIdSuffix: candidate.candidate_id.slice(-8),
        assertionCodeCount: failureCodes.length,
      });
      arrangementLogger.debug('Arrangement verification failure codes', {
        codes: failureCodes,
      });
      set({
        arrangementStatus: 'error',
        arrangementError: 'Candidate failed fingerprint or topology checks',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(candidate.composition);
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
      set({
        arrangementStatus: 'error',
        arrangementError: validation.message || 'Candidate composition invalid',
      });
      return false;
    }

    const selection = recoverPianoRollSelectionAfterTopology(
      prepared,
      state.pianoRollTrackId,
      state.pianoRollNoteId,
      state.pianoRollNoteIds,
    );
    const historySnapshot = snapshotNoteEditState(state);
    const nextTrackControls = mergeTrackControls(state.trackControls, prepared);

    arrangementLogger.info('Arrangement candidate apply', {
      operation: state.arrangementOperation,
      candidateIdSuffix: candidate.candidate_id.slice(-8),
      fingerprintPrefix: arrangementFingerprintPrefix(verification.localCandidateFingerprint),
      revision: String(state.arrangementBaseRevision || '').slice(0, 48),
      trackCount: prepared.tracks?.length || 0,
      status: 'apply',
    });

    applyCompositionEdit(set, get, {
      nextComposition: prepared,
      selectedTrackId: selection.trackId,
      selectedNoteId: selection.noteId,
      selectedNoteIds: selection.noteIds,
      action: 'arrangement-apply',
      noteSummary: null,
      historySnapshot,
      statePatch: {
        trackControls: nextTrackControls,
        ...reconcileMotifUiAfterCompositionChange(state, prepared),
        ...clearedArrangementPreviewState({ preserveControls: true }),
        ...clearedDevelopmentPreviewState({ preserveControls: true }),
        ...clearedReharmonizePreviewState({ preserveControls: true }),
        arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      },
    });

    // Explicit notation refresh after topology apply (do not await for store return).
    void get().refreshMusicXmlFromEditedComposition();
    return true;
  },

  startReharmonizePreview: async () => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: 'Canonical composition.v2 required for reharmonization',
      });
      return false;
    }
    if (!state.harmonySelectionStartBar || !state.harmonySelectionEndBar) {
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: 'Select a bar range before requesting a preview',
      });
      return false;
    }
    if (!state.reharmonizeTargetTrackIds.length) {
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: 'Select explicit target tracks before requesting a preview',
      });
      return false;
    }

    reharmonizeRequestSeq += 1;
    const requestId = reharmonizeRequestSeq;
    const baseRevision = state.compositionRevision;
    console.info('[musicStore] Reharmonize preview started', {
      requestId,
      operation: state.reharmonizeOperation,
      contentPolicy: state.reharmonizeContentPolicy,
      engine: state.reharmonizeEngine,
      startBar: state.harmonySelectionStartBar,
      endBar: state.harmonySelectionEndBar,
      targetCount: state.reharmonizeTargetTrackIds.length,
    });
    set({
      reharmonizeStatus: 'loading',
      reharmonizeError: '',
      reharmonizeWarnings: [],
      reharmonizeRequestId: requestId,
      reharmonizeBaseRevision: baseRevision,
      reharmonizeCandidate: null,
      reharmonizeHarmonyChanges: [],
      reharmonizeTrackChanges: [],
      reharmonizePreservation: [],
      reharmonizeCompatibility: null,
    });

    try {
      const response = await previewReharmonization({
        composition,
        selection: {
          start_bar: state.harmonySelectionStartBar,
          end_bar: state.harmonySelectionEndBar,
        },
        operation: state.reharmonizeOperation,
        content_policy: state.reharmonizeContentPolicy,
        target_track_ids: state.reharmonizeTargetTrackIds,
        engine: state.reharmonizeEngine,
        instruction: state.reharmonizeInstruction || null,
        tonal_context: {
          allow_modulation: state.reharmonizeAllowModulation,
          target_key: state.reharmonizeAllowModulation ? (state.reharmonizeTargetKey || null) : null,
          target_chord: state.reharmonizeTargetChord || null,
        },
        selection_options: {
          provider: state.reharmonizeEngine === 'ai' ? (state.selectedProvider || null) : null,
          model: state.reharmonizeEngine === 'ai' ? (state.selectedModel || null) : null,
        },
      });

      const latest = get();
      if (requestId !== latest.reharmonizeRequestId) {
        console.warn('[musicStore] Ignoring stale reharmonize preview response', { requestId });
        return false;
      }
      if (latest.compositionRevision !== baseRevision) {
        console.warn('[musicStore] Reharmonize preview stale base revision');
        set({
          reharmonizeStatus: 'stale',
          reharmonizeError: 'Composition changed while preview was loading',
          reharmonizeCandidate: null,
        });
        return false;
      }

      set({
        reharmonizeStatus: 'ready',
        reharmonizeError: '',
        reharmonizeWarnings: Array.isArray(response.warnings) ? response.warnings : [],
        reharmonizeBaseFingerprint: response.base_fingerprint,
        reharmonizeProposalFingerprint: response.proposal_fingerprint,
        reharmonizeCandidate: response.composition,
        reharmonizeHarmonyChanges: response.harmony_changes || [],
        reharmonizeTrackChanges: response.track_changes || [],
        reharmonizePreservation: response.preservation || [],
        reharmonizeCompatibility: response.compatibility || null,
        reharmonizeProvider: response.provider || null,
        reharmonizeModel: response.model || null,
        reharmonizeStartTick: response.start_tick,
        reharmonizeEndTick: response.end_tick,
        reharmonizeActiveKey: response.active_key || null,
        reharmonizeRecommendedTargetTrackIds: response.recommended_target_track_ids || [],
      });
      console.info('[musicStore] Reharmonize preview ready', {
        requestId,
        provider: response.provider,
        changedSpanCount: response.harmony_changes?.length || 0,
        compatibilityStatus: response.compatibility?.status || null,
      });
      return true;
    } catch (error) {
      if (requestId !== get().reharmonizeRequestId) {
        return false;
      }
      const message = error instanceof ReharmonizeApiError
        ? error.message
        : (error.message || 'Reharmonize preview failed');
      console.error('[musicStore] Reharmonize preview failed', {
        requestId,
        code: error.code || null,
        message,
      });
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: message,
        reharmonizeCandidate: null,
      });
      return false;
    }
  },

  applyReharmonizePreview: async () => {
    const state = get();
    if (state.reharmonizeStatus !== 'ready' || !state.reharmonizeCandidate) {
      console.warn('[musicStore] applyReharmonizePreview ignored; no ready candidate');
      return false;
    }
    if (state.compositionRevision !== state.reharmonizeBaseRevision) {
      set({
        reharmonizeStatus: 'stale',
        reharmonizeError: 'Base composition changed; request a new preview',
      });
      return false;
    }

    const currentFingerprint = await compositionSourceFingerprint(state.editedMusicJson);
    const verification = verifyReharmonizationCandidate({
      baseComposition: state.editedMusicJson,
      candidateComposition: state.reharmonizeCandidate,
      startTick: state.reharmonizeStartTick,
      endTick: state.reharmonizeEndTick,
      authorizedTrackIds: state.reharmonizeTargetTrackIds,
      preserveMelody: state.reharmonizeContentPolicy !== 'preserve_harmony_adapt_melody',
      preserveHarmony: state.reharmonizeContentPolicy !== 'preserve_melody_adapt_harmony',
      baseFingerprint: currentFingerprint,
      responseBaseFingerprint: state.reharmonizeBaseFingerprint,
      proposalFingerprint: state.reharmonizeProposalFingerprint,
      responseProposalFingerprint: state.reharmonizeProposalFingerprint,
    });
    if (!verification.ok) {
      console.warn('[musicStore] applyReharmonizePreview rejected verification', {
        failureCodes: verification.failures.map((item) => item.code).slice(0, 8),
      });
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: 'Preview failed preservation or fingerprint checks',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(state.reharmonizeCandidate);
    const validation = validateMusicJson(prepared);
    if (!validation.valid) {
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: validation.message,
      });
      return false;
    }

    console.info('[musicStore] Reharmonize preview applied', {
      operation: state.reharmonizeOperation,
      contentPolicy: state.reharmonizeContentPolicy,
      changedSpanCount: state.reharmonizeHarmonyChanges.length,
      changedTrackCount: state.reharmonizeTrackChanges.filter((item) => item.events_changed > 0).length,
    });
    applyCompositionEdit(set, get, {
      nextComposition: prepared,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: null,
      selectedNoteIds: [],
      action: 'reharmonize-apply',
      noteSummary: null,
      statePatch: {
        ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
        reharmonizeStatus: 'idle',
      },
    });
    return true;
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
    harmonySelectionStartBar: state.harmonySelectionStartBar,
    harmonySelectionEndBar: state.harmonySelectionEndBar,
    harmonySelectedSpanStartTick: state.harmonySelectedSpanStartTick,
    trackControls: state.trackControls ? { ...state.trackControls } : {},
  };
}

function applyCompositionEdit(set, get, options) {
  return applyNoteEdit(set, get, options);
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
  statePatch = {},
  keepReharmonizePreview = false,
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
    playbackStatus: 'idle',
    playbackSeconds: 0,
    playbackBar: 1,
    analysisSelectedSectionKey: recoverAnalysisSectionKey(
      nextComposition,
      state.analysisSelectedSectionKey,
    ),
    ...(keepReharmonizePreview ? {} : clearedReharmonizePreviewState({ preserveControls: true })),
    ...(keepReharmonizePreview ? {} : clearedDevelopmentPreviewState({ preserveControls: true })),
    ...(keepReharmonizePreview ? {} : clearedArrangementPreviewState({ preserveControls: true })),
    ...statePatch,
  });
  markProjectDirty(set, get);
  const analysisReason = action?.startsWith('harmony')
    || action === 'reharmonize-apply'
    || action === 'development-apply'
    || action === 'arrangement-apply'
    ? action
    : 'note-edit';
  scheduleAnalysisRequest(get, { reason: analysisReason });
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
    ...clearedMotifUiState(),
    ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
    ...initialHarmonyUiState,
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

function clearedMotifUiState() {
  return { ...initialMotifUiState };
}

function clearedReharmonizePreviewState({ preserveControls = false } = {}) {
  if (!preserveControls) {
    return { ...initialReharmonizePreviewState };
  }
  return {
    reharmonizeStatus: 'idle',
    reharmonizeError: '',
    reharmonizeWarnings: [],
    reharmonizeRequestId: 0,
    reharmonizeBaseFingerprint: null,
    reharmonizeBaseRevision: null,
    reharmonizeProposalFingerprint: null,
    reharmonizeCandidate: null,
    reharmonizeHarmonyChanges: [],
    reharmonizeTrackChanges: [],
    reharmonizePreservation: [],
    reharmonizeCompatibility: null,
    reharmonizeProvider: null,
    reharmonizeModel: null,
    reharmonizeStartTick: null,
    reharmonizeEndTick: null,
    reharmonizeActiveKey: null,
    reharmonizeRecommendedTargetTrackIds: [],
  };
}

function clearedDevelopmentPreviewState({ preserveControls = false } = {}) {
  if (!preserveControls) {
    return { ...initialDevelopmentPreviewState };
  }
  return {
    developmentStatus: 'idle',
    developmentError: '',
    developmentWarnings: [],
    developmentRequestId: 0,
    developmentBaseRevision: null,
    developmentEditSourceFingerprint: null,
    developmentCandidates: [],
    developmentSelectedCandidateId: null,
    developmentAuditionActive: false,
    developmentProvider: null,
    developmentModel: null,
  };
}

function clearedArrangementPreviewState({ preserveControls = false } = {}) {
  if (!preserveControls) {
    return { ...initialArrangementPreviewState };
  }
  return {
    arrangementStatus: 'idle',
    arrangementError: '',
    arrangementStaleReason: null,
    arrangementWarnings: [],
    arrangementRequestId: 0,
    arrangementBaseRevision: null,
    arrangementEditSourceFingerprint: null,
    arrangementResponseCatalogFingerprint: null,
    arrangementControlsFingerprint: null,
    arrangementCandidates: [],
    arrangementRejectedAttempts: [],
    arrangementSelectedCandidateId: null,
    arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
    arrangementCandidateTrackControls: {},
    arrangementProvider: null,
    arrangementModel: null,
  };
}

function uniqueStringIds(values) {
  const seen = new Set();
  const ids = [];
  for (const value of values) {
    const id = String(value || '').trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

function arrangementControlsFingerprint(state) {
  return JSON.stringify({
    operation: state.arrangementOperation,
    sourceTrackIds: state.arrangementSourceTrackIds || [],
    protectedTrackIds: state.arrangementProtectedTrackIds || [],
    before: state.arrangementInstrumentationBefore || [],
    after: state.arrangementInstrumentationAfter || [],
    allowUnlistedAfter: Boolean(state.arrangementAllowUnlistedAfter),
    preserveMelody: state.arrangementPreserveMelody !== false,
    preserveHarmony: state.arrangementPreserveHarmony !== false,
    rangeAdjustment: state.arrangementRangeAdjustment,
    candidateCount: state.arrangementCandidateCount,
    instruction: state.arrangementInstruction || '',
  });
}

function markArrangementStaleIfCatalogChanged(set, get, nextFingerprint) {
  const state = get();
  if (!nextFingerprint) {
    return;
  }
  const hasPreview = state.arrangementStatus === 'ready'
    || state.arrangementStatus === 'stale'
    || (Array.isArray(state.arrangementCandidates) && state.arrangementCandidates.length > 0);
  if (!hasPreview) {
    return;
  }
  const previous = state.arrangementResponseCatalogFingerprint || state.arrangementCatalogFingerprint;
  if (previous && previous !== nextFingerprint) {
    arrangementLogger.debug('Arrangement preview marked stale (catalog fingerprint changed)', {
      operation: state.arrangementOperation,
      previousPrefix: arrangementFingerprintPrefix(previous),
      nextPrefix: arrangementFingerprintPrefix(nextFingerprint),
    });
    set({
      arrangementStatus: 'stale',
      arrangementStaleReason: 'catalog_changed',
      arrangementError: 'Instrument catalog changed; request a new preview',
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      arrangementCatalogFingerprint: nextFingerprint,
    });
  }
}

function recoverPianoRollSelectionAfterTopology(composition, trackId, noteId, noteIds) {
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const trackExists = tracks.some((track) => String(track.id) === String(trackId));
  if (!trackExists) {
    const fallbackTrackId = pickDefaultTrackId(composition);
    return {
      trackId: fallbackTrackId,
      noteId: null,
      noteIds: [],
    };
  }
  const existingNoteIds = filterExistingNoteIds(composition, trackId, noteIds || []);
  const nextNoteId = noteId && existingNoteIds.includes(String(noteId))
    ? noteId
    : (existingNoteIds[0] || null);
  return {
    trackId,
    noteId: nextNoteId,
    noteIds: existingNoteIds,
  };
}

function createMotifEntityId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function isCreativeMotifOperation(operation) {
  return MOTIF_CREATIVE_OPERATIONS.has(operation);
}

function buildMotifOperationParameters(operationParams = {}) {
  const params = operationParams && typeof operationParams === 'object' ? operationParams : {};
  const mapped = {};
  const copyIfPresent = (key) => {
    if (params[key] != null) {
      mapped[key] = params[key];
    }
  };
  [
    'transpose_semitones',
    'inversion_axis_pitch',
    'time_scale_numerator',
    'time_scale_denominator',
    'sequence_steps',
    'sequence_interval_semitones',
    'sequence_step_ticks',
  ].forEach(copyIfPresent);
  return mapped;
}

function validateMotifApplyRequest(state) {
  const motif = (state.editedMusicJson?.motifs || []).find(
    (item) => item.id === state.motifSelectedMotifId,
  );
  if (!motif) {
    return 'Selected motif was not found in the composition';
  }
  const occurrence = (motif.occurrences || []).find(
    (item) => item.id === state.motifSelectedOccurrenceId,
  );
  if (!occurrence) {
    return 'Selected motif occurrence was not found';
  }
  const track = (state.editedMusicJson?.tracks || []).find(
    (item) => String(item.id) === String(state.motifDestinationTrackId),
  );
  if (!track) {
    return 'Destination track was not found';
  }
  if (isCreativeMotifOperation(state.motifOperation)) {
    if (!state.selectedProvider && !state.selectedModel) {
      return 'LLM provider or model selection is required for creative motif operations';
    }
    const strength = Number(state.motifVariationStrength);
    if (!Number.isFinite(strength) || strength < 0 || strength > 1) {
      return 'variation_strength must be a finite float in 0..1';
    }
  }
  if (state.motifOperation === 'transpose' && state.motifOperationParams?.transpose_semitones == null) {
    return 'transpose_semitones is required for transpose';
  }
  if (state.motifOperation === 'sequence') {
    const required = ['sequence_steps', 'sequence_interval_semitones', 'sequence_step_ticks'];
    const missing = required.filter((key) => state.motifOperationParams?.[key] == null);
    if (missing.length) {
      return `${missing.join(', ')} required for sequence`;
    }
  }
  return null;
}

function buildMotifApplyPayload(state) {
  const payload = {
    composition: state.editedMusicJson,
    source: {
      motif_id: state.motifSelectedMotifId,
      occurrence_id: state.motifSelectedOccurrenceId,
    },
    destination: {
      section_id: state.motifDestinationSectionId || undefined,
      track_id: state.motifDestinationTrackId,
      start_bar: state.motifDestinationStartBar,
      start_tick: state.motifDestinationStartTick ?? undefined,
    },
    operation: state.motifOperation,
    parameters: buildMotifOperationParameters(state.motifOperationParams),
  };
  if (isCreativeMotifOperation(state.motifOperation)) {
    payload.variation_strength = state.motifVariationStrength;
    payload.selection = {
      provider: state.selectedProvider || null,
      model: state.selectedModel || null,
    };
  }
  return payload;
}

function reconcileMotifUiAfterCompositionChange(state, composition, {
  clearedSelection = false,
  reconcileWarnings = [],
} = {}) {
  const motifs = Array.isArray(composition?.motifs) ? composition.motifs : [];
  let motifSelectedMotifId = clearedSelection ? null : state.motifSelectedMotifId;
  if (motifSelectedMotifId && !motifs.some((item) => item.id === motifSelectedMotifId)) {
    motifSelectedMotifId = null;
  }
  let motifSelectedOccurrenceId = clearedSelection ? null : state.motifSelectedOccurrenceId;
  let motifHighlightedUsageKey = clearedSelection ? null : state.motifHighlightedUsageKey;
  if (motifSelectedMotifId) {
    const motif = motifs.find((item) => item.id === motifSelectedMotifId);
    const occurrence = motif?.occurrences?.find((item) => item.id === motifSelectedOccurrenceId);
    if (!occurrence) {
      const original = motif?.occurrences?.find((item) => item.relationship === 'original');
      motifSelectedOccurrenceId = original?.id || null;
      motifHighlightedUsageKey = original
        ? `canonical:${motifSelectedMotifId}:${original.id}`
        : null;
    }
  } else {
    motifSelectedOccurrenceId = null;
    motifHighlightedUsageKey = null;
  }
  if (motifHighlightedUsageKey && motifSelectedMotifId) {
    const stillValid = motifs.some((motif) => (
      (motif.occurrences || []).some(
        (occurrence) => motifHighlightedUsageKey === `canonical:${motif.id}:${occurrence.id}`,
      )
    ));
    if (!stillValid) {
      motifHighlightedUsageKey = motifSelectedMotifId && motifSelectedOccurrenceId
        ? `canonical:${motifSelectedMotifId}:${motifSelectedOccurrenceId}`
        : null;
    }
  }
  return {
    motifSelectedMotifId,
    motifSelectedOccurrenceId,
    motifHighlightedUsageKey,
    motifApplyStatus: 'idle',
    motifApplyError: '',
    motifApplyWarnings: [],
    motifReconcileWarnings: Array.isArray(reconcileWarnings) ? reconcileWarnings : [],
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
