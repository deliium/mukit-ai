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
  makeNoteRef,
  noteRefKey,
  normalizeNoteRef,
  rangeSelectBetween,
  reconcileSelection,
  resolveNoteRefs,
  selectionSummary,
  selectionTickRange,
  toIdSet,
  uniqueNoteRefs,
  visibleTracks,
} from '../utils/compositionEditorSelection.js';
import {
  REJECT as EDITOR_REJECT,
  copyNotes,
  cutNotes,
  deleteNotes,
  deltaNoteVelocities,
  duplicateNotes,
  humanizeNotes,
  legatoNotes,
  nudgeNoteLengths,
  pasteNotes,
  quantizeNoteEnds,
  quantizeNotes,
  setNoteArticulations,
  setNoteLengths,
  setNoteVelocities,
  transposeNotes,
} from '../utils/compositionEditorOperations.js';
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
import {
  DYNAMIC_LEVELS,
  isCanonicalComposition,
  validateMusicJson,
} from '../utils/musicJsonValidation.js';
import { compositionRevisionKey, notationRevisionKey } from '../utils/playbackPosition.js';
import { recordCompositionCommit } from '../utils/editorPerfInstrumentation.js';
import {
  deriveLoopRangeFromSelection,
  normalizePlaybackLoop,
  reconcilePlaybackLoop,
} from '../utils/playbackLoop.js';
import {
  MAX_UNDO_HISTORY,
  SNAP_VALUES,
  applyTieChain as applyTrackTieChain,
  countV2FeatureSummary,
  createTrackNote,
  defaultDurationForSnap,
  deleteTrackNote,
  ensureCompositionNoteIds,
  pickDefaultTrackId,
  removeTieChain as removeTrackTieChain,
  sanitizeNoteSummary,
  snapIntervalTicks,
  toggleNoteArticulation as toggleTrackNoteArticulation,
  updateTrackNote,
} from '../utils/pianoRollEvents.js';
import {
  defaultTargetTrackIds,
  normalizeBarRange,
  selectedTickBoundaries,
} from '../utils/pianoRollSelection.js';
import { barStartTick, compileTimeline } from '../utils/compositionTimeline.js';
import {
  DEFAULT_NAV_MAX_ZOOM,
  DEFAULT_NAV_MIN_ZOOM,
  barToStartTick,
  clampEditCursorTick,
  fitCompositionZoom,
  gotoNextBar as nextBarTick,
  gotoNextSection as nextSectionTick,
  gotoPrevBar as prevBarTick,
  gotoPrevSection as prevSectionTick,
  gotoSection as sectionStartTick,
  listSectionsForNavigation,
  scrollLeftForCenterTick,
  selectionZoomWindow,
  stepZoom,
  tickToBar,
} from '../utils/editorNavigation.js';
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

const logger = createAppLogger('musicStore');
const arrangementLogger = createAppLogger('musicStore.arrangement');

let playbackTransportSeq = 0;
function nextPlaybackTransportSeq() {
  playbackTransportSeq += 1;
  return playbackTransportSeq;
}

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
const MIN_PIANO_ROLL_ZOOM = DEFAULT_NAV_MIN_ZOOM;
const MAX_PIANO_ROLL_ZOOM = DEFAULT_NAV_MAX_ZOOM;
const NAV_LOG_DEBOUNCE_MS = 200;
let navLogTimer = null;
let viewportScrollRequestSeq = 0;

function logNavigationDebounced(payload) {
  if (navLogTimer) {
    clearTimeout(navLogTimer);
  }
  navLogTimer = setTimeout(() => {
    navLogTimer = null;
    logger.debug('Editor navigation', payload);
  }, NAV_LOG_DEBOUNCE_MS);
}

function buildViewportScrollRequest({ scrollLeft = null, centerTick = null, reason = 'navigate' } = {}) {
  viewportScrollRequestSeq += 1;
  return {
    id: viewportScrollRequestSeq,
    scrollLeft: scrollLeft == null || !Number.isFinite(Number(scrollLeft)) ? null : Number(scrollLeft),
    centerTick: centerTick == null || !Number.isFinite(Number(centerTick)) ? null : Number(centerTick),
    reason: reason == null ? 'navigate' : String(reason),
  };
}

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
  /**
   * Ephemeral loop bounds for transport. Not a Composition V2 field.
   * Shape: { startTick, endTick, enabled } | null
   */
  playbackLoop: null,
  /** Optional auto-follow of playback cursor in the piano roll. */
  playbackAutoFollow: false,
  /**
   * One-shot transport intent consumed by PlaybackControls.
   * Shape: { seq, type: 'play'|'pause'|'resume'|'stop'|'toggle', startTick?: number|null }
   */
  playbackTransportIntent: null,
  trackControls: {},
  compositionRevision: 'empty',
  notationRevision: 'empty',
  uiError: '',
  warnings: [],
  pianoRollTrackId: null,
  pianoRollNoteId: null,
  pianoRollNoteIds: [],
  /** Multi-track note selection as `{ trackId, eventId }[]` (canonical editor selection). */
  editorSelectionRefs: [],
  editorSelectionPrimary: null,
  /** Anchor for Shift-range extension (not part of composition history). */
  editorSelectionAnchor: null,
  /** Store-owned clipboard payload from copyNotes / cutNotes (not browser clipboard). */
  editorClipboard: null,
  /** Ephemeral UI: hidden tracks excluded from hit-testing / box select. */
  hiddenTrackIds: [],
  /** Ephemeral UI: locked tracks selectable but rejected as edit targets. */
  lockedTrackIds: [],
  pianoRollSnap: '1/8',
  pianoRollZoom: DEFAULT_PIANO_ROLL_ZOOM,
  pianoRollEditStatus: 'idle',
  pianoRollNotationStatus: 'idle',
  pianoRollNotationError: '',
  /** Last guarded editor command feedback for UI (code/message only). */
  editorCommandFeedback: null,
  editCursorTick: 0,
  /**
   * Ephemeral scroll request token for PianoRollEditor (not scrollLeft source of truth).
   * Shape: { id, scrollLeft, centerTick, reason } | null
   */
  viewportScrollRequest: null,
  compositionEditUndoStack: [],
  compositionEditRedoStack: [],

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
    const prev = get();
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
      editCursorTick: 0,
      viewportScrollRequest: null,
      compositionEditUndoStack: [],
      compositionEditRedoStack: [],
      generationMeta,
      ...editorPrefsForCompositionReplace(prev, composition),
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
    const prev = get();
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
      editCursorTick: 0,
      viewportScrollRequest: null,
      compositionEditUndoStack: [],
      compositionEditRedoStack: [],
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
      ...editorPrefsForCompositionReplace(prev, nextComposition),
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
    const state = get();
    const normalized = editedMusicJson
      ? coerceEditableComposition(editedMusicJson)
      : editedMusicJson;
    const validation = normalized
      ? validateMusicJson(normalized)
      : { valid: false, message: 'Composition required' };
    const previousEventCount = countEvents(state.editedMusicJson);
    const nextEventCount = countEvents(normalized);
    const isValidCanonical = Boolean(
      normalized
      && validation.valid
      && isCanonicalComposition(normalized),
    );

    logger.debug('Edited music JSON change requested', {
      hasJson: Boolean(normalized),
      schemaVersion: normalized?.schema_version || 'legacy',
      canonical: isCanonicalComposition(normalized),
      valid: validation.valid,
      previousEventCount,
      nextEventCount,
    });

    // Invalid / incomplete JSON stays repairable in the editor without
    // replacing canonical undo/redo snapshots or committing history.
    if (!isValidCanonical) {
      const revision = compositionRevisionKey(normalized);
      const notationRev = notationRevisionKey(normalized);
      const staleNotation = notationStalePatch(state.notationRevision, notationRev);
      const previousTrackId = state.pianoRollTrackId;
      const nextTrackId = pickDefaultTrackId(normalized, previousTrackId);
      const selection = reconcileLegacyPianoRollSelection(
        normalized,
        nextTrackId,
        state.pianoRollNoteId,
        state.pianoRollNoteIds,
      );
      if (previousTrackId && nextTrackId && previousTrackId !== nextTrackId) {
        logger.warn('Stale piano-roll track recovered after invalid JSON edit', {
          previousTrackId,
          nextTrackId,
        });
      }
      logger.warn('Invalid JSON kept repairable; history unchanged', {
        message: validation.message || 'invalid composition',
        historyDepth: state.compositionEditUndoStack?.length || 0,
      });
      set({
        editedMusicJson: normalized,
        compositionRevision: revision,
        notationRevision: notationRev,
        ...staleNotation,
        trackControls: mergeTrackControls(state.trackControls, normalized),
        pianoRollTrackId: selection.trackId,
        pianoRollNoteId: selection.noteId,
        pianoRollNoteIds: selection.noteIds,
        editCursorTick: clampEditCursorTick(normalized, state.editCursorTick),
        analysisSelectedSectionKey: recoverAnalysisSectionKey(
          normalized,
          state.analysisSelectedSectionKey,
        ),
        ...clearedReharmonizePreviewState(),
        ...clearedDevelopmentPreviewState(),
        ...clearedArrangementPreviewState(),
      });
      markProjectDirty(set, get);
      return;
    }

    const previousTrackId = state.pianoRollTrackId;
    const nextTrackId = pickDefaultTrackId(normalized, previousTrackId);
    if (previousTrackId && nextTrackId && previousTrackId !== nextTrackId) {
      logger.warn('Stale piano-roll track recovered after JSON edit', {
        previousTrackId,
        nextTrackId,
      });
    }
    const selection = reconcileLegacyPianoRollSelection(
      normalized,
      nextTrackId,
      state.pianoRollNoteId,
      state.pianoRollNoteIds,
    );
    commitCompositionTransaction(set, get, {
      nextComposition: normalized,
      selectedTrackId: selection.trackId,
      selectedNoteId: selection.noteId,
      selectedNoteIds: selection.noteIds,
      action: 'json-edit',
      noteSummary: null,
      affectedNoteCount: selection.noteIds.length,
      affectedTrackCount: normalized.tracks?.length || 0,
      statePatch: {
        ...clearedMotifUiState(),
        ...initialHarmonyUiState,
      },
    });
  },

  resetEditedMusicJson: () => {
    const state = get();
    const music = state.generatedMusicJson;
    logger.debug('Edited music JSON reset requested');
    if (!music || !isCanonicalComposition(music) || !validateMusicJson(music).valid) {
      logger.warn('Reset to generated rejected; generated composition invalid');
      set({
        editedMusicJson: music,
        compositionRevision: compositionRevisionKey(music),
        notationRevision: notationRevisionKey(music),
        trackControls: buildDefaultTrackControls(music),
        pianoRollTrackId: pickDefaultTrackId(music),
        pianoRollNoteId: null,
        pianoRollNoteIds: [],
        editCursorTick: 0,
        viewportScrollRequest: null,
        pianoRollEditStatus: 'idle',
        analysisSelectedSectionKey: recoverAnalysisSectionKey(music, state.analysisSelectedSectionKey),
        ...clearedMotifUiState(),
        ...clearedReharmonizePreviewState(),
        ...clearedDevelopmentPreviewState(),
        ...clearedArrangementPreviewState(),
        ...initialHarmonyUiState,
      });
      markProjectDirty(set, get);
      return;
    }
    commitCompositionTransaction(set, get, {
      nextComposition: music,
      selectedTrackId: pickDefaultTrackId(music),
      selectedNoteId: null,
      selectedNoteIds: [],
      action: 'json-reset',
      noteSummary: null,
      statePatch: {
        trackControls: buildDefaultTrackControls(music),
        editCursorTick: 0,
        viewportScrollRequest: null,
        ...clearedMotifUiState(),
        ...clearedReharmonizePreviewState(),
        ...clearedDevelopmentPreviewState(),
        ...clearedArrangementPreviewState(),
        ...initialHarmonyUiState,
      },
    });
  },

  selectPianoRollTrack: (trackId) => {
    const state = get();
    const previous = state.pianoRollTrackId;
    const next = pickDefaultTrackId(state.editedMusicJson, trackId);
    logger.debug('Piano-roll track selected', { previousTrackId: previous, nextTrackId: next });
    const patch = {
      pianoRollTrackId: next,
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
      editorSelectionRefs: [],
      editorSelectionPrimary: null,
      editorSelectionAnchor: null,
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

  /**
   * Legacy single-track note selection. Prefer setEditorSelection for multi-track refs.
   * options.extend toggles membership (Ctrl/Cmd-click semantics historically).
   * options.range extends via rangeSelectBetween from the selection anchor (Shift-click).
   */
  selectPianoRollNote: (noteId, options = {}) => {
    const state = get();
    const trackId = state.pianoRollTrackId;
    if (!noteId || !trackId) {
      get().clearEditorSelection();
      return;
    }
    const ref = makeNoteRef(trackId, noteId);
    if (!ref) {
      return;
    }
    if (options.range) {
      get().extendEditorSelectionTo(ref);
      return;
    }
    if (options.extend || options.toggle) {
      get().toggleEditorSelectionRef(ref);
      return;
    }
    get().setEditorSelection({ refs: [ref], primary: ref, anchor: ref });
  },

  setEditorSelection: ({ refs = [], primary = null, anchor = undefined } = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    const list = uniqueNoteRefs(refs);
    const primaryRef = normalizeNoteRef(primary) || list[0] || null;
    const reconciled = reconcileSelection(composition, {
      refs: list,
      primary: primaryRef,
    }, { hiddenTrackIds: state.hiddenTrackIds, dropHidden: true });
    const fields = editorSelectionToStoreFields(
      composition,
      reconciled.refs,
      reconciled.primary,
      state.pianoRollTrackId,
    );
    const nextAnchor = anchor === undefined
      ? (reconciled.primary || state.editorSelectionAnchor)
      : normalizeNoteRef(anchor);
    const summary = selectionSummary(composition, reconciled.refs, {
      hiddenTrackIds: state.hiddenTrackIds,
      lockedTrackIds: state.lockedTrackIds,
    });
    logger.debug('Editor selection set', {
      selectedCount: summary.selectedCount,
      trackCount: summary.trackCount,
    });
    set({
      ...fields,
      editorSelectionAnchor: nextAnchor,
      editorCommandFeedback: null,
    });
  },

  toggleEditorSelectionRef: (ref) => {
    const state = get();
    const target = normalizeNoteRef(ref);
    if (!target) {
      return;
    }
    const key = noteRefKey(target);
    const current = uniqueNoteRefs(state.editorSelectionRefs);
    const exists = current.some((item) => noteRefKey(item) === key);
    const nextRefs = exists
      ? current.filter((item) => noteRefKey(item) !== key)
      : [...current, target];
    const primary = target;
    const summary = selectionSummary(state.editedMusicJson, nextRefs, {
      hiddenTrackIds: state.hiddenTrackIds,
      lockedTrackIds: state.lockedTrackIds,
    });
    logger.debug('Editor selection toggled', {
      selectedCount: summary.selectedCount,
      trackCount: summary.trackCount,
      added: !exists,
    });
    get().setEditorSelection({
      refs: nextRefs,
      primary: nextRefs.length ? primary : null,
      anchor: state.editorSelectionAnchor || primary,
    });
  },

  extendEditorSelectionTo: (ref) => {
    const state = get();
    const to = normalizeNoteRef(ref);
    if (!to) {
      return;
    }
    const from = normalizeNoteRef(state.editorSelectionAnchor)
      || normalizeNoteRef(state.editorSelectionPrimary)
      || to;
    const ranged = rangeSelectBetween(state.editedMusicJson, from, to, {
      hiddenTrackIds: state.hiddenTrackIds,
    });
    logger.debug('Editor selection range extended', {
      selectedCount: ranged.length,
    });
    get().setEditorSelection({
      refs: ranged,
      primary: to,
      anchor: from,
    });
  },

  selectAllVisible: () => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      logger.warn('selectAllVisible ignored; no canonical composition');
      set({ editorCommandFeedback: { code: 'empty_selection', message: 'No composition' } });
      return { ok: false, code: 'empty_selection' };
    }
    const refs = [];
    for (const track of visibleTracks(composition, state.hiddenTrackIds)) {
      for (const event of track.events || []) {
        const ref = makeNoteRef(track.id, event.id);
        if (ref) {
          refs.push(ref);
        }
      }
    }
    const unique = uniqueNoteRefs(refs);
    logger.debug('selectAllVisible', { selectedCount: unique.length });
    get().setEditorSelection({
      refs: unique,
      primary: unique[0] || null,
      anchor: unique[0] || null,
    });
    return { ok: true, selectedCount: unique.length };
  },

  clearEditorSelection: () => {
    const state = get();
    logger.debug('clearEditorSelection', {
      previousCount: (state.editorSelectionRefs || []).length,
    });
    set({
      editorSelectionRefs: [],
      editorSelectionPrimary: null,
      editorSelectionAnchor: null,
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
      editorCommandFeedback: null,
    });
  },

  setHiddenTrackIds: (ids) => {
    const state = get();
    const next = reconcileTrackIdLists(state.editedMusicJson, ids);
    const reconciled = reconcileSelection(state.editedMusicJson, {
      refs: state.editorSelectionRefs,
      primary: state.editorSelectionPrimary,
    }, { hiddenTrackIds: next, dropHidden: true });
    const fields = editorSelectionToStoreFields(
      state.editedMusicJson,
      reconciled.refs,
      reconciled.primary,
      state.pianoRollTrackId,
    );
    logger.debug('hiddenTrackIds updated', {
      hiddenCount: next.length,
      selectedCount: reconciled.refs.length,
      droppedCount: reconciled.droppedCount,
    });
    set({
      hiddenTrackIds: next,
      ...fields,
      editorSelectionAnchor: reconciled.primary,
    });
  },

  setLockedTrackIds: (ids) => {
    const state = get();
    const next = reconcileTrackIdLists(state.editedMusicJson, ids);
    logger.debug('lockedTrackIds updated', { lockedCount: next.length });
    set({ lockedTrackIds: next });
  },

  toggleHiddenTrackId: (trackId) => {
    const id = trackId != null ? String(trackId) : '';
    if (!id) {
      return;
    }
    const state = get();
    const current = toIdSet(state.hiddenTrackIds);
    if (current.has(id)) {
      current.delete(id);
    } else {
      current.add(id);
    }
    get().setHiddenTrackIds([...current]);
  },

  toggleLockedTrackId: (trackId) => {
    const id = trackId != null ? String(trackId) : '';
    if (!id) {
      return;
    }
    const state = get();
    const current = toIdSet(state.lockedTrackIds);
    if (current.has(id)) {
      current.delete(id);
    } else {
      current.add(id);
    }
    get().setLockedTrackIds([...current]);
  },

  copySelection: () => {
    const state = get();
    const refs = currentEditorRefs(state);
    const result = copyNotes(state.editedMusicJson, refs);
    if (!result.ok) {
      logger.warn('copySelection guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    logger.debug('copySelection', {
      noteCount: result.summary.noteCount,
      trackCount: result.summary.trackCount,
    });
    set({
      editorClipboard: result.clipboard,
      editorCommandFeedback: null,
    });
    return { ok: true, summary: result.summary };
  },

  cutSelection: () => {
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('cutSelection guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    const result = cutNotes(state.editedMusicJson, refs, {
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      logger.warn('cutSelection guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: null,
      selectedNoteIds: [],
      editorSelectionRefs: [],
      editorSelectionPrimary: null,
      action: 'cut',
      affectedNoteCount: result.summary?.deletedCount ?? refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorClipboard: result.clipboard,
        editorSelectionAnchor: null,
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('cutSelection committed', {
      deletedCount: result.summary?.deletedCount,
      trackCount: result.summary?.trackCount,
    });
    return { ok: true, summary: result.summary };
  },

  pasteClipboard: (options = {}) => {
    const state = get();
    if (!state.editorClipboard) {
      logger.warn('pasteClipboard guarded', { code: EDITOR_REJECT.empty_clipboard });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.empty_clipboard,
          message: 'Clipboard is empty',
        },
      });
      return { ok: false, code: EDITOR_REJECT.empty_clipboard };
    }
    const pasteTick = Number.isFinite(Number(options.pasteTick))
      ? Math.round(Number(options.pasteTick))
      : state.editCursorTick;
    const result = pasteNotes(state.editedMusicJson, state.editorClipboard, {
      pasteTick,
      activeTrackId: state.pianoRollTrackId,
      lockedTrackIds: state.lockedTrackIds,
      preferActiveTrackForSingleTrackClip: Boolean(options.preferActiveTrackForSingleTrackClip),
    });
    if (!result.ok) {
      logger.warn('pasteClipboard guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    const selection = result.selection;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
      selectedNoteId: selection.primary?.eventId || null,
      selectedNoteIds: null,
      editorSelectionRefs: selection.refs,
      editorSelectionPrimary: selection.primary,
      action: 'paste',
      affectedNoteCount: result.summary?.pastedCount ?? selection.refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: selection.primary,
        editorCommandFeedback: null,
        editCursorTick: pasteTick,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('pasteClipboard committed', {
      pastedCount: result.summary?.pastedCount,
      trackCount: result.summary?.trackCount,
      pasteTick,
    });
    return { ok: true, summary: result.summary };
  },

  duplicateSelection: () => {
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('duplicateSelection guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    const result = duplicateNotes(state.editedMusicJson, refs, {
      snapValue: state.pianoRollSnap,
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      logger.warn('duplicateSelection guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    const selection = result.selection;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
      selectedNoteId: selection.primary?.eventId || null,
      editorSelectionRefs: selection.refs,
      editorSelectionPrimary: selection.primary,
      action: 'duplicate',
      affectedNoteCount: result.summary?.duplicatedCount ?? selection.refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: selection.primary,
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('duplicateSelection committed', {
      duplicatedCount: result.summary?.duplicatedCount,
      trackCount: result.summary?.trackCount,
      pasteTick: result.summary?.pasteTick,
    });
    return { ok: true, summary: result.summary };
  },

  deleteSelection: () => {
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('deleteSelection guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    const result = deleteNotes(state.editedMusicJson, refs, {
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      logger.warn('deleteSelection guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: state.pianoRollTrackId,
      selectedNoteId: null,
      selectedNoteIds: [],
      editorSelectionRefs: [],
      editorSelectionPrimary: null,
      action: 'delete-selection',
      affectedNoteCount: result.summary?.deletedCount ?? refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: null,
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('deleteSelection committed', {
      deletedCount: result.summary?.deletedCount,
      trackCount: result.summary?.trackCount,
    });
    return { ok: true, summary: result.summary };
  },

  transposeSelection: (semitones) => {
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('transposeSelection guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    const result = transposeNotes(state.editedMusicJson, refs, {
      semitones,
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      logger.warn('transposeSelection guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }
    const selection = result.selection;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
      selectedNoteId: selection.primary?.eventId || null,
      editorSelectionRefs: selection.refs,
      editorSelectionPrimary: selection.primary,
      action: 'transpose',
      affectedNoteCount: result.summary?.transposedCount ?? refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: selection.primary,
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('transposeSelection committed', {
      semitones,
      noteCount: result.summary?.transposedCount,
    });
    return { ok: true, summary: result.summary };
  },

  quantizeSelection: (options = {}) => commitSelectionTransform(set, get, {
    action: 'quantize',
    logName: 'quantizeSelection',
    options: {
      mode: options.mode || 'start',
      snapValue: options.snapValue,
      strength: options.strength,
    },
    run: (state, refs) => quantizeNotes(state.editedMusicJson, refs, {
      mode: options.mode || 'start',
      snapValue: options.snapValue ?? state.pianoRollSnap,
      strength: options.strength ?? 100,
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.quantizedCount ?? refs.length,
  }),

  setSelectionVelocity: (velocity) => commitSelectionTransform(set, get, {
    action: 'velocity-set',
    logName: 'setSelectionVelocity',
    options: { velocity },
    run: (state, refs) => setNoteVelocities(state.editedMusicJson, refs, {
      velocity,
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.affectedCount ?? refs.length,
  }),

  deltaSelectionVelocity: (delta) => commitSelectionTransform(set, get, {
    action: 'velocity-delta',
    logName: 'deltaSelectionVelocity',
    options: { delta },
    run: (state, refs) => deltaNoteVelocities(state.editedMusicJson, refs, {
      delta,
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.affectedCount ?? refs.length,
  }),

  setSelectionNoteLength: (options = {}) => {
    const startedAt = nowMs();
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('setSelectionNoteLength guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }

    let result;
    let action = 'note-length-set';
    let logOptions = {};
    if (options.quantizeEnds) {
      action = 'note-length-quantize-ends';
      logOptions = {
        snapValue: options.snapValue ?? state.pianoRollSnap,
        strength: options.strength ?? 100,
      };
      result = quantizeNoteEnds(state.editedMusicJson, refs, {
        snapValue: options.snapValue ?? state.pianoRollSnap,
        strength: options.strength ?? 100,
        lockedTrackIds: state.lockedTrackIds,
      });
    } else {
      let durationTicks = options.durationTicks;
      if (options.toGrid || durationTicks == null) {
        const tpq = Number(state.editedMusicJson?.ticks_per_quarter) || 480;
        durationTicks = defaultDurationForSnap(
          options.snapValue ?? state.pianoRollSnap,
          tpq,
        ).durationTicks;
      }
      logOptions = { durationTicks, toGrid: Boolean(options.toGrid) };
      result = setNoteLengths(state.editedMusicJson, refs, {
        durationTicks,
        lockedTrackIds: state.lockedTrackIds,
      });
    }

    if (!result.ok) {
      logger.warn('setSelectionNoteLength guarded', { code: result.code, message: result.message });
      set({ editorCommandFeedback: { code: result.code, message: result.message } });
      return { ok: false, code: result.code, message: result.message };
    }

    const selection = result.selection;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
      selectedNoteId: selection.primary?.eventId || null,
      editorSelectionRefs: selection.refs,
      editorSelectionPrimary: selection.primary,
      action,
      affectedNoteCount: result.summary?.affectedCount
        ?? result.summary?.quantizedCount
        ?? refs.length,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: selection.primary,
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('setSelectionNoteLength committed', {
      action,
      noteCount: result.summary?.affectedCount ?? result.summary?.quantizedCount,
    });
    logger.debug('setSelectionNoteLength options', {
      ...logOptions,
      elapsedMs: Math.round(nowMs() - startedAt),
    });
    return { ok: true, summary: result.summary };
  },

  nudgeSelectionNoteLength: (steps = 1) => commitSelectionTransform(set, get, {
    action: 'note-length-nudge',
    logName: 'nudgeSelectionNoteLength',
    options: { steps },
    run: (state, refs) => nudgeNoteLengths(state.editedMusicJson, refs, {
      snapValue: state.pianoRollSnap,
      steps,
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.affectedCount ?? refs.length,
  }),

  legatoSelection: () => commitSelectionTransform(set, get, {
    action: 'legato',
    logName: 'legatoSelection',
    run: (state, refs) => legatoNotes(state.editedMusicJson, refs, {
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.affectedCount ?? refs.length,
  }),

  humanizeSelection: (options = {}) => commitSelectionTransform(set, get, {
    action: 'humanize',
    logName: 'humanizeSelection',
    options: {
      timingAmount: options.timingAmount ?? 0,
      velocityAmount: options.velocityAmount ?? 0,
    },
    run: (state, refs) => humanizeNotes(state.editedMusicJson, refs, {
      timingAmount: options.timingAmount ?? 0,
      velocityAmount: options.velocityAmount ?? 0,
      random: options.random,
      lockedTrackIds: state.lockedTrackIds,
    }),
    affectedNoteCount: (result, refs) => result.summary?.affectedCount ?? refs.length,
  }),

  setSelectionArticulation: (articulation, mode = 'toggle') => {
    const startedAt = nowMs();
    const state = get();
    const refs = currentEditorRefs(state);
    if (!refs.length) {
      logger.warn('setSelectionArticulation guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    const result = setNoteArticulations(state.editedMusicJson, refs, {
      articulation,
      mode,
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      logger.warn('setSelectionArticulation guarded', {
        code: result.code,
        message: result.message,
        skippedCount: result.summary?.skippedCount,
      });
      set({
        editorCommandFeedback: {
          code: result.code,
          message: result.message,
          skippedCount: result.summary?.skippedCount ?? 0,
        },
      });
      return {
        ok: false,
        code: result.code,
        message: result.message,
        summary: result.summary,
      };
    }

    const selection = result.selection;
    const skippedCount = result.summary?.skippedCount ?? 0;
    const feedback = skippedCount > 0
      ? {
        code: 'articulation_skipped',
        message: `Applied with ${skippedCount} note(s) skipped (tie/rules)`,
        skippedCount,
      }
      : null;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
      selectedNoteId: selection.primary?.eventId || null,
      editorSelectionRefs: selection.refs,
      editorSelectionPrimary: selection.primary,
      action: 'articulation-selection',
      affectedNoteCount: result.summary?.affectedCount ?? 0,
      affectedTrackCount: result.summary?.trackCount ?? 0,
      statePatch: {
        editorSelectionAnchor: selection.primary,
        editorCommandFeedback: feedback,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('setSelectionArticulation committed', {
      articulation,
      mode,
      affectedCount: result.summary?.affectedCount,
      skippedCount,
    });
    logger.debug('setSelectionArticulation options', {
      articulation,
      mode,
      elapsedMs: Math.round(nowMs() - startedAt),
    });
    return { ok: true, summary: result.summary };
  },

  upsertDynamicMark: (options = {}) => {
    const startedAt = nowMs();
    const state = get();
    const composition = state.editedMusicJson;
    const trackId = options.trackId || state.pianoRollTrackId;
    if (!composition || !trackId) {
      logger.warn('upsertDynamicMark guarded', { code: 'missing_track' });
      return { ok: false, code: 'missing_track' };
    }
    if (toIdSet(state.lockedTrackIds).has(String(trackId))) {
      logger.warn('upsertDynamicMark guarded', { code: EDITOR_REJECT.locked_targets });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.locked_targets,
          message: 'Active track is locked',
        },
      });
      return { ok: false, code: EDITOR_REJECT.locked_targets };
    }
    const level = options.level;
    if (!DYNAMIC_LEVELS.includes(level)) {
      logger.warn('upsertDynamicMark guarded', { code: EDITOR_REJECT.invalid_options });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.invalid_options,
          message: 'Unsupported dynamic level',
        },
      });
      return { ok: false, code: EDITOR_REJECT.invalid_options };
    }
    const tick = resolveDynamicTick(state, options);
    if (tick == null) {
      logger.warn('upsertDynamicMark guarded', { code: EDITOR_REJECT.invalid_timing });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.invalid_timing,
          message: 'Could not resolve dynamics tick',
        },
      });
      return { ok: false, code: EDITOR_REJECT.invalid_timing };
    }
    const durationLimit = Number(composition.duration_ticks);
    if (!Number.isInteger(durationLimit) || tick < 0 || tick > durationLimit) {
      logger.warn('upsertDynamicMark guarded', { code: EDITOR_REJECT.invalid_timing });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.invalid_timing,
          message: 'Dynamics tick out of bounds',
        },
      });
      return { ok: false, code: EDITOR_REJECT.invalid_timing };
    }

    let found = false;
    const nextComposition = {
      ...composition,
      tracks: composition.tracks.map((track) => {
        if (String(track.id) !== String(trackId)) {
          return track;
        }
        const marks = Array.isArray(track.dynamic_marks) ? [...track.dynamic_marks] : [];
        const index = marks.findIndex((mark) => Number(mark.tick) === tick);
        if (index >= 0) {
          found = true;
          marks[index] = { ...marks[index], tick, level };
        } else {
          marks.push({ tick, level });
        }
        marks.sort((a, b) => a.tick - b.tick);
        const unique = [];
        const seen = new Set();
        for (const mark of marks) {
          if (seen.has(mark.tick)) {
            continue;
          }
          seen.add(mark.tick);
          unique.push(mark);
        }
        return { ...track, dynamic_marks: unique };
      }),
    };

    const ok = commitCompositionTransaction(set, get, {
      nextComposition,
      selectedTrackId: trackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      editorSelectionRefs: state.editorSelectionRefs,
      editorSelectionPrimary: state.editorSelectionPrimary,
      action: found ? 'dynamics-update' : 'dynamics-create',
      affectedNoteCount: 0,
      affectedTrackCount: 1,
      statePatch: {
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('upsertDynamicMark committed', {
      trackId,
      tick,
      level,
      updated: found,
    });
    logger.debug('upsertDynamicMark options', {
      at: options.at || 'cursor',
      elapsedMs: Math.round(nowMs() - startedAt),
    });
    return { ok: true, tick, level, updated: found };
  },

  removeDynamicMark: (options = {}) => {
    const startedAt = nowMs();
    const state = get();
    const composition = state.editedMusicJson;
    const trackId = options.trackId || state.pianoRollTrackId;
    if (!composition || !trackId) {
      logger.warn('removeDynamicMark guarded', { code: 'missing_track' });
      return { ok: false, code: 'missing_track' };
    }
    if (toIdSet(state.lockedTrackIds).has(String(trackId))) {
      logger.warn('removeDynamicMark guarded', { code: EDITOR_REJECT.locked_targets });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.locked_targets,
          message: 'Active track is locked',
        },
      });
      return { ok: false, code: EDITOR_REJECT.locked_targets };
    }
    const tick = resolveDynamicTick(state, options);
    if (tick == null) {
      logger.warn('removeDynamicMark guarded', { code: EDITOR_REJECT.invalid_timing });
      return { ok: false, code: EDITOR_REJECT.invalid_timing };
    }

    const track = composition.tracks.find((entry) => String(entry.id) === String(trackId));
    const marks = Array.isArray(track?.dynamic_marks) ? track.dynamic_marks : [];
    if (!marks.some((mark) => Number(mark.tick) === tick)) {
      logger.warn('removeDynamicMark guarded', { code: EDITOR_REJECT.no_op });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.no_op,
          message: 'No dynamic mark at tick',
        },
      });
      return { ok: false, code: EDITOR_REJECT.no_op };
    }

    const nextComposition = {
      ...composition,
      tracks: composition.tracks.map((entry) => {
        if (String(entry.id) !== String(trackId)) {
          return entry;
        }
        return {
          ...entry,
          dynamic_marks: (entry.dynamic_marks || []).filter((mark) => Number(mark.tick) !== tick),
        };
      }),
    };

    const ok = commitCompositionTransaction(set, get, {
      nextComposition,
      selectedTrackId: trackId,
      selectedNoteId: state.pianoRollNoteId,
      selectedNoteIds: state.pianoRollNoteIds,
      editorSelectionRefs: state.editorSelectionRefs,
      editorSelectionPrimary: state.editorSelectionPrimary,
      action: 'dynamics-remove',
      affectedNoteCount: 0,
      affectedTrackCount: 1,
      statePatch: {
        editorCommandFeedback: null,
      },
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('removeDynamicMark committed', { trackId, tick });
    logger.debug('removeDynamicMark options', {
      at: options.at || 'cursor',
      elapsedMs: Math.round(nowMs() - startedAt),
    });
    return { ok: true, tick };
  },

  nudgeSelectionByTicks: (deltaTicks) => {
    const state = get();
    const refs = currentEditorRefs(state);
    const delta = Math.round(Number(deltaTicks));
    if (!refs.length) {
      logger.warn('nudgeSelectionByTicks guarded', { code: EDITOR_REJECT.empty_selection });
      set({
        editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
      });
      return { ok: false, code: EDITOR_REJECT.empty_selection };
    }
    if (!Number.isInteger(delta) || delta === 0) {
      return { ok: false, code: EDITOR_REJECT.no_op };
    }
    const composition = state.editedMusicJson;
    const locked = toIdSet(state.lockedTrackIds);
    const resolved = resolveNoteRefs(composition, refs, {
      lockedTrackIds: state.lockedTrackIds,
      includeHidden: false,
      includeLocked: true,
    });
    if (!resolved.length) {
      logger.warn('nudgeSelectionByTicks guarded', { code: EDITOR_REJECT.missing_targets });
      return { ok: false, code: EDITOR_REJECT.missing_targets };
    }
    if (resolved.some((item) => locked.has(item.ref.trackId))) {
      logger.warn('nudgeSelectionByTicks guarded', { code: EDITOR_REJECT.locked_targets });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.locked_targets,
          message: 'Selection includes locked tracks',
        },
      });
      return { ok: false, code: EDITOR_REJECT.locked_targets };
    }
    const durationLimit = Number(composition?.duration_ticks);
    const byTrack = new Map();
    for (const item of resolved) {
      if (!byTrack.has(item.ref.trackId)) {
        byTrack.set(item.ref.trackId, new Map());
      }
      byTrack.get(item.ref.trackId).set(item.ref.eventId, item);
    }
    let nextComposition = composition;
    const tracks = composition.tracks.map((track) => {
      const edits = byTrack.get(String(track.id));
      if (!edits) {
        return track;
      }
      const events = (track.events || []).map((event) => {
        const hit = edits.get(String(event.id));
        if (!hit) {
          return event;
        }
        const desiredStart = event.start_tick + delta;
        if (desiredStart < 0 || desiredStart + event.duration_ticks > durationLimit) {
          return null;
        }
        return { ...event, start_tick: desiredStart };
      });
      if (events.some((event) => event === null)) {
        return null;
      }
      return { ...track, events };
    });
    if (tracks.some((track) => track === null)) {
      logger.warn('nudgeSelectionByTicks guarded', { code: EDITOR_REJECT.invalid_timing });
      set({
        editorCommandFeedback: {
          code: EDITOR_REJECT.invalid_timing,
          message: 'Nudge would leave composition bounds',
        },
      });
      return { ok: false, code: EDITOR_REJECT.invalid_timing };
    }
    nextComposition = { ...composition, tracks };
    const ok = commitCompositionTransaction(set, get, {
      nextComposition,
      selectedTrackId: state.editorSelectionPrimary?.trackId || state.pianoRollTrackId,
      selectedNoteId: state.editorSelectionPrimary?.eventId || state.pianoRollNoteId,
      editorSelectionRefs: refs,
      editorSelectionPrimary: state.editorSelectionPrimary,
      action: 'nudge-ticks',
      affectedNoteCount: resolved.length,
      affectedTrackCount: byTrack.size,
    });
    if (!ok) {
      return { ok: false, code: 'validation_failed' };
    }
    logger.info('nudgeSelectionByTicks committed', {
      deltaTicks: delta,
      noteCount: resolved.length,
    });
    return { ok: true, summary: { noteCount: resolved.length, deltaTicks: delta } };
  },

  nudgeSelectionBySnap: (direction = 1) => {
    const state = get();
    const { snapTicks } = snapIntervalTicks(
      state.pianoRollSnap,
      state.editedMusicJson?.ticks_per_quarter || 480,
    );
    if (!snapTicks) {
      return { ok: false, code: EDITOR_REJECT.invalid_options };
    }
    const sign = Number(direction) < 0 ? -1 : 1;
    return get().nudgeSelectionByTicks(sign * snapTicks);
  },

  toggleNoteArticulation: (trackId, noteId, articulation) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        logger.warn('toggleNoteArticulation rejected non-canonical composition');
        return null;
      }
      const result = toggleTrackNoteArticulation(current, trackId, noteId, articulation);
      if (!result.note) {
        logger.warn('toggleNoteArticulation rejected', {
          trackId,
          noteId,
          articulation,
          message: result.warning,
        });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        logger.warn('toggleNoteArticulation failed validation', { message: validation.message });
        return null;
      }
      commitCompositionTransaction(set, get, {
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
      logger.info('toggleNoteArticulation applied', {
        trackId,
        noteId,
        articulation,
        articulations: result.note.articulations,
      });
      return result.note;
    } catch (error) {
      logger.error('toggleNoteArticulation unexpected failure', {
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
        logger.warn('applyTieChain rejected non-canonical composition');
        return false;
      }
      const ids = Array.isArray(noteIds) && noteIds.length ? noteIds : state.pianoRollNoteIds;
      const result = applyTrackTieChain(current, trackId, ids);
      if (!result.notes?.length) {
        logger.warn('applyTieChain rejected incompatible selection', {
          trackId,
          noteIds: ids.length,
          message: result.warning,
        });
        return false;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        logger.warn('applyTieChain failed validation', { message: validation.message });
        return false;
      }
      commitCompositionTransaction(set, get, {
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
      logger.info('applyTieChain applied', {
        trackId,
        groupId: result.groupId,
        noteCount: result.notes.length,
      });
      return true;
    } catch (error) {
      logger.error('applyTieChain unexpected failure', {
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
        logger.warn('removeTieChain rejected non-canonical composition');
        return false;
      }
      const ids = Array.isArray(noteIds) && noteIds.length ? noteIds : state.pianoRollNoteIds;
      const result = removeTrackTieChain(current, trackId, ids);
      if (!result.clearedCount) {
        logger.warn('removeTieChain rejected', {
          trackId,
          noteIds: ids.length,
          message: result.warning,
        });
        return false;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        logger.warn('removeTieChain failed validation', { message: validation.message });
        return false;
      }
      commitCompositionTransaction(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: state.pianoRollNoteId,
        selectedNoteIds: ids,
        action: 'tie-remove',
        noteSummary: { clearedCount: result.clearedCount },
        featureCounts: countV2FeatureSummary(result.composition),
      });
      logger.info('removeTieChain applied', {
        trackId,
        clearedCount: result.clearedCount,
      });
      return true;
    } catch (error) {
      logger.error('removeTieChain unexpected failure', {
        trackId,
        message: error.message,
      });
      return false;
    }
  },

  setPianoRollSnap: (snapValue) => {
    if (!SNAP_VALUES.includes(snapValue)) {
      logger.warn('Rejected invalid piano-roll snap', { snapValue });
      return;
    }
    logger.info('Piano-roll snap changed', { snapValue });
    set({ pianoRollSnap: snapValue });
  },

  setPianoRollZoom: (zoom) => {
    const value = Number(zoom);
    if (!Number.isFinite(value) || value < MIN_PIANO_ROLL_ZOOM || value > MAX_PIANO_ROLL_ZOOM) {
      logger.warn('Rejected invalid piano-roll zoom', { zoom });
      return;
    }
    logger.debug('Piano-roll zoom changed', { zoom: value, pixelsPerTick: value });
    set({ pianoRollZoom: value });
  },

  /**
   * Set edit cursor tick without history. Clamped to composition duration.
   */
  setEditCursorTick: (tick) => {
    const state = get();
    const next = clampEditCursorTick(state.editedMusicJson, tick);
    if (next === state.editCursorTick) {
      return next;
    }
    set({ editCursorTick: next });
    logNavigationDebounced({
      command: 'setEditCursorTick',
      tick: next,
      bar: tickToBar(state.editedMusicJson, next),
    });
    return next;
  },

  requestViewportScroll: ({ scrollLeft = null, centerTick = null, reason = 'navigate' } = {}) => {
    const request = buildViewportScrollRequest({ scrollLeft, centerTick, reason });
    set({ viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'requestViewportScroll',
      reason: request.reason,
      scrollLeft: request.scrollLeft,
      centerTick: request.centerTick,
      requestId: request.id,
    });
    return request;
  },

  gotoBar: (bar) => {
    const state = get();
    const composition = state.editedMusicJson;
    const startTick = barToStartTick(composition, Number(bar));
    if (startTick == null) {
      logger.warn('gotoBar malformed target', { bar });
      return null;
    }
    const tick = clampEditCursorTick(composition, startTick);
    const request = buildViewportScrollRequest({
      centerTick: tick,
      reason: 'gotoBar',
    });
    set({
      editCursorTick: tick,
      viewportScrollRequest: request,
    });
    logNavigationDebounced({
      command: 'gotoBar',
      bar: Number(bar),
      tick,
      requestId: request.id,
    });
    return tick;
  },

  gotoPrevBar: () => {
    const state = get();
    const tick = prevBarTick(state.editedMusicJson, state.editCursorTick);
    const request = buildViewportScrollRequest({ centerTick: tick, reason: 'gotoPrevBar' });
    set({ editCursorTick: tick, viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'gotoPrevBar',
      tick,
      bar: tickToBar(state.editedMusicJson, tick),
      requestId: request.id,
    });
    return tick;
  },

  gotoNextBar: () => {
    const state = get();
    const tick = nextBarTick(state.editedMusicJson, state.editCursorTick);
    const request = buildViewportScrollRequest({ centerTick: tick, reason: 'gotoNextBar' });
    set({ editCursorTick: tick, viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'gotoNextBar',
      tick,
      bar: tickToBar(state.editedMusicJson, tick),
      requestId: request.id,
    });
    return tick;
  },

  gotoSection: (sectionKey) => {
    const state = get();
    const composition = state.editedMusicJson;
    const sections = listSectionsForNavigation(composition);
    const match = sections.find((section) => section.key === sectionKey)
      || sections.find((section) => section.id != null && section.id === sectionKey);
    if (!match) {
      logger.warn('gotoSection malformed target', { sectionKey });
      return null;
    }
    const tick = sectionStartTick(composition, match.key);
    const request = buildViewportScrollRequest({ centerTick: tick, reason: 'gotoSection' });
    set({ editCursorTick: tick, viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'gotoSection',
      sectionKey: match.key,
      tick,
      startBar: match.startBar,
      requestId: request.id,
    });
    return tick;
  },

  gotoPrevSection: () => {
    const state = get();
    const tick = prevSectionTick(state.editedMusicJson, state.editCursorTick);
    const request = buildViewportScrollRequest({ centerTick: tick, reason: 'gotoPrevSection' });
    set({ editCursorTick: tick, viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'gotoPrevSection',
      tick,
      bar: tickToBar(state.editedMusicJson, tick),
      requestId: request.id,
    });
    return tick;
  },

  gotoNextSection: () => {
    const state = get();
    const tick = nextSectionTick(state.editedMusicJson, state.editCursorTick);
    const request = buildViewportScrollRequest({ centerTick: tick, reason: 'gotoNextSection' });
    set({ editCursorTick: tick, viewportScrollRequest: request });
    logNavigationDebounced({
      command: 'gotoNextSection',
      tick,
      bar: tickToBar(state.editedMusicJson, tick),
      requestId: request.id,
    });
    return tick;
  },

  zoomIn: (options = {}) => {
    const state = get();
    const nextZoom = stepZoom(state.pianoRollZoom, 1, {
      minZoom: MIN_PIANO_ROLL_ZOOM,
      maxZoom: MAX_PIANO_ROLL_ZOOM,
    });
    const patch = { pianoRollZoom: nextZoom };
    if (options.requestScroll) {
      const centerTick = Number.isFinite(Number(options.centerTick))
        ? Number(options.centerTick)
        : state.editCursorTick;
      patch.viewportScrollRequest = buildViewportScrollRequest({
        centerTick,
        reason: 'zoomIn',
      });
    }
    set(patch);
    logNavigationDebounced({
      command: 'zoomIn',
      zoom: nextZoom,
      requestScroll: Boolean(options.requestScroll),
    });
    return nextZoom;
  },

  zoomOut: (options = {}) => {
    const state = get();
    const nextZoom = stepZoom(state.pianoRollZoom, -1, {
      minZoom: MIN_PIANO_ROLL_ZOOM,
      maxZoom: MAX_PIANO_ROLL_ZOOM,
    });
    const patch = { pianoRollZoom: nextZoom };
    if (options.requestScroll) {
      const centerTick = Number.isFinite(Number(options.centerTick))
        ? Number(options.centerTick)
        : state.editCursorTick;
      patch.viewportScrollRequest = buildViewportScrollRequest({
        centerTick,
        reason: 'zoomOut',
      });
    }
    set(patch);
    logNavigationDebounced({
      command: 'zoomOut',
      zoom: nextZoom,
      requestScroll: Boolean(options.requestScroll),
    });
    return nextZoom;
  },

  zoomToFit: (options = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    const duration = Number(composition?.duration_ticks) || 0;
    const clientWidth = Math.max(0, Number(options.clientWidth) || 0);
    const fitted = fitCompositionZoom({
      durationTicks: duration,
      clientWidth,
      minZoom: MIN_PIANO_ROLL_ZOOM,
      maxZoom: MAX_PIANO_ROLL_ZOOM,
    });
    const centerTick = Math.round(duration / 2);
    const scroll = scrollLeftForCenterTick({
      centerTick,
      pixelsPerTick: fitted.pixelsPerTick,
      clientWidth,
      durationTicks: duration,
    });
    const request = buildViewportScrollRequest({
      scrollLeft: scroll.scrollLeft,
      centerTick,
      reason: 'zoomToFit',
    });
    set({
      pianoRollZoom: fitted.pixelsPerTick,
      viewportScrollRequest: request,
    });
    logNavigationDebounced({
      command: 'zoomToFit',
      zoom: fitted.pixelsPerTick,
      durationTicks: duration,
      clientWidth,
      requestId: request.id,
    });
    return fitted.pixelsPerTick;
  },

  zoomToSelection: (options = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    const refs = Array.isArray(options.refs) && options.refs.length
      ? options.refs
      : currentEditorRefs(state);
    const clientWidth = Math.max(0, Number(options.clientWidth) || 0);
    const windowed = selectionZoomWindow(composition, refs, {
      pixelsPerTick: state.pianoRollZoom,
      clientWidth,
      paddingTicks: Number.isFinite(Number(options.paddingTicks))
        ? Number(options.paddingTicks)
        : undefined,
      minZoom: MIN_PIANO_ROLL_ZOOM,
      maxZoom: MAX_PIANO_ROLL_ZOOM,
      adjustZoom: options.adjustZoom !== false,
    });
    if (!windowed.ok) {
      logger.warn('zoomToSelection unavailable', { reason: windowed.reason || null });
      return null;
    }
    const nextZoom = windowed.pixelsPerTick != null
      ? windowed.pixelsPerTick
      : state.pianoRollZoom;
    const request = buildViewportScrollRequest({
      scrollLeft: windowed.scrollLeft,
      centerTick: windowed.centerTick,
      reason: 'zoomToSelection',
    });
    set({
      pianoRollZoom: nextZoom,
      viewportScrollRequest: request,
    });
    logNavigationDebounced({
      command: 'zoomToSelection',
      zoom: nextZoom,
      startTick: windowed.startTick,
      endTick: windowed.endTick,
      selectedCount: refs.length,
      requestId: request.id,
    });
    return {
      zoom: nextZoom,
      scrollLeft: windowed.scrollLeft,
      startTick: windowed.startTick,
      endTick: windowed.endTick,
    };
  },

  createNote: (trackId, noteDraft) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        logger.warn('createNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const result = createTrackNote(current, trackId, noteDraft);
      if (!result.note) {
        logger.warn('createNote rejected', { trackId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        logger.warn('createNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      commitCompositionTransaction(set, get, {
        nextComposition: result.composition,
        selectedTrackId: trackId,
        selectedNoteId: result.note.id,
        action: 'create',
        noteSummary: sanitizeNoteSummary(result.note),
      });
      return result.note;
    } catch (error) {
      logger.error('createNote unexpected failure', { trackId, message: error.message });
      set({ pianoRollEditStatus: 'error' });
      return null;
    }
  },

  updateNote: (trackId, noteId, patch, options = {}) => {
    try {
      const state = get();
      const current = state.editedMusicJson;
      if (!isCanonicalComposition(current)) {
        logger.warn('updateNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const before = findNote(current, trackId, noteId);
      const result = updateTrackNote(current, trackId, noteId, patch);
      if (!result.note) {
        logger.warn('updateNote rejected', { trackId, noteId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      const validation = validateMusicJson(result.composition);
      if (!validation.valid) {
        logger.warn('updateNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return null;
      }
      commitCompositionTransaction(set, get, {
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
      logger.error('updateNote unexpected failure', {
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
        logger.warn('deleteNote ignored with no selection', { trackId });
        return false;
      }
      if (!isCanonicalComposition(current)) {
        logger.warn('deleteNote rejected non-canonical composition');
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      const result = deleteTrackNote(current, trackId, noteId);
      if (!result.deleted) {
        logger.warn('deleteNote rejected', { trackId, noteId, message: result.warning });
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      const reconciled = applyMotifReconciliation(result.composition, [noteId]);
      const validation = validateMusicJson(reconciled.composition);
      if (!validation.valid) {
        logger.warn('deleteNote failed validation', { message: validation.message });
        set({ pianoRollEditStatus: 'error' });
        return false;
      }
      commitCompositionTransaction(set, get, {
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
      logger.error('deleteNote unexpected failure', {
        trackId,
        noteId,
        message: error.message,
      });
      set({ pianoRollEditStatus: 'error' });
      return false;
    }
  },

  undoCompositionEdit: () => {
    const state = get();
    if (!state.compositionEditUndoStack.length) {
      logger.warn('undoCompositionEdit ignored; stack empty');
      return false;
    }
    const previous = state.compositionEditUndoStack[state.compositionEditUndoStack.length - 1];
    const currentSnapshot = snapshotCompositionEditState(state);
    const nextUndo = state.compositionEditUndoStack.slice(0, -1);
    const nextRedo = [...state.compositionEditRedoStack, currentSnapshot].slice(-MAX_UNDO_HISTORY);
    const revision = compositionRevisionKey(previous.editedMusicJson);
    const notationRev = notationRevisionKey(previous.editedMusicJson);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    const selection = reconcileLegacyPianoRollSelection(
      previous.editedMusicJson,
      previous.pianoRollTrackId,
      previous.pianoRollNoteId,
      previous.pianoRollNoteIds,
      {
        editorSelectionRefs: previous.editorSelectionRefs,
        editorSelectionPrimary: previous.editorSelectionPrimary,
        hiddenTrackIds: state.hiddenTrackIds,
      },
    );
    const editCursorTick = clampEditCursorTick(
      previous.editedMusicJson,
      previous.editCursorTick ?? state.editCursorTick,
    );
    logger.info('undoCompositionEdit applied', {
      action: 'undo',
      affectedNoteCount: selection.editorSelectionRefs.length || selection.noteIds.length,
      affectedTrackCount: previous.editedMusicJson?.tracks?.length || 0,
      historyDepth: nextUndo.length,
      revisionPrefix: revision.slice(0, 48),
    });
    set({
      editedMusicJson: previous.editedMusicJson,
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: previous.trackControls
        ? { ...previous.trackControls }
        : mergeTrackControls(state.trackControls, previous.editedMusicJson),
      pianoRollTrackId: selection.trackId,
      pianoRollNoteId: selection.noteId,
      pianoRollNoteIds: selection.noteIds,
      editorSelectionRefs: selection.editorSelectionRefs,
      editorSelectionPrimary: selection.editorSelectionPrimary,
      editorSelectionAnchor: previous.editorSelectionAnchor
        || selection.editorSelectionPrimary,
      editCursorTick,
      harmonySelectionStartBar: previous.harmonySelectionStartBar ?? state.harmonySelectionStartBar,
      harmonySelectionEndBar: previous.harmonySelectionEndBar ?? state.harmonySelectionEndBar,
      harmonySelectedSpanStartTick: previous.harmonySelectedSpanStartTick ?? null,
      compositionEditUndoStack: nextUndo,
      compositionEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      playbackLoop: reconcilePlaybackLoop(state.playbackLoop, previous.editedMusicJson),
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

  redoCompositionEdit: () => {
    const state = get();
    if (!state.compositionEditRedoStack.length) {
      logger.warn('redoCompositionEdit ignored; stack empty');
      return false;
    }
    const next = state.compositionEditRedoStack[state.compositionEditRedoStack.length - 1];
    const currentSnapshot = snapshotCompositionEditState(state);
    const nextRedo = state.compositionEditRedoStack.slice(0, -1);
    const nextUndo = [...state.compositionEditUndoStack, currentSnapshot].slice(-MAX_UNDO_HISTORY);
    const revision = compositionRevisionKey(next.editedMusicJson);
    const notationRev = notationRevisionKey(next.editedMusicJson);
    const staleNotation = notationStalePatch(state.notationRevision, notationRev);
    const selection = reconcileLegacyPianoRollSelection(
      next.editedMusicJson,
      next.pianoRollTrackId,
      next.pianoRollNoteId,
      next.pianoRollNoteIds,
      {
        editorSelectionRefs: next.editorSelectionRefs,
        editorSelectionPrimary: next.editorSelectionPrimary,
        hiddenTrackIds: state.hiddenTrackIds,
      },
    );
    const editCursorTick = clampEditCursorTick(
      next.editedMusicJson,
      next.editCursorTick ?? state.editCursorTick,
    );
    logger.info('redoCompositionEdit applied', {
      action: 'redo',
      affectedNoteCount: selection.editorSelectionRefs.length || selection.noteIds.length,
      affectedTrackCount: next.editedMusicJson?.tracks?.length || 0,
      historyDepth: nextUndo.length,
      revisionPrefix: revision.slice(0, 48),
    });
    set({
      editedMusicJson: next.editedMusicJson,
      compositionRevision: revision,
      notationRevision: notationRev,
      ...staleNotation,
      trackControls: next.trackControls
        ? { ...next.trackControls }
        : mergeTrackControls(state.trackControls, next.editedMusicJson),
      pianoRollTrackId: selection.trackId,
      pianoRollNoteId: selection.noteId,
      pianoRollNoteIds: selection.noteIds,
      editorSelectionRefs: selection.editorSelectionRefs,
      editorSelectionPrimary: selection.editorSelectionPrimary,
      editorSelectionAnchor: next.editorSelectionAnchor
        || selection.editorSelectionPrimary,
      editCursorTick,
      harmonySelectionStartBar: next.harmonySelectionStartBar ?? state.harmonySelectionStartBar,
      harmonySelectionEndBar: next.harmonySelectionEndBar ?? state.harmonySelectionEndBar,
      harmonySelectedSpanStartTick: next.harmonySelectedSpanStartTick ?? null,
      compositionEditUndoStack: nextUndo,
      compositionEditRedoStack: nextRedo,
      pianoRollEditStatus: 'idle',
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      playbackLoop: reconcilePlaybackLoop(state.playbackLoop, next.editedMusicJson),
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
      logger.error('AI edit completion rejected invalid composition', {
        message: validation.message,
      });
      set({
        aiEditStatus: 'error',
        aiEditError: validation.message || 'Edited composition failed validation',
      });
      return false;
    }

    const ok = commitCompositionTransaction(set, get, {
      nextComposition: prepared,
      selectedTrackId: pickDefaultTrackId(prepared, state.pianoRollTrackId),
      selectedNoteId: null,
      selectedNoteIds: [],
      action: 'ai-edit',
      noteSummary: null,
      affectedNoteCount: countEvents(prepared),
      affectedTrackCount: prepared.tracks?.length || 0,
      statePatch: {
        generatedMusicJson: prepared,
        musicXml: musicxml || state.musicXml || '',
        aiEditStatus: 'success',
        aiEditError: '',
        aiEditWarnings: Array.isArray(warnings) ? warnings : [],
        ...clearedMotifUiState(),
        ...clearedReharmonizePreviewState(),
        ...clearedDevelopmentPreviewState(),
        ...clearedArrangementPreviewState(),
      },
    });
    if (!ok) {
      set({
        aiEditStatus: 'error',
        aiEditError: 'Edited composition failed validation',
      });
      return false;
    }
    logger.info('Project autosave-dirty transition after AI edit', {
      projectId: state.currentProjectId,
      revisionPrefix: get().compositionRevision.slice(0, 48),
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
    logger.debug('Playback status changed', { playbackStatus });
    set({ playbackStatus });
  },

  setPlaybackPosition: ({ seconds = 0, bar = 1 } = {}) => {
    set({
      playbackSeconds: Number(seconds) || 0,
      playbackBar: Number(bar) || 1,
    });
  },

  setPlaybackAutoFollow: (enabled) => {
    const next = Boolean(enabled);
    logger.debug('Playback auto-follow', { enabled: next });
    set({ playbackAutoFollow: next });
  },

  /**
   * Ordinary Play / Play From Cursor / Space toggle.
   * PlaybackControls consumes `playbackTransportIntent`.
   */
  requestPlaybackTransport: ({ type = 'play', startTick = null } = {}) => {
    const seq = nextPlaybackTransportSeq();
    const intent = {
      seq,
      type: String(type || 'play'),
      startTick: startTick == null || !Number.isFinite(Number(startTick))
        ? null
        : Math.max(0, Math.round(Number(startTick))),
    };
    logger.info('Transport intent', {
      type: intent.type,
      startTick: intent.startTick,
      seq: intent.seq,
      loopEnabled: Boolean(get().playbackLoop?.enabled),
    });
    set({ playbackTransportIntent: intent });
    return intent;
  },

  playFromCursor: () => {
    const state = get();
    const tick = clampEditCursorTick(state.editedMusicJson, state.editCursorTick);
    return get().requestPlaybackTransport({ type: 'play', startTick: tick });
  },

  togglePlaybackTransport: () => {
    const status = get().playbackStatus;
    if (status === 'playing') {
      return get().requestPlaybackTransport({ type: 'pause' });
    }
    if (status === 'paused') {
      return get().requestPlaybackTransport({ type: 'resume' });
    }
    // Ordinary play: start at 0 (PlaybackControls stops/resets before prepare).
    return get().requestPlaybackTransport({ type: 'play', startTick: null });
  },

  setLoopFromSelection: () => {
    const state = get();
    const composition = state.editedMusicJson;
    const noteRange = selectionTickRange(composition, state.editorSelectionRefs);
    const derived = deriveLoopRangeFromSelection({
      composition,
      noteRange,
      startBar: state.aiEditStartBar,
      endBar: state.aiEditEndBar,
      barRangeResolver: (startBar, endBar, comp) => selectedTickBoundaries(startBar, endBar, {
        composition: comp,
        timeSignature: comp?.time_signature,
        ticksPerQuarter: comp?.ticks_per_quarter,
        durationTicks: comp?.duration_ticks,
      }),
    });
    if (!derived) {
      logger.warn('Set loop from selection failed; no valid range', {
        selectedCount: Array.isArray(state.editorSelectionRefs)
          ? state.editorSelectionRefs.length
          : 0,
        aiEditStartBar: state.aiEditStartBar,
        aiEditEndBar: state.aiEditEndBar,
      });
      set({ playbackLoop: null });
      return null;
    }
    const loop = normalizePlaybackLoop({
      startTick: derived.startTick,
      endTick: derived.endTick,
      enabled: true,
    }, composition);
    logger.info('Playback loop set from selection', {
      startTick: loop?.startTick,
      endTick: loop?.endTick,
      source: derived.source,
      enabled: true,
    });
    set({ playbackLoop: loop });
    return loop;
  },

  clearPlaybackLoop: () => {
    if (!get().playbackLoop) {
      return;
    }
    logger.info('Playback loop cleared');
    set({ playbackLoop: null });
  },

  setPlaybackLoopEnabled: (enabled) => {
    const state = get();
    const current = state.playbackLoop;
    if (!current) {
      logger.warn('Cannot enable playback loop; no bounds set');
      return null;
    }
    const next = normalizePlaybackLoop({
      ...current,
      enabled: Boolean(enabled),
    }, state.editedMusicJson);
    if (!next) {
      logger.warn('Playback loop became invalid while toggling enabled', {
        startTick: current.startTick,
        endTick: current.endTick,
      });
      set({ playbackLoop: null });
      return null;
    }
    logger.info('Playback loop enabled changed', {
      enabled: next.enabled,
      startTick: next.startTick,
      endTick: next.endTick,
    });
    set({ playbackLoop: next });
    return next;
  },

  setPlaybackLoop: (loop) => {
    const composition = get().editedMusicJson;
    if (loop == null) {
      set({ playbackLoop: null });
      return null;
    }
    const next = normalizePlaybackLoop(loop, composition);
    if (!next) {
      logger.warn('Rejected invalid playback loop', {
        startTick: loop?.startTick,
        endTick: loop?.endTick,
      });
      set({ playbackLoop: null });
      return null;
    }
    set({ playbackLoop: next });
    return next;
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
          compositionEditUndoStack: [],
          compositionEditRedoStack: [],
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
    commitCompositionTransaction(set, get, {
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
    commitCompositionTransaction(set, get, {
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
    commitCompositionTransaction(set, get, {
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
      logger.error('Motif apply completion rejected invalid composition', { message });
      set({
        motifApplyStatus: 'error',
        motifApplyError: message,
      });
      return false;
    }

    const newOccurrenceId = result?.new_occurrence_id || null;
    const createdEventIds = Array.isArray(result?.created_event_ids) ? result.created_event_ids : [];
    const destinationTrackId = pickDefaultTrackId(
      prepared,
      result?.destination_track_id || state.motifDestinationTrackId,
    );
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: prepared,
      selectedTrackId: destinationTrackId,
      selectedNoteId: null,
      selectedNoteIds: createdEventIds,
      action: 'motif-apply',
      noteSummary: {
        motifId: result?.motif_id || state.motifSelectedMotifId,
        newOccurrenceId,
        createdEventCount: createdEventIds.length,
      },
      affectedNoteCount: createdEventIds.length,
      affectedTrackCount: 1,
      statePatch: {
        musicXml: musicxml || state.musicXml || '',
        motifApplyStatus: 'success',
        motifApplyError: '',
        motifApplyWarnings: Array.isArray(warnings) ? warnings : [],
        motifSelectedMotifId: result?.motif_id || state.motifSelectedMotifId,
        motifSelectedOccurrenceId: newOccurrenceId || state.motifSelectedOccurrenceId,
        motifHighlightedUsageKey: result?.motif_id && newOccurrenceId
          ? `canonical:${result.motif_id}:${newOccurrenceId}`
          : state.motifHighlightedUsageKey,
        ...clearedReharmonizePreviewState({ preserveControls: true }),
        ...clearedDevelopmentPreviewState({ preserveControls: true }),
        ...clearedArrangementPreviewState({ preserveControls: true }),
      },
    });
    if (!ok) {
      set({
        motifApplyStatus: 'error',
        motifApplyError: 'Motif apply result failed validation',
      });
      return false;
    }
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
    commitCompositionTransaction(set, get, {
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

    commitCompositionTransaction(set, get, {
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
    const historySnapshot = snapshotCompositionEditState(state);
    const nextTrackControls = mergeTrackControls(state.trackControls, prepared);

    arrangementLogger.info('Arrangement candidate apply', {
      operation: state.arrangementOperation,
      candidateIdSuffix: candidate.candidate_id.slice(-8),
      fingerprintPrefix: arrangementFingerprintPrefix(verification.localCandidateFingerprint),
      revision: String(state.arrangementBaseRevision || '').slice(0, 48),
      trackCount: prepared.tracks?.length || 0,
      status: 'apply',
    });

    commitCompositionTransaction(set, get, {
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
    commitCompositionTransaction(set, get, {
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

function snapshotCompositionEditState(state) {
  return {
    editedMusicJson: state.editedMusicJson,
    pianoRollTrackId: state.pianoRollTrackId,
    pianoRollNoteId: state.pianoRollNoteId,
    pianoRollNoteIds: state.pianoRollNoteIds || [],
    editorSelectionRefs: Array.isArray(state.editorSelectionRefs)
      ? state.editorSelectionRefs.map((ref) => ({ ...ref }))
      : [],
    editorSelectionPrimary: state.editorSelectionPrimary
      ? { ...state.editorSelectionPrimary }
      : null,
    editorSelectionAnchor: state.editorSelectionAnchor
      ? { ...state.editorSelectionAnchor }
      : null,
    editCursorTick: Number.isFinite(Number(state.editCursorTick)) ? Number(state.editCursorTick) : 0,
    harmonySelectionStartBar: state.harmonySelectionStartBar,
    harmonySelectionEndBar: state.harmonySelectionEndBar,
    harmonySelectedSpanStartTick: state.harmonySelectedSpanStartTick,
    trackControls: state.trackControls ? { ...state.trackControls } : {},
  };
}

function currentEditorRefs(state) {
  if (Array.isArray(state.editorSelectionRefs) && state.editorSelectionRefs.length) {
    return uniqueNoteRefs(state.editorSelectionRefs);
  }
  const trackId = state.pianoRollTrackId;
  const ids = Array.isArray(state.pianoRollNoteIds) && state.pianoRollNoteIds.length
    ? state.pianoRollNoteIds
    : (state.pianoRollNoteId ? [state.pianoRollNoteId] : []);
  if (!trackId || !ids.length) {
    return [];
  }
  return uniqueNoteRefs(ids.map((id) => makeNoteRef(trackId, id)).filter(Boolean));
}

/**
 * Sync multi-track editor refs with legacy single-track piano-roll fields.
 * Motif/AI panels keep using pianoRollNoteIds on the primary track.
 */
function editorSelectionToStoreFields(composition, refs, primary, fallbackTrackId) {
  const list = uniqueNoteRefs(refs);
  let primaryRef = normalizeNoteRef(primary);
  if (primaryRef) {
    const key = noteRefKey(primaryRef);
    if (!list.some((ref) => noteRefKey(ref) === key)) {
      primaryRef = null;
    }
  }
  if (!primaryRef && list.length) {
    primaryRef = list[0];
  }
  const trackId = primaryRef?.trackId
    || pickDefaultTrackId(composition, fallbackTrackId);
  const sameTrackIds = list
    .filter((ref) => ref.trackId === String(trackId))
    .map((ref) => ref.eventId);
  return {
    editorSelectionRefs: list,
    editorSelectionPrimary: primaryRef,
    pianoRollTrackId: trackId || null,
    pianoRollNoteId: primaryRef?.eventId || null,
    pianoRollNoteIds: sameTrackIds,
  };
}

/**
 * Reconcile piano-roll selection fields using Task 1 note refs (multi-track aware).
 */
function reconcileLegacyPianoRollSelection(
  composition,
  trackId,
  noteId,
  noteIds,
  {
    editorSelectionRefs = null,
    editorSelectionPrimary = null,
    hiddenTrackIds = null,
  } = {},
) {
  let refs;
  let primary;
  if (Array.isArray(editorSelectionRefs) && editorSelectionRefs.length) {
    refs = editorSelectionRefs;
    primary = editorSelectionPrimary;
  } else {
    const resolvedTrackId = trackId ? String(trackId) : pickDefaultTrackId(composition);
    const candidateIds = Array.isArray(noteIds) && noteIds.length
      ? noteIds
      : (noteId ? [noteId] : []);
    refs = uniqueNoteRefs(
      candidateIds.map((id) => makeNoteRef(resolvedTrackId, id)).filter(Boolean),
    );
    primary = noteId ? makeNoteRef(resolvedTrackId, noteId) : null;
  }
  const reconciled = reconcileSelection(composition, {
    refs,
    primary,
  }, { hiddenTrackIds, dropHidden: true });
  const fields = editorSelectionToStoreFields(
    composition,
    reconciled.refs,
    reconciled.primary,
    trackId,
  );
  return {
    trackId: fields.pianoRollTrackId,
    noteId: fields.pianoRollNoteId,
    noteIds: fields.pianoRollNoteIds,
    editorSelectionRefs: fields.editorSelectionRefs,
    editorSelectionPrimary: fields.editorSelectionPrimary,
    droppedCount: reconciled.droppedCount,
  };
}

function reconcileTrackIdLists(composition, ids) {
  const existing = new Set(
    (Array.isArray(composition?.tracks) ? composition.tracks : [])
      .map((track) => (track?.id != null ? String(track.id) : null))
      .filter(Boolean),
  );
  return [...toIdSet(ids)].filter((id) => existing.has(id));
}

function editorPrefsForCompositionReplace(state, composition) {
  const nextHidden = reconcileTrackIdLists(composition, state.hiddenTrackIds);
  const nextLocked = reconcileTrackIdLists(composition, state.lockedTrackIds);
  logger.debug('editor prefs reconciled on composition replace', {
    hiddenCount: nextHidden.length,
    lockedCount: nextLocked.length,
    droppedHidden: (state.hiddenTrackIds || []).length - nextHidden.length,
    droppedLocked: (state.lockedTrackIds || []).length - nextLocked.length,
  });
  return {
    hiddenTrackIds: nextHidden,
    lockedTrackIds: nextLocked,
    editorSelectionRefs: [],
    editorSelectionPrimary: null,
    editorSelectionAnchor: null,
    editorClipboard: null,
    editorCommandFeedback: null,
    viewportScrollRequest: null,
    // Transport/loop are ephemeral UI — clear on full composition replacement.
    playbackLoop: null,
    playbackTransportIntent: null,
    playbackAutoFollow: Boolean(state.playbackAutoFollow),
  };
}

function nowMs() {
  return typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
}

function resolveDynamicTick(state, options = {}) {
  if (Number.isInteger(Number(options.tick)) && Number(options.tick) >= 0) {
    return Math.round(Number(options.tick));
  }
  if (options.at === 'bar') {
    const bar = Number(options.bar)
      || Number(state.aiEditStartBar)
      || null;
    if (bar) {
      const timeline = compileTimeline(state.editedMusicJson);
      const start = timeline ? barStartTick(timeline, bar) : null;
      if (start != null) {
        return start;
      }
    }
  }
  const cursor = Number(state.editCursorTick);
  return Number.isFinite(cursor) && cursor >= 0 ? Math.round(cursor) : 0;
}

function commitSelectionTransform(set, get, {
  action,
  logName,
  run,
  options = null,
  affectedNoteCount,
}) {
  const startedAt = nowMs();
  const state = get();
  const refs = currentEditorRefs(state);
  if (!refs.length) {
    logger.warn(`${logName} guarded`, { code: EDITOR_REJECT.empty_selection });
    set({
      editorCommandFeedback: { code: EDITOR_REJECT.empty_selection, message: 'Nothing selected' },
    });
    return { ok: false, code: EDITOR_REJECT.empty_selection };
  }
  const result = run(state, refs);
  if (!result.ok) {
    logger.warn(`${logName} guarded`, { code: result.code, message: result.message });
    set({ editorCommandFeedback: { code: result.code, message: result.message } });
    return { ok: false, code: result.code, message: result.message, summary: result.summary };
  }
  const selection = result.selection;
  const noteCount = typeof affectedNoteCount === 'function'
    ? affectedNoteCount(result, refs)
    : (result.summary?.affectedCount ?? refs.length);
  const ok = commitCompositionTransaction(set, get, {
    nextComposition: result.composition,
    selectedTrackId: selection.primary?.trackId || state.pianoRollTrackId,
    selectedNoteId: selection.primary?.eventId || null,
    editorSelectionRefs: selection.refs,
    editorSelectionPrimary: selection.primary,
    action,
    affectedNoteCount: noteCount,
    affectedTrackCount: result.summary?.trackCount ?? 0,
    statePatch: {
      editorSelectionAnchor: selection.primary,
      editorCommandFeedback: null,
    },
  });
  if (!ok) {
    return { ok: false, code: 'validation_failed' };
  }
  logger.info(`${logName} committed`, {
    noteCount,
    ...(result.summary?.noOp ? { noOp: true } : {}),
  });
  if (options) {
    logger.debug(`${logName} options`, {
      ...options,
      elapsedMs: Math.round(nowMs() - startedAt),
    });
  } else {
    logger.debug(`${logName} timing`, { elapsedMs: Math.round(nowMs() - startedAt) });
  }
  return { ok: true, summary: result.summary };
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

/**
 * Single canonical composition commit path: validate caller-supplied composition,
 * one undo entry, selection/cursor clamp, preview invalidation, dirty/autosave.
 */
function commitCompositionTransaction(set, get, {
  nextComposition,
  selectedTrackId,
  selectedNoteId,
  selectedNoteIds = null,
  editorSelectionRefs = undefined,
  editorSelectionPrimary = undefined,
  action,
  noteSummary,
  featureCounts = null,
  affectedNoteCount = null,
  affectedTrackCount = null,
  skipHistory = false,
  historySnapshot = null,
  statePatch = {},
  keepReharmonizePreview = false,
}) {
  const startedAt = typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
  const state = get();
  const validation = validateMusicJson(nextComposition);
  if (!validation.valid || !isCanonicalComposition(nextComposition)) {
    logger.warn('Composition transaction rejected', {
      action,
      message: validation.message || 'invalid composition',
    });
    return false;
  }

  const revision = compositionRevisionKey(nextComposition);
  const notationRev = notationRevisionKey(nextComposition);
  const staleNotation = notationStalePatch(state.notationRevision, notationRev);
  const hiddenTrackIds = Object.prototype.hasOwnProperty.call(statePatch, 'hiddenTrackIds')
    ? statePatch.hiddenTrackIds
    : state.hiddenTrackIds;
  const selection = reconcileLegacyPianoRollSelection(
    nextComposition,
    selectedTrackId,
    selectedNoteId,
    selectedNoteIds ?? (selectedNoteId ? [selectedNoteId] : []),
    {
      // Only use multi-track refs when the caller passes them explicitly; otherwise
      // rebuild from legacy note ids so create/update do not keep a stale box selection.
      editorSelectionRefs: editorSelectionRefs !== undefined ? editorSelectionRefs : null,
      editorSelectionPrimary: editorSelectionPrimary !== undefined
        ? editorSelectionPrimary
        : null,
      hiddenTrackIds,
    },
  );
  const resolvedNoteIds = selection.noteIds;
  const resolvedNoteId = selection.noteId;
  const resolvedTrackId = selection.trackId;
  const nextCursor = clampEditCursorTick(
    nextComposition,
    Object.prototype.hasOwnProperty.call(statePatch, 'editCursorTick')
      ? statePatch.editCursorTick
      : state.editCursorTick,
  );
  const noteCount = affectedNoteCount != null
    ? Number(affectedNoteCount)
    : resolvedNoteIds.length;
  const trackCount = affectedTrackCount != null
    ? Number(affectedTrackCount)
    : (Array.isArray(nextComposition?.tracks) ? nextComposition.tracks.length : 0);

  let compositionEditUndoStack = state.compositionEditUndoStack || [];
  let compositionEditRedoStack = state.compositionEditRedoStack || [];
  if (!skipHistory) {
    const snapshot = historySnapshot || snapshotCompositionEditState(state);
    compositionEditUndoStack = [...compositionEditUndoStack, snapshot].slice(-MAX_UNDO_HISTORY);
    compositionEditRedoStack = [];
  }

  const undoDepth = compositionEditUndoStack.length;
  logger.info('Composition transaction committed', {
    action,
    affectedNoteCount: noteCount,
    affectedTrackCount: trackCount,
    historyDepth: undoDepth,
    revisionPrefix: revision.slice(0, 48),
    skipHistory: Boolean(skipHistory),
  });
  recordCompositionCommit(action || 'commit');
  const elapsedMs = (
    typeof performance !== 'undefined' && performance.now
      ? performance.now()
      : Date.now()
  ) - startedAt;
  logger.debug('Composition transaction diagnostics', {
    action,
    elapsedMs: Math.round(elapsedMs),
    previousEventCount: countEvents(state.editedMusicJson),
    nextEventCount: countEvents(nextComposition),
    featureCounts: featureCounts || countV2FeatureSummary(nextComposition),
    noteSummary: noteSummary || null,
    editCursorTick: nextCursor,
    selectedCount: selection.editorSelectionRefs.length,
  });

  const nextHidden = reconcileTrackIdLists(nextComposition, hiddenTrackIds);
  const nextLocked = reconcileTrackIdLists(
    nextComposition,
    Object.prototype.hasOwnProperty.call(statePatch, 'lockedTrackIds')
      ? statePatch.lockedTrackIds
      : state.lockedTrackIds,
  );
  const loopSource = Object.prototype.hasOwnProperty.call(statePatch, 'playbackLoop')
    ? statePatch.playbackLoop
    : state.playbackLoop;
  const nextPlaybackLoop = reconcilePlaybackLoop(loopSource, nextComposition);
  if (state.playbackLoop && !nextPlaybackLoop) {
    logger.warn('Cleared stale playback loop after composition edit', {
      previousStartTick: state.playbackLoop.startTick,
      previousEndTick: state.playbackLoop.endTick,
    });
  }

  set({
    editedMusicJson: nextComposition,
    compositionRevision: revision,
    notationRevision: notationRev,
    ...staleNotation,
    trackControls: mergeTrackControls(state.trackControls, nextComposition),
    pianoRollTrackId: resolvedTrackId,
    pianoRollNoteId: resolvedNoteId,
    pianoRollNoteIds: resolvedNoteIds,
    editorSelectionRefs: selection.editorSelectionRefs,
    editorSelectionPrimary: selection.editorSelectionPrimary,
    pianoRollEditStatus: 'idle',
    compositionEditUndoStack,
    compositionEditRedoStack,
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
    hiddenTrackIds: nextHidden,
    lockedTrackIds: nextLocked,
    playbackLoop: nextPlaybackLoop,
    // Re-apply clamped cursor after statePatch so ordinary edits do not reset to 0.
    editCursorTick: nextCursor,
  });
  markProjectDirty(set, get);
  const analysisReason = action?.startsWith('harmony')
    || action === 'reharmonize-apply'
    || action === 'development-apply'
    || action === 'arrangement-apply'
    || action === 'ai-edit'
    || action === 'motif-apply'
    || action === 'json-edit'
    || action === 'json-reset'
    ? action
    : 'note-edit';
  scheduleAnalysisRequest(get, { reason: analysisReason });
  return true;
}

function findNote(composition, trackId, noteId) {
  const track = composition?.tracks?.find((item) => String(item.id) === String(trackId));
  if (!track || !Array.isArray(track.events)) {
    return null;
  }
  return track.events.find((event) => String(event.id) === String(noteId)) || null;
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
  const prev = get();
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
    editCursorTick: 0,
    viewportScrollRequest: null,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    uiError: '',
    ...editorPrefsForCompositionReplace(prev, composition),
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
