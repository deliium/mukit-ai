import { create } from 'zustand';
import {
  cancelAdaptiveTransition,
  commandAdaptiveScore,
  createAdaptiveScore,
  getAdaptiveScore,
  getCurrentAdaptiveTransition,
  listAdaptiveScores,
  scheduleAdaptiveTransition,
  validateAdaptiveScore,
} from '../api/adaptiveScoreApi.js';
import {
  analyzeComposition,
  AnalysisApiError,
  applyMotif,
  ArrangementApiError,
  computeEmbedding,
  DevelopmentApiError,
  EmbeddingApiError,
  evaluateCritique,
  importMidi,
  importMusicXml,
  loadArrangementInstruments,
  MotifApiError,
  previewCompositionArrangement,
  previewCompositionDevelopment,
  previewMultiAgentWorkflow,
  previewReharmonization,
  approveAutonomousCheckpoint,
  approveAutonomousStage,
  branchAutonomousStage,
  cancelAutonomousRun,
  instructAutonomousStage,
  openAutonomousStage,
  pauseAutonomousRun,
  previewAutonomousPlan,
  rejectAutonomousArrangement,
  resumeAutonomousRun,
  retryAutonomousStage,
  skipAutonomousStage,
  startAutonomousRun,
  ReharmonizeApiError,
  resolveMusicalReference,
  REHARMONIZE_CONTENT_POLICIES,
  REHARMONIZE_ENGINES,
  REHARMONIZE_OPERATIONS,
  renderMusicXmlPreview,
  searchRelatedMotifs,
  searchSimilarEmbeddings,
  TranscriptionApiError,
  transcribeAudio,
  AudioRecoveryApiError,
  enqueueAudioRecoveryJob,
  getAudioRecoveryJob,
  deleteAudioRecoveryJob,
  bindAudioRecoveryJob,
  fetchAudioRecoveryAssetBlobUrl,
  fetchAudioRecoveryAssetJson,
  fetchBoundAudioRecovery,
} from '../api/musicApi.js';
import { briefFingerprint } from '../utils/autonomousControl.js';
import { mintOperationRunId } from '../utils/operationSummaryText.js';
import {
  createLivePredictAbortController,
  predictLiveAccompaniment,
} from '../api/livePerformanceApi.js';
import { createAppLogger } from '../utils/appLogger.js';
import { isScoreReadOnly, setCollaborationActorId } from '../utils/collaborationAccess.js';
import {
  applyAudioTranscriptionToComposition,
  defaultSelectedProvisionalIds,
  DEFAULT_AUDIO_CONFIDENCE_THRESHOLD,
} from '../utils/audioTranscriptionApply.js';
import { applyAudioRecoveryToComposition } from '../utils/audioRecoveryApply.js';
import {
  DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
  defaultSelectedRecoveryProvisionalIds,
  defaultScaffoldingInstallFlags,
} from '../utils/audioRecoveryGates.js';
import {
  buildOverlayFromEventMap,
  syncOverlayAfterCompositionEdit,
} from '../utils/audioRecoveryOverlay.js';
import {
  assertRecoveryAllowedForOtherPhases,
  assertMonoAudioAllowedForRecoveryPhase,
  assertMidiCaptureAllowedForRecoveryPhase,
  assertLiveAllowedForRecoveryPhase,
} from '../utils/audioRecoveryPhaseGuards.js';
import {
  alignmentMapOpts,
  barRangeToAudioWindow,
  sourceSecondsToTick,
  tickToSourceSeconds,
} from '../utils/audioAlignment.js';
import { probeAudioInputSupport } from '../utils/audioInputSupport.js';
import { createAudioRecorder } from '../utils/audioRecorder.js';
import { createMidiAccessSession } from '../utils/midiInputAccess.js';
import { MIDI_MESSAGE_KINDS, parseMidiMessage } from '../utils/midiInputMessages.js';
import { probeWebMidiSupport } from '../utils/midiInputSupport.js';
import { createMidiPerformanceCapture } from '../utils/midiPerformanceCapture.js';
import { createMidiMetronome } from '../utils/midiMetronome.js';
import { applyMidiTakeToComposition } from '../utils/midiTakeApply.js';
import { getLiveClock } from '../utils/liveClock.js';
import {
  assertLiveAllowedForMidiPhase,
  assertMidiCaptureAllowedForLivePhase,
  createLiveMidiStream,
} from '../utils/liveMidiStream.js';
import { createLiveAccompanimentScheduler } from '../utils/liveAccompanimentScheduler.js';
import { createLivePatternEngine } from '../utils/livePatternEngine.js';
import { createLiveJamRoleEngine } from '../utils/liveJamRoleEngine.js';
import { createLiveJamContext } from '../utils/liveJamContext.js';
import { createLiveLatencyTracker } from '../utils/liveLatency.js';
import { activeHarmonyAtTick } from '../utils/liveHarmonyContext.js';
import {
  getHarmonyHoldCount,
  updateHarmonyBelief,
} from '../utils/liveHarmonyBelief.js';
import { extractLivePerformanceFeatures } from '../utils/livePerformanceFeatures.js';
import { requireLivePlaybackEngine } from '../utils/livePlaybackEngineAccess.js';
import {
  applyAiJamTakeToComposition,
  applyCoPerformanceTakeToComposition,
  defaultCommitHarmonySpans,
} from '../utils/liveTakeApply.js';
import {
  LIVE_ENGINE_UNAVAILABLE,
  LIVE_MIDI_PHASE_EXCLUSION,
  LIVE_PREDICT_REQUEST_SCHEMA,
  createIdleLiveSession,
  readLiveHorizonBounds,
} from '../utils/liveSessionContracts.js';
import {
  JAM_PREDICT_UNAVAILABLE,
  clampJamControls,
  createDefaultJamControls,
  createIdleHarmonyBelief,
  isJamMode,
  readLiveJamSettings,
  resolveJamRolePartition,
} from '../utils/liveJamContracts.js';
import {
  buildCurrentEmbedScope,
  buildSectionEmbedScope,
  buildSimilarityQueryPayload,
  buildStyleReferenceFromMusicalReference,
  cosineSimilarity,
  fingerprintPrefix,
  normalizeMusicalReferenceSession,
  RELATED_MOTIFS_DEFAULT_TOP_K,
  SIMILARITY_DEFAULT_TOP_K,
} from '../utils/compositionEmbeddingReference.js';
import {
  buildConditioningRequestFields,
  collectMultiRefBorrowRows,
  loadConditioningSession,
} from '../utils/referenceConditioningPolicy.js';
import { listAnalysisSectionOptions } from '../utils/compositionAnalysis.js';
import { normalizeCritiqueResult } from '../utils/compositionCritique.js';
import {
  activityLevelsMateriallyChanged,
  buildDefaultMixerControls,
  createDefaultTrackControl,
  mergeMixerControls,
  mixerControlsStateKey,
  normalizeTrackControlPatch,
  sanitizeMixerLogMeta,
} from '../utils/playbackMixerControls.js';
import {
  ProjectRevisionConflictError,
  applyAsBranch as applyAsBranchRequest,
  checkoutBranch as checkoutBranchRequest,
  commitRevision as commitRevisionRequest,
  createBranch as createBranchRequest,
  createProject as createProjectRequest,
  deleteProject as deleteProjectRequest,
  duplicateProject as duplicateProjectRequest,
  getProject as getProjectRequest,
  getRevision as getRevisionRequest,
  listBranches as listBranchesRequest,
  listProjects as listProjectsRequest,
  listRevisions as listRevisionsRequest,
  nameRevision as nameRevisionRequest,
  patchProject as patchProjectRequest,
  renameBranch as renameBranchRequest,
  restoreRevision as restoreRevisionRequest,
  fetchCollaborationStatus,
  listCollaborationActors,
  createCollaborationActor,
  listProjectMembers,
  grantProjectMember,
  listProjectComments,
  createProjectComment,
  listProjectReviews,
  openProjectReview,
  decideProjectReview,
  listProjectActivity,
} from '../api/projectApi.js';
import {
  PLAYBACK_MIXER_SCOPE_ARRANGEMENT,
  PLAYBACK_MIXER_SCOPE_DEVELOPMENT,
  PLAYBACK_MIXER_SCOPE_PREVIEW,
  PLAYBACK_MIXER_SCOPE_VERSION,
  PLAYBACK_MIXER_SCOPE_WORKING,
  PLAYBACK_SOURCE_ARRANGEMENT,
  PLAYBACK_SOURCE_DEVELOPMENT,
  PLAYBACK_SOURCE_GENERATION,
  PLAYBACK_SOURCE_VERSION,
  exclusiveAuditionPatch,
  resolvePlaybackSource,
} from '../utils/playbackSource.js';
import {
  AI_CANDIDATE_STATUS,
  aiCandidateLogFields,
  aiRuntimeFieldsFromResponse,
  buildAiCandidateEnvelope,
  buildArtifactRoleMapFromLog,
  buildHistoryAiProvenance,
  captureAiRequestContext,
  detectAiRequestStale,
  fingerprintCompositionOrNull,
  makeAiCandidateId,
  toHistoryAiWarningCodes,
  buildGenerationMetaFromCandidate,
  validateArtifactRoleMap,
} from '../utils/compositionCandidateLifecycle.js';
import { compareCompositions } from '../utils/compositionVersionComparison.js';
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
  compositionEditFingerprint,
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
  midiToPitch,
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
import { barDurationTicks, barStartTick, compileTimeline } from '../utils/compositionTimeline.js';
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
export const VERSION_HISTORY_PAGE_SIZE = 25;
export { ANALYSIS_DEBOUNCE_MS };
export { resolvePlaybackSource };

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
  motifCandidate: null,
  motifAuditionActive: false,
  motifCompareResult: null,
  motifRequestCapture: null,
};

export const DEFAULT_REHARMONIZE_OPERATION = 'increase_tension';
export const DEFAULT_REHARMONIZE_CONTENT_POLICY = 'preserve_melody_adapt_harmony';
export const DEFAULT_REHARMONIZE_ENGINE = 'deterministic';

const initialHarmonyUiState = {
  harmonySelectionStartBar: null,
  harmonySelectionEndBar: null,
  harmonySelectedSpanStartTick: null,
};

export const initialAdaptiveScoreState = {
  adaptiveScoreId: null,
  adaptiveDocumentRevision: null,
  adaptiveBindingStatus: null,
  adaptiveScore: null,
  adaptiveScoreList: [],
  adaptiveFindings: [],
  adaptiveSelectedStateId: null,
  adaptiveCommandError: '',
  adaptiveStatus: 'idle',
  adaptiveScheduledTransition: null,
};

let adaptiveScoreRequestSeq = 0;

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
  reharmonizeAuditionActive: false,
  reharmonizeCompareResult: null,
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

const initialVersionHistoryState = {
  versionBranches: [],
  versionBranchesStatus: 'idle',
  versionBranchesError: '',
  versionRevisions: [],
  versionRevisionsStatus: 'idle',
  versionRevisionsError: '',
  versionRevisionsNextBefore: null,
  versionSelectedRevisionId: null,
  versionCompareRevisionId: null,
  versionRevisionDetails: {},
  versionCompareResult: null,
  versionCompareStatus: 'idle',
  versionCompareError: '',
  versionAuditionActive: false,
  versionAuditionTrackControls: {},
  versionActionStatus: 'idle',
  versionActionError: '',
  versionRestoreBlockReason: null,
};

const logger = createAppLogger('musicStore');
const arrangementLogger = createAppLogger('musicStore.arrangement');
const embeddingLogger = createAppLogger('musicStore.embeddings');
const midiLogger = createAppLogger('midiInput');
const liveLogger = createAppLogger('liveMidi');
const jamLogger = createAppLogger('liveJam');
const audioLogger = createAppLogger('audioTranscription');
const audioRecoveryLogger = createAppLogger('audioRecovery');
const audioAlignmentLogger = createAppLogger('audioAlignment');
const collaborationLogger = createAppLogger('collaboration');
let sourceSeekRequestSeq = 0;
let lastSourceSeekDebugAt = 0;

export const AUDIO_PHASES = Object.freeze({
  IDLE: 'idle',
  REQUESTING_MIC: 'requesting_mic',
  RECORDING: 'recording',
  UPLOADING: 'uploading',
  TRANSCRIBING: 'transcribing',
  REVIEW: 'review',
  APPLYING: 'applying',
  ERROR: 'error',
});

export const AUDIO_RECOVERY_PHASES = Object.freeze({
  IDLE: 'idle',
  REQUESTING_MIC: 'requesting_mic',
  RECORDING: 'recording',
  UPLOADING: 'uploading',
  RUNNING: 'running',
  REVIEW: 'review',
  APPLYING: 'applying',
  BINDING: 'binding',
  BOUND: 'bound',
  ERROR: 'error',
});

const initialAudioTranscriptionState = {
  audioSupport: null,
  audioPhase: AUDIO_PHASES.IDLE,
  audioPreview: null,
  audioSelectedProvisionalIds: [],
  audioIncludeLowConfidence: false,
  audioQuantizeOnApply: false,
  audioDestinationTrackId: null,
  audioErrorCode: null,
  audioErrorMessage: '',
  audioConfidenceThreshold: DEFAULT_AUDIO_CONFIDENCE_THRESHOLD,
};

const initialAudioRecoveryState = {
  recoveryPhase: AUDIO_RECOVERY_PHASES.IDLE,
  recoveryJobId: null,
  recoveryJobStatus: null,
  recoveryPreview: null,
  recoverySelectedProvisionalIds: [],
  recoveryIncludeLowConfidence: false,
  recoveryStemRoleMap: {},
  recoveryStemSolo: null,
  recoveryInstallFlags: null,
  recoveryConfidenceThreshold: DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD,
  recoveryErrorCode: null,
  recoveryErrorMessage: '',
  recoveryBindWarning: null,
  recoverySourceAudioAssetId: null,
  recoveryResultAssetId: null,
  recoveryAlignmentAssetId: null,
  alignmentDocument: null,
  roundtripProvenance: null,
  recoveryOverlay: null,
  recoverySourceObjectUrl: null,
  recoveryDisableSeparation: false,
  // Source audition sync clock (HTMLAudio — not a playbackSource).
  sourceAuditionMode: 'idle', // idle | playing | scrubbing
  sourcePlayheadTick: null,
  audioWindowHighlight: null, // { startSeconds, endSeconds, startBar, endBar } | null
  sourceSeekRequest: null, // { id, seconds, reason } — panel applies to HTMLAudio
};

/** @type {ReturnType<typeof createAudioRecorder> | null} */
let audioRecorderSession = null;
/** @type {ReturnType<typeof createAudioRecorder> | null} */
let recoveryRecorderSession = null;

function clearedAudioTranscriptionState() {
  return { ...initialAudioTranscriptionState };
}

function clearedAudioRecoveryState(get) {
  if (get) {
    revokeRecoverySourceUrl(get);
  }
  return { ...initialAudioRecoveryState, recoverySourceObjectUrl: null };
}

function revokeRecoverySourceUrl(get) {
  const url = get?.()?.recoverySourceObjectUrl;
  if (url && typeof URL !== 'undefined') {
    try {
      URL.revokeObjectURL(url);
    } catch {
      // ignore
    }
  }
}

function transitionAudioPhase(set, get, nextPhase, reason) {
  const current = get().audioPhase;
  if (current === nextPhase) {
    return true;
  }
  audioLogger.info('Audio transcription phase transition', {
    from: current,
    to: nextPhase,
    reason,
  });
  set({ audioPhase: nextPhase });
  return true;
}

function transitionRecoveryPhase(set, get, nextPhase, reason) {
  const current = get().recoveryPhase;
  if (current === nextPhase) {
    return true;
  }
  audioRecoveryLogger.info('Audio recovery phase transition', {
    from: current,
    to: nextPhase,
    reason,
  });
  set({ recoveryPhase: nextPhase });
  return true;
}

/** @typedef {'idle'|'enabling'|'ready'|'armed'|'counting_in'|'recording'|'stopping'|'unavailable'|'denied'|'error'} MidiPhase */

export const MIDI_PHASES = Object.freeze({
  IDLE: 'idle',
  ENABLING: 'enabling',
  READY: 'ready',
  ARMED: 'armed',
  COUNTING_IN: 'counting_in',
  RECORDING: 'recording',
  STOPPING: 'stopping',
  UNAVAILABLE: 'unavailable',
  DENIED: 'denied',
  ERROR: 'error',
});

const MIDI_PHASE_TRANSITIONS = Object.freeze({
  idle: new Set(['enabling', 'unavailable', 'ready']),
  enabling: new Set(['ready', 'denied', 'unavailable', 'error', 'idle']),
  ready: new Set(['armed', 'idle', 'unavailable', 'error', 'enabling']),
  armed: new Set(['counting_in', 'recording', 'ready', 'idle']),
  counting_in: new Set(['recording', 'stopping', 'ready', 'idle']),
  recording: new Set(['stopping', 'ready', 'idle', 'error']),
  stopping: new Set(['idle', 'ready']),
  unavailable: new Set(['idle', 'enabling', 'ready']),
  denied: new Set(['idle', 'enabling']),
  error: new Set(['idle', 'enabling', 'ready']),
});

const initialMidiInputState = {
  midiSupport: null,
  midiAccessStatus: 'idle',
  midiInputs: [],
  midiSelectedInputId: null,
  midiArmed: false,
  midiPhase: MIDI_PHASES.IDLE,
  midiDestinationTrackId: null,
  midiMetronomeEnabled: true,
  midiCountInBars: 1,
  midiQuantizeAfterRecord: false,
  midiTestKeyboardEnabled: false,
  midiActiveNotes: [],
  midiTakeSummary: null,
  midiErrorCode: null,
  midiErrorMessage: '',
};

const initialLivePerformanceState = {
  /** @type {'idle'|'arming'|'running'|'degraded'|'stopping'|'cancelled'} */
  livePhase: 'idle',
  liveSessionId: null,
  liveHorizonBars: readLiveHorizonBounds().barsDefault,
  liveHorizonMs: readLiveHorizonBounds().msDefault,
  liveStreamNoteOnCount: 0,
  liveStreamNoteOffCount: 0,
  liveStreamRingSize: 0,
  liveErrorCode: null,
  liveErrorMessage: '',
  liveSessionSnapshot: null,
  /** @type {import('../utils/liveJamContracts.js').JamMode | null} */
  jamMode: null,
  jamControls: createDefaultJamControls(),
  jamBelief: createIdleHarmonyBelief(),
  jamPredictUnavailable: false,
  jamHarmonyHoldCount: 0,
  /** Last bounded features snapshot for cold predict (session-only). */
  jamLastFeatures: null,
};

/** @type {ReturnType<typeof createMidiAccessSession> | null} */
let midiAccessSession = null;
/** @type {(() => void) | null} */
let midiAccessUnsubscribe = null;
/** @type {(() => void) | null} */
let midiInputUnsubscribe = null;
/** @type {ReturnType<typeof createMidiPerformanceCapture> | null} */
let midiCaptureSession = null;
/** @type {ReturnType<ReturnType<typeof createMidiPerformanceCapture>['stop']> | null} */
let midiPendingTake = null;
/** @type {ReturnType<typeof setTimeout> | null} */
let midiCountInTimer = null;
/** @type {ReturnType<typeof createMidiMetronome> | null} */
let midiMetronomeSession = null;
/** @type {ReturnType<typeof createLiveMidiStream> | null} */
let liveMidiStreamSession = null;
/** @type {ReturnType<typeof createLiveAccompanimentScheduler> | null} */
let liveAccompanimentSchedulerSession = null;
/** @type {ReturnType<typeof createLivePatternEngine> | null} */
let livePatternEngineSession = null;
/** @type {ReturnType<typeof createLiveJamContext> | null} */
let liveJamContextSession = null;
/** @type {ReturnType<typeof createLiveJamRoleEngine> | null} */
let liveJamRoleEngineSession = null;
/** @type {ReturnType<typeof createLiveLatencyTracker> | null} */
let liveLatencyTrackerSession = null;
/** @type {AbortController | null} */
let livePredictAbortController = null;
/** @type {string | null} */
let livePredictInFlightRequestId = null;
let livePredictEpoch = 0;

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

/** Ephemeral musical-reference / similarity session (never persisted). */
const initialMusicalReferenceSessionState = {
  musicalReferenceEnabled: false,
  musicalReference: null,
  musicalReferenceComposition: null,
  musicalReferenceStatus: 'idle',
  musicalReferenceError: '',
  musicalReferenceRequestId: 0,
  musicalReferenceB: null,
  musicalReferenceBComposition: null,
  musicalReferenceBStatus: 'idle',
  musicalReferenceBError: '',
  musicalReferenceBRequestId: 0,
  similarityHits: [],
  similarityStatus: 'idle',
  similarityError: '',
  similarityRequestId: 0,
  relatedMotifHits: [],
  relatedMotifStatus: 'idle',
  relatedMotifError: '',
  relatedMotifRequestId: 0,
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
  developmentCompareResult: null,
  developmentProvider: null,
  developmentModel: null,
  developmentReferenceProvenance: null,
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
  arrangementCompareResult: null,
  arrangementProvider: null,
  arrangementModel: null,
};

function isManualSaveReason(reason) {
  return reason === 'manual' || reason === 'manual-force';
}

function historyStateFromProject(project) {
  if (!project || typeof project !== 'object') {
    return {
      activeBranchId: null,
      activeBranchName: null,
      currentRevisionId: null,
      currentRevisionSequence: null,
      workingVersion: null,
      workingFingerprint: null,
      projectCollaboration: null,
    };
  }
  return {
    activeBranchId: project.active_branch_id ?? null,
    activeBranchName: project.active_branch_name ?? null,
    currentRevisionId: project.current_revision_id ?? null,
    currentRevisionSequence: project.current_revision_sequence ?? null,
    workingVersion: project.working_version ?? null,
    workingFingerprint: project.working_fingerprint ?? null,
    projectCollaboration: project.collaboration ?? null,
  };
}

function historyStateFromDurable(result) {
  if (!result || typeof result !== 'object') {
    return historyStateFromProject(null);
  }
  return {
    activeBranchId: result.active_branch_id ?? null,
    activeBranchName: result.active_branch_name ?? null,
    currentRevisionId: result.current_revision_id ?? null,
    currentRevisionSequence: result.current_revision_sequence ?? null,
    workingVersion: result.working_version ?? null,
    workingFingerprint: result.working_fingerprint ?? null,
  };
}

function normalizeAiProvider(provider) {
  const value = String(provider || '').trim().toLowerCase();
  if (value === 'openai' || value === 'deepseek' || value === 'fake') {
    return value;
  }
  return null;
}

function projectIsDirtyForUnload(state) {
  if (!state?.currentProjectId) {
    return false;
  }
  if (state.saveStatus === 'saving' || state.saveStatus === 'unsaved' || state.saveStatus === 'conflict') {
    return true;
  }
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  return persistRevision !== state.lastSavedPersistRevision;
}

export { projectIsDirtyForUnload };

async function flushProjectDraft(get, { reason = 'navigation-flush' } = {}) {
  const state = get();
  if (!state.currentProjectId) {
    return null;
  }
  cancelAutosaveTimer();
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  if (persistRevision === state.lastSavedPersistRevision && state.saveStatus !== 'unsaved') {
    return null;
  }
  return get().saveCurrentProject({ reason });
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
let musicalReferenceRequestSeq = 0;
let musicalReferenceBRequestSeq = 0;
let similarityRequestSeq = 0;
let relatedMotifRequestSeq = 0;
let versionBranchesRequestSeq = 0;
let versionRevisionsRequestSeq = 0;
let versionDetailRequestSeq = 0;
let versionActionRequestSeq = 0;
let generationRequestSeq = 0;

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

function clearMidiCountInTimer() {
  if (midiCountInTimer != null) {
    clearTimeout(midiCountInTimer);
    midiCountInTimer = null;
  }
  if (midiMetronomeSession) {
    midiMetronomeSession.clear();
  }
}

function disposeMidiMetronome() {
  if (midiMetronomeSession) {
    midiMetronomeSession.dispose();
    midiMetronomeSession = null;
  }
}

function disposeMidiInputSubscription() {
  if (midiInputUnsubscribe) {
    try {
      midiInputUnsubscribe();
    } catch (error) {
      midiLogger.warn('MIDI input unsubscribe failed', {
        message: error instanceof Error ? error.message : 'unknown',
      });
    }
    midiInputUnsubscribe = null;
  }
}

function beginMidiCapture(set, get, { originTick, atMs } = {}) {
  const state = get();
  const composition = state.editedMusicJson;
  if (!midiCaptureSession) {
    midiCaptureSession = createMidiPerformanceCapture({
      composition,
      originTick: originTick ?? state.editCursorTick ?? 0,
    });
  }
  midiPendingTake = null;
  midiCaptureSession.start({
    originTick: originTick ?? state.editCursorTick ?? 0,
    composition,
    atMs,
  });
  if (!transitionMidiPhase(set, get, MIDI_PHASES.RECORDING, 'capture-start')) {
    return false;
  }
  set({
    midiArmed: true,
    midiErrorCode: null,
    midiErrorMessage: '',
    midiTakeSummary: null,
  });
  midiLogger.info('MIDI recording started', {
    originTick: originTick ?? state.editCursorTick ?? 0,
    destinationTrackId: state.midiDestinationTrackId || state.pianoRollTrackId,
    countInBars: state.midiCountInBars,
  });
  return true;
}

function commitMidiTakeBuffer(set, get, take, { quantizeAfter = null } = {}) {
  const state = get();
  const trackId =
    state.midiDestinationTrackId
    || state.pianoRollTrackId
    || pickDefaultTrackId(state.editedMusicJson);
  if (!take || (!take.noteCount && !take.pedalCount)) {
    midiLogger.warn('MIDI commit skipped — empty take', { code: 'midi_empty_take' });
    set({
      midiErrorCode: 'midi_empty_take',
      midiErrorMessage: 'Nothing was recorded.',
      midiTakeSummary: take,
    });
    return false;
  }

  const applied = applyMidiTakeToComposition(state.editedMusicJson, {
    trackId,
    notes: take.notes,
    sustainPedals: take.sustainPedals,
    lockedTrackIds: state.lockedTrackIds,
  });
  if (!applied.ok) {
    midiLogger.error('MIDI take commit validation failed', {
      code: applied.code,
      message: applied.message || applied.code,
    });
    set({
      midiErrorCode: applied.code,
      midiErrorMessage: applied.message || applied.code,
      midiTakeSummary: {
        noteCount: take.noteCount,
        pedalCount: take.pedalCount,
        originTick: take.originTick,
        endTick: take.endTick,
      },
    });
    return false;
  }

  const primary = applied.noteRefs[0] || null;
  const ok = commitCompositionTransaction(set, get, {
    nextComposition: applied.composition,
    selectedTrackId: trackId,
    selectedNoteId: primary?.eventId || null,
    selectedNoteIds: applied.noteRefs
      .filter((ref) => ref.trackId === trackId)
      .map((ref) => ref.eventId),
    editorSelectionRefs: applied.noteRefs,
    editorSelectionPrimary: primary,
    action: 'midi-record',
    noteSummary: {
      noteCount: applied.noteRefs.length,
      pedalCount: take.pedalCount,
      barsAdded: applied.barsAdded,
    },
    affectedNoteCount: applied.noteRefs.length,
    affectedTrackCount: 1,
    statePatch: {
      midiTakeSummary: {
        noteCount: take.noteCount,
        pedalCount: take.pedalCount,
        originTick: take.originTick,
        endTick: take.endTick,
        barsAdded: applied.barsAdded,
      },
      midiArmed: false,
      midiActiveNotes: [],
      midiErrorCode: null,
      midiErrorMessage: '',
    },
  });

  if (!ok) {
    midiLogger.error('MIDI take transaction rejected', { code: 'midi_commit_failed' });
    set({
      midiErrorCode: 'midi_commit_failed',
      midiErrorMessage: 'Could not commit recorded notes.',
    });
    return false;
  }

  midiPendingTake = null;
  midiLogger.info('MIDI take committed', {
    noteCount: take.noteCount,
    pedalCount: take.pedalCount,
    barsAdded: applied.barsAdded,
  });

  const shouldQuantize = quantizeAfter != null
    ? Boolean(quantizeAfter)
    : Boolean(get().midiQuantizeAfterRecord);
  if (shouldQuantize && applied.noteRefs.length) {
    get().quantizeMidiTakeNotes(applied.noteRefs);
  }

  transitionMidiPhase(set, get, MIDI_PHASES.READY, 'commit');
  return true;
}

function transitionMidiPhase(set, get, nextPhase, reason = '') {
  const current = get().midiPhase;
  const allowed = MIDI_PHASE_TRANSITIONS[current];
  if (!allowed || !allowed.has(nextPhase)) {
    midiLogger.warn('MIDI phase transition rejected', {
      from: current,
      to: nextPhase,
      reason,
    });
    return false;
  }
  if (current === nextPhase) {
    return true;
  }
  midiLogger.info('MIDI phase transition', { from: current, to: nextPhase, reason });
  set({ midiPhase: nextPhase });
  return true;
}

function handleMidiAccessEvent(set, get, event) {
  if (!event || typeof event !== 'object') {
    return;
  }
  if (event.type === 'inputs' && Array.isArray(event.inputs)) {
    set({ midiInputs: event.inputs });
    const selected = get().midiSelectedInputId;
    if (selected && !event.inputs.some((input) => input.id === selected)) {
      set({ midiSelectedInputId: null });
      disposeMidiInputSubscription();
    }
    return;
  }
  if (event.type === 'disconnect') {
    const selected = get().midiSelectedInputId;
    const recording =
      get().midiPhase === MIDI_PHASES.RECORDING
      || get().midiPhase === MIDI_PHASES.COUNTING_IN;
    set({
      midiInputs: Array.isArray(event.inputs) ? event.inputs : get().midiInputs,
      midiErrorCode: 'midi_device_disconnected',
      midiErrorMessage: 'MIDI device disconnected',
      midiActiveNotes: [],
      ...(selected && event.deviceId === selected ? { midiSelectedInputId: null } : {}),
    });
    if (selected && event.deviceId === selected) {
      disposeMidiInputSubscription();
    }
    if (recording) {
      clearMidiCountInTimer();
      midiLogger.warn('MIDI disconnect during record — buffering partial take', {
        code: 'midi_device_disconnected',
        deviceId: event.deviceId ? String(event.deviceId).slice(0, 12) : null,
      });
      if (midiCaptureSession && midiCaptureSession.isCapturing()) {
        midiPendingTake = midiCaptureSession.stop();
        set({
          midiTakeSummary: {
            noteCount: midiPendingTake.noteCount,
            pedalCount: midiPendingTake.pedalCount,
            originTick: midiPendingTake.originTick,
            endTick: midiPendingTake.endTick,
            partial: true,
          },
        });
      }
      transitionMidiPhase(set, get, MIDI_PHASES.READY, 'disconnect-during-record');
      set({ midiArmed: false });
    }
    return;
  }
  if (event.type === 'reconnect') {
    set({
      midiInputs: Array.isArray(event.inputs) ? event.inputs : get().midiInputs,
    });
  }
}

function upsertActiveMidiNote(activeNotes, pitch) {
  if (!pitch) {
    return activeNotes;
  }
  if (activeNotes.includes(pitch)) {
    return activeNotes;
  }
  return [...activeNotes, pitch];
}

function removeActiveMidiNote(activeNotes, pitch) {
  if (!pitch) {
    return activeNotes;
  }
  return activeNotes.filter((value) => value !== pitch);
}

/**
 * Live MIDI → active-note highlights + capture buffer while recording.
 */
function abortLivePredictInFlight(reason = 'cancel') {
  if (livePredictAbortController) {
    try {
      livePredictAbortController.abort();
    } catch {
      // ignore
    }
    livePredictAbortController = null;
  }
  livePredictInFlightRequestId = null;
  liveLogger.info('live predict abort', { reason });
}

/**
 * Fire-and-forget cold-path predict — max one in-flight; never awaited on hot path.
 * Jam mode wires belief → active_harmony, bounded features, controls, role_mask.
 */
function requestLivePredictFill(get, set, { clock, harmony, horizon, sessionId }) {
  if (livePredictInFlightRequestId) {
    liveLogger.debug('predict skip — in flight', {
      inFlight: livePredictInFlightRequestId.slice(0, 12),
    });
    return;
  }
  if (!liveAccompanimentSchedulerSession) {
    return;
  }
  const epoch = livePredictEpoch;
  const requestId = `pred-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const abortGate = createLivePredictAbortController();
  if (!abortGate.ok) {
    liveLogger.warn('predict skip — AbortController unavailable', { code: abortGate.code });
    set({
      jamPredictUnavailable: true,
    });
    jamLogger.info('fallback', { code: JAM_PREDICT_UNAVAILABLE, reason: abortGate.code });
    return;
  }
  const controller = abortGate.controller;
  livePredictAbortController = controller;
  livePredictInFlightRequestId = requestId;

  const state = get();
  const jamMode = state.jamMode;
  const controls = state.jamControls || createDefaultJamControls();
  const belief = state.jamBelief || createIdleHarmonyBelief();
  const densityFactor = controls.density === 'high' ? 0.8
    : controls.density === 'low' ? 0.35
      : 0.55;

  /** @type {Record<string, unknown>} */
  const body = {
    schema_version: LIVE_PREDICT_REQUEST_SCHEMA,
    session_id: sessionId,
    request_id: requestId,
    clock: {
      tick: clock.tick,
      bar: clock.bar,
      beat: clock.beatInBar,
      tempo: clock.tempo,
    },
    active_harmony: {
      symbol: belief.symbol ?? harmony?.symbol ?? null,
      start_tick: harmony?.start_tick ?? null,
      duration_ticks: harmony?.duration_ticks ?? null,
    },
    features: state.jamLastFeatures && typeof state.jamLastFeatures === 'object'
      ? {
        schema_version: state.jamLastFeatures.schema || 'live.performance.features.v1',
        pitch_activity: state.jamLastFeatures.pitch_activity,
        beat: state.jamLastFeatures.beat,
        probable_key: state.jamLastFeatures.probable_key,
        probable_harmony: state.jamLastFeatures.probable_harmony,
        phrase: state.jamLastFeatures.phrase,
        density: densityFactor,
        recent_note_count: state.liveStreamNoteOnCount || 0,
      }
      : {
        density: densityFactor,
        recent_note_count: state.liveStreamNoteOnCount || 0,
      },
    horizon,
  };

  if (isJamMode(jamMode)) {
    body.jam_mode = jamMode;
    body.controls = {
      complexity: controls.complexity,
      density: controls.density,
      style: controls.style,
      responsiveness: controls.responsiveness,
    };
    body.belief = {
      symbol: belief.symbol,
      confidence: belief.confidence,
      held: Boolean(belief.held),
      reason_code: belief.reason_code,
    };
    try {
      const partition = resolveJamRolePartition(jamMode, controls.complexity);
      body.role_mask = partition.ai_roles;
    } catch {
      // leave role_mask unset
    }
  }

  liveLatencyTrackerSession?.markStart('generation');
  // Intentionally not awaited — cold path.
  predictLiveAccompaniment(body, { signal: controller.signal })
    .then((chunk) => {
      liveLatencyTrackerSession?.markEnd('generation');
      if (epoch !== livePredictEpoch) {
        liveLogger.warn('predict stale discard', { request_id: requestId.slice(0, 12) });
        return;
      }
      if (get().livePhase !== 'running' && get().livePhase !== 'degraded') {
        return;
      }
      if (livePredictInFlightRequestId !== requestId) {
        return;
      }
      if (chunk && liveAccompanimentSchedulerSession) {
        liveAccompanimentSchedulerSession.ingestChunk(chunk);
        if (chunk.latency_ms?.generation != null) {
          liveLatencyTrackerSession?.record('generation', chunk.latency_ms.generation);
        }
        if (get().jamPredictUnavailable) {
          set({ jamPredictUnavailable: false });
          jamLogger.info('predict recovered', { request_id: requestId.slice(0, 12) });
        }
      }
    })
    .catch((error) => {
      liveLatencyTrackerSession?.markEnd('generation');
      if (error?.code === 'aborted') {
        return;
      }
      const unavailable = error?.status === 503
        || error?.code === 'LIVE_PREDICT_UNAVAILABLE'
        || error?.code === JAM_PREDICT_UNAVAILABLE
        || !error?.status;
      if (unavailable) {
        set({ jamPredictUnavailable: true });
        jamLogger.info('fallback', {
          code: JAM_PREDICT_UNAVAILABLE,
          status: error?.status ?? null,
          errCode: error?.code || 'error',
        });
      }
      liveLogger.warn('predict fill failed', {
        code: error?.code || 'error',
        request_id: requestId.slice(0, 12),
      });
    })
    .finally(() => {
      if (livePredictInFlightRequestId === requestId) {
        livePredictInFlightRequestId = null;
        livePredictAbortController = null;
      }
    });
}

/**
 * Live MIDI → active-note highlights + capture buffer while recording.
 */
function handleLiveMidiMessage(set, get, event) {
  const data = event && event.data != null ? event.data : null;
  const parsed = parseMidiMessage(data);
  const recording = get().midiPhase === MIDI_PHASES.RECORDING
    && midiCaptureSession
    && midiCaptureSession.isCapturing();

  if (recording) {
    midiCaptureSession.injectMessage(data, {
      atMs: event && Number.isFinite(Number(event.timeStamp))
        ? Number(event.timeStamp)
        : undefined,
    });
  }

  if (
    liveMidiStreamSession
    && liveMidiStreamSession.isActive()
    && (get().livePhase === 'running' || get().livePhase === 'degraded')
  ) {
    const clock = getLiveClock(get().playbackSeconds, get().editedMusicJson);
    liveMidiStreamSession.pushMessage(data, { tick: clock.tick });
    const snap = liveMidiStreamSession.getSnapshot();
    set({
      liveStreamNoteOnCount: snap.noteOnCount,
      liveStreamNoteOffCount: snap.noteOffCount,
      liveStreamRingSize: snap.ringSize,
    });
  }

  if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_ON) {
    const { pitch } = midiToPitch(parsed.note);
    if (!pitch) {
      return;
    }
    set({ midiActiveNotes: upsertActiveMidiNote(get().midiActiveNotes, pitch) });
    return;
  }
  if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_OFF) {
    const { pitch } = midiToPitch(parsed.note);
    if (!pitch) {
      return;
    }
    set({ midiActiveNotes: removeActiveMidiNote(get().midiActiveNotes, pitch) });
  }
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
  generationCandidate: null,
  generationAuditionActive: false,
  generationCompareResult: null,
  generationRequestCapture: null,
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
  /** Ephemeral development-candidate mixer (isolated from working). */
  developmentCandidateTrackControls: {},
  /** Ephemeral AI preview mixer (generation / edit / motif / reharmonize). */
  previewTrackControls: {},
  /** Authoritative audition source key last armed by transport. */
  playbackSourceKey: null,
  /** Engine operation epoch mirrored for UI (ephemeral). */
  playbackOperationEpoch: 0,
  /** Bounded activity meters; updated ≤10–20 Hz when materially changed. */
  playbackActivity: { tracks: {}, clipped: false },
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

  /** Selected durable composer profile id for generate soft conditioning (session). */
  composerProfileId: null,
  /** Profile strength: off | light | normal | strong (default off). */
  composerProfileStrength: 'off',
  /** Cached list rows from GET /composer-profiles (id/name/updated_at/source_count). */
  composerProfileList: [],
  /** Last preview soft-fragment payload (session). */
  composerProfilePreview: null,

  aiEditStartBar: null,
  aiEditEndBar: null,
  aiEditTrackMode: 'current',
  aiEditTrackIds: null,
  aiEditInstruction: '',
  aiEditStatus: 'idle',
  aiEditError: '',
  aiEditWarnings: [],
  aiEditCandidate: null,
  aiEditAuditionActive: false,
  aiEditCompareResult: null,
  aiEditRequestCapture: null,

  activeView: 'home',
  currentProjectId: null,
  currentProjectName: '',
  activeBranchId: null,
  activeBranchName: null,
  currentRevisionId: null,
  currentRevisionSequence: null,
  workingVersion: null,
  workingFingerprint: null,
  projectCollaboration: null,
  collaborationEnabled: false,
  collaborationActorId: '',
  collaborationActors: [],
  collaborationMembers: [],
  collaborationComments: [],
  collaborationReviews: [],
  collaborationActivity: [],
  saveConflict: null,
  ...initialVersionHistoryState,
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

  critiqueResult: null,
  critiqueStatus: 'idle',
  critiqueError: '',
  critiqueStratumFilter: 'all',

  ...initialHarmonyUiState,
  ...initialAdaptiveScoreState,
  ...initialReharmonizePreviewState,
  multiAgentStatus: 'idle',
  multiAgentError: '',
  multiAgentCandidate: null,
  multiAgentRequestId: 0,
  multiAgentRevisionMode: 'off',
  multiAgentRevisionHistory: [],
  multiAgentPassCandidates: [],
  multiAgentStopReason: null,
  multiAgentOperationSummary: null,
  multiAgentOperationRunId: null,
  multiAgentRevisionLoopStatus: 'idle',
  multiAgentComparePassIndex: null,
  multiAgentAuditionActive: false,
  multiAgentAbortController: null,
  autonomousStatus: 'idle',
  autonomousError: '',
  autonomousRun: null,
  autonomousRunId: null,
  autonomousBaselineFingerprint: null,
  autonomousLoadedFingerprint: null,
  autonomousAbortController: null,
  autonomousPreview: null,
  autonomousPreviewFingerprint: null,
  ...initialDevelopmentPreviewState,
  ...initialMusicalReferenceSessionState,
  ...initialArrangementPreviewState,
  composerTabRequest: null,
  composerTabRequestSeq: 0,
  ...initialMotifUiState,
  ...initialMidiInputState,
  ...initialLivePerformanceState,
  ...initialAudioTranscriptionState,
  ...initialAudioRecoveryState,

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

  startGeneration: async () => {
    if (get().generationStatus === 'loading') {
      console.warn('[musicStore] Duplicate generation blocked', {
        provider: get().selectedProvider,
        model: get().selectedModel,
      });
      return false;
    }
    generationRequestSeq += 1;
    const requestId = generationRequestSeq;
    const capture = captureAiRequestContext(get());
    const sourceFingerprint = await fingerprintCompositionOrNull(get().editedMusicJson);
    // Abandon if a newer startGeneration began while fingerprinting.
    if (requestId !== generationRequestSeq) {
      return false;
    }
    console.info('[musicStore] LLM generation started', {
      provider: get().selectedProvider,
      model: get().selectedModel,
      requestId,
      projectId: capture.projectId,
      sourcePrefix: editFingerprintLogPrefix(sourceFingerprint),
    });
    set({
      generationStatus: 'loading',
      uiError: '',
      warnings: [],
      generationCandidate: null,
      generationAuditionActive: false,
      generationCompareResult: null,
      generationRequestCapture: {
        ...capture,
        requestId,
        sourceFingerprint,
        promptSnapshot: buildPromptSnapshot(get().prompt),
      },
    });
    return true;
  },

  completeGeneration: async ({
    music,
    musicxml,
    warnings = [],
    provider = null,
    model = null,
    model_id = null,
    model_version = null,
    runtime = null,
    capability = null,
    operation = null,
    generation_parameters = null,
    requested_model_id = null,
    resolved_model_id = null,
    fallback_applied = false,
    pipeline_id = null,
    stages = null,
    seed = null,
  }) => {
    const capture = get().generationRequestCapture;
    if (!capture || get().generationStatus !== 'loading') {
      console.warn('[musicStore] Ignoring generation response without active request');
      return false;
    }
    const requestId = capture.requestId;
    const stale = detectAiRequestStale(capture, get());
    if (stale.stale) {
      console.warn('[musicStore] Generation response rejected as stale', {
        reason: stale.reason,
        requestId,
      });
      set({
        generationStatus: 'error',
        uiError: 'Composition or project changed during generation; request a new preview',
        generationCandidate: null,
        generationAuditionActive: false,
        generationCompareResult: null,
      });
      return false;
    }

    let composition;
    try {
      const { composition: withIds } = ensureCompositionNoteIds(music);
      composition = prepareCompositionForStore(withIds);
    } catch (error) {
      console.warn('[musicStore] Generation candidate prepare failed', {
        code: error.code || 'prepare_failed',
      });
      set({
        generationStatus: 'error',
        uiError: error.message || 'Generated composition invalid',
      });
      return false;
    }
    const validation = validateMusicJson(composition);
    if (!validation.valid || !isCanonicalComposition(composition)) {
      console.error('[musicStore] Generated music JSON failed validation', {
        message: validation.message,
      });
      set({
        generationStatus: 'error',
        uiError: validation.message || 'Generated composition invalid',
      });
      return false;
    }

    const candidateFingerprint = await compositionEditFingerprint(composition);
    if (get().generationRequestCapture?.requestId !== requestId) {
      console.warn('[musicStore] Ignoring superseded generation response', { requestId });
      return false;
    }
    const enrichedGenerationParameters = (() => {
      const base = generation_parameters && typeof generation_parameters === 'object'
        ? { ...generation_parameters }
        : {};
      if (pipeline_id && !base.pipeline_id) {
        base.pipeline_id = pipeline_id;
      }
      if (seed != null && base.seed == null) {
        base.seed = seed;
      }
      if (Array.isArray(stages) && stages.length && !Array.isArray(base.stages)) {
        base.stages = stages;
      }
      if (!base.provenance_schema && (base.pipeline_id || base.stages)) {
        base.provenance_schema = 'generation.provenance.v1';
      }
      return Object.keys(base).length ? base : null;
    })();

    const candidate = buildAiCandidateEnvelope({
      candidateId: makeAiCandidateId('gen'),
      operationType: 'generate-apply',
      composition,
      sourceFingerprint: capture.sourceFingerprint,
      candidateFingerprint,
      provider: provider || get().selectedProvider || null,
      model: model || get().selectedModel || null,
      ...aiRuntimeFieldsFromResponse({
        model_id,
        model_version,
        runtime,
        capability,
        operation,
        generation_parameters: enrichedGenerationParameters,
        requested_model_id,
        resolved_model_id,
        fallback_applied,
      }),
      instruction: capture.promptSnapshot?.instructions || get().prompt?.instructions || null,
      warnings,
      musicXml: musicxml || '',
      extras: {
        prompt: capture.promptSnapshot || buildPromptSnapshot(get().prompt),
        request_id: requestId,
        pipeline_id: enrichedGenerationParameters?.pipeline_id || pipeline_id || null,
        seed: enrichedGenerationParameters?.seed ?? seed ?? null,
        stages: enrichedGenerationParameters?.stages || stages || [],
      },
    });

    console.info('[musicStore] Generation candidate staged', {
      ...aiCandidateLogFields(candidate),
      requestId,
      barCount: composition.bar_count,
      pipelineId: candidate.pipeline_id || null,
      seed: candidate.seed ?? null,
      stageModelIds: Array.isArray(candidate.stages)
        ? candidate.stages.map((stage) => stage?.model_id).filter(Boolean)
        : [],
    });
    set({
      generationCandidate: candidate,
      generationAuditionActive: false,
      generationCompareResult: null,
      generationStatus: 'success',
      uiError: '',
      warnings,
      // Keep legacy generatedMusicJson as a non-authoritative preview mirror for UI emptiness checks.
      generatedMusicJson: composition,
      musicXml: musicxml || '',
    });
    return true;
  },

  failGeneration: (message) => {
    console.error('[musicStore] LLM generation failed', { message });
    set({
      generationStatus: 'error',
      uiError: message,
      generationAuditionActive: false,
    });
  },

  rejectGenerationCandidate: () => {
    const candidate = get().generationCandidate;
    console.info('[musicStore] Generation candidate rejected', {
      ...aiCandidateLogFields(candidate),
    });
    set({
      generationCandidate: null,
      generationAuditionActive: false,
      generationCompareResult: null,
      generationStatus: 'idle',
      generatedMusicJson: get().editedMusicJson,
      musicXml: '',
      warnings: [],
      uiError: '',
    });
    return true;
  },

  setGenerationAuditionActive: (active) => {
    const enabled = Boolean(active);
    const candidate = get().generationCandidate;
    if (enabled && (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY)) {
      return false;
    }
    console.info('[musicStore] Generation audition toggled', {
      active: enabled,
      ...aiCandidateLogFields(candidate),
    });
    set({
      generationAuditionActive: enabled,
      ...(enabled
        ? exclusiveAuditionPatch(PLAYBACK_SOURCE_GENERATION, ARRANGEMENT_AUDITION_SOURCE)
        : {}),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshGenerationComparison: () => {
    const candidate = get().generationCandidate;
    if (!candidate) {
      set({ generationCompareResult: null });
      return null;
    }
    const result = compareCompositions(get().editedMusicJson, candidate.composition, {
      leftLabel: 'working',
      rightLabel: 'generation-candidate',
    });
    console.debug('[musicStore] Generation comparison ready', {
      identical: Boolean(result?.identical),
      added: Number(result?.events?.added) || 0,
      removed: Number(result?.events?.removed) || 0,
      changed: Number(result?.events?.changed) || 0,
      ...aiCandidateLogFields(candidate),
    });
    set({ generationCompareResult: result });
    return result;
  },

  applyGenerationCandidate: async ({ asNewBranch = false, branchName = null } = {}) => {
    const state = get();
    const candidate = state.generationCandidate;
    if (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY) {
      return false;
    }
    const stale = detectAiRequestStale(state.generationRequestCapture, state);
    if (stale.stale) {
      console.warn('[musicStore] Generation apply blocked; request context stale', {
        reason: stale.reason,
      });
      set({
        generationCandidate: {
          ...candidate,
          status: AI_CANDIDATE_STATUS.STALE,
        },
        generationStatus: 'error',
        uiError: 'Source changed; request a new generation preview',
      });
      return false;
    }
    const liveSourceFp = await fingerprintCompositionOrNull(state.editedMusicJson);
    const liveCandidateFp = await compositionEditFingerprint(candidate.composition);
    if (
      liveSourceFp !== candidate.source_fingerprint
      || liveCandidateFp !== candidate.candidate_fingerprint
    ) {
      console.warn('[musicStore] Generation apply blocked by fingerprint mismatch', {
        ...aiCandidateLogFields(candidate),
      });
      set({
        generationCandidate: {
          ...candidate,
          status: AI_CANDIDATE_STATUS.STALE,
        },
        generationStatus: 'error',
        uiError: 'Candidate fingerprints no longer match; request a new preview',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(
      ensureCompositionNoteIds(candidate.composition).composition,
    );
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
      set({
        generationStatus: 'error',
        uiError: validation.message || 'Candidate composition invalid',
      });
      return false;
    }

    set({
      generationCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.APPLYING },
    });

    const generationMeta = buildGenerationMetaFromCandidate(
      candidate,
      candidate.prompt || buildPromptSnapshot(state.prompt),
    );
    console.info('[musicStore] Generation apply provenance', {
      pipelineId: generationMeta.pipeline_id,
      seed: generationMeta.seed,
      modelIds: generationMeta.stage_model_ids,
      ...aiCandidateLogFields(candidate),
    });

    // No open project: documented local-only install (no durable history claim).
    if (!state.currentProjectId) {
      console.info('[musicStore] Generation apply local-only (no project)', {
        ...aiCandidateLogFields(candidate),
      });
      cancelAnalysisLifecycle();
      const ok = commitCompositionTransaction(set, get, {
        nextComposition: prepared,
        selectedTrackId: state.pianoRollTrackId,
        selectedNoteId: state.pianoRollNoteId,
        selectedNoteIds: state.pianoRollNoteIds,
        action: 'generate-apply',
        noteSummary: null,
        statePatch: {
          generatedMusicJson: prepared,
          musicXml: candidate.music_xml || '',
          generationMeta,
          generationCandidate: null,
          generationAuditionActive: false,
          generationCompareResult: null,
          generationStatus: 'idle',
          warnings: candidate.warnings || [],
          pianoRollTrackId: pickDefaultTrackId(prepared, state.pianoRollTrackId),
          pianoRollNoteId: null,
          pianoRollNoteIds: [],
          editCursorTick: 0,
          ...editorPrefsForCompositionReplace(state, prepared),
          ...clearedAnalysisState(),
          ...clearedMotifUiState(),
          ...clearedReharmonizePreviewState(),
          ...clearedDevelopmentPreviewState(),
          ...clearedArrangementPreviewState(),
          ...initialHarmonyUiState,
  ...initialAdaptiveScoreState,
        },
      });
      return ok;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        generationCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        generationStatus: 'error',
        uiError: 'Missing branch CAS fields for durable generation apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            generationCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
            generationStatus: 'error',
            uiError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'generate-apply',
          ai: {
            ...buildHistoryAiProvenance(candidate, {
              provider: normalizeAiProvider(generationMeta.provider),
              model: generationMeta.model,
              user_instruction: candidate.instruction || undefined,
            }),
          },
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'generate-apply',
          checkpoint_dirty_draft: true,
          ai: {
            ...buildHistoryAiProvenance(candidate, {
              provider: normalizeAiProvider(generationMeta.provider),
              model: generationMeta.model,
              user_instruction: candidate.instruction || undefined,
            }),
          },
        });
      }

      if (get().currentProjectId !== state.currentProjectId) {
        console.warn('[musicStore] Generation apply ignored after project switch');
        return false;
      }

      console.info('[musicStore] Generation candidate applied', {
        ...aiCandidateLogFields(candidate),
        asNewBranch: Boolean(asNewBranch),
        revisionCreated: Boolean(durable?.revision_created),
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'generate-apply',
      });
      set({
        generationMeta,
        generationCandidate: null,
        generationAuditionActive: false,
        generationCompareResult: null,
        generationStatus: 'idle',
        generatedMusicJson: get().editedMusicJson,
        musicXml: candidate.music_xml || '',
        warnings: candidate.warnings || [],
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      if (asNewBranch) {
        await get().loadVersionBranches().catch(() => {});
        await get().loadVersionRevisions({ reset: true }).catch(() => {});
      }
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        console.warn('[musicStore] Generation apply conflict', {
          code: error.code,
          ...aiCandidateLogFields(candidate),
        });
        set({
          generationCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
          generationStatus: 'error',
          uiError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.warn('[musicStore] Generation apply failed', {
        status: error.status || null,
        ...aiCandidateLogFields(candidate),
      });
      set({
        generationCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        generationStatus: 'error',
        uiError: error.message || 'Failed to apply generation candidate',
      });
      throw error;
    }
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

  completeImport: async ({
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
    const projectId = get().currentProjectId;
    console.info('[musicStore] Composition import completed', {
      schemaVersion: nextComposition.schema_version,
      trackCount: nextComposition.tracks?.length || 0,
      eventCount,
      barCount: nextComposition.bar_count || 0,
      importStatus: importReport?.status || null,
      importIssueCount: importReport?.issues?.length || 0,
      projectId,
      durable: Boolean(projectId),
      ...featureSummary,
    });
    console.debug('[musicStore] Import state install starting', {
      compositionRevision: revision.slice(0, 48),
      musicXmlLength: musicxml?.length || 0,
      hasNotationReport: Boolean(notationReport),
    });

    cancelAnalysisLifecycle();
    const prev = get();
    const localInstallPatch = {
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
      aiEditCandidate: null,
      aiEditAuditionActive: false,
      aiEditCompareResult: null,
      aiEditRequestCapture: null,
      generationCandidate: null,
      generationAuditionActive: false,
      generationCompareResult: null,
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
  ...initialAdaptiveScoreState,
      ...clearedAudioTranscriptionState(),
      ...clearedAudioRecoveryState(get),
    };

    // No open project: local-only replace (no durable history claim).
    if (!projectId) {
      set(localInstallPatch);
      markProjectDirty(set, get);
      return true;
    }

    if (
      !prev.activeBranchId
      || prev.workingVersion == null
      || !prev.currentRevisionId
      || !prev.workingFingerprint
    ) {
      console.error('[musicStore] Open-project import missing CAS fields');
      set({
        importStatus: 'error',
        importError: 'Missing branch history fields for durable import; reload the project',
      });
      return false;
    }

    try {
      const durable = await commitRevisionRequest(projectId, {
        branch_id: prev.activeBranchId,
        expected_active_branch_id: prev.activeBranchId,
        expected_working_version: prev.workingVersion,
        expected_head_revision_id: prev.currentRevisionId,
        expected_source_fingerprint: prev.workingFingerprint,
        composition: nextComposition,
        operation_type: 'import',
        checkpoint_dirty_draft: true,
      });
      if (get().currentProjectId !== projectId) {
        console.warn('[musicStore] Import durable result ignored after project switch');
        return false;
      }
      console.info('[musicStore] Durable import revision committed', {
        projectId,
        revisionCreated: Boolean(durable?.revision_created),
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: true,
        markSaved: true,
        action: 'import',
      });
      set({
        musicXml: musicxml || '',
        importStatus: 'success',
        importError: '',
        importReport: importReport || null,
        notationReport: notationReport || null,
        generationMeta: null,
        generationCandidate: null,
        generationAuditionActive: false,
        generationCompareResult: null,
        aiEditStartBar: null,
        aiEditEndBar: null,
        aiEditTrackMode: 'current',
        aiEditTrackIds: null,
        aiEditInstruction: '',
        aiEditStatus: 'idle',
        aiEditError: '',
        aiEditWarnings: [],
        aiEditCandidate: null,
        aiEditAuditionActive: false,
        aiEditCompareResult: null,
        aiEditRequestCapture: null,
        ...clearedVersionHistoryState(),
        ...clearedAudioTranscriptionState(),
        ...clearedAudioRecoveryState(get),
      });
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        console.warn('[musicStore] Durable import conflict', { projectId });
        set({
          importStatus: 'error',
          importError: 'Revision conflict during import; reload or retry',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        return false;
      }
      console.error('[musicStore] Durable import failed', {
        projectId,
        message: error.message,
      });
      set({
        importStatus: 'error',
        importError: error.message || 'Import commit failed',
      });
      return false;
    }
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

  setComposerProfileSelection: ({ profileId = null, strength = 'off' } = {}) => {
    const nextStrength = ['off', 'light', 'normal', 'strong'].includes(strength)
      ? strength
      : 'off';
    console.debug('[musicStore] Composer profile selection', {
      profileId: profileId || null,
      strength: nextStrength,
    });
    set({
      composerProfileId: profileId || null,
      composerProfileStrength: nextStrength,
    });
  },

  setComposerProfileList: (items) => {
    const list = Array.isArray(items) ? items : [];
    console.debug('[musicStore] Composer profile list cached', { count: list.length });
    set({ composerProfileList: list });
  },

  setComposerProfilePreview: (preview) => {
    set({ composerProfilePreview: preview || null });
  },

  setMusicalReferenceFeatureMask: ({ enabled = false, dimensions = null } = {}) => {
    const state = get();
    const current = state.musicalReference;
    if (!current) {
      embeddingLogger.debug('Reference feature mask ignored; no musical reference');
      return;
    }
    const next = normalizeMusicalReferenceSession({
      ...current,
      referenceFeatureMaskEnabled: Boolean(enabled),
      dimensions: enabled ? dimensions : null,
    });
    embeddingLogger.debug('Musical reference feature mask updated', {
      enabled: Boolean(enabled),
      dimensionCount: Array.isArray(next?.dimensions) ? next.dimensions.length : 0,
    });
    set({ musicalReference: next });
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

  setCritiqueStratumFilter: (filter) => {
    set({ critiqueStratumFilter: filter || 'all' });
  },

  resetCritique: () => {
    set({
      critiqueResult: null,
      critiqueStatus: 'idle',
      critiqueError: '',
    });
  },

  requestCritique: async ({
    force = false,
    includeModelCritique = false,
    reason = 'request',
  } = {}) => {
    const state = get();
    const composition = state.editedMusicJson || state.generatedMusicJson;
    if (!composition || !isCanonicalComposition(composition)) {
      set({
        critiqueStatus: 'error',
        critiqueError: 'Critique requires a valid composition.v2 document',
      });
      return null;
    }
    if (!force && state.critiqueStatus === 'loading') {
      return null;
    }
    console.debug('[musicStore] Critique evaluate started', { reason, includeModelCritique });
    set({ critiqueStatus: 'loading', critiqueError: '' });
    try {
      let scope = { kind: 'composition' };
      try {
        scope = buildAnalysisRequestScope({
          analysisScope: state.analysisScope,
          composition,
          sectionKey: state.analysisSelectedSectionKey,
          trackId: state.pianoRollTrackId,
        });
      } catch {
        scope = { kind: 'composition' };
      }
      const response = await evaluateCritique(composition, {
        scope,
        includeModelCritique,
      });
      const normalized = normalizeCritiqueResult(response);
      set({
        critiqueResult: normalized,
        critiqueStatus: 'success',
        critiqueError: '',
      });
      return normalized;
    } catch (error) {
      const message = error?.message || 'Critique evaluate failed';
      console.warn('[musicStore] Critique evaluate failed', {
        code: error?.code,
        message,
      });
      set({
        critiqueStatus: 'error',
        critiqueError: message,
      });
      return null;
    }
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
  ...initialAdaptiveScoreState,
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
  ...initialAdaptiveScoreState,
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
  ...initialAdaptiveScoreState,
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
    // Source audition seek when alignment is present (not a playbackSource).
    if (state.alignmentDocument && state.recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND) {
      get().seekSourceAudioToTick(next, { reason: 'edit-cursor' });
    }
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
    if (state.alignmentDocument && state.recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND) {
      get().seekSourceAudioToBar(Number(bar), { reason: 'gotoBar' });
    }
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
    const overlayRestore = Object.prototype.hasOwnProperty.call(previous, 'recoveryOverlay')
      ? {
        recoveryOverlay: Array.isArray(previous.recoveryOverlay)
          ? previous.recoveryOverlay.map((entry) => ({ ...entry }))
          : null,
      }
      : {};
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
      ...overlayRestore,
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
    const overlayRestore = Object.prototype.hasOwnProperty.call(next, 'recoveryOverlay')
      ? {
        recoveryOverlay: Array.isArray(next.recoveryOverlay)
          ? next.recoveryOverlay.map((entry) => ({ ...entry }))
          : null,
      }
      : {};
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
      ...overlayRestore,
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
    if (get().alignmentDocument) {
      get().setAudioWindowHighlightFromBars(normalized.startBar, normalized.endBar);
      get().seekSourceAudioToBar(normalized.startBar, { reason: 'ai-bar-selection' });
    }
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
    get().clearAudioWindowHighlight();
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

  startAiEdit: async () => {
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
    const capture = captureAiRequestContext(state);
    const sourceFingerprint = await fingerprintCompositionOrNull(state.editedMusicJson);
    console.info('[musicStore] AI edit started', {
      startBar: state.aiEditStartBar,
      endBar: state.aiEditEndBar,
      trackMode: state.aiEditTrackMode,
      trackScopeCount: (state.aiEditTrackIds || []).length,
      provider: state.selectedProvider,
      model: state.selectedModel,
      sourcePrefix: editFingerprintLogPrefix(sourceFingerprint),
    });
    set({
      aiEditStatus: 'loading',
      aiEditError: '',
      aiEditWarnings: [],
      aiEditCandidate: null,
      aiEditAuditionActive: false,
      aiEditCompareResult: null,
      aiEditRequestCapture: {
        ...capture,
        sourceFingerprint,
        instruction,
        startBar: state.aiEditStartBar,
        endBar: state.aiEditEndBar,
        trackIds: Array.isArray(state.aiEditTrackIds) ? [...state.aiEditTrackIds] : [],
        trackMode: state.aiEditTrackMode,
      },
    });
    return true;
  },

  failAiEdit: (message) => {
    const safeMessage = message || 'AI region edit failed';
    console.error('[musicStore] AI edit failed', { message: safeMessage });
    set({
      aiEditStatus: 'error',
      aiEditError: safeMessage,
      aiEditAuditionActive: false,
    });
  },

  completeAiEdit: async ({
    composition,
    musicxml = '',
    warnings = [],
    provider = null,
    model = null,
    generation_parameters = null,
  } = {}) => {
    const capture = get().aiEditRequestCapture;
    if (!capture || get().aiEditStatus !== 'loading') {
      console.warn('[musicStore] Ignoring AI edit response without active request');
      return false;
    }
    const stale = detectAiRequestStale(capture, get());
    if (stale.stale) {
      console.warn('[musicStore] AI edit response rejected as stale', { reason: stale.reason });
      set({
        aiEditStatus: 'error',
        aiEditError: 'Composition or project changed during edit; request a new preview',
        aiEditCandidate: null,
      });
      return false;
    }

    let prepared;
    try {
      prepared = prepareCompositionForStore(ensureCompositionNoteIds(composition).composition);
    } catch (error) {
      set({
        aiEditStatus: 'error',
        aiEditError: error.message || 'Edited composition invalid',
      });
      return false;
    }
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

    const candidateFingerprint = await compositionEditFingerprint(prepared);
    if (get().aiEditRequestCapture !== capture) {
      return false;
    }
    const candidate = buildAiCandidateEnvelope({
      candidateId: makeAiCandidateId('edit'),
      operationType: 'ai-region-edit-apply',
      composition: prepared,
      sourceFingerprint: capture.sourceFingerprint,
      candidateFingerprint,
      provider: provider || get().selectedProvider || null,
      model: model || get().selectedModel || null,
      instruction: capture.instruction,
      warnings,
      declaredRanges: [{ start_bar: capture.startBar, end_bar: capture.endBar }],
      declaredTrackIds: capture.trackIds || [],
      musicXml: musicxml || '',
      generationParameters:
        generation_parameters && typeof generation_parameters === 'object'
          ? generation_parameters
          : null,
    });
    console.info('[musicStore] AI edit candidate staged', {
      ...aiCandidateLogFields(candidate),
      startBar: capture.startBar,
      endBar: capture.endBar,
      hasGenerationParameters: Boolean(candidate.generation_parameters),
    });
    set({
      aiEditCandidate: candidate,
      aiEditAuditionActive: false,
      aiEditCompareResult: null,
      aiEditStatus: 'success',
      aiEditError: '',
      aiEditWarnings: Array.isArray(warnings) ? warnings : [],
    });
    return true;
  },

  rejectAiEditCandidate: () => {
    console.info('[musicStore] AI edit candidate rejected', {
      ...aiCandidateLogFields(get().aiEditCandidate),
    });
    set({
      aiEditCandidate: null,
      aiEditAuditionActive: false,
      aiEditCompareResult: null,
      aiEditStatus: 'idle',
      aiEditError: '',
      aiEditWarnings: [],
    });
    return true;
  },

  setAiEditAuditionActive: (active) => {
    const enabled = Boolean(active);
    const candidate = get().aiEditCandidate;
    if (enabled && (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY)) {
      return false;
    }
    set({
      aiEditAuditionActive: enabled,
      ...(enabled
        ? {
          ...exclusiveAuditionPatch(PLAYBACK_SOURCE_GENERATION, ARRANGEMENT_AUDITION_SOURCE),
          generationAuditionActive: false,
        }
        : {}),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshAiEditComparison: () => {
    const candidate = get().aiEditCandidate;
    if (!candidate) {
      set({ aiEditCompareResult: null });
      return null;
    }
    const result = compareCompositions(get().editedMusicJson, candidate.composition, {
      leftLabel: 'working',
      rightLabel: 'ai-edit-candidate',
    });
    set({ aiEditCompareResult: result });
    return result;
  },

  applyAiEditCandidate: async ({ asNewBranch = false, branchName = null } = {}) => {
    const state = get();
    const candidate = state.aiEditCandidate;
    if (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY) {
      return false;
    }
    const stale = detectAiRequestStale(state.aiEditRequestCapture, state);
    if (stale.stale) {
      set({
        aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.STALE },
        aiEditStatus: 'error',
        aiEditError: 'Source changed; request a new edit preview',
      });
      return false;
    }
    const liveSourceFp = await fingerprintCompositionOrNull(state.editedMusicJson);
    const liveCandidateFp = await compositionEditFingerprint(candidate.composition);
    if (
      liveSourceFp !== candidate.source_fingerprint
      || liveCandidateFp !== candidate.candidate_fingerprint
    ) {
      set({
        aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.STALE },
        aiEditStatus: 'error',
        aiEditError: 'Candidate fingerprints no longer match; request a new preview',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(
      ensureCompositionNoteIds(candidate.composition).composition,
    );
    const validation = validateMusicJson(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared)) {
      set({
        aiEditStatus: 'error',
        aiEditError: validation.message || 'Candidate composition invalid',
      });
      return false;
    }

    set({ aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.APPLYING } });

    const aiPayload = buildHistoryAiProvenance(candidate, {
      provider: normalizeAiProvider(candidate.provider),
      model: candidate.model,
      user_instruction: candidate.instruction || undefined,
    });

    if (!state.currentProjectId) {
      cancelAnalysisLifecycle();
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
          musicXml: candidate.music_xml || state.musicXml || '',
          aiEditStatus: 'idle',
          aiEditError: '',
          aiEditWarnings: candidate.warnings || [],
          aiEditCandidate: null,
          aiEditAuditionActive: false,
          aiEditCompareResult: null,
          ...editorPrefsForCompositionReplace(state, prepared),
          ...clearedMotifUiState(),
          ...clearedReharmonizePreviewState(),
          ...clearedDevelopmentPreviewState(),
          ...clearedArrangementPreviewState(),
        },
      });
      return ok;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        aiEditStatus: 'error',
        aiEditError: 'Missing branch CAS fields for durable AI edit apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
            aiEditStatus: 'error',
            aiEditError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'ai-region-edit-apply',
          declared_scope: {
            ranges: candidate.declared_ranges || [],
            track_ids: candidate.declared_track_ids || [],
          },
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'ai-region-edit-apply',
          checkpoint_dirty_draft: true,
          declared_scope: {
            ranges: candidate.declared_ranges || [],
            track_ids: candidate.declared_track_ids || [],
          },
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      console.info('[musicStore] AI edit candidate applied', {
        ...aiCandidateLogFields(candidate),
        asNewBranch: Boolean(asNewBranch),
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'ai-edit',
      });
      set({
        musicXml: candidate.music_xml || get().musicXml || '',
        aiEditStatus: 'idle',
        aiEditError: '',
        aiEditWarnings: candidate.warnings || [],
        aiEditCandidate: null,
        aiEditAuditionActive: false,
        aiEditCompareResult: null,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        set({
          aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
          aiEditStatus: 'error',
          aiEditError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      set({
        aiEditCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        aiEditStatus: 'error',
        aiEditError: error.message || 'Failed to apply AI edit candidate',
      });
      throw error;
    }
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

  syncMixerControlsForScope: (mixerScope, musicJson) => {
    const stateKey = mixerControlsStateKey(mixerScope);
    set((state) => ({
      [stateKey]: mergeTrackControls(state[stateKey] || {}, musicJson),
    }));
  },

  /**
   * Scope-aware mixer patch. Does not touch composition JSON / undo / persistence.
   */
  updateMixerTrackControl: (mixerScope, trackId, patch = {}) => {
    const id = String(trackId || '');
    if (!id) {
      logger.warn('Rejected mixer control update; missing trackId', sanitizeMixerLogMeta({ mixerScope }));
      return null;
    }
    const stateKey = mixerControlsStateKey(mixerScope);
    let nextControl = null;
    set((state) => {
      const bucket = state[stateKey] || {};
      const current = bucket[id] || defaultControl();
      nextControl = normalizeTrackControlPatch(patch, current, current.volumeMidi);
      logger.debug('Mixer control updated', sanitizeMixerLogMeta({
        mixerScope,
        trackId: id,
        muted: nextControl.muted,
        solo: nextControl.solo,
        trimDb: nextControl.trimDb,
        panOffset: nextControl.panOffset,
        reverbSend: nextControl.reverbSend,
        volumeMidi: nextControl.volumeMidi,
        presetId: nextControl.presetId,
      }));
      return {
        [stateKey]: {
          ...bucket,
          [id]: nextControl,
        },
      };
    });
    return nextControl;
  },

  toggleTrackMute: (trackId) => {
    const state = get();
    const current = state.trackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, {
      muted: !current.muted,
    });
  },

  toggleTrackSolo: (trackId) => {
    const state = get();
    const current = state.trackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, {
      solo: !current.solo,
    });
  },

  setTrackVolume: (trackId, volumeMidi) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, {
      volumeMidi,
    });
  },

  setTrackTrimDb: (trackId, trimDb) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, { trimDb });
  },

  setTrackPanOffset: (trackId, panOffset) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, { panOffset });
  },

  setTrackReverbSend: (trackId, reverbSend) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, { reverbSend });
  },

  syncDevelopmentCandidateTrackControls: (musicJson) => {
    get().syncMixerControlsForScope(PLAYBACK_MIXER_SCOPE_DEVELOPMENT, musicJson);
  },

  toggleDevelopmentCandidateMute: (trackId) => {
    const current = get().developmentCandidateTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_DEVELOPMENT, trackId, {
      muted: !current.muted,
    });
  },

  toggleDevelopmentCandidateSolo: (trackId) => {
    const current = get().developmentCandidateTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_DEVELOPMENT, trackId, {
      solo: !current.solo,
    });
  },

  setDevelopmentCandidateVolume: (trackId, volumeMidi) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_DEVELOPMENT, trackId, { volumeMidi });
  },

  syncPreviewTrackControls: (musicJson) => {
    get().syncMixerControlsForScope(PLAYBACK_MIXER_SCOPE_PREVIEW, musicJson);
  },

  togglePreviewMute: (trackId) => {
    const current = get().previewTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_PREVIEW, trackId, {
      muted: !current.muted,
    });
  },

  togglePreviewSolo: (trackId) => {
    const current = get().previewTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_PREVIEW, trackId, {
      solo: !current.solo,
    });
  },

  setPreviewVolume: (trackId, volumeMidi) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_PREVIEW, trackId, { volumeMidi });
  },

  setPlaybackSourceKey: (playbackSourceKey) => {
    set({ playbackSourceKey: playbackSourceKey == null ? null : String(playbackSourceKey) });
  },

  setPlaybackOperationEpoch: (playbackOperationEpoch) => {
    set({ playbackOperationEpoch: Number(playbackOperationEpoch) || 0 });
  },

  setPlaybackActivity: (snapshot) => {
    const next = {
      tracks: snapshot?.tracks && typeof snapshot.tracks === 'object' ? { ...snapshot.tracks } : {},
      clipped: Boolean(snapshot?.clipped),
    };
    const previous = get().playbackActivity;
    if (!activityLevelsMateriallyChanged(previous, next)) {
      return false;
    }
    set({ playbackActivity: next });
    return true;
  },

  resetPlaybackActivity: () => {
    set({ playbackActivity: { tracks: {}, clipped: false } });
  },

  setUiError: (uiError) => {
    if (uiError) {
      console.error('[musicStore] UI error set', { uiError });
    }
    set({ uiError });
  },

  goHome: async () => {
    const projectId = get().currentProjectId;
    console.info('[musicStore] View transition', { activeView: 'home', projectId });
    try {
      await flushProjectDraft(get, { reason: 'go-home-flush' });
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        console.warn('[musicStore] Home navigation blocked by save conflict', {
          projectId,
          code: error.code,
        });
        return;
      }
      console.warn('[musicStore] Home flush failed; continuing to home', {
        projectId,
        status: error.status || null,
      });
    }
    cancelAutosaveTimer();
    set({ activeView: 'home' });
    await get().loadProjectList();
  },

  reloadCurrentProject: async () => {
    const projectId = get().currentProjectId;
    if (!projectId) {
      return null;
    }
    console.info('[musicStore] Reloading current project after conflict', { projectId });
    cancelAutosaveTimer();
    const project = await getProjectRequest(projectId);
    hydrateProject(set, get, project, { openComposer: true, markSaved: true });
    return project;
  },

  /**
   * Conflict recovery: preserve the local draft by forking it onto a new branch
   * using fresh server CAS tokens (without discarding local composition).
   */
  saveConflictAsNewBranch: async (branchName) => {
    const state = get();
    const projectId = state.currentProjectId;
    const name = String(branchName || '').trim();
    const localComposition = state.editedMusicJson;
    if (!projectId || !name) {
      console.warn('[FIX:conflict-branch] saveConflictAsNewBranch ignored', {
        hasProject: Boolean(projectId),
        nameLength: name.length,
      });
      return null;
    }
    console.info('[FIX:conflict-branch] Saving conflict draft as new branch', {
      projectId,
      nameLength: name.length,
      saveStatus: state.saveStatus,
    });
    cancelAutosaveTimer();
    try {
      const project = await getProjectRequest(projectId);
      if (get().currentProjectId !== projectId) {
        return null;
      }
      const historyFields = historyStateFromProject(project);
      set({
        ...historyFields,
        saveConflict: null,
      });
      if (
        !historyFields.activeBranchId
        || historyFields.workingVersion == null
        || !historyFields.currentRevisionId
        || !historyFields.workingFingerprint
      ) {
        set({
          saveStatus: 'error',
          saveError: 'Missing branch CAS fields after conflict refresh',
        });
        return null;
      }
      const durable = await applyAsBranchRequest(projectId, {
        name,
        source_branch_id: historyFields.activeBranchId,
        expected_active_branch_id: historyFields.activeBranchId,
        expected_working_version: historyFields.workingVersion,
        expected_head_revision_id: historyFields.currentRevisionId,
        expected_source_fingerprint: historyFields.workingFingerprint,
        composition: localComposition,
        operation_type: 'manual-checkpoint',
      });
      if (get().currentProjectId !== projectId) {
        return null;
      }
      installDurableHistoryResult(set, get, durable, {
        clearUndo: true,
        markSaved: true,
        action: 'conflict-save-as-branch',
      });
      set({
        saveStatus: 'saved',
        saveError: '',
        saveConflict: null,
        ...clearedVersionHistoryState(),
      });
      console.info('[FIX:conflict-branch] Conflict draft saved as new branch', {
        projectId,
        branchId: durable?.active_branch_id,
      });
      await get().loadVersionBranches();
      await get().loadVersionRevisions({ reset: true });
      return durable;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        console.warn('[FIX:conflict-branch] Save-as-branch still conflicted', {
          projectId,
          code: error.code,
        });
        set({
          saveStatus: 'conflict',
          saveError: 'Project changed elsewhere. Reload or save as a new branch.',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.error('[FIX:conflict-branch] Save-as-branch failed', {
        projectId,
        status: error.status || null,
      });
      set({
        saveStatus: 'error',
        saveError: error.message || 'Failed to save as new branch',
      });
      throw error;
    }
  },

  loadProjectList: async () => {
    console.debug('[musicStore] Loading project list');
    set({ projectListStatus: 'loading' });
    try {
      const response = await listProjectsRequest();
      const projects = response?.projects || [];
      console.info('[musicStore] Project list loaded', { count: projects.length });
      set({ projectList: projects, projectListStatus: 'success', uiError: '' });
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
    const currentId = get().currentProjectId;
    if (currentId && currentId !== projectId) {
      try {
        await flushProjectDraft(get, { reason: 'project-switch-flush' });
      } catch (error) {
        if (error instanceof ProjectRevisionConflictError) {
          console.warn('[musicStore] Project switch blocked by save conflict', {
            fromProjectId: currentId,
            toProjectId: projectId,
            code: error.code,
          });
          throw error;
        }
        console.warn('[musicStore] Project switch flush failed; continuing open', {
          fromProjectId: currentId,
          toProjectId: projectId,
          status: error.status || null,
        });
      }
    }
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
          activeBranchId: null,
          activeBranchName: null,
          currentRevisionId: null,
          currentRevisionSequence: null,
          workingVersion: null,
          workingFingerprint: null,
          projectCollaboration: null,
          saveConflict: null,
          ...clearedVersionHistoryState(),
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
          generationCandidate: null,
          generationAuditionActive: false,
          generationCompareResult: null,
          generationRequestCapture: null,
          generationStatus: 'idle',
          ...clearedAnalysisState(),
          ...clearedMotifUiState(),
          ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
          ...clearedArrangementPreviewState(),
          ...initialHarmonyUiState,
  ...initialAdaptiveScoreState,
          ...clearedAudioTranscriptionState(),
          ...clearedAudioRecoveryState(get),
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
    const branchId = state.activeBranchId;
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
    const manual = isManualSaveReason(reason);

    if (!projectId) {
      console.debug('[FIX] Save skipped; no open project', {
        reason,
        fingerprintDirty,
        eventCount,
      });
      if (manual) {
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

    // Autosave stays gated on persist fingerprint; manual Save always commits/PATCHes.
    if (!fingerprintDirty && !manual) {
      console.debug('[FIX] Save skipped; persist fingerprint clean', {
        reason,
        projectId,
        fingerprintDirty,
        eventCount,
        persistRevision: persistRevisionAtStart.slice(0, 48),
      });
      set({ saveStatus: 'saved', saveError: '', saveConflict: null });
      return null;
    }

    console.debug('[FIX] Save starting', {
      reason,
      projectId,
      branchId,
      fingerprintDirty,
      eventCount,
      persistRevision: persistRevisionAtStart.slice(0, 48),
      workingVersion: state.workingVersion,
    });

    const requestId = ++autosaveRequestSeq;
    const composition = state.editedMusicJson;
    const captureBranchId = branchId;
    const captureWorkingVersion = state.workingVersion;
    const captureFingerprint = state.workingFingerprint;

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
      mode: manual ? 'draft-then-checkpoint' : 'draft-autosave',
      workingVersion: captureWorkingVersion,
    });
    console.debug('[musicStore] Save status transition', { from: state.saveStatus, to: 'saving', reason });
    set({ saveStatus: 'saving', saveError: '', saveConflict: null });

    try {
      let projectOrDurable;
      const hasCas = Boolean(
        captureBranchId
        && captureWorkingVersion != null
        && captureFingerprint,
      );
      const draftPayload = {
        composition: composition || undefined,
        clear_composition: !composition,
        ...(hasGenerationMeta ? { generation: generationForSave } : {}),
      };
      if (hasCas) {
        draftPayload.branch_id = captureBranchId;
        draftPayload.expected_active_branch_id = captureBranchId;
        draftPayload.expected_working_version = captureWorkingVersion;
        draftPayload.expected_source_fingerprint = captureFingerprint;
      }

      // Draft autosave (and the draft half of explicit Save) always PATCHes.
      const draftResult = await patchProjectRequest(projectId, draftPayload);
      let historyFields = historyStateFromProject(draftResult);
      projectOrDurable = draftResult;

      if (manual && historyFields.activeBranchId && historyFields.currentRevisionId != null
        && historyFields.workingVersion != null && historyFields.workingFingerprint) {
        projectOrDurable = await commitRevisionRequest(projectId, {
          branch_id: historyFields.activeBranchId,
          expected_active_branch_id: historyFields.activeBranchId,
          expected_working_version: historyFields.workingVersion,
          expected_head_revision_id: historyFields.currentRevisionId,
          expected_source_fingerprint: historyFields.workingFingerprint,
          composition: composition || undefined,
          clear_composition: !composition,
          operation_type: 'manual-checkpoint',
        });
        historyFields = historyStateFromDurable(projectOrDurable);
      }

      if (requestId !== autosaveRequestSeq) {
        console.warn('[musicStore] Ignoring stale save response', {
          requestId,
          latest: autosaveRequestSeq,
          projectId,
          branchId: captureBranchId,
        });
        return projectOrDurable;
      }
      const stillCurrent = get().currentProjectId === projectId
        && (!captureBranchId || get().activeBranchId === captureBranchId);
      if (!stillCurrent) {
        console.warn('[musicStore] Save completed after project/branch closed', {
          projectId,
          requestId,
          branchId: captureBranchId,
        });
        return projectOrDurable;
      }

      set({ generationMeta: generationForSave });
      const currentPersistRevision = projectPersistRevisionKey(get().editedMusicJson, generationForSave);
      if (currentPersistRevision !== persistRevisionAtStart) {
        console.debug('[FIX] Save completed but newer persist fingerprint exists', {
          projectId,
          savedPersistRevision: persistRevisionAtStart.slice(0, 48),
          currentPersistRevision: currentPersistRevision.slice(0, 48),
          eventCount: countEvents(get().editedMusicJson),
        });
        set({
          ...historyFields,
          lastSavedPersistRevision: persistRevisionAtStart,
          saveStatus: 'unsaved',
          saveError: '',
          saveConflict: null,
          currentProjectName: draftResult.name || get().currentProjectName,
        });
        scheduleAutosave(set, get);
        return projectOrDurable;
      }
      console.info('[musicStore] Project saved', {
        reason,
        projectId,
        eventCount,
        revisionCreated: Boolean(projectOrDurable.revision_created),
      });
      console.debug('[musicStore] Save status transition', { from: 'saving', to: 'saved', reason });
      set({
        ...historyFields,
        lastSavedPersistRevision: persistRevisionAtStart,
        saveStatus: 'saved',
        saveError: '',
        saveConflict: null,
        currentProjectName: draftResult.name || projectOrDurable.name || get().currentProjectName,
        versionRestoreBlockReason: computeVersionRestoreBlockReason({
          ...get(),
          ...historyFields,
          lastSavedPersistRevision: persistRevisionAtStart,
          saveStatus: 'saved',
        }),
      });
      return projectOrDurable;
    } catch (error) {
      if (requestId !== autosaveRequestSeq) {
        return null;
      }
      if (error instanceof ProjectRevisionConflictError) {
        console.warn('[musicStore] Project save conflict; stopping autosave retries', {
          reason,
          projectId,
          code: error.code,
          expectedWorkingVersion: error.conflict?.expected_working_version ?? null,
          currentWorkingVersion: error.conflict?.current_working_version ?? null,
        });
        cancelAutosaveTimer();
        set({
          saveStatus: 'conflict',
          saveError: 'Project changed elsewhere. Reload or save as a new branch.',
          saveConflict: error.conflict,
        });
        throw error;
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
      set({ saveStatus: 'error', saveError: error.message, saveConflict: null });
      throw error;
    }
  },

  loadVersionBranches: async () => {
    const state = get();
    const projectId = state.currentProjectId;
    if (!projectId) {
      return null;
    }
    versionBranchesRequestSeq += 1;
    const requestId = versionBranchesRequestSeq;
    console.debug('[musicStore] Version branches load started', { projectId, requestId });
    set({ versionBranchesStatus: 'loading', versionBranchesError: '' });
    try {
      const response = await listBranchesRequest(projectId);
      if (requestId !== versionBranchesRequestSeq || get().currentProjectId !== projectId) {
        console.warn('[musicStore] Ignoring stale version branches response', { requestId, projectId });
        return null;
      }
      const branches = Array.isArray(response?.branches) ? response.branches : [];
      console.info('[musicStore] Version branches loaded', {
        projectId,
        requestId,
        count: branches.length,
      });
      set({
        versionBranches: branches,
        versionBranchesStatus: 'ready',
        versionBranchesError: '',
        versionRestoreBlockReason: computeVersionRestoreBlockReason(get()),
      });
      return branches;
    } catch (error) {
      if (requestId !== versionBranchesRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      console.warn('[musicStore] Version branches load failed', {
        projectId,
        requestId,
        status: error.status || null,
      });
      set({
        versionBranchesStatus: 'error',
        versionBranchesError: error.message || 'Failed to load branches',
      });
      throw error;
    }
  },

  loadVersionRevisions: async ({ reset = true } = {}) => {
    const state = get();
    const projectId = state.currentProjectId;
    const branchId = state.activeBranchId;
    if (!projectId || !branchId) {
      return null;
    }
    const beforeSequence = reset ? null : state.versionRevisionsNextBefore;
    if (!reset && beforeSequence == null) {
      return state.versionRevisions;
    }
    versionRevisionsRequestSeq += 1;
    const requestId = versionRevisionsRequestSeq;
    console.debug('[musicStore] Version revisions load started', {
      projectId,
      branchId,
      requestId,
      reset: Boolean(reset),
      beforeSequence,
    });
    set({
      versionRevisionsStatus: 'loading',
      versionRevisionsError: '',
      ...(reset
        ? {
          versionRevisions: [],
          versionRevisionsNextBefore: null,
          versionSelectedRevisionId: null,
          versionCompareRevisionId: null,
          versionRevisionDetails: {},
          versionCompareResult: null,
          versionAuditionActive: false,
          versionAuditionTrackControls: {},
        }
        : {}),
    });
    try {
      const response = await listRevisionsRequest(projectId, {
        branchId,
        limit: VERSION_HISTORY_PAGE_SIZE,
        beforeSequence,
      });
      if (
        requestId !== versionRevisionsRequestSeq
        || get().currentProjectId !== projectId
        || get().activeBranchId !== branchId
      ) {
        console.warn('[musicStore] Ignoring stale version revisions response', {
          requestId,
          projectId,
          branchId,
        });
        return null;
      }
      const page = Array.isArray(response?.revisions) ? response.revisions : [];
      const nextBefore = response?.next_before_sequence ?? null;
      const merged = reset ? page : [...(get().versionRevisions || []), ...page];
      console.info('[musicStore] Version revisions loaded', {
        projectId,
        branchId,
        requestId,
        pageCount: page.length,
        totalCount: merged.length,
        hasMore: nextBefore != null,
      });
      set({
        versionRevisions: merged,
        versionRevisionsNextBefore: nextBefore,
        versionRevisionsStatus: 'ready',
        versionRevisionsError: '',
        versionRestoreBlockReason: computeVersionRestoreBlockReason({
          ...get(),
          versionRevisions: merged,
        }),
      });
      return merged;
    } catch (error) {
      if (
        requestId !== versionRevisionsRequestSeq
        || get().currentProjectId !== projectId
      ) {
        return null;
      }
      console.warn('[musicStore] Version revisions load failed', {
        projectId,
        requestId,
        status: error.status || null,
      });
      set({
        versionRevisionsStatus: 'error',
        versionRevisionsError: error.message || 'Failed to load revisions',
      });
      throw error;
    }
  },

  ensureVersionRevisionDetail: async (revisionId) => {
    const normalizedId = typeof revisionId === 'string' && revisionId.trim()
      ? revisionId.trim()
      : null;
    const state = get();
    const projectId = state.currentProjectId;
    if (!projectId || !normalizedId) {
      return null;
    }
    const cached = state.versionRevisionDetails?.[normalizedId];
    if (cached && Object.prototype.hasOwnProperty.call(cached, 'composition')) {
      return cached;
    }
    versionDetailRequestSeq += 1;
    const requestId = versionDetailRequestSeq;
    console.debug('[musicStore] Version revision detail load started', {
      projectId,
      revisionId: normalizedId,
      requestId,
    });
    try {
      const detail = await getRevisionRequest(projectId, normalizedId);
      if (
        requestId !== versionDetailRequestSeq
        || get().currentProjectId !== projectId
      ) {
        console.warn('[musicStore] Ignoring stale version revision detail', {
          requestId,
          projectId,
        });
        return null;
      }
      const entry = {
        revision: detail?.revision || null,
        composition: detail?.composition ?? null,
      };
      const keepIds = new Set([
        get().versionSelectedRevisionId,
        get().versionCompareRevisionId,
        normalizedId,
      ].filter(Boolean));
      const nextDetails = pruneVersionRevisionDetails(
        {
          ...(get().versionRevisionDetails || {}),
          [normalizedId]: entry,
        },
        keepIds,
      );
      set({ versionRevisionDetails: nextDetails });
      console.debug('[musicStore] Version revision detail ready', {
        projectId,
        revisionId: normalizedId,
        requestId,
        hasComposition: entry.composition != null,
      });
      return entry;
    } catch (error) {
      if (requestId !== versionDetailRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      console.warn('[musicStore] Version revision detail failed', {
        projectId,
        revisionId: normalizedId,
        status: error.status || null,
      });
      throw error;
    }
  },

  selectVersionRevision: async (revisionId) => {
    const normalizedId = typeof revisionId === 'string' && revisionId.trim()
      ? revisionId.trim()
      : null;
    const state = get();
    if (!normalizedId) {
      set({
        versionSelectedRevisionId: null,
        versionAuditionActive: false,
        versionAuditionTrackControls: {},
        versionCompareResult: null,
        versionRestoreBlockReason: computeVersionRestoreBlockReason(state),
      });
      return null;
    }
    console.debug('[musicStore] Version revision selected', { revisionId: normalizedId });
    set({
      versionSelectedRevisionId: normalizedId,
      versionAuditionActive: false,
      versionAuditionTrackControls: {},
      versionActionError: '',
    });
    const detail = await get().ensureVersionRevisionDetail(normalizedId);
    await get().refreshVersionComparison();
    set({
      versionRestoreBlockReason: computeVersionRestoreBlockReason(get()),
    });
    return detail;
  },

  selectVersionCompareRevision: async (revisionId) => {
    const normalizedId = typeof revisionId === 'string' && revisionId.trim()
      ? revisionId.trim()
      : null;
    console.debug('[musicStore] Version compare revision selected', {
      revisionId: normalizedId,
    });
    set({
      versionCompareRevisionId: normalizedId,
      versionCompareResult: null,
      versionCompareError: '',
    });
    if (normalizedId) {
      await get().ensureVersionRevisionDetail(normalizedId);
    }
    await get().refreshVersionComparison();
    return normalizedId;
  },

  refreshVersionComparison: async () => {
    const state = get();
    const leftId = state.versionSelectedRevisionId;
    const rightId = state.versionCompareRevisionId;
    if (!leftId && !rightId) {
      set({
        versionCompareResult: null,
        versionCompareStatus: 'idle',
        versionCompareError: '',
      });
      return null;
    }
    set({ versionCompareStatus: 'loading', versionCompareError: '' });
    try {
      let leftComposition = state.editedMusicJson;
      let rightComposition = state.editedMusicJson;
      let leftLabel = 'working';
      let rightLabel = 'working';

      if (leftId) {
        const detail = await get().ensureVersionRevisionDetail(leftId);
        leftComposition = detail?.composition ?? null;
        leftLabel = 'revision';
      }
      if (rightId) {
        const detail = await get().ensureVersionRevisionDetail(rightId);
        rightComposition = detail?.composition ?? null;
        rightLabel = 'revision';
      } else if (leftId) {
        // Selected vs working draft
        rightComposition = state.editedMusicJson;
        rightLabel = 'working';
      }

      const result = compareCompositions(leftComposition, rightComposition, {
        leftLabel,
        rightLabel,
      });
      if (
        get().versionSelectedRevisionId !== leftId
        || get().versionCompareRevisionId !== rightId
      ) {
        return null;
      }
      console.debug('[musicStore] Version comparison ready', {
        leftLabel,
        rightLabel,
        identical: Boolean(result?.identical),
        added: Number(result?.events?.added) || 0,
        removed: Number(result?.events?.removed) || 0,
        changed: Number(result?.events?.changed) || 0,
      });
      set({
        versionCompareResult: result,
        versionCompareStatus: 'ready',
        versionCompareError: '',
      });
      return result;
    } catch (error) {
      console.warn('[musicStore] Version comparison failed', {
        code: error?.code || 'compare_failed',
      });
      set({
        versionCompareResult: null,
        versionCompareStatus: 'error',
        versionCompareError: error.message || 'Comparison failed',
      });
      return null;
    }
  },

  setVersionAuditionActive: async (active) => {
    const enabled = Boolean(active);
    const state = get();
    if (!enabled) {
      console.info('[musicStore] Version audition toggled', { active: false });
      set({
        versionAuditionActive: false,
        playbackStatus: 'idle',
        playbackSeconds: 0,
        playbackBar: 1,
      });
      return true;
    }
    const revisionId = state.versionSelectedRevisionId;
    if (!revisionId) {
      return false;
    }
    const detail = await get().ensureVersionRevisionDetail(revisionId);
    if (!detail || get().versionSelectedRevisionId !== revisionId) {
      return false;
    }
    const composition = detail.composition;
    const nextLoop = reconcilePlaybackLoop(state.playbackLoop, composition);
    console.info('[musicStore] Version audition toggled', {
      active: true,
      revisionId,
      hasComposition: composition != null,
    });
    set({
      versionAuditionActive: true,
      versionAuditionTrackControls: buildDefaultTrackControls(composition),
      ...exclusiveAuditionPatch(PLAYBACK_SOURCE_VERSION, ARRANGEMENT_AUDITION_SOURCE),
      playbackLoop: nextLoop,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  syncVersionAuditionTrackControls: (musicJson) => {
    set((current) => ({
      versionAuditionTrackControls: mergeTrackControls(
        current.versionAuditionTrackControls,
        musicJson,
      ),
    }));
  },

  toggleVersionAuditionMute: (trackId) => {
    const existing = get().versionAuditionTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_VERSION, trackId, {
      muted: !existing.muted,
    });
  },

  toggleVersionAuditionSolo: (trackId) => {
    const existing = get().versionAuditionTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_VERSION, trackId, {
      solo: !existing.solo,
    });
  },

  setVersionAuditionVolume: (trackId, volumeMidi) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_VERSION, trackId, { volumeMidi });
  },

  nameVersionRevision: async (revisionId, name) => {
    const projectId = get().currentProjectId;
    const normalizedId = String(revisionId || '').trim();
    if (!projectId || !normalizedId) {
      return null;
    }
    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      const item = await nameRevisionRequest(projectId, normalizedId, {
        name: name == null ? null : String(name),
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      set((current) => ({
        versionRevisions: (current.versionRevisions || []).map((row) => (
          row.id === normalizedId ? { ...row, ...item } : row
        )),
        versionRevisionDetails: current.versionRevisionDetails?.[normalizedId]
          ? {
            ...current.versionRevisionDetails,
            [normalizedId]: {
              ...current.versionRevisionDetails[normalizedId],
              revision: {
                ...(current.versionRevisionDetails[normalizedId].revision || {}),
                ...item,
              },
            },
          }
          : current.versionRevisionDetails,
        versionActionStatus: 'idle',
        versionActionError: '',
      }));
      console.info('[musicStore] Version revision named', {
        projectId,
        revisionId: normalizedId,
        hasName: Boolean(item?.name),
      });
      return item;
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return null;
      }
      console.warn('[musicStore] Version revision name failed', {
        projectId,
        revisionId: normalizedId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to name revision',
      });
      throw error;
    }
  },

  createVersionBranch: async ({ name, fromRevisionId, checkout = false } = {}) => {
    const projectId = get().currentProjectId;
    if (!projectId) {
      return null;
    }
    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      const branch = await createBranchRequest(projectId, {
        name: String(name || '').trim(),
        from_revision_id: fromRevisionId,
        checkout: false,
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      console.info('[musicStore] Version branch created', {
        projectId,
        branchId: branch?.id,
        checkout: Boolean(checkout),
      });
      set({ versionActionStatus: 'idle', versionActionError: '' });
      await get().loadVersionBranches();
      if (checkout && branch?.id) {
        await get().checkoutVersionBranch(branch.id);
      }
      return branch;
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return null;
      }
      if (error instanceof ProjectRevisionConflictError) {
        set({
          versionActionStatus: 'error',
          versionActionError: 'Revision conflict',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.warn('[musicStore] Version branch create failed', {
        projectId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to create branch',
      });
      throw error;
    }
  },

  renameVersionBranch: async (branchId, name) => {
    const projectId = get().currentProjectId;
    const normalizedId = String(branchId || '').trim();
    if (!projectId || !normalizedId) {
      return null;
    }
    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      const branch = await renameBranchRequest(projectId, normalizedId, {
        name: String(name || '').trim(),
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      set((current) => ({
        versionBranches: (current.versionBranches || []).map((row) => (
          row.id === normalizedId ? { ...row, ...branch } : row
        )),
        activeBranchName: current.activeBranchId === normalizedId
          ? (branch?.name || current.activeBranchName)
          : current.activeBranchName,
        versionActionStatus: 'idle',
        versionActionError: '',
      }));
      console.info('[musicStore] Version branch renamed', {
        projectId,
        branchId: normalizedId,
      });
      return branch;
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return null;
      }
      console.warn('[musicStore] Version branch rename failed', {
        projectId,
        branchId: normalizedId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to rename branch',
      });
      throw error;
    }
  },

  checkoutVersionBranch: async (branchId) => {
    const state = get();
    const projectId = state.currentProjectId;
    const normalizedId = String(branchId || '').trim();
    if (!projectId || !normalizedId) {
      return null;
    }
    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
    ) {
      set({
        versionActionStatus: 'error',
        versionActionError: 'Missing branch CAS fields for checkout',
      });
      return null;
    }
    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      await flushProjectDraft(get, { reason: 'branch-checkout-flush' });
      const durable = await checkoutBranchRequest(projectId, normalizedId, {
        expected_active_branch_id: get().activeBranchId,
        expected_working_version: get().workingVersion,
        expected_head_revision_id: get().currentRevisionId,
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      console.info('[musicStore] Version branch checkout completed', {
        projectId,
        branchId: normalizedId,
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: true,
        markSaved: true,
        action: 'branch-checkout',
      });
      set({
        versionActionStatus: 'idle',
        versionActionError: '',
        ...clearedVersionHistoryState(),
      });
      await get().loadVersionBranches();
      await get().loadVersionRevisions({ reset: true });
      return durable;
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return null;
      }
      if (error instanceof ProjectRevisionConflictError) {
        set({
          versionActionStatus: 'error',
          versionActionError: 'Revision conflict',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.warn('[musicStore] Version branch checkout failed', {
        projectId,
        branchId: normalizedId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to checkout branch',
      });
      throw error;
    }
  },

  restoreVersionRevision: async (revisionId, { checkpointIfDirty = false } = {}) => {
    const state = get();
    const projectId = state.currentProjectId;
    const normalizedId = String(revisionId || '').trim();
    if (!projectId || !normalizedId) {
      return { ok: false, reason: 'missing-ids' };
    }
    const blockReason = computeVersionRestoreBlockReason(state);
    if (blockReason) {
      set({ versionRestoreBlockReason: blockReason });
      if (!checkpointIfDirty) {
        console.debug('[musicStore] Version restore blocked', {
          projectId,
          revisionId: normalizedId,
          reason: blockReason,
        });
        return { ok: false, reason: blockReason };
      }
      console.info('[musicStore] Version restore checkpointing dirty draft', {
        projectId,
        revisionId: normalizedId,
        reason: blockReason,
      });
      await get().saveCurrentProject({ reason: 'manual' });
      const afterSave = computeVersionRestoreBlockReason(get());
      if (afterSave) {
        set({
          versionRestoreBlockReason: afterSave,
          versionActionStatus: 'error',
          versionActionError: 'Draft still dirty after checkpoint',
        });
        return { ok: false, reason: afterSave };
      }
    }

    const cas = get();
    if (
      !cas.activeBranchId
      || cas.workingVersion == null
      || !cas.currentRevisionId
    ) {
      return { ok: false, reason: 'missing-cas' };
    }

    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      const durable = await restoreRevisionRequest(projectId, normalizedId, {
        branch_id: cas.activeBranchId,
        expected_active_branch_id: cas.activeBranchId,
        expected_working_version: cas.workingVersion,
        expected_head_revision_id: cas.currentRevisionId,
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return { ok: false, reason: 'stale' };
      }
      console.info('[musicStore] Version restore completed', {
        projectId,
        revisionId: normalizedId,
        createdRevisionId: durable?.created_revision_ids?.[0] || null,
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: false,
        markSaved: true,
        action: 'revision-restore',
      });
      set({
        versionActionStatus: 'idle',
        versionActionError: '',
        versionAuditionActive: false,
        versionRestoreBlockReason: null,
      });
      await get().loadVersionRevisions({ reset: true });
      return { ok: true, durable };
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return { ok: false, reason: 'stale' };
      }
      if (error instanceof ProjectRevisionConflictError) {
        set({
          versionActionStatus: 'error',
          versionActionError: 'Revision conflict',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.warn('[musicStore] Version restore failed', {
        projectId,
        revisionId: normalizedId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to restore revision',
      });
      throw error;
    }
  },

  applyCompositionAsBranch: async ({
    name,
    composition,
    clearComposition = false,
    operationType = 'generate-apply',
    declaredScope = null,
    ai = null,
  } = {}) => {
    const state = get();
    const projectId = state.currentProjectId;
    if (
      !projectId
      || !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      return null;
    }
    versionActionRequestSeq += 1;
    const requestId = versionActionRequestSeq;
    set({ versionActionStatus: 'loading', versionActionError: '' });
    try {
      const durable = await applyAsBranchRequest(projectId, {
        name: String(name || '').trim(),
        source_branch_id: state.activeBranchId,
        expected_active_branch_id: state.activeBranchId,
        expected_working_version: state.workingVersion,
        expected_head_revision_id: state.currentRevisionId,
        expected_source_fingerprint: state.workingFingerprint,
        composition: clearComposition ? undefined : composition,
        clear_composition: Boolean(clearComposition),
        operation_type: operationType,
        declared_scope: declaredScope || undefined,
        ai: ai || undefined,
      });
      if (requestId !== versionActionRequestSeq || get().currentProjectId !== projectId) {
        return null;
      }
      console.info('[musicStore] Apply-as-branch completed', {
        projectId,
        branchId: durable?.active_branch_id,
        operationType,
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: true,
        markSaved: true,
        action: 'apply-as-branch',
      });
      set({
        versionActionStatus: 'idle',
        versionActionError: '',
        ...clearedVersionHistoryState(),
      });
      await get().loadVersionBranches();
      await get().loadVersionRevisions({ reset: true });
      return durable;
    } catch (error) {
      if (requestId !== versionActionRequestSeq) {
        return null;
      }
      if (error instanceof ProjectRevisionConflictError) {
        set({
          versionActionStatus: 'error',
          versionActionError: 'Revision conflict',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      console.warn('[musicStore] Apply-as-branch failed', {
        projectId,
        status: error.status || null,
      });
      set({
        versionActionStatus: 'error',
        versionActionError: error.message || 'Failed to apply as branch',
      });
      throw error;
    }
  },

  refreshCollaboration: async () => {
    const projectId = get().currentProjectId;
    if (!projectId || !get().collaborationEnabled) {
      return;
    }
    const [members, comments, reviews, activity] = await Promise.all([
      listProjectMembers(projectId),
      listProjectComments(projectId),
      listProjectReviews(projectId),
      listProjectActivity(projectId),
    ]);
    set({
      collaborationMembers: members || [],
      collaborationComments: comments || [],
      collaborationReviews: reviews || [],
      collaborationActivity: activity || [],
    });
  },

  loadCollaborationStatus: async () => {
    try {
      const status = await fetchCollaborationStatus();
      const enabled = Boolean(status?.enabled);
      set({ collaborationEnabled: enabled });
      if (!enabled) {
        return status;
      }
      const actors = await listCollaborationActors();
      set({ collaborationActors: actors || [] });
      return status;
    } catch (error) {
      collaborationLogger.debug('Collaboration status unavailable', {
        message: error.message,
      });
      set({ collaborationEnabled: false });
      return { enabled: false };
    }
  },

  selectCollaborationActor: async (actorId) => {
    const next = String(actorId || '').trim();
    setCollaborationActorId(next);
    set({ collaborationActorId: next });
    if (get().collaborationEnabled) {
      await get().refreshCollaboration();
    }
  },

  createCollaborationActorByName: async (displayName) => {
    const created = await createCollaborationActor(displayName);
    collaborationLogger.info('Collaboration actor created', {
      actor_id: created?.id || null,
      display_name_len: String(displayName || '').length,
    });
    const actors = await listCollaborationActors();
    set({ collaborationActors: actors || [] });
    return created;
  },

  shareProjectMember: async (actorId, role) => {
    const projectId = get().currentProjectId;
    const granted = await grantProjectMember(projectId, { actor_id: actorId, role });
    collaborationLogger.info('Project member shared', {
      project_id: projectId,
      actor_id: actorId,
      role,
    });
    await get().refreshCollaboration();
    return granted;
  },

  postCollaborationComment: async ({ targetKind, body, revisionId, sectionId, trackId, startBar, endBar }) => {
    const projectId = get().currentProjectId;
    const created = await createProjectComment(projectId, {
      target_kind: targetKind,
      body,
      revision_id: revisionId || null,
      section_id: sectionId || null,
      track_id: trackId || null,
      start_bar: startBar ?? null,
      end_bar: endBar ?? null,
    });
    collaborationLogger.info('Comment submitted', {
      project_id: projectId,
      comment_id: created?.id || null,
      actor_id: get().collaborationActorId || null,
      target_kind: targetKind,
      body_len: String(body || '').length,
    });
    await get().refreshCollaboration();
    return created;
  },

  openCollaborationReview: async (revisionId) => {
    const projectId = get().currentProjectId;
    const opened = await openProjectReview(projectId, revisionId);
    collaborationLogger.info('Review opened', {
      project_id: projectId,
      revision_id: revisionId,
      review_id: opened?.id || null,
    });
    await get().refreshCollaboration();
    return opened;
  },

  decideCollaborationReview: async (reviewId, decision) => {
    const projectId = get().currentProjectId;
    const decided = await decideProjectReview(projectId, reviewId, decision);
    collaborationLogger.info('Review decided', {
      project_id: projectId,
      review_id: reviewId,
      decision,
    });
    await get().refreshCollaboration();
    const project = await getProjectRequest(projectId);
    set({ ...historyStateFromProject(project) });
    return decided;
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

  startMotifApply: async () => {
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
    const creative = isCreativeMotifOperation(state.motifOperation);
    const capture = captureAiRequestContext(state);
    const sourceFingerprint = await fingerprintCompositionOrNull(state.editedMusicJson);
    console.info('[musicStore] Motif apply started', {
      motifId: state.motifSelectedMotifId,
      occurrenceId: state.motifSelectedOccurrenceId,
      operation: state.motifOperation,
      creative,
      destinationTrackId: state.motifDestinationTrackId,
      destinationStartBar: state.motifDestinationStartBar,
      sourcePrefix: editFingerprintLogPrefix(sourceFingerprint),
    });
    set({
      motifApplyStatus: 'loading',
      motifApplyError: '',
      motifApplyWarnings: [],
      motifCandidate: creative ? null : state.motifCandidate,
      motifAuditionActive: false,
      motifCompareResult: creative ? null : state.motifCompareResult,
      motifRequestCapture: {
        ...capture,
        sourceFingerprint,
        creative,
        operation: state.motifOperation,
        motifId: state.motifSelectedMotifId,
        occurrenceId: state.motifSelectedOccurrenceId,
        destinationTrackId: state.motifDestinationTrackId,
        destinationStartBar: state.motifDestinationStartBar,
        destinationStartTick: state.motifDestinationStartTick,
        variationStrength: state.motifVariationStrength,
      },
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
      motifAuditionActive: false,
    });
  },

  completeMotifApply: async ({
    composition,
    musicxml = '',
    warnings = [],
    result = null,
    provider = null,
    model = null,
  } = {}) => {
    const capture = get().motifRequestCapture;
    if (!capture || get().motifApplyStatus !== 'loading') {
      console.warn('[musicStore] Ignoring motif apply response without active request');
      return false;
    }

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

    // Creative AI motif: stage preview candidate; do not mutate working composition.
    if (capture.creative) {
      const stale = detectAiRequestStale(capture, get());
      if (stale.stale) {
        console.warn('[musicStore] Creative motif response rejected as stale', { reason: stale.reason });
        set({
          motifApplyStatus: 'error',
          motifApplyError: 'Composition or project changed during motif preview; request again',
          motifCandidate: null,
        });
        return false;
      }
      const candidateFingerprint = await compositionEditFingerprint(prepared);
      if (get().motifRequestCapture !== capture) {
        return false;
      }
      const endBar = Number(capture.destinationStartBar) || 1;
      const candidate = buildAiCandidateEnvelope({
        candidateId: makeAiCandidateId('motif'),
        operationType: 'creative-motif-apply',
        composition: prepared,
        sourceFingerprint: capture.sourceFingerprint,
        candidateFingerprint,
        provider: provider || get().selectedProvider || null,
        model: model || get().selectedModel || null,
        instruction: null,
        warnings,
        declaredRanges: [{ start_bar: endBar, end_bar: endBar }],
        declaredTrackIds: capture.destinationTrackId ? [capture.destinationTrackId] : [],
        musicXml: musicxml || '',
        extras: {
          motif_result: result,
          motif_operation: capture.operation,
          variation_strength: capture.variationStrength,
        },
      });
      console.info('[musicStore] Creative motif candidate staged', {
        ...aiCandidateLogFields(candidate),
        operation: capture.operation,
      });
      set({
        motifCandidate: candidate,
        motifAuditionActive: false,
        motifCompareResult: null,
        motifApplyStatus: 'success',
        motifApplyError: '',
        motifApplyWarnings: Array.isArray(warnings) ? warnings : [],
      });
      return true;
    }

    // Mechanical motif: direct undoable edit (not AI experimentation).
    const state = get();
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
        motifCandidate: null,
        motifAuditionActive: false,
        motifCompareResult: null,
        motifRequestCapture: null,
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

  rejectMotifCandidate: () => {
    console.info('[musicStore] Creative motif candidate rejected', {
      ...aiCandidateLogFields(get().motifCandidate),
    });
    set({
      motifCandidate: null,
      motifAuditionActive: false,
      motifCompareResult: null,
      motifApplyStatus: 'idle',
      motifApplyError: '',
      motifApplyWarnings: [],
      motifRequestCapture: null,
    });
    return true;
  },

  setMotifAuditionActive: (active) => {
    const enabled = Boolean(active);
    const candidate = get().motifCandidate;
    if (enabled && (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY)) {
      return false;
    }
    set({
      motifAuditionActive: enabled,
      ...(enabled
        ? {
          ...exclusiveAuditionPatch(PLAYBACK_SOURCE_GENERATION, ARRANGEMENT_AUDITION_SOURCE),
          generationAuditionActive: false,
          aiEditAuditionActive: false,
          motifAuditionActive: true,
        }
        : {}),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshMotifComparison: () => {
    const candidate = get().motifCandidate;
    if (!candidate) {
      set({ motifCompareResult: null });
      return null;
    }
    const result = compareCompositions(get().editedMusicJson, candidate.composition, {
      leftLabel: 'working',
      rightLabel: 'motif-candidate',
    });
    set({ motifCompareResult: result });
    return result;
  },

  applyMotifCandidate: async ({ asNewBranch = false, branchName = null } = {}) => {
    const state = get();
    const candidate = state.motifCandidate;
    if (!candidate || candidate.status !== AI_CANDIDATE_STATUS.READY) {
      return false;
    }
    const stale = detectAiRequestStale(state.motifRequestCapture, state);
    if (stale.stale) {
      set({
        motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.STALE },
        motifApplyStatus: 'error',
        motifApplyError: 'Source changed; request a new motif preview',
      });
      return false;
    }
    const liveSourceFp = await fingerprintCompositionOrNull(state.editedMusicJson);
    const liveCandidateFp = await compositionEditFingerprint(candidate.composition);
    if (
      liveSourceFp !== candidate.source_fingerprint
      || liveCandidateFp !== candidate.candidate_fingerprint
    ) {
      set({
        motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.STALE },
        motifApplyStatus: 'error',
        motifApplyError: 'Candidate fingerprints no longer match; request a new preview',
      });
      return false;
    }

    const prepared = prepareCompositionForStore(
      ensureCompositionNoteIds(candidate.composition).composition,
    );
    const validation = validateMusicJson(prepared);
    const motifValidation = validateMotifDefinitions(prepared);
    if (!validation.valid || !isCanonicalComposition(prepared) || !motifValidation.valid) {
      set({
        motifApplyStatus: 'error',
        motifApplyError: validation.message || motifValidation.message || 'Candidate invalid',
      });
      return false;
    }

    set({ motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.APPLYING } });

    const motifResult = candidate.motif_result || null;
    const createdEventIds = Array.isArray(motifResult?.created_event_ids)
      ? motifResult.created_event_ids
      : [];
    const destinationTrackId = pickDefaultTrackId(
      prepared,
      motifResult?.destination_track_id || candidate.declared_track_ids?.[0] || state.motifDestinationTrackId,
    );
    const newOccurrenceId = motifResult?.new_occurrence_id || null;

    const aiPayload = buildHistoryAiProvenance(candidate, {
      provider: normalizeAiProvider(candidate.provider),
      model: candidate.model,
    });

    const localStatePatch = {
      musicXml: candidate.music_xml || state.musicXml || '',
      motifApplyStatus: 'idle',
      motifApplyError: '',
      motifApplyWarnings: candidate.warnings || [],
      motifSelectedMotifId: motifResult?.motif_id || state.motifSelectedMotifId,
      motifSelectedOccurrenceId: newOccurrenceId || state.motifSelectedOccurrenceId,
      motifHighlightedUsageKey: motifResult?.motif_id && newOccurrenceId
        ? `canonical:${motifResult.motif_id}:${newOccurrenceId}`
        : state.motifHighlightedUsageKey,
      motifCandidate: null,
      motifAuditionActive: false,
      motifCompareResult: null,
      motifRequestCapture: null,
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    };

    if (!state.currentProjectId) {
      console.info('[musicStore] Creative motif apply local-only (no project)', {
        ...aiCandidateLogFields(candidate),
      });
      return commitCompositionTransaction(set, get, {
        nextComposition: prepared,
        selectedTrackId: destinationTrackId,
        selectedNoteId: null,
        selectedNoteIds: createdEventIds,
        action: 'creative-motif-apply',
        noteSummary: {
          motifId: motifResult?.motif_id || state.motifSelectedMotifId,
          newOccurrenceId,
          createdEventCount: createdEventIds.length,
        },
        affectedNoteCount: createdEventIds.length,
        affectedTrackCount: 1,
        statePatch: localStatePatch,
      });
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        motifApplyStatus: 'error',
        motifApplyError: 'Missing branch CAS fields for durable motif apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
            motifApplyStatus: 'error',
            motifApplyError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'creative-motif-apply',
          declared_scope: {
            ranges: candidate.declared_ranges || [],
            track_ids: candidate.declared_track_ids || [],
          },
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'creative-motif-apply',
          checkpoint_dirty_draft: true,
          declared_scope: {
            ranges: candidate.declared_ranges || [],
            track_ids: candidate.declared_track_ids || [],
          },
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      console.info('[musicStore] Creative motif candidate applied', {
        ...aiCandidateLogFields(candidate),
        asNewBranch: Boolean(asNewBranch),
      });
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'creative-motif-apply',
      });
      set({
        ...localStatePatch,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        set({
          motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
          motifApplyStatus: 'error',
          motifApplyError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      set({
        motifCandidate: { ...candidate, status: AI_CANDIDATE_STATUS.READY },
        motifApplyStatus: 'error',
        motifApplyError: error.message || 'Motif apply failed',
      });
      throw error;
    }
  },

  applyMotifTransformation: async () => {
    const started = await get().startMotifApply();
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
        provider: response.provider,
        model: response.model,
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
      ...(enabled
        ? exclusiveAuditionPatch(PLAYBACK_SOURCE_DEVELOPMENT, ARRANGEMENT_AUDITION_SOURCE)
        : {}),
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
      developmentCompareResult: null,
    });
  },

  setMusicalReferenceEnabled: (enabled) => {
    const next = Boolean(enabled);
    embeddingLogger.debug('Musical reference toggle', { enabled: next });
    if (!next) {
      set({
        musicalReferenceEnabled: false,
        musicalReferenceError: '',
        musicalReferenceStatus: 'idle',
        musicalReferenceB: null,
        musicalReferenceBComposition: null,
        musicalReferenceBStatus: 'idle',
        musicalReferenceBError: '',
        musicalReferenceBRequestId: 0,
      });
      return;
    }
    set({ musicalReferenceEnabled: true });
    if (!get().projectList?.length) {
      get().loadProjectList().catch(() => {});
    }
  },

  clearMusicalReference: () => {
    embeddingLogger.debug('Musical reference cleared');
    set({
      ...initialMusicalReferenceSessionState,
    });
  },

  clearSimilarityHits: () => {
    embeddingLogger.debug('Similarity hits cleared');
    set({
      similarityHits: [],
      similarityStatus: 'idle',
      similarityError: '',
      similarityRequestId: 0,
    });
  },

  clearRelatedMotifHits: () => {
    set({
      relatedMotifHits: [],
      relatedMotifStatus: 'idle',
      relatedMotifError: '',
      relatedMotifRequestId: 0,
    });
  },

  /**
   * Load another project's composition as musical reference (ephemeral).
   * Does not open/switch the working project.
   */
  selectMusicalReferenceProject: async (projectId) => {
    const id = typeof projectId === 'string' ? projectId.trim() : '';
    if (!id) {
      set({
        musicalReference: null,
        musicalReferenceComposition: null,
        musicalReferenceStatus: 'idle',
        musicalReferenceError: '',
      });
      return false;
    }

    musicalReferenceRequestSeq += 1;
    const requestId = musicalReferenceRequestSeq;
    const listed = (get().projectList || []).find((item) => item.id === id);
    set({
      musicalReferenceEnabled: true,
      musicalReferenceStatus: 'loading',
      musicalReferenceError: '',
      musicalReferenceRequestId: requestId,
      musicalReference: normalizeMusicalReferenceSession({
        projectId: id,
        projectName: listed?.name || null,
        scope: { kind: 'composition' },
        scoreVsCurrent: null,
      }),
      musicalReferenceComposition: null,
      similarityHits: [],
      similarityStatus: 'idle',
      similarityError: '',
    });

    embeddingLogger.debug('Musical reference project load started', {
      projectId: id,
      requestId,
    });

    try {
      const project = await getProjectRequest(id);
      if (requestId !== get().musicalReferenceRequestId) {
        return false;
      }
      const composition = project?.composition
        ? prepareCompositionForStore(project.composition)
        : null;
      if (!composition || !isCanonicalComposition(composition)) {
        set({
          musicalReferenceStatus: 'error',
          musicalReferenceError: 'Reference project has no canonical composition.v2',
          musicalReferenceComposition: null,
        });
        return false;
      }
      const sections = listAnalysisSectionOptions(composition);
      const firstSection = sections[0] || null;
      const scopeResult = firstSection
        ? buildSectionEmbedScope(firstSection)
        : { ok: true, scope: { kind: 'composition' } };
      if (!scopeResult.ok) {
        set({
          musicalReferenceStatus: 'error',
          musicalReferenceError: scopeResult.message,
        });
        return false;
      }
      const session = normalizeMusicalReferenceSession({
        projectId: id,
        projectName: project.name || listed?.name || null,
        sectionIndex: firstSection?.index ?? null,
        sectionKey: firstSection?.key ?? null,
        sectionLabel: firstSection?.label ?? null,
        scope: scopeResult.scope,
        scoreVsCurrent: null,
      });
      set({
        musicalReference: session,
        musicalReferenceComposition: composition,
        musicalReferenceStatus: 'ready',
        musicalReferenceError: '',
      });
      embeddingLogger.debug('Musical reference project ready', {
        projectId: id,
        sectionCount: sections.length,
        requestId,
      });
      await get().refreshMusicalReferenceScore();
      return true;
    } catch (error) {
      if (requestId !== get().musicalReferenceRequestId) {
        return false;
      }
      embeddingLogger.error('Musical reference project load failed', {
        projectId: id,
        message: error.message || null,
      });
      set({
        musicalReferenceStatus: 'error',
        musicalReferenceError: error.message || 'Failed to load reference project',
        musicalReferenceComposition: null,
      });
      return false;
    }
  },

  clearMusicalReferenceB: () => {
    embeddingLogger.debug('Musical reference B cleared');
    set({
      musicalReferenceB: null,
      musicalReferenceBComposition: null,
      musicalReferenceBStatus: 'idle',
      musicalReferenceBError: '',
      musicalReferenceBRequestId: 0,
    });
  },

  /**
   * Load a second musical reference (ephemeral) for multi-ref conditioning (A + B).
   */
  selectMusicalReferenceBProject: async (projectId) => {
    const id = typeof projectId === 'string' ? projectId.trim() : '';
    if (!id) {
      get().clearMusicalReferenceB();
      return false;
    }

    musicalReferenceBRequestSeq += 1;
    const requestId = musicalReferenceBRequestSeq;
    const listed = (get().projectList || []).find((item) => item.id === id);
    set({
      musicalReferenceEnabled: true,
      musicalReferenceBStatus: 'loading',
      musicalReferenceBError: '',
      musicalReferenceBRequestId: requestId,
      musicalReferenceB: normalizeMusicalReferenceSession({
        projectId: id,
        projectName: listed?.name || null,
        scope: { kind: 'composition' },
        scoreVsCurrent: null,
        referenceFeatureMaskEnabled: true,
      }),
      musicalReferenceBComposition: null,
    });

    embeddingLogger.debug('Musical reference B project load started', {
      projectId: id,
      requestId,
    });

    try {
      const project = await getProjectRequest(id);
      if (requestId !== get().musicalReferenceBRequestId) {
        return false;
      }
      const composition = project?.composition
        ? prepareCompositionForStore(project.composition)
        : null;
      if (!composition || !isCanonicalComposition(composition)) {
        set({
          musicalReferenceBStatus: 'error',
          musicalReferenceBError: 'Reference B project has no canonical composition.v2',
          musicalReferenceBComposition: null,
        });
        return false;
      }
      const session = normalizeMusicalReferenceSession({
        projectId: id,
        projectName: project.name || listed?.name || null,
        scope: { kind: 'composition' },
        scoreVsCurrent: null,
        referenceFeatureMaskEnabled: true,
      });
      set({
        musicalReferenceB: session,
        musicalReferenceBComposition: composition,
        musicalReferenceBStatus: 'ready',
        musicalReferenceBError: '',
      });
      embeddingLogger.debug('Musical reference B project ready', {
        projectId: id,
        requestId,
      });
      return true;
    } catch (error) {
      if (requestId !== get().musicalReferenceBRequestId) {
        return false;
      }
      embeddingLogger.error('Musical reference B project load failed', {
        projectId: id,
        message: error.message || null,
      });
      set({
        musicalReferenceBStatus: 'error',
        musicalReferenceBError: error.message || 'Failed to load reference B project',
        musicalReferenceBComposition: null,
      });
      return false;
    }
  },

  selectMusicalReferenceSection: async (sectionKey) => {
    const state = get();
    const composition = state.musicalReferenceComposition;
    if (!composition || !state.musicalReference?.projectId) {
      return false;
    }
    const options = listAnalysisSectionOptions(composition);
    const option = options.find((item) => item.key === sectionKey);
    if (!option) {
      set({ musicalReferenceError: 'Section not found on reference composition' });
      return false;
    }
    const scopeResult = buildSectionEmbedScope(option);
    if (!scopeResult.ok) {
      set({ musicalReferenceError: scopeResult.message });
      return false;
    }
    const session = normalizeMusicalReferenceSession({
      ...state.musicalReference,
      sectionIndex: option.index,
      sectionKey: option.key,
      sectionLabel: option.label,
      scope: scopeResult.scope,
      scoreVsCurrent: null,
      sourceFingerprint: null,
      fingerprintPrefix: null,
    });
    embeddingLogger.debug('Musical reference section selected', {
      projectId: session?.projectId || null,
      sectionIndex: option.index,
    });
    set({
      musicalReference: session,
      musicalReferenceEnabled: true,
      musicalReferenceError: '',
      musicalReferenceStatus: 'ready',
    });
    await get().refreshMusicalReferenceScore();
    return true;
  },

  /**
   * Advisory affinity between current development scope and the selected musical reference.
   * Score is structural affinity — not musical quality.
   */
  refreshMusicalReferenceScore: async () => {
    const state = get();
    const reference = state.musicalReference;
    const refComposition = state.musicalReferenceComposition;
    const working = state.editedMusicJson;
    if (
      !state.musicalReferenceEnabled
      || !reference?.scope
      || !refComposition
      || !isCanonicalComposition(working)
      || !isCanonicalComposition(refComposition)
    ) {
      return null;
    }

    musicalReferenceRequestSeq += 1;
    const requestId = musicalReferenceRequestSeq;
    set({ musicalReferenceRequestId: requestId, musicalReferenceError: '' });

    try {
      const currentScope = buildCurrentEmbedScope({
        composition: working,
        sourceStartBar: state.developmentSourceStartBar,
        sourceEndBar: state.developmentSourceEndBar,
        sourceSectionKey: state.developmentSourceSectionKey,
      });
      if (!currentScope.ok) {
        return null;
      }

      const [currentResult, resolved] = await Promise.all([
        computeEmbedding(working, currentScope.scope),
        resolveMusicalReference({
          project_id: reference.projectId,
          scope: reference.scope,
          expected_fingerprint: reference.sourceFingerprint || undefined,
          mode: reference.mode || 'prompt_features',
        }).catch(async () => {
          // Fallback: embed the already-loaded reference composition inline.
          const embedded = await computeEmbedding(refComposition, reference.scope);
          return {
            embedding: embedded.embedding,
            provenance: {
              source_fingerprint: embedded.embedding.source_fingerprint,
              project_id: reference.projectId,
              scope: reference.scope,
            },
          };
        }),
      ]);

      if (requestId !== get().musicalReferenceRequestId) {
        return null;
      }

      const score = cosineSimilarity(
        currentResult.embedding?.vector,
        resolved.embedding?.vector,
      );
      const sourceFingerprint = resolved.provenance?.source_fingerprint
        || resolved.embedding?.source_fingerprint
        || null;
      const next = normalizeMusicalReferenceSession({
        ...get().musicalReference,
        sourceFingerprint,
        fingerprintPrefix: fingerprintPrefix(sourceFingerprint),
        scoreVsCurrent: score,
      });
      set({ musicalReference: next, musicalReferenceStatus: 'ready' });
      embeddingLogger.debug('Musical reference score updated', {
        projectId: next?.projectId || null,
        fingerprintPrefix: next?.fingerprintPrefix || null,
        hasScore: score != null,
      });
      return score;
    } catch (error) {
      if (requestId !== get().musicalReferenceRequestId) {
        return null;
      }
      const message = error instanceof EmbeddingApiError
        ? error.message
        : (error.message || 'Failed to score musical reference');
      embeddingLogger.error('Musical reference score failed', {
        code: error.code || null,
      });
      set({ musicalReferenceError: message });
      return null;
    }
  },

  /**
   * Compact top-k similar sections across workspace projects (advisory).
   */
  searchSimilarSections: async ({ topK = SIMILARITY_DEFAULT_TOP_K } = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!isCanonicalComposition(composition)) {
      set({
        similarityStatus: 'error',
        similarityError: 'Canonical composition.v2 required for similar sections',
      });
      return [];
    }

    const scopeResult = buildCurrentEmbedScope({
      composition,
      sourceStartBar: state.developmentSourceStartBar,
      sourceEndBar: state.developmentSourceEndBar,
      sourceSectionKey: state.developmentSourceSectionKey,
    });
    if (!scopeResult.ok) {
      set({ similarityStatus: 'error', similarityError: scopeResult.message });
      return [];
    }

    const queryBuilt = buildSimilarityQueryPayload({
      composition,
      scope: scopeResult.scope,
      topK,
      excludeProjectId: state.currentProjectId || null,
    });
    if (!queryBuilt.ok) {
      set({ similarityStatus: 'error', similarityError: queryBuilt.message });
      return [];
    }

    similarityRequestSeq += 1;
    const requestId = similarityRequestSeq;
    set({
      similarityStatus: 'loading',
      similarityError: '',
      similarityRequestId: requestId,
      similarityHits: [],
    });
    embeddingLogger.debug('Similar sections search started', {
      requestId,
      scopeKind: scopeResult.scope.kind,
      topK,
    });

    try {
      const response = await searchSimilarEmbeddings(queryBuilt.query);
      if (requestId !== get().similarityRequestId) {
        return [];
      }
      set({
        similarityHits: response.hits,
        similarityStatus: 'ready',
        similarityError: '',
      });
      embeddingLogger.debug('Similar sections ready', {
        requestId,
        hitCount: response.hits.length,
        queryFingerprintPrefix: response.query_fingerprint_prefix || null,
      });
      return response.hits;
    } catch (error) {
      if (requestId !== get().similarityRequestId) {
        return [];
      }
      const message = error instanceof EmbeddingApiError
        ? error.message
        : (error.message || 'Similar sections search failed');
      embeddingLogger.error('Similar sections failed', {
        code: error.code || null,
        status: error.status || null,
      });
      set({
        similarityStatus: 'error',
        similarityError: message,
        similarityHits: [],
      });
      return [];
    }
  },

  /**
   * Light Motifs-tab helper: related motifs for the currently selected motif.
   */
  searchRelatedMotifsForCurrent: async ({
    topK = RELATED_MOTIFS_DEFAULT_TOP_K,
    searchCrossProject = false,
  } = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    const motifId = state.motifSelectedMotifId;
    if (!isCanonicalComposition(composition) || !motifId) {
      set({
        relatedMotifStatus: 'error',
        relatedMotifError: 'Select a motif on a canonical composition',
      });
      return [];
    }

    relatedMotifRequestSeq += 1;
    const requestId = relatedMotifRequestSeq;
    set({
      relatedMotifStatus: 'loading',
      relatedMotifError: '',
      relatedMotifRequestId: requestId,
      relatedMotifHits: [],
    });
    embeddingLogger.debug('Related motifs search started', {
      requestId,
      motifId,
      occurrenceId: state.motifSelectedOccurrenceId || null,
    });

    try {
      const response = await searchRelatedMotifs({
        composition,
        motifId,
        occurrenceId: state.motifSelectedOccurrenceId || null,
        topK,
        searchCrossProject,
      });
      if (requestId !== get().relatedMotifRequestId) {
        return [];
      }
      set({
        relatedMotifHits: response.hits,
        relatedMotifStatus: 'ready',
        relatedMotifError: '',
      });
      embeddingLogger.debug('Related motifs ready', {
        requestId,
        hitCount: response.hits.length,
      });
      return response.hits;
    } catch (error) {
      if (requestId !== get().relatedMotifRequestId) {
        return [];
      }
      const message = error instanceof EmbeddingApiError
        ? error.message
        : (error.message || 'Related motifs search failed');
      embeddingLogger.error('Related motifs failed', {
        code: error.code || null,
      });
      set({
        relatedMotifStatus: 'error',
        relatedMotifError: message,
        relatedMotifHits: [],
      });
      return [];
    }
  },

  rejectDevelopmentCandidate: (candidateId) => {
    const state = get();
    const id = candidateId || state.developmentSelectedCandidateId;
    const next = (state.developmentCandidates || []).filter((item) => item.candidate_id !== id);
    console.info('[musicStore] Development candidate rejected', {
      candidateIdSuffix: String(id || '').slice(-8),
      remaining: next.length,
    });
    const selectedStill = next.some((item) => item.candidate_id === state.developmentSelectedCandidateId);
    set({
      developmentCandidates: next,
      developmentSelectedCandidateId: selectedStill
        ? state.developmentSelectedCandidateId
        : (next[0]?.candidate_id || null),
      developmentAuditionActive: false,
      developmentCompareResult: null,
      developmentStatus: next.length ? state.developmentStatus : 'idle',
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshDevelopmentComparison: () => {
    const state = get();
    const candidate = findDevelopmentCandidateById(
      state.developmentCandidates,
      state.developmentSelectedCandidateId,
    );
    if (!candidate) {
      set({ developmentCompareResult: null });
      return null;
    }
    const result = compareCompositions(state.editedMusicJson, candidate.composition, {
      leftLabel: 'working',
      rightLabel: 'development-candidate',
    });
    set({ developmentCompareResult: result });
    return result;
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
      hasMusicalReference: Boolean(
        state.musicalReferenceEnabled && state.musicalReference?.projectId,
      ),
      musicalReferenceProjectId: state.musicalReference?.projectId || null,
      musicalReferenceScopeKind: state.musicalReference?.scope?.kind || null,
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
      developmentReferenceProvenance: null,
    });

    try {
      let styleReference;
      if (state.musicalReferenceEnabled && state.musicalReference) {
        const refInput = {
          ...state.musicalReference,
          composition: state.musicalReferenceComposition || state.musicalReference.composition,
        };
        if (state.musicalReference.referenceFeatureMaskEnabled) {
          refInput.dimensions = state.musicalReference.dimensions;
        } else {
          delete refInput.dimensions;
        }
        const built = buildStyleReferenceFromMusicalReference(refInput);
        if (!built.ok) {
          set({
            developmentStatus: 'error',
            developmentError: built.message || 'Invalid musical reference',
          });
          return false;
        }
        styleReference = built.styleReference || undefined;
      }

      const conditioningExtras = {};
      if (styleReference) {
        const policyState = loadConditioningSession();
        const { rows: borrowRows } = collectMultiRefBorrowRows({
          enabled: Boolean(state.musicalReference?.referenceFeatureMaskEnabled),
          dimensions: state.musicalReference?.dimensions,
          borrowSourceByDim: policyState.borrowSourceByDim,
          primary: state.musicalReference,
          primaryComposition: state.musicalReferenceComposition || state.musicalReference?.composition,
          secondary: state.musicalReferenceB,
          secondaryComposition: state.musicalReferenceBComposition,
        });
        const conditioning = buildConditioningRequestFields({
          policyState,
          borrowRows,
          activeProjectId: state.currentProjectId,
          includePolicy: true,
        });
        if (conditioning.ok) {
          Object.assign(conditioningExtras, conditioning.fields);
          if (conditioningExtras.style_references) {
            styleReference = undefined;
          } else if (conditioningExtras.style_reference) {
            styleReference = conditioningExtras.style_reference;
            delete conditioningExtras.style_reference;
          }
        }
      }

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
        ...(styleReference ? { style_reference: styleReference } : {}),
        ...conditioningExtras,
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
          developmentReferenceProvenance: null,
        });
        return false;
      }

      const selectedId = response.candidates[0]?.candidate_id || null;
      const provenance = response.reference_provenance && typeof response.reference_provenance === 'object'
        ? response.reference_provenance
        : null;
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
        developmentReferenceProvenance: provenance,
      });
      console.info('[musicStore] Development preview ready', {
        requestId,
        returnedCandidateCount: response.candidates.length,
        editSourcePrefix: editFingerprintLogPrefix(response.edit_source_fingerprint),
        warningCodeCount: (response.warning_codes || []).length,
        hasReferenceProvenance: Boolean(provenance),
        referenceFingerprintPrefix: fingerprintPrefix(provenance?.source_fingerprint),
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
        developmentReferenceProvenance: null,
      });
      return false;
    }
  },

  applySelectedDevelopmentCandidate: async ({ asNewBranch = false, branchName = null } = {}) => {
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
        developmentCompareResult: null,
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

    const outputRange = candidate.output_range || {};
    const declaredRanges = Number.isInteger(outputRange.start_bar) && Number.isInteger(outputRange.end_bar)
      ? [{ start_bar: outputRange.start_bar, end_bar: outputRange.end_bar }]
      : [];
    // Development already verifies edit fingerprints + outside-range preservation before
    // Apply. Do not send a tight declared_scope here: fake/real drafts may rewrite
    // harmony/section metadata whose derived bar spans fall outside the vary window and
    // would falsely trip server scope_escape_bars while event preservation still holds.
    const provenance = state.developmentReferenceProvenance;
    const generationParameters = provenance && typeof provenance === 'object'
      ? {
          reference_provenance: {
            schema_version: provenance.schema_version || 'composition.reference_provenance.v1',
            project_id: provenance.project_id ?? null,
            revision_id: provenance.revision_id ?? null,
            scope_kind: provenance.scope?.kind || null,
            scope_digest_prefix: typeof provenance.scope_digest === 'string'
              ? provenance.scope_digest.slice(0, 12)
              : null,
            source_fingerprint_prefix: fingerprintPrefix(provenance.source_fingerprint),
            embedding_model_id: provenance.embedding_model_id || null,
            profile_id: provenance.profile_id || null,
            algorithm_version: provenance.algorithm_version || null,
            artist_label_used: false,
          },
        }
      : undefined;
    const aiPayload = {
      provider: normalizeAiProvider(state.developmentProvider || state.selectedProvider),
      model: state.developmentModel || state.selectedModel || null,
      user_instruction: state.developmentInstruction || undefined,
      candidate_id: candidate.candidate_id,
      candidate_fingerprint: candidate.candidate_fingerprint,
      warning_codes: toHistoryAiWarningCodes(state.developmentWarnings),
      ...(generationParameters ? { generation_parameters: generationParameters } : {}),
    };

    console.info('[musicStore] Development candidate apply', {
      operation: state.developmentOperation,
      candidateIdSuffix: candidate.candidate_id.slice(-8),
      editSourcePrefix: editFingerprintLogPrefix(verification.localSourceFingerprint),
      barCount: prepared.bar_count,
      outputRange: declaredRanges[0] || null,
      asNewBranch: Boolean(asNewBranch),
      durable: Boolean(state.currentProjectId),
    });

    if (!state.currentProjectId) {
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
          developmentCompareResult: null,
        },
      });
      return true;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        developmentStatus: 'error',
        developmentError: 'Missing branch CAS fields for durable development apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            developmentStatus: 'error',
            developmentError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'development-apply',
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'development-apply',
          checkpoint_dirty_draft: true,
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'development-apply',
      });
      set({
        ...clearedDevelopmentPreviewState({ preserveControls: true }),
        developmentAuditionActive: false,
        developmentCompareResult: null,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        set({
          developmentStatus: 'error',
          developmentError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      set({
        developmentStatus: 'error',
        developmentError: error.message || 'Development apply failed',
      });
      throw error;
    }
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
        ...exclusiveAuditionPatch(PLAYBACK_SOURCE_ARRANGEMENT, ARRANGEMENT_AUDITION_SOURCE),
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
    const current = get().arrangementCandidateTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_ARRANGEMENT, trackId, {
      muted: !current.muted,
    });
  },

  toggleArrangementCandidateSolo: (trackId) => {
    const current = get().arrangementCandidateTrackControls[trackId] || defaultControl();
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_ARRANGEMENT, trackId, {
      solo: !current.solo,
    });
  },

  setArrangementCandidateVolume: (trackId, volumeMidi) => {
    return get().updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_ARRANGEMENT, trackId, { volumeMidi });
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

  applySelectedArrangementCandidate: async ({ asNewBranch = false, branchName = null } = {}) => {
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
        arrangementCompareResult: null,
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
    const aiPayload = {
      provider: normalizeAiProvider(state.arrangementProvider || state.selectedProvider),
      model: state.arrangementModel || state.selectedModel || null,
      user_instruction: state.arrangementInstruction || undefined,
      candidate_id: candidate.candidate_id,
      candidate_fingerprint: candidate.candidate_fingerprint,
      warning_codes: toHistoryAiWarningCodes(state.arrangementWarnings),
    };
    const localStatePatch = {
      trackControls: nextTrackControls,
      ...reconcileMotifUiAfterCompositionChange(state, prepared),
      ...clearedArrangementPreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      arrangementCompareResult: null,
    };

    arrangementLogger.info('Arrangement candidate apply', {
      operation: state.arrangementOperation,
      candidateIdSuffix: candidate.candidate_id.slice(-8),
      fingerprintPrefix: arrangementFingerprintPrefix(verification.localCandidateFingerprint),
      revision: String(state.arrangementBaseRevision || '').slice(0, 48),
      trackCount: prepared.tracks?.length || 0,
      status: 'apply',
      asNewBranch: Boolean(asNewBranch),
      durable: Boolean(state.currentProjectId),
    });

    if (!state.currentProjectId) {
      commitCompositionTransaction(set, get, {
        nextComposition: prepared,
        selectedTrackId: selection.trackId,
        selectedNoteId: selection.noteId,
        selectedNoteIds: selection.noteIds,
        action: 'arrangement-apply',
        noteSummary: null,
        historySnapshot,
        statePatch: localStatePatch,
      });
      void get().refreshMusicXmlFromEditedComposition();
      return true;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        arrangementStatus: 'error',
        arrangementError: 'Missing branch CAS fields for durable arrangement apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            arrangementStatus: 'error',
            arrangementError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'arrangement-apply',
          declared_scope: {
            ranges: [],
            track_ids: state.arrangementSourceTrackIds || [],
          },
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'arrangement-apply',
          checkpoint_dirty_draft: true,
          declared_scope: {
            ranges: [],
            track_ids: state.arrangementSourceTrackIds || [],
          },
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'arrangement-apply',
      });
      set({
        ...localStatePatch,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      void get().refreshMusicXmlFromEditedComposition();
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        set({
          arrangementStatus: 'error',
          arrangementError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      set({
        arrangementStatus: 'error',
        arrangementError: error.message || 'Arrangement apply failed',
      });
      throw error;
    }
  },

  rejectArrangementCandidate: (candidateId) => {
    const state = get();
    const id = candidateId || state.arrangementSelectedCandidateId;
    const next = (state.arrangementCandidates || []).filter((item) => item.candidate_id !== id);
    arrangementLogger.info('Arrangement candidate rejected', {
      candidateIdSuffix: String(id || '').slice(-8),
      remaining: next.length,
    });
    const selectedStill = next.some((item) => item.candidate_id === state.arrangementSelectedCandidateId);
    set({
      arrangementCandidates: next,
      arrangementSelectedCandidateId: selectedStill
        ? state.arrangementSelectedCandidateId
        : (next[0]?.candidate_id || null),
      arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
      arrangementCompareResult: null,
      arrangementStatus: next.length ? state.arrangementStatus : 'idle',
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshArrangementComparison: () => {
    const state = get();
    const candidate = findArrangementCandidateById(
      state.arrangementCandidates,
      state.arrangementSelectedCandidateId,
    );
    if (!candidate) {
      set({ arrangementCompareResult: null });
      return null;
    }
    const result = compareCompositions(state.editedMusicJson, candidate.composition, {
      leftLabel: 'working',
      rightLabel: 'arrangement-candidate',
    });
    set({ arrangementCompareResult: result });
    return result;
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

  applyReharmonizePreview: async ({ asNewBranch = false, branchName = null } = {}) => {
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

    const currentFingerprint = await compositionEditFingerprint(state.editedMusicJson);
    const liveProposalFingerprint = await compositionEditFingerprint(state.reharmonizeCandidate);
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
      proposalFingerprint: liveProposalFingerprint,
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

    // Deterministic engine remains honest: only claim AI provider when engine used one.
    const usedAiEngine = state.reharmonizeEngine !== 'deterministic';
    const aiPayload = {
      provider: usedAiEngine
        ? normalizeAiProvider(state.reharmonizeProvider || state.selectedProvider)
        : null,
      model: usedAiEngine ? (state.reharmonizeModel || state.selectedModel || null) : null,
      user_instruction: state.reharmonizeInstruction || undefined,
      candidate_fingerprint: liveProposalFingerprint,
      warning_codes: toHistoryAiWarningCodes(state.reharmonizeWarnings),
    };
    const declaredRanges = state.harmonySelectionStartBar && state.harmonySelectionEndBar
      ? [{ start_bar: state.harmonySelectionStartBar, end_bar: state.harmonySelectionEndBar }]
      : [];

    console.info('[musicStore] Reharmonize preview applied', {
      operation: state.reharmonizeOperation,
      contentPolicy: state.reharmonizeContentPolicy,
      engine: state.reharmonizeEngine,
      changedSpanCount: state.reharmonizeHarmonyChanges.length,
      changedTrackCount: state.reharmonizeTrackChanges.filter((item) => item.events_changed > 0).length,
      asNewBranch: Boolean(asNewBranch),
      durable: Boolean(state.currentProjectId),
      aiAttributed: usedAiEngine,
    });

    const clearPatch = {
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
      reharmonizeStatus: 'idle',
      reharmonizeAuditionActive: false,
      reharmonizeCompareResult: null,
    };

    if (!state.currentProjectId) {
      commitCompositionTransaction(set, get, {
        nextComposition: prepared,
        selectedTrackId: state.pianoRollTrackId,
        selectedNoteId: null,
        selectedNoteIds: [],
        action: 'reharmonize-apply',
        noteSummary: null,
        statePatch: clearPatch,
      });
      return true;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: 'Missing branch CAS fields for durable reharmonize apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            reharmonizeStatus: 'error',
            reharmonizeError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'reharmonize-apply',
          declared_scope: {
            ranges: declaredRanges,
            track_ids: state.reharmonizeTargetTrackIds || [],
          },
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'reharmonize-apply',
          checkpoint_dirty_draft: true,
          declared_scope: {
            ranges: declaredRanges,
            track_ids: state.reharmonizeTargetTrackIds || [],
          },
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'reharmonize-apply',
      });
      set({
        ...clearPatch,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      return true;
    } catch (error) {
      if (error instanceof ProjectRevisionConflictError) {
        set({
          reharmonizeStatus: 'error',
          reharmonizeError: 'Revision conflict; reload or retry apply',
          saveStatus: 'conflict',
          saveConflict: error.conflict,
        });
        throw error;
      }
      set({
        reharmonizeStatus: 'error',
        reharmonizeError: error.message || 'Reharmonize apply failed',
      });
      throw error;
    }
  },

  rejectReharmonizePreview: () => {
    console.info('[musicStore] Reharmonize candidate rejected');
    set({
      ...clearedReharmonizePreviewState({ preserveControls: true }),
      reharmonizeAuditionActive: false,
      reharmonizeCompareResult: null,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  setReharmonizeAuditionActive: (active) => {
    const enabled = Boolean(active);
    if (enabled && !get().reharmonizeCandidate) {
      return false;
    }
    set({
      reharmonizeAuditionActive: enabled,
      ...(enabled
        ? {
          ...exclusiveAuditionPatch(PLAYBACK_SOURCE_GENERATION, ARRANGEMENT_AUDITION_SOURCE),
          generationAuditionActive: false,
          aiEditAuditionActive: false,
          motifAuditionActive: false,
          reharmonizeAuditionActive: true,
        }
        : {}),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  refreshReharmonizeComparison: () => {
    const candidate = get().reharmonizeCandidate;
    if (!candidate) {
      set({ reharmonizeCompareResult: null });
      return null;
    }
    const result = compareCompositions(get().editedMusicJson, candidate, {
      leftLabel: 'working',
      rightLabel: 'reharmonize-candidate',
    });
    set({ reharmonizeCompareResult: result });
    return result;
  },

  probeMidiSupport: () => {
    const support = probeWebMidiSupport();
    midiLogger.debug('MIDI support probed from store', {
      supported: support.supported,
      reason: support.reason,
    });
    set({ midiSupport: support });
    return support;
  },

  enableMidiAccess: async (deps = {}) => {
    const state = get();
    if (!transitionMidiPhase(set, get, MIDI_PHASES.ENABLING, 'enable')) {
      return { ok: false, reason: state.midiPhase };
    }
    set({
      midiAccessStatus: 'enabling',
      midiErrorCode: null,
      midiErrorMessage: '',
    });

    disposeMidiInputSubscription();
    if (midiAccessUnsubscribe) {
      midiAccessUnsubscribe();
      midiAccessUnsubscribe = null;
    }
    if (midiAccessSession) {
      midiAccessSession.dispose();
      midiAccessSession = null;
    }

    midiAccessSession = createMidiAccessSession(deps);
    midiAccessUnsubscribe = midiAccessSession.subscribe((event) => {
      handleMidiAccessEvent(set, get, event);
    });

    const result = await midiAccessSession.enable();
    if (!result.ok) {
      const phase = result.reason === 'permission_denied'
        ? MIDI_PHASES.DENIED
        : MIDI_PHASES.UNAVAILABLE;
      transitionMidiPhase(set, get, phase, 'enable-failed');
      set({
        midiAccessStatus: 'error',
        midiInputs: [],
        midiSelectedInputId: null,
        midiErrorCode: result.reason,
        midiErrorMessage: result.reason,
      });
      midiLogger.info('MIDI enable failed in store', { reason: result.reason });
      return result;
    }

    const destination =
      get().midiDestinationTrackId
      || get().pianoRollTrackId
      || pickDefaultTrackId(get().editedMusicJson);
    const selectedInputId = result.preferredInputId || result.inputs[0]?.id || null;
    transitionMidiPhase(set, get, MIDI_PHASES.READY, 'enable-ok');
    set({
      midiAccessStatus: 'ready',
      midiSupport: { supported: true, reason: 'available' },
      midiInputs: result.inputs,
      midiSelectedInputId: selectedInputId,
      midiDestinationTrackId: destination,
      midiErrorCode: null,
      midiErrorMessage: '',
    });
    if (selectedInputId) {
      get().selectMidiInput(selectedInputId);
    }
    midiLogger.info('MIDI enable succeeded in store', {
      inputCount: result.inputs.length,
      hasSelection: Boolean(selectedInputId),
      destinationTrackId: destination,
    });
    return result;
  },

  selectMidiInput: (inputId) => {
    const state = get();
    const id = inputId == null ? null : String(inputId);
    if (id && !state.midiInputs.some((input) => input.id === id)) {
      midiLogger.warn('MIDI select ignored — unknown input', {
        deviceId: id.slice(0, 12),
      });
      return false;
    }
    disposeMidiInputSubscription();
    set({ midiSelectedInputId: id });
    if (midiAccessSession && id) {
      midiAccessSession.selectAndRemember(id);
      midiInputUnsubscribe = midiAccessSession.subscribeInput(id, (event) => {
        handleLiveMidiMessage(set, get, event);
      });
    }
    midiLogger.info('MIDI input selected', {
      deviceId: id ? id.slice(0, 12) : null,
    });
    return true;
  },

  setMidiDestinationTrackId: (trackId) => {
    const composition = get().editedMusicJson;
    const next = trackId
      ? pickDefaultTrackId(composition, trackId)
      : pickDefaultTrackId(composition, get().pianoRollTrackId);
    midiLogger.debug('MIDI destination track set', { trackId: next });
    set({ midiDestinationTrackId: next });
  },

  setMidiMetronomeEnabled: (enabled) => {
    set({ midiMetronomeEnabled: Boolean(enabled) });
  },

  setMidiCountInBars: (bars) => {
    const n = Math.max(0, Math.min(2, Math.round(Number(bars) || 0)));
    set({ midiCountInBars: n });
  },

  setMidiQuantizeAfterRecord: (enabled) => {
    set({ midiQuantizeAfterRecord: Boolean(enabled) });
  },

  setMidiTestKeyboardEnabled: (enabled) => {
    set({ midiTestKeyboardEnabled: Boolean(enabled) });
  },

  armMidiRecording: () => {
    const state = get();
    const recoveryGuard = assertMidiCaptureAllowedForRecoveryPhase(state.recoveryPhase);
    if (!recoveryGuard.ok) {
      midiLogger.warn('MIDI arm rejected — recovery phase exclusion', {
        code: recoveryGuard.code,
        recoveryPhase: state.recoveryPhase,
      });
      set({
        midiErrorCode: recoveryGuard.code,
        midiErrorMessage: 'Finish or discard audio recovery before arming MIDI record.',
      });
      return false;
    }
    const liveGuard = assertMidiCaptureAllowedForLivePhase(state.livePhase);
    if (!liveGuard.ok) {
      midiLogger.warn('MIDI arm rejected — live session exclusion', {
        code: liveGuard.code,
        livePhase: state.livePhase,
      });
      set({
        midiErrorCode: LIVE_MIDI_PHASE_EXCLUSION,
        midiErrorMessage: 'Stop co-performance before arming MIDI record.',
      });
      return false;
    }
    if (state.midiPhase !== MIDI_PHASES.READY && state.midiPhase !== MIDI_PHASES.ARMED) {
      midiLogger.warn('MIDI arm rejected — invalid phase', { phase: state.midiPhase });
      return false;
    }
    const destination =
      state.midiDestinationTrackId
      || state.pianoRollTrackId
      || pickDefaultTrackId(state.editedMusicJson);
    if (!destination) {
      midiLogger.warn('MIDI arm rejected — no destination track', {
        code: 'midi_no_destination',
      });
      set({
        midiErrorCode: 'midi_no_destination',
        midiErrorMessage: 'Select a destination track before arming.',
      });
      return false;
    }
    if (!transitionMidiPhase(set, get, MIDI_PHASES.ARMED, 'arm')) {
      return false;
    }
    set({
      midiArmed: true,
      midiDestinationTrackId: destination,
      midiErrorCode: null,
      midiErrorMessage: '',
      midiTakeSummary: null,
    });
    midiLogger.info('MIDI recording armed', { destinationTrackId: destination });
    return true;
  },

  disarmMidiRecording: () => {
    const state = get();
    if (state.midiPhase === MIDI_PHASES.RECORDING || state.midiPhase === MIDI_PHASES.COUNTING_IN) {
      midiLogger.warn('MIDI disarm rejected during capture', { phase: state.midiPhase });
      return false;
    }
    transitionMidiPhase(set, get, MIDI_PHASES.READY, 'disarm');
    set({
      midiArmed: false,
      midiActiveNotes: [],
    });
    midiLogger.info('MIDI recording disarmed');
    return true;
  },

  panicMidiNotes: () => {
    set({ midiActiveNotes: [] });
    midiLogger.info('MIDI panic — cleared active notes', { code: 'midi_panic' });
  },

  /**
   * Start co-performance / AI Jam MIDI stream (Transport-synced).
   * Exclusive with midiPhase capture. Requires jamMode (set via setJamMode).
   * Requires shared PlaybackControls engine for accompaniment scheduling.
   */
  startLiveCoPerformance: ({ sessionId = null, requireEngine = true } = {}) => {
    const state = get();
    const recoveryGuard = assertLiveAllowedForRecoveryPhase(state.recoveryPhase);
    if (!recoveryGuard.ok) {
      liveLogger.info('live start rejected — recoveryPhase exclusion', {
        code: recoveryGuard.code,
        recoveryPhase: state.recoveryPhase,
      });
      set({
        liveErrorCode: recoveryGuard.code,
        liveErrorMessage: 'Finish or discard audio recovery before starting co-performance.',
        livePhase: 'idle',
      });
      return { ok: false, code: recoveryGuard.code };
    }
    const midiGuard = assertLiveAllowedForMidiPhase(state.midiPhase);
    if (!midiGuard.ok) {
      liveLogger.info('live start rejected — midiPhase exclusion', {
        code: midiGuard.code,
        midiPhase: state.midiPhase,
      });
      set({
        liveErrorCode: LIVE_MIDI_PHASE_EXCLUSION,
        liveErrorMessage: 'Stop MIDI recording before starting co-performance.',
        livePhase: 'idle',
      });
      return { ok: false, code: LIVE_MIDI_PHASE_EXCLUSION };
    }
    if (state.livePhase === 'running' || state.livePhase === 'degraded') {
      liveLogger.info('live start ignored — already running', { phase: state.livePhase });
      return { ok: false, code: 'live_session_already_active' };
    }
    if (!isJamMode(state.jamMode)) {
      jamLogger.info('live start rejected — jam mode required', { mode: state.jamMode });
      set({
        liveErrorCode: 'jam_mode_required',
        liveErrorMessage: 'Select a jam mode (User melody or User chords) before Start.',
        livePhase: 'idle',
      });
      return { ok: false, code: 'jam_mode_required' };
    }

    const engineGate = requireLivePlaybackEngine();
    if (requireEngine && !engineGate.ok) {
      liveLogger.warn('live start rejected — engine unavailable', { code: engineGate.code });
      set({
        liveErrorCode: LIVE_ENGINE_UNAVAILABLE,
        liveErrorMessage: 'Playback engine is not ready for co-performance.',
        livePhase: 'idle',
      });
      return { ok: false, code: LIVE_ENGINE_UNAVAILABLE };
    }

    const id = sessionId || `live-${Date.now().toString(36)}`;
    const bounds = readLiveHorizonBounds();
    const horizon = {
      bars: Number(state.liveHorizonBars) || bounds.barsDefault,
      ms: Number(state.liveHorizonMs) || bounds.msDefault,
    };
    const jamMode = state.jamMode;
    const controls = state.jamControls || createDefaultJamControls();

    if (!liveMidiStreamSession) {
      liveMidiStreamSession = createLiveMidiStream({
        sessionId: id,
        getTick: () => getLiveClock(get().playbackSeconds, get().editedMusicJson).tick,
      });
    }
    const started = liveMidiStreamSession.start({
      sessionId: id,
      getTick: () => getLiveClock(get().playbackSeconds, get().editedMusicJson).tick,
    });
    if (!started.ok) {
      set({
        liveErrorCode: started.code || 'live_session_context_invalid',
        liveErrorMessage: 'Could not start live MIDI stream.',
      });
      return started;
    }

    if (engineGate.ok) {
      if (!liveAccompanimentSchedulerSession) {
        liveAccompanimentSchedulerSession = createLiveAccompanimentScheduler();
      }
      if (!livePatternEngineSession) {
        livePatternEngineSession = createLivePatternEngine({
          ticksPerBeat: Number(get().editedMusicJson?.ticks_per_quarter) || 480,
        });
      }
      if (!liveJamRoleEngineSession) {
        liveJamRoleEngineSession = createLiveJamRoleEngine({
          ticksPerBeat: Number(get().editedMusicJson?.ticks_per_quarter) || 480,
        });
      }
      if (!liveJamContextSession) {
        liveJamContextSession = createLiveJamContext({
          barTicks: (Number(get().editedMusicJson?.ticks_per_quarter) || 480) * 4,
        });
      }
      if (!liveLatencyTrackerSession) {
        liveLatencyTrackerSession = createLiveLatencyTracker();
      }
      liveAccompanimentSchedulerSession.setComposition(get().editedMusicJson);
      const schedStart = liveAccompanimentSchedulerSession.start();
      if (!schedStart.ok) {
        liveMidiStreamSession.cancel({ reason: 'scheduler-start-failed' });
        set({
          liveErrorCode: schedStart.code || LIVE_ENGINE_UNAVAILABLE,
          liveErrorMessage: 'Could not start live accompaniment scheduler.',
          livePhase: 'idle',
        });
        return schedStart;
      }
      // Seed jam roles into horizon immediately (hot path; no AI await).
      const clock = getLiveClock(get().playbackSeconds, get().editedMusicJson);
      const harmony = activeHarmonyAtTick(get().editedMusicJson, clock.tick);
      const seedBelief = updateHarmonyBelief(
        createIdleHarmonyBelief(),
        null,
        harmony,
        controls.responsiveness,
      );
      liveJamContextSession.refresh({
        belief: seedBelief,
        features: null,
        v2HarmonySpans: get().editedMusicJson?.harmony || [],
        nowTick: clock.tick,
      });
      liveAccompanimentSchedulerSession.maintainHorizonWithJam({
        playheadTick: clock.tick,
        belief: seedBelief,
        jamMode,
        controls,
        jamContext: liveJamContextSession.getSnapshot(),
        jamRoleEngine: liveJamRoleEngineSession,
        horizon,
      });
      set({ jamBelief: seedBelief, jamHarmonyHoldCount: getHarmonyHoldCount() });
    }

    const snapshot = createIdleLiveSession(id, { horizon, jam_mode: jamMode });
    snapshot.phase = 'running';
    snapshot.jam_controls = {
      complexity: controls.complexity,
      density: controls.density,
      style: controls.style,
      responsiveness: controls.responsiveness,
    };
    snapshot.belief = get().jamBelief || createIdleHarmonyBelief();
    set({
      livePhase: 'running',
      liveSessionId: id,
      liveHorizonBars: horizon.bars,
      liveHorizonMs: horizon.ms,
      liveStreamNoteOnCount: 0,
      liveStreamNoteOffCount: 0,
      liveStreamRingSize: 0,
      liveErrorCode: null,
      liveErrorMessage: '',
      liveSessionSnapshot: snapshot,
      jamPredictUnavailable: false,
    });
    liveLogger.info('live co-performance started', {
      sessionId: id.slice(0, 12),
      horizonBars: horizon.bars,
      horizonMs: horizon.ms,
      hasEngine: engineGate.ok,
      jamMode,
    });
    jamLogger.info('mode/control', {
      jamMode,
      complexity: controls.complexity,
      density: controls.density,
      style: controls.style,
    });
    return { ok: true, sessionId: id };
  },

  stopLiveCoPerformance: ({ reason = 'stop' } = {}) => {
    livePredictEpoch += 1;
    abortLivePredictInFlight(reason);
    if (!liveMidiStreamSession || !liveMidiStreamSession.isActive()) {
      const phase = get().livePhase;
      if (phase === 'idle' || phase === 'cancelled') {
        return { ok: false, phase };
      }
    }
    set({ livePhase: 'stopping' });
    if (liveAccompanimentSchedulerSession) {
      liveAccompanimentSchedulerSession.stop({ clearBuffer: false, reason });
    }
    // Freeze jam context for optional Commit harmony spans (Task 8).
    liveJamContextSession?.freeze?.();
    const result = liveMidiStreamSession
      ? liveMidiStreamSession.stop({ reason })
      : { ok: true, snapshot: null };
    set({
      livePhase: 'idle',
      liveStreamNoteOnCount: result.snapshot?.noteOnCount ?? get().liveStreamNoteOnCount,
      liveStreamNoteOffCount: result.snapshot?.noteOffCount ?? get().liveStreamNoteOffCount,
      liveStreamRingSize: result.snapshot?.ringSize ?? get().liveStreamRingSize,
      liveSessionSnapshot: result.snapshot
        ? {
          ...createIdleLiveSession(get().liveSessionId || 'live', {
            horizon: {
              bars: get().liveHorizonBars,
              ms: get().liveHorizonMs,
            },
            jam_mode: get().jamMode,
          }),
          phase: 'idle',
          belief: get().jamBelief,
          jam_flags: {
            predict_unavailable: Boolean(get().jamPredictUnavailable),
            harmony_hold_count: get().jamHarmonyHoldCount || 0,
          },
        }
        : get().liveSessionSnapshot,
    });
    liveLogger.info('live co-performance stopped', {
      reason,
      noteOnCount: result.snapshot?.noteOnCount ?? 0,
    });
    return { ok: true, reason, snapshot: result.snapshot };
  },

  cancelLiveCoPerformance: ({ reason = 'cancel' } = {}) => {
    livePredictEpoch += 1;
    abortLivePredictInFlight(reason);
    if (liveAccompanimentSchedulerSession) {
      liveAccompanimentSchedulerSession.cancel({ reason });
      liveAccompanimentSchedulerSession = null;
    }
    livePatternEngineSession = null;
    liveJamRoleEngineSession = null;
    if (liveJamContextSession) {
      liveJamContextSession.clear({ reason });
      liveJamContextSession = null;
    }
    liveLatencyTrackerSession = null;
    if (liveMidiStreamSession) {
      liveMidiStreamSession.cancel({ reason });
    }
    const preservedMode = get().jamMode;
    const preservedControls = get().jamControls;
    set({
      ...initialLivePerformanceState,
      liveHorizonBars: get().liveHorizonBars,
      liveHorizonMs: get().liveHorizonMs,
      jamMode: preservedMode,
      jamControls: preservedControls,
      livePhase: 'idle',
      liveErrorCode: null,
      liveErrorMessage: '',
    });
    liveLogger.info('live co-performance cancelled', { reason });
    return { ok: true, reason };
  },

  setJamMode: (mode) => {
    if (mode == null || mode === '') {
      set({ jamMode: null });
      jamLogger.info('mode clear');
      return { ok: true, jamMode: null };
    }
    if (!isJamMode(mode)) {
      jamLogger.info('mode reject', { mode: String(mode).slice(0, 32) });
      return { ok: false, code: 'jam_mode_invalid' };
    }
    if (get().livePhase === 'running' || get().livePhase === 'degraded') {
      return { ok: false, code: 'live_session_already_active' };
    }
    set({ jamMode: mode });
    jamLogger.info('mode set', { jamMode: mode });
    return { ok: true, jamMode: mode };
  },

  setJamControls: (partial = {}) => {
    const merged = {
      ...(get().jamControls || createDefaultJamControls()),
      ...(partial && typeof partial === 'object' ? partial : {}),
    };
    const clamped = clampJamControls(merged);
    if (!clamped.ok) {
      return { ok: false, code: clamped.code };
    }
    set({ jamControls: clamped.controls });
    jamLogger.info('controls set', {
      complexity: clamped.controls.complexity,
      density: clamped.controls.density,
      style: clamped.controls.style,
      responsiveness: clamped.controls.responsiveness,
    });
    return { ok: true, controls: clamped.controls };
  },

  /**
   * Warm-path horizon maintain (rAF / position poll). Never awaits predict.
   * When jamMode is set: features → belief → jamContext → maintainHorizonWithJam.
   */
  pumpLiveAccompaniment: () => {
    if (get().livePhase !== 'running' && get().livePhase !== 'degraded') {
      return { ok: false };
    }
    if (!liveAccompanimentSchedulerSession) {
      return { ok: false };
    }
    const jamMode = get().jamMode;
    const useJam = isJamMode(jamMode) && liveJamRoleEngineSession;
    if (!useJam && !livePatternEngineSession) {
      return { ok: false };
    }

    const clock = getLiveClock(get().playbackSeconds, get().editedMusicJson);
    const harmony = activeHarmonyAtTick(get().editedMusicJson, clock.tick);
    const controls = get().jamControls || createDefaultJamControls();
    const horizon = {
      bars: get().liveHorizonBars,
      ms: get().liveHorizonMs,
    };

    let belief = get().jamBelief || createIdleHarmonyBelief();
    let features = get().jamLastFeatures;
    let result;

    if (useJam) {
      const jamSettings = readLiveJamSettings();
      const fromTick = Math.max(0, clock.tick - jamSettings.analysisMaxTicks);
      const recent = liveMidiStreamSession?.getRecentEvents?.({
        fromTick,
        maxEvents: jamSettings.analysisMaxEvents,
      }) || [];
      features = extractLivePerformanceFeatures({
        events: recent,
        clock: {
          tick: clock.tick,
          bar: clock.bar,
          beatInBar: clock.beatInBar,
          tickInBar: clock.tickInBar,
          ticksPerBeat: clock.ticksPerBeat,
          tempo: clock.tempo,
        },
        settings: jamSettings,
        latencyTracker: liveLatencyTrackerSession,
      });
      belief = updateHarmonyBelief(
        belief,
        features,
        harmony,
        controls.responsiveness,
        jamSettings,
      );
      if (!liveJamContextSession) {
        liveJamContextSession = createLiveJamContext({
          barTicks: (Number(get().editedMusicJson?.ticks_per_quarter) || 480) * 4,
        });
      }
      liveJamContextSession.refresh({
        belief,
        features,
        v2HarmonySpans: get().editedMusicJson?.harmony || [],
        nowTick: clock.tick,
      });
      liveLatencyTrackerSession?.markStart('scheduling');
      result = liveAccompanimentSchedulerSession.maintainHorizonWithJam({
        playheadTick: clock.tick,
        belief,
        jamMode,
        controls,
        jamContext: liveJamContextSession.getSnapshot(),
        jamRoleEngine: liveJamRoleEngineSession,
        horizon,
      });
      liveLatencyTrackerSession?.markEnd('scheduling');
      set({
        jamBelief: belief,
        jamLastFeatures: features,
        jamHarmonyHoldCount: getHarmonyHoldCount(),
      });
    } else {
      liveLatencyTrackerSession?.markStart('scheduling');
      result = liveAccompanimentSchedulerSession.maintainHorizonWithPattern({
        playheadTick: clock.tick,
        harmonySymbol: harmony.symbol,
        horizon,
        patternEngine: livePatternEngineSession,
      });
      liveLatencyTrackerSession?.markEnd('scheduling');
    }

    // Cold-path AI fill — never awaited here.
    if (result.coverage?.needsFill || result.degraded) {
      requestLivePredictFill(get, set, {
        clock,
        harmony,
        horizon,
        sessionId: get().liveSessionId || 'live',
      });
    }

    const deg = result.degradation
      || (useJam
        ? liveJamRoleEngineSession?.getDegradation?.()
        : livePatternEngineSession?.getDegradation?.());
    if (deg?.active && get().livePhase === 'running') {
      set({ livePhase: 'degraded' });
      liveMidiStreamSession?.markDegraded?.();
    } else if (!deg?.active && get().livePhase === 'degraded') {
      set({ livePhase: 'running' });
      liveMidiStreamSession?.clearDegraded?.();
    }
    const latency = liveLatencyTrackerSession?.snapshot() || null;
    if (get().liveSessionSnapshot) {
      set({
        liveSessionSnapshot: {
          ...get().liveSessionSnapshot,
          phase: get().livePhase,
          jam_mode: jamMode,
          jam_controls: {
            complexity: controls.complexity,
            density: controls.density,
            style: controls.style,
            responsiveness: controls.responsiveness,
          },
          transport: {
            playing: get().playbackStatus === 'playing',
            tick: clock.tick,
            bar: clock.bar,
            beat: clock.beatInBar,
          },
          active_harmony: {
            symbol: belief?.symbol ?? harmony.symbol,
            start_tick: harmony.start_tick,
            duration_ticks: harmony.duration_ticks,
          },
          belief: {
            symbol: belief?.symbol ?? null,
            confidence: belief?.confidence ?? 0,
            held: Boolean(belief?.held),
            reason_code: belief?.reason_code ?? null,
          },
          degradation: {
            active: Boolean(deg?.active),
            code: deg?.code || null,
            count: deg?.count || 0,
          },
          jam_flags: {
            predict_unavailable: Boolean(get().jamPredictUnavailable),
            harmony_hold_count: get().jamHarmonyHoldCount || 0,
          },
          latency_ms: latency
            ? {
              midi_input: latency.midi_input,
              analysis: latency.analysis,
              generation: latency.generation,
              scheduling: latency.scheduling,
            }
            : get().liveSessionSnapshot.latency_ms,
        },
      });
    }
    return result;
  },

  setLiveHorizon: ({ bars = null, ms = null } = {}) => {
    const bounds = readLiveHorizonBounds();
    const nextBars = bars == null
      ? get().liveHorizonBars
      : Math.max(bounds.barsMin, Math.min(bounds.barsMax, Number(bars)));
    const nextMs = ms == null
      ? get().liveHorizonMs
      : Math.max(bounds.msMin, Math.min(bounds.msMax, Number(ms)));
    set({ liveHorizonBars: nextBars, liveHorizonMs: nextMs });
  },

  /**
   * Explicit Commit of stream and/or accompaniment into one V2 transaction.
   * When jamMode is set: multi-track role map + optional ensure/harmony spans.
   * When jamMode is null: single-track co-performance Commit (non-jam opt-in).
   */
  commitLiveCoPerformance: ({
    trackId = null,
    roleTracks = null,
    ensureMissingTracks = false,
    commitHarmonySpans = undefined,
    includeStream = true,
    includeAccompaniment = true,
  } = {}) => {
    const state = get();
    const destination =
      trackId
      || state.midiDestinationTrackId
      || state.pianoRollTrackId
      || (state.editedMusicJson?.tracks?.[0]?.id ?? null);

    const streamNotes = includeStream && liveMidiStreamSession
      ? liveMidiStreamSession.getClosedNotes()
      : [];
    const accompanimentEvents = includeAccompaniment && liveAccompanimentSchedulerSession
      ? liveAccompanimentSchedulerSession.getBuffer().getEvents()
      : [];

    const jamMode = state.jamMode;
    if (isJamMode(jamMode)) {
      const controls = state.jamControls || createDefaultJamControls();
      const jamCtx = liveJamContextSession?.getSnapshot?.() || null;
      const plannedWindow = jamCtx?.planned_window || [];
      const harmonyDefault = defaultCommitHarmonySpans(jamMode);
      const applied = applyAiJamTakeToComposition(state.editedMusicJson, {
        jamMode,
        userTrackId: destination,
        roleTracks: roleTracks || {},
        streamNotes,
        accompanimentEvents,
        ensureMissingTracks: Boolean(ensureMissingTracks),
        commitHarmonySpans: commitHarmonySpans != null
          ? Boolean(commitHarmonySpans)
          : harmonyDefault,
        plannedWindow,
        instrumentSet: controls.instrument_set || {},
        complexity: controls.complexity || 'medium',
        lockedTrackIds: state.lockedTrackIds,
      });
      if (!applied.ok) {
        jamLogger.warn('jam commit failed', { code: applied.code });
        set({
          liveErrorCode: applied.code,
          liveErrorMessage: applied.message || applied.code,
        });
        return { ok: false, code: applied.code };
      }

      const primary = applied.noteRefs[0] || null;
      const affectedTracks = new Set(applied.noteRefs.map((ref) => ref.trackId));
      const selectedTrack = applied.userTrackId || destination || [...affectedTracks][0] || null;
      const ok = commitCompositionTransaction(set, get, {
        nextComposition: applied.composition,
        selectedTrackId: selectedTrack,
        selectedNoteId: primary?.eventId || null,
        selectedNoteIds: applied.noteRefs
          .filter((ref) => ref.trackId === selectedTrack)
          .map((ref) => ref.eventId),
        editorSelectionRefs: applied.noteRefs,
        editorSelectionPrimary: primary,
        action: 'ai-jam-commit',
        noteSummary: {
          noteCount: applied.noteRefs.length,
          barsAdded: applied.barsAdded,
          userCount: applied.userCount,
          roleCounts: applied.roleCounts,
          harmonySpansCommitted: applied.harmonySpansCommitted,
        },
        affectedNoteCount: applied.noteRefs.length,
        affectedTrackCount: Math.max(1, affectedTracks.size),
        statePatch: {
          liveErrorCode: null,
          liveErrorMessage: '',
        },
      });
      if (!ok) {
        return { ok: false, code: 'live_commit_failed' };
      }
      jamLogger.info('jam commit ok', {
        userCount: applied.userCount,
        roleCounts: applied.roleCounts,
        tracksEnsured: (applied.tracksEnsured || []).length,
        harmonySpansCommitted: applied.harmonySpansCommitted || 0,
        trackIdPrefixes: [...affectedTracks].map((id) => String(id).slice(0, 16)),
      });
      get().cancelLiveCoPerformance({ reason: 'commit' });
      return {
        ok: true,
        noteCount: applied.noteRefs.length,
        userCount: applied.userCount,
        roleCounts: applied.roleCounts,
      };
    }

    // Non-jam single-track Commit.
    if (!destination) {
      liveLogger.warn('live commit rejected — no destination', { code: 'midi_no_destination' });
      set({
        liveErrorCode: 'midi_no_destination',
        liveErrorMessage: 'Select a destination track before commit.',
      });
      return { ok: false, code: 'midi_no_destination' };
    }

    const applied = applyCoPerformanceTakeToComposition(state.editedMusicJson, {
      trackId: destination,
      streamNotes,
      accompanimentEvents,
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!applied.ok) {
      liveLogger.warn('live commit failed', { code: applied.code });
      set({
        liveErrorCode: applied.code,
        liveErrorMessage: applied.message || applied.code,
      });
      return { ok: false, code: applied.code };
    }

    const primary = applied.noteRefs[0] || null;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: applied.composition,
      selectedTrackId: destination,
      selectedNoteId: primary?.eventId || null,
      selectedNoteIds: applied.noteRefs
        .filter((ref) => ref.trackId === destination)
        .map((ref) => ref.eventId),
      editorSelectionRefs: applied.noteRefs,
      editorSelectionPrimary: primary,
      action: 'co-performance-commit',
      noteSummary: {
        noteCount: applied.noteRefs.length,
        barsAdded: applied.barsAdded,
      },
      affectedNoteCount: applied.noteRefs.length,
      affectedTrackCount: 1,
      statePatch: {
        liveErrorCode: null,
        liveErrorMessage: '',
      },
    });
    if (!ok) {
      return { ok: false, code: 'live_commit_failed' };
    }
    liveLogger.info('live commit ok', {
      noteCount: applied.noteRefs.length,
      trackId: String(destination).slice(0, 24),
    });
    get().cancelLiveCoPerformance({ reason: 'commit' });
    return { ok: true, noteCount: applied.noteRefs.length };
  },

  /**
   * Test/QWERTY injection path — same capture + active-note handling as Web MIDI.
   * @param {Iterable<number> | ArrayLike<number>} data
   * @param {{ atMs?: number }} [meta]
   */
  injectMidiMessage: (data, meta = {}) => {
    handleLiveMidiMessage(set, get, {
      data,
      timeStamp: meta.atMs,
    });
  },

  startMidiRecording: ({ originTick = null, skipCountIn = false, Tone = null } = {}) => {
    const state = get();
    const liveGuard = assertMidiCaptureAllowedForLivePhase(state.livePhase);
    if (!liveGuard.ok) {
      midiLogger.warn('MIDI start rejected — live session exclusion', {
        code: liveGuard.code,
        livePhase: state.livePhase,
      });
      set({
        midiErrorCode: LIVE_MIDI_PHASE_EXCLUSION,
        midiErrorMessage: 'Stop co-performance before recording MIDI.',
      });
      return false;
    }
    if (
      state.midiPhase !== MIDI_PHASES.ARMED
      && state.midiPhase !== MIDI_PHASES.READY
      && !(state.midiTestKeyboardEnabled && (
        state.midiPhase === MIDI_PHASES.IDLE
        || state.midiPhase === MIDI_PHASES.UNAVAILABLE
      ))
    ) {
      midiLogger.warn('MIDI start rejected — invalid phase', { phase: state.midiPhase });
      return false;
    }
    if (
      (state.midiPhase === MIDI_PHASES.IDLE || state.midiPhase === MIDI_PHASES.UNAVAILABLE)
      && state.midiTestKeyboardEnabled
    ) {
      transitionMidiPhase(set, get, MIDI_PHASES.READY, 'test-keyboard-ready');
      set({ midiAccessStatus: 'ready' });
    }
    if (get().midiPhase === MIDI_PHASES.READY && !get().midiArmed) {
      if (!get().armMidiRecording()) {
        return false;
      }
    }

    const composition = get().editedMusicJson;
    const origin = originTick != null
      ? Math.max(0, Math.round(Number(originTick)))
      : Math.max(0, Math.round(Number(get().editCursorTick) || 0));
    const countInBars = skipCountIn ? 0 : Math.max(0, Number(get().midiCountInBars) || 0);
    const metronomeEnabled = Boolean(get().midiMetronomeEnabled);

    clearMidiCountInTimer();
    midiPendingTake = null;

    if (metronomeEnabled || countInBars > 0) {
      disposeMidiMetronome();
      midiMetronomeSession = createMidiMetronome({
        Tone,
        composition,
      });
      midiMetronomeSession.schedule({
        composition,
        countInBars,
        continueDuringRecord: metronomeEnabled,
      });
    }

    if (countInBars > 0) {
      if (!transitionMidiPhase(set, get, MIDI_PHASES.COUNTING_IN, 'count-in')) {
        return false;
      }
      const tempo = Number(composition?.tempo) || 100;
      const meter = composition?.time_signature || '4/4';
      const tpq = Number(composition?.ticks_per_quarter) || 480;
      const barTicks = barDurationTicks(meter, tpq) || (tpq * 4);
      const secondsPerBar = (barTicks / tpq) * (60 / tempo);
      const delayMs = Math.max(0, countInBars * secondsPerBar * 1000);
      midiLogger.info('MIDI count-in scheduled', {
        countInBars,
        delayMs: Math.round(delayMs),
        bpm: tempo,
        metronomeEnabled,
      });
      midiCountInTimer = setTimeout(() => {
        midiCountInTimer = null;
        if (get().midiPhase !== MIDI_PHASES.COUNTING_IN) {
          return;
        }
        beginMidiCapture(set, get, { originTick: origin });
      }, delayMs);
      return true;
    }

    return beginMidiCapture(set, get, { originTick: origin });
  },

  stopMidiRecording: ({ commit = true } = {}) => {
    clearMidiCountInTimer();
    disposeMidiMetronome();
    const state = get();
    if (
      state.midiPhase !== MIDI_PHASES.RECORDING
      && state.midiPhase !== MIDI_PHASES.COUNTING_IN
      && !midiPendingTake
    ) {
      midiLogger.warn('MIDI stop ignored — not capturing', { phase: state.midiPhase });
      return false;
    }

    if (!transitionMidiPhase(set, get, MIDI_PHASES.STOPPING, 'stop')) {
      // Allow stop from counting_in even if transition table is strict
      if (state.midiPhase === MIDI_PHASES.COUNTING_IN) {
        set({ midiPhase: MIDI_PHASES.STOPPING });
      } else {
        return false;
      }
    }

    let take = midiPendingTake;
    if (midiCaptureSession && midiCaptureSession.isCapturing()) {
      take = midiCaptureSession.stop();
      midiPendingTake = take;
    }

    set({ midiActiveNotes: [], midiArmed: false });

    if (!commit) {
      midiPendingTake = null;
      if (midiCaptureSession) {
        midiCaptureSession.discard();
      }
      transitionMidiPhase(set, get, MIDI_PHASES.READY, 'stop-discard');
      set({ midiTakeSummary: null });
      midiLogger.info('MIDI recording stopped without commit');
      return true;
    }

    const ok = commitMidiTakeBuffer(set, get, take);
    if (!ok) {
      transitionMidiPhase(set, get, MIDI_PHASES.READY, 'stop-commit-failed');
    }
    return ok;
  },

  commitPendingMidiTake: () => {
    if (!midiPendingTake) {
      midiLogger.warn('No pending MIDI take to commit');
      return false;
    }
    return commitMidiTakeBuffer(set, get, midiPendingTake);
  },

  discardMidiTake: () => {
    clearMidiCountInTimer();
    midiPendingTake = null;
    if (midiCaptureSession) {
      midiCaptureSession.discard();
    }
    set({
      midiTakeSummary: null,
      midiActiveNotes: [],
      midiArmed: false,
      midiErrorCode: null,
      midiErrorMessage: '',
    });
    if (
      get().midiPhase === MIDI_PHASES.RECORDING
      || get().midiPhase === MIDI_PHASES.COUNTING_IN
      || get().midiPhase === MIDI_PHASES.STOPPING
      || get().midiPhase === MIDI_PHASES.ARMED
    ) {
      transitionMidiPhase(set, get, MIDI_PHASES.READY, 'discard');
    }
    midiLogger.info('MIDI take discarded');
    return true;
  },

  quantizeMidiTakeNotes: (noteRefs, options = {}) => {
    const state = get();
    const refs = Array.isArray(noteRefs) ? noteRefs : [];
    if (!refs.length || !state.editedMusicJson) {
      midiLogger.info('MIDI quantize skipped', { reason: 'no_refs' });
      return false;
    }
    const snapValue = options.snapValue || state.pianoRollSnap || '1/8';
    const strength = options.strength != null ? options.strength : 100;
    const result = quantizeNotes(state.editedMusicJson, refs, {
      mode: options.mode || 'start',
      snapValue,
      strength,
      lockedTrackIds: state.lockedTrackIds,
    });
    if (!result.ok) {
      midiLogger.warn('MIDI quantize rejected', {
        code: result.code,
        message: result.message,
      });
      return false;
    }
    if (result.noOp) {
      midiLogger.info('MIDI quantize no-op', { refCount: refs.length, snap: snapValue });
      return true;
    }
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: result.composition,
      selectedTrackId: state.midiDestinationTrackId || state.pianoRollTrackId,
      editorSelectionRefs: refs,
      editorSelectionPrimary: refs[0] || null,
      action: 'midi-quantize',
      noteSummary: { quantizedCount: result.quantizedCount, snap: snapValue, strength },
      affectedNoteCount: result.quantizedCount,
      affectedTrackCount: 1,
    });
    midiLogger.info('MIDI quantize applied', {
      refCount: refs.length,
      snap: snapValue,
      strength,
      ok,
    });
    return ok;
  },

  resetMidiInputSession: () => {
    clearMidiCountInTimer();
    disposeMidiMetronome();
    disposeMidiInputSubscription();
    if (midiAccessUnsubscribe) {
      midiAccessUnsubscribe();
      midiAccessUnsubscribe = null;
    }
    if (midiAccessSession) {
      midiAccessSession.dispose();
      midiAccessSession = null;
    }
    midiPendingTake = null;
    if (midiCaptureSession) {
      midiCaptureSession.discard();
      midiCaptureSession = null;
    }
    set({ ...initialMidiInputState });
    midiLogger.info('MIDI session reset');
  },

  probeAudioSupport: () => {
    const support = probeAudioInputSupport();
    set({ audioSupport: support });
    return support;
  },

  setAudioDestinationTrackId: (trackId) => {
    const next = trackId == null ? null : String(trackId);
    audioLogger.debug('Audio destination track set', { trackId: next });
    set({ audioDestinationTrackId: next });
  },

  setAudioIncludeLowConfidence: (enabled) => {
    const include = Boolean(enabled);
    const state = get();
    const preview = state.audioPreview;
    let selected = state.audioSelectedProvisionalIds;
    if (preview) {
      if (include) {
        selected = (preview.notes || []).map((n) => String(n.provisional_id));
      } else {
        selected = defaultSelectedProvisionalIds(
          preview,
          state.audioConfidenceThreshold,
        );
      }
    }
    audioLogger.info('Audio include-low-confidence toggled', {
      include,
      selectedCount: selected.length,
    });
    set({
      audioIncludeLowConfidence: include,
      audioSelectedProvisionalIds: selected,
    });
  },

  setAudioQuantizeOnApply: (enabled) => {
    set({ audioQuantizeOnApply: Boolean(enabled) });
  },

  setAudioSelectedProvisionalIds: (ids) => {
    const next = Array.isArray(ids) ? ids.map(String) : [];
    set({ audioSelectedProvisionalIds: next });
  },

  toggleAudioProvisionalId: (provisionalId) => {
    const id = String(provisionalId);
    const current = new Set(get().audioSelectedProvisionalIds.map(String));
    if (current.has(id)) {
      current.delete(id);
    } else {
      current.add(id);
    }
    set({ audioSelectedProvisionalIds: Array.from(current) });
  },

  startAudioRecording: async () => {
    const state = get();
    const recoveryGuard = assertMonoAudioAllowedForRecoveryPhase(state.recoveryPhase);
    if (!recoveryGuard.ok) {
      audioLogger.info('Audio record rejected — recovery phase exclusion', {
        recoveryPhase: state.recoveryPhase,
        reason: recoveryGuard.reason,
      });
      return { ok: false, code: recoveryGuard.code, reason: recoveryGuard.reason };
    }
    if (state.audioPhase === AUDIO_PHASES.RECORDING) {
      return { ok: false, code: 'already_recording' };
    }
    transitionAudioPhase(set, get, AUDIO_PHASES.REQUESTING_MIC, 'record-start');
    set({ audioErrorCode: null, audioErrorMessage: '' });
    if (!audioRecorderSession) {
      audioRecorderSession = createAudioRecorder();
    }
    const result = await audioRecorderSession.start();
    if (!result.ok) {
      audioLogger.warn('Audio record start failed', { code: result.code });
      set({
        audioPhase: AUDIO_PHASES.ERROR,
        audioErrorCode: result.code,
        audioErrorMessage: result.code === 'permission_denied'
          ? 'Microphone permission denied.'
          : 'Microphone recording is unavailable.',
      });
      return result;
    }
    transitionAudioPhase(set, get, AUDIO_PHASES.RECORDING, 'recording');
    return { ok: true };
  },

  stopAudioRecordingAndTranscribe: async () => {
    if (!audioRecorderSession || !audioRecorderSession.isRecording()) {
      return { ok: false, code: 'not_recording' };
    }
    const stopped = await audioRecorderSession.stop();
    if (!stopped.ok) {
      set({
        audioPhase: AUDIO_PHASES.ERROR,
        audioErrorCode: stopped.code,
        audioErrorMessage: 'Failed to encode recording.',
      });
      return stopped;
    }
    return get().transcribeAudioBlob(stopped.blob);
  },

  cancelAudioRecording: () => {
    if (audioRecorderSession) {
      audioRecorderSession.cancel();
    }
    transitionAudioPhase(set, get, AUDIO_PHASES.IDLE, 'cancel-record');
    return { ok: true };
  },

  transcribeAudioFile: async (file) => {
    if (!file) {
      return { ok: false, code: 'audio_empty_upload' };
    }
    return get().transcribeAudioBlob(file);
  },

  transcribeAudioBlob: async (blob) => {
    const state = get();
    const composition = state.editedMusicJson;
    transitionAudioPhase(set, get, AUDIO_PHASES.UPLOADING, 'upload');
    set({ audioErrorCode: null, audioErrorMessage: '' });
    audioLogger.info('Audio transcription upload starting', {
      uploadBytes: blob?.size ?? null,
    });
    transitionAudioPhase(set, get, AUDIO_PHASES.TRANSCRIBING, 'transcribe');
    try {
      const tempoBpm = composition?.tempo_bpm != null
        ? Number(composition.tempo_bpm)
        : null;
      const ticksPerQuarter = composition?.ticks_per_quarter != null
        ? Number(composition.ticks_per_quarter)
        : null;
      const result = await transcribeAudio(blob, {
        tempoBpm,
        ticksPerQuarter,
        originTick: state.editCursorTick || 0,
      });
      const preview = result.preview;
      const threshold = Number(preview.summary?.include_threshold)
        || state.audioConfidenceThreshold
        || DEFAULT_AUDIO_CONFIDENCE_THRESHOLD;
      const selected = defaultSelectedProvisionalIds(preview, threshold);
      const destination = state.audioDestinationTrackId
        || state.pianoRollTrackId
        || pickDefaultTrackId(composition);
      audioLogger.info('Audio transcription preview ready', {
        noteCount: preview.summary?.note_count ?? preview.notes?.length ?? 0,
        lowConfidenceCount: preview.summary?.low_confidence_count ?? 0,
        selectedCount: selected.length,
        engineId: result.engine?.id || preview.engine?.id || null,
      });
      set({
        audioPhase: AUDIO_PHASES.REVIEW,
        audioPreview: preview,
        audioSelectedProvisionalIds: selected,
        audioConfidenceThreshold: threshold,
        audioIncludeLowConfidence: false,
        audioDestinationTrackId: destination,
        audioErrorCode: null,
        audioErrorMessage: '',
      });
      return { ok: true, preview };
    } catch (error) {
      const code = error instanceof TranscriptionApiError
        ? (error.code || 'audio_internal_error')
        : 'audio_internal_error';
      audioLogger.warn('Audio transcription failed', {
        code,
        status: error.status || null,
      });
      set({
        audioPhase: AUDIO_PHASES.ERROR,
        audioErrorCode: code,
        audioErrorMessage: error.message || 'Transcription failed',
      });
      return { ok: false, code, message: error.message };
    }
  },

  applyAudioTranscription: () => {
    const state = get();
    if (!state.audioPreview || state.audioPhase !== AUDIO_PHASES.REVIEW) {
      audioLogger.warn('Audio apply rejected — not in review', {
        phase: state.audioPhase,
      });
      return { ok: false, code: 'invalid_phase' };
    }
    const trackId = state.audioDestinationTrackId
      || state.pianoRollTrackId
      || pickDefaultTrackId(state.editedMusicJson);
    if (!trackId) {
      set({
        audioErrorCode: 'audio_track_missing',
        audioErrorMessage: 'Select a destination track before applying.',
      });
      return { ok: false, code: 'audio_track_missing' };
    }
    transitionAudioPhase(set, get, AUDIO_PHASES.APPLYING, 'apply');
    const applied = applyAudioTranscriptionToComposition(state.editedMusicJson, {
      trackId,
      preview: state.audioPreview,
      selectedIds: state.audioSelectedProvisionalIds,
      includeLowConfidence: state.audioIncludeLowConfidence,
      quantize: state.audioQuantizeOnApply,
      snapValue: state.pianoRollSnap || '1/8',
      lockedTrackIds: state.lockedTrackIds,
      threshold: state.audioConfidenceThreshold,
    });
    if (!applied.ok) {
      audioLogger.warn('Audio apply failed', { code: applied.code });
      set({
        audioPhase: AUDIO_PHASES.REVIEW,
        audioErrorCode: applied.code,
        audioErrorMessage: applied.message || applied.code,
      });
      return applied;
    }
    const primary = applied.noteRefs[0] || null;
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: applied.composition,
      selectedTrackId: trackId,
      selectedNoteId: primary?.eventId || null,
      selectedNoteIds: applied.noteRefs
        .filter((ref) => ref.trackId === trackId)
        .map((ref) => ref.eventId),
      editorSelectionRefs: applied.noteRefs,
      editorSelectionPrimary: primary,
      action: 'audio-transcribe',
      noteSummary: {
        noteCount: applied.noteRefs.length,
        excludedLow: applied.excludedLow,
        quantize: state.audioQuantizeOnApply,
        barsAdded: applied.barsAdded,
      },
      affectedNoteCount: applied.noteRefs.length,
      affectedTrackCount: 1,
      statePatch: {
        ...clearedAudioTranscriptionState(),
        audioDestinationTrackId: trackId,
      },
    });
    audioLogger.info('Audio transcription applied to composition', {
      noteCount: applied.noteRefs.length,
      excludedLow: applied.excludedLow,
      quantize: state.audioQuantizeOnApply,
      ok,
    });
    return { ok, noteRefs: applied.noteRefs, barsAdded: applied.barsAdded };
  },

  discardAudioTranscription: () => {
    if (audioRecorderSession) {
      audioRecorderSession.cancel();
    }
    audioLogger.info('Audio transcription discarded');
    set({ ...clearedAudioTranscriptionState() });
    return { ok: true };
  },

  setRecoveryDisableSeparation: (disabled) => {
    set({ recoveryDisableSeparation: Boolean(disabled) });
  },

  setRecoveryIncludeLowConfidence: (enabled) => {
    set({ recoveryIncludeLowConfidence: Boolean(enabled) });
  },

  setRecoverySelectedProvisionalIds: (ids) => {
    set({ recoverySelectedProvisionalIds: Array.isArray(ids) ? ids.map(String) : [] });
  },

  setRecoveryStemRoleMap: (map) => {
    set({
      recoveryStemRoleMap: map && typeof map === 'object' && !Array.isArray(map) ? { ...map } : {},
    });
  },

  setRecoveryStemSolo: (stem) => {
    set({ recoveryStemSolo: stem == null ? null : String(stem) });
  },

  setRecoveryInstallFlags: (flags) => {
    set({
      recoveryInstallFlags: flags && typeof flags === 'object' ? { ...flags } : null,
    });
  },

  startRecoveryRecording: async () => {
    const state = get();
    const guard = assertRecoveryAllowedForOtherPhases(
      state.midiPhase,
      state.livePhase,
      state.audioPhase,
    );
    if (!guard.ok) {
      audioRecoveryLogger.info('Recovery record rejected — phase exclusion', {
        reason: guard.reason,
      });
      return { ok: false, code: guard.code, reason: guard.reason };
    }
    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.REQUESTING_MIC, 'record-start');
    set({ recoveryErrorCode: null, recoveryErrorMessage: '', recoveryBindWarning: null });
    if (!recoveryRecorderSession) {
      recoveryRecorderSession = createAudioRecorder();
    }
    const result = await recoveryRecorderSession.start();
    if (!result.ok) {
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.ERROR,
        recoveryErrorCode: result.code,
        recoveryErrorMessage: result.code === 'permission_denied'
          ? 'Microphone permission denied.'
          : 'Microphone recording is unavailable.',
      });
      return result;
    }
    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.RECORDING, 'recording');
    return { ok: true };
  },

  stopRecoveryRecordingAndEnqueue: async () => {
    if (!recoveryRecorderSession || !recoveryRecorderSession.isRecording()) {
      return { ok: false, code: 'not_recording' };
    }
    const stopped = await recoveryRecorderSession.stop();
    if (!stopped.ok) {
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.ERROR,
        recoveryErrorCode: stopped.code,
        recoveryErrorMessage: 'Failed to encode recording.',
      });
      return stopped;
    }
    return get().enqueueRecoveryBlob(stopped.blob);
  },

  cancelRecoveryRecording: () => {
    if (recoveryRecorderSession) {
      recoveryRecorderSession.cancel();
    }
    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.IDLE, 'cancel-record');
    return { ok: true };
  },

  enqueueRecoveryFile: async (file) => {
    if (!file) {
      return { ok: false, code: 'audio_empty_upload' };
    }
    return get().enqueueRecoveryBlob(file);
  },

  enqueueRecoveryBlob: async (blob) => {
    const state = get();
    const guard = assertRecoveryAllowedForOtherPhases(
      state.midiPhase,
      state.livePhase,
      state.audioPhase,
    );
    if (!guard.ok) {
      audioRecoveryLogger.info('Recovery upload rejected — phase exclusion', {
        reason: guard.reason,
      });
      return { ok: false, code: guard.code, reason: guard.reason };
    }
    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.UPLOADING, 'upload');
    set({ recoveryErrorCode: null, recoveryErrorMessage: '', recoveryBindWarning: null });
    try {
      transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.RUNNING, 'job');
      const job = await enqueueAudioRecoveryJob(blob, {
        projectId: state.currentProjectId,
        disableSeparation: state.recoveryDisableSeparation,
      });
      let finalJob = job;
      if (job?.status === 'queued' || job?.status === 'running') {
        finalJob = await getAudioRecoveryJob(job.id);
      }
      if (finalJob?.status === 'failed') {
        set({
          recoveryPhase: AUDIO_RECOVERY_PHASES.ERROR,
          recoveryJobId: finalJob.id,
          recoveryJobStatus: finalJob.status,
          recoveryErrorCode: finalJob.error_code || 'audio_recovery_job_failed',
          recoveryErrorMessage: finalJob.error_message || 'Recovery job failed.',
        });
        return { ok: false, code: finalJob.error_code || 'audio_recovery_job_failed' };
      }
      const preview = finalJob?.preview || null;
      const threshold = Number(preview?.summary?.include_threshold)
        || state.recoveryConfidenceThreshold
        || DEFAULT_RECOVERY_CONFIDENCE_THRESHOLD;
      const selected = defaultSelectedRecoveryProvisionalIds(preview, threshold);
      const installFlags = defaultScaffoldingInstallFlags(preview?.scaffolding, threshold);
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.REVIEW,
        recoveryJobId: finalJob?.id || null,
        recoveryJobStatus: finalJob?.status || null,
        recoveryPreview: preview,
        recoverySelectedProvisionalIds: selected,
        recoveryInstallFlags: installFlags,
        recoveryConfidenceThreshold: threshold,
        recoveryErrorCode: null,
        recoveryErrorMessage: '',
      });
      audioRecoveryLogger.info('Recovery job ready for review', {
        jobId: finalJob?.id,
        noteCount: preview?.summary?.note_count ?? null,
        stemCount: preview?.summary?.stem_count ?? null,
      });
      return { ok: true, job: finalJob };
    } catch (error) {
      const code = error instanceof AudioRecoveryApiError
        ? error.code
        : 'audio_recovery_error';
      audioRecoveryLogger.warn('Recovery enqueue failed', { code });
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.ERROR,
        recoveryErrorCode: code,
        recoveryErrorMessage: error?.message || 'Audio recovery failed.',
      });
      return { ok: false, code };
    }
  },

  setSourceAuditionMode: (mode) => {
    const next = ['idle', 'playing', 'scrubbing'].includes(mode) ? mode : 'idle';
    const prev = get().sourceAuditionMode;
    if (prev === next) return next;
    set({ sourceAuditionMode: next });
    audioAlignmentLogger.info('Source audition mode', { from: prev, to: next });
    return next;
  },

  /**
   * HTMLAudio timeupdate → sourcePlayheadTick via alignment sync clock.
   * Does not touch Tone playbackSource.
   */
  onSourceAudioTimeUpdate: (currentTimeSeconds) => {
    const state = get();
    if (!state.alignmentDocument || !state.editedMusicJson) {
      return null;
    }
    const timeline = compileTimeline(state.editedMusicJson);
    if (!timeline) return null;
    const opts = alignmentMapOpts(state.alignmentDocument);
    const tick = sourceSecondsToTick(timeline, Number(currentTimeSeconds), opts);
    if (tick == null) return null;
    if (tick === state.sourcePlayheadTick) return tick;
    set({ sourcePlayheadTick: tick });
    const now = Date.now();
    if (now - lastSourceSeekDebugAt > 400) {
      lastSourceSeekDebugAt = now;
      audioAlignmentLogger.debug('source timeupdate', {
        tick,
        seconds: Number(Number(currentTimeSeconds).toFixed(3)),
      });
    }
    return tick;
  },

  /**
   * Seek source HTMLAudio to a composition tick (via sourceSeekRequest).
   */
  seekSourceAudioToTick: (tick, { reason = 'seek-tick' } = {}) => {
    const state = get();
    if (!state.alignmentDocument) {
      audioAlignmentLogger.warn('seek without alignment', { reason });
      return null;
    }
    if (!state.editedMusicJson) return null;
    const timeline = compileTimeline(state.editedMusicJson);
    if (!timeline) return null;
    const opts = alignmentMapOpts(state.alignmentDocument);
    const seconds = tickToSourceSeconds(timeline, Number(tick), opts);
    if (seconds == null) return null;
    sourceSeekRequestSeq += 1;
    const request = { id: sourceSeekRequestSeq, seconds, reason, tick: Number(tick) };
    set({
      sourceSeekRequest: request,
      sourcePlayheadTick: Number(tick),
    });
    audioAlignmentLogger.info('seek source to tick', {
      tick: Number(tick),
      seconds: Number(seconds.toFixed(3)),
      reason,
    });
    return request;
  },

  seekSourceAudioToBar: (bar, { reason = 'seek-bar' } = {}) => {
    const state = get();
    const composition = state.editedMusicJson;
    if (!composition || !state.alignmentDocument) {
      audioAlignmentLogger.warn('seek-from-bar without alignment', { bar });
      return null;
    }
    const window = barRangeToAudioWindow(
      Number(bar),
      Number(bar),
      composition,
      state.alignmentDocument,
    );
    if (!window) return null;
    sourceSeekRequestSeq += 1;
    const request = {
      id: sourceSeekRequestSeq,
      seconds: window.startSeconds,
      reason,
      tick: window.startTick,
      bar: Number(bar),
    };
    set({
      sourceSeekRequest: request,
      sourcePlayheadTick: window.startTick,
      audioWindowHighlight: {
        startSeconds: window.startSeconds,
        endSeconds: window.endSeconds,
        startBar: window.startBar,
        endBar: window.endBar,
        startTick: window.startTick,
        endTick: window.endTick,
      },
    });
    audioAlignmentLogger.info('seek-from-bar', {
      bar: Number(bar),
      seconds: Number(window.startSeconds.toFixed(3)),
    });
    return request;
  },

  setAudioWindowHighlightFromBars: (startBar, endBar) => {
    const state = get();
    if (!state.alignmentDocument || !state.editedMusicJson) {
      set({ audioWindowHighlight: null });
      return null;
    }
    const window = barRangeToAudioWindow(
      Number(startBar),
      Number(endBar),
      state.editedMusicJson,
      state.alignmentDocument,
    );
    if (!window) {
      set({ audioWindowHighlight: null });
      return null;
    }
    const highlight = {
      startSeconds: window.startSeconds,
      endSeconds: window.endSeconds,
      startBar: window.startBar,
      endBar: window.endBar,
      startTick: window.startTick,
      endTick: window.endTick,
    };
    set({ audioWindowHighlight: highlight });
    return highlight;
  },

  clearAudioWindowHighlight: () => {
    if (get().audioWindowHighlight == null) return;
    set({ audioWindowHighlight: null });
  },

  applyAudioRecovery: async () => {
    const state = get();
    if (!state.recoveryPreview || state.recoveryPhase !== AUDIO_RECOVERY_PHASES.REVIEW) {
      return { ok: false, code: 'audio_recovery_not_in_review' };
    }
    if (!state.currentProjectId) {
      audioRecoveryLogger.warn('Apply requires open project for Bind', {
        code: 'audio_recovery_project_required',
      });
      set({
        recoveryErrorCode: 'audio_recovery_project_required',
        recoveryErrorMessage: 'Open or create a project before Apply → Bind.',
      });
      return { ok: false, code: 'audio_recovery_project_required' };
    }
    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.APPLYING, 'apply');
    const applied = applyAudioRecoveryToComposition(state.editedMusicJson, {
      preview: state.recoveryPreview,
      stemRoleMap: state.recoveryStemRoleMap,
      includeLowConfidence: state.recoveryIncludeLowConfidence,
      selectedIds: state.recoverySelectedProvisionalIds,
      threshold: state.recoveryConfidenceThreshold,
      installFlags: state.recoveryInstallFlags || undefined,
    });
    if (!applied.ok) {
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.REVIEW,
        recoveryErrorCode: applied.code,
        recoveryErrorMessage: applied.message || applied.code,
      });
      return applied;
    }
    const ok = commitCompositionTransaction(set, get, {
      nextComposition: applied.composition,
      action: 'audio-recovery-apply',
      noteSummary: {
        noteCount: applied.eventMap.length,
        excludedLow: applied.excludedLow,
        stems: applied.stemsApplied,
      },
      affectedNoteCount: applied.eventMap.length,
      affectedTrackCount: new Set(applied.eventMap.map((e) => e.track_id)).size,
    });
    if (!ok) {
      set({ recoveryPhase: AUDIO_RECOVERY_PHASES.REVIEW });
      return { ok: false, code: 'audio_recovery_commit_failed' };
    }

    transitionRecoveryPhase(set, get, AUDIO_RECOVERY_PHASES.BINDING, 'bind');
    try {
      const bindResult = await bindAudioRecoveryJob(state.recoveryJobId, {
        project_id: state.currentProjectId,
        preview_fingerprint: state.recoveryPreview.preview_fingerprint,
        event_map: applied.eventMap,
        composition: applied.composition,
      });
      const overlay = buildOverlayFromEventMap(
        applied.eventMap,
        state.recoveryPreview.notes,
      );
      let sourceUrl = null;
      let alignmentDocument = null;
      try {
        const fetched = await fetchAudioRecoveryAssetBlobUrl(bindResult.source_audio_asset_id);
        revokeRecoverySourceUrl(get);
        sourceUrl = fetched.blobUrl;
      } catch {
        audioRecoveryLogger.warn('Source audio blob fetch failed after bind');
      }
      if (bindResult.alignment_asset_id) {
        try {
          alignmentDocument = await fetchAudioRecoveryAssetJson(bindResult.alignment_asset_id);
        } catch {
          audioRecoveryLogger.warn('Alignment JSON fetch failed after bind');
        }
      }
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.BOUND,
        recoverySourceAudioAssetId: bindResult.source_audio_asset_id,
        recoveryResultAssetId: bindResult.result_asset_id,
        recoveryAlignmentAssetId: bindResult.alignment_asset_id || null,
        alignmentDocument,
        recoveryOverlay: overlay,
        recoverySourceObjectUrl: sourceUrl,
        recoveryBindWarning: null,
        recoveryErrorCode: null,
        recoveryErrorMessage: '',
        roundtripProvenance: bindResult.roundtrip_provenance || null,
      });
      audioRecoveryLogger.info('Recovery Apply→Bind complete', {
        jobId: state.recoveryJobId,
        sourceAudioAssetId: bindResult.source_audio_asset_id,
        resultAssetId: bindResult.result_asset_id,
        alignmentAssetId: bindResult.alignment_asset_id || null,
        overlayCount: overlay.length,
      });
      return { ok: true, bindResult, eventMap: applied.eventMap };
    } catch (error) {
      const code = error instanceof AudioRecoveryApiError
        ? error.code
        : 'audio_recovery_bind_failed';
      audioRecoveryLogger.warn('Bind failed after Apply — V2 notes kept', { code });
      set({
        recoveryPhase: AUDIO_RECOVERY_PHASES.REVIEW,
        recoveryBindWarning: 'Confidence overlay not saved',
        recoveryErrorCode: code,
        recoveryErrorMessage: error?.message || 'Bind failed; notes were applied without overlay.',
      });
      return { ok: true, bindFailed: true, code, eventMap: applied.eventMap };
    }
  },

  discardAudioRecovery: async () => {
    if (recoveryRecorderSession) {
      recoveryRecorderSession.cancel();
    }
    const jobId = get().recoveryJobId;
    const bound = get().recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND;
    if (jobId && !bound) {
      try {
        await deleteAudioRecoveryJob(jobId);
      } catch {
        audioRecoveryLogger.warn('Recovery job delete failed on discard', { jobId });
      }
    }
    audioRecoveryLogger.info('Audio recovery discarded', { jobId, bound });
    set({ ...clearedAudioRecoveryState(get) });
    return { ok: true };
  },

  setMultiAgentRevisionMode: (mode) => {
    const allowed = new Set(['off', 'fast', 'balanced', 'thorough']);
    const next = allowed.has(mode) ? mode : 'off';
    set({ multiAgentRevisionMode: next });
  },

  setMultiAgentComparePassIndex: (passIndex) => {
    set({
      multiAgentComparePassIndex: passIndex,
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
  },

  setMultiAgentAuditionActive: (active) => {
    const enabled = Boolean(active);
    const state = get();
    const hasPass = Array.isArray(state.multiAgentPassCandidates)
      && state.multiAgentPassCandidates.some((c) => c?.composition);
    const hasFinal = Boolean(state.multiAgentCandidate?.composition);
    if (enabled && !hasPass && !hasFinal) {
      return false;
    }
    console.info('[musicStore] Multi-agent audition toggled', {
      active: enabled,
      passIndex: state.multiAgentComparePassIndex,
      passCandidateCount: Array.isArray(state.multiAgentPassCandidates)
        ? state.multiAgentPassCandidates.length
        : 0,
    });
    set({
      multiAgentAuditionActive: enabled,
      ...(enabled
        ? {
            ...exclusiveAuditionPatch(PLAYBACK_SOURCE_GENERATION, ARRANGEMENT_AUDITION_SOURCE),
            generationAuditionActive: false,
            aiEditAuditionActive: false,
            motifAuditionActive: false,
            reharmonizeAuditionActive: false,
            multiAgentAuditionActive: true,
          }
        : {}),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
    });
    return true;
  },

  cancelMultiAgentPreview: () => {
    const state = get();
    const controller = state.multiAgentAbortController;
    if (controller) {
      controller.abort();
    }
    set({
      multiAgentRevisionLoopStatus: 'cancelled',
      multiAgentStatus: state.multiAgentCandidate ? 'success' : 'idle',
      multiAgentAbortController: null,
      multiAgentOperationRunId: null,
    });
  },

  previewMultiAgentWorkflow: async () => {
    const state = get();
    const source = state.editedMusicJson || state.generatedMusicJson;
    if (!source || !isCanonicalComposition(source)) {
      set({
        multiAgentStatus: 'error',
        multiAgentError: 'Canonical composition.v2 required for multi-agent preview',
      });
      return null;
    }
    if (state.multiAgentAbortController) {
      state.multiAgentAbortController.abort();
    }
    const abortController =
      typeof globalThis.AbortController !== 'undefined' ? new globalThis.AbortController() : null;
    const operationRunId = mintOperationRunId();
    const requestId = (state.multiAgentRequestId || 0) + 1;
    const revisionMode = state.multiAgentRevisionMode || 'off';
    set({
      multiAgentStatus: 'loading',
      multiAgentError: '',
      multiAgentRequestId: requestId,
      multiAgentRevisionLoopStatus: 'running',
      multiAgentAbortController: abortController,
      multiAgentRevisionHistory: [],
      multiAgentPassCandidates: [],
      multiAgentStopReason: null,
      multiAgentOperationSummary: null,
      multiAgentOperationRunId: operationRunId,
      multiAgentComparePassIndex: null,
      multiAgentAuditionActive: false,
    });
    try {
      const response = await previewMultiAgentWorkflow(
        {
          composition: source,
          workflow_id: 'agent_spine_v1',
          max_revisions: 0,
          revision_mode: revisionMode,
          operation_run_id: operationRunId,
        },
        abortController ? { signal: abortController.signal } : {},
      );
      if (get().multiAgentRequestId !== requestId) {
        return null;
      }
      const sourceFingerprint = await compositionEditFingerprint(source);
      const candidateFingerprint = response.candidate_fingerprint
        || await compositionEditFingerprint(response.candidate);
      const roleMap = buildArtifactRoleMapFromLog(response.artifact_log || [], {
        requireRevisionPlan: response.recommendation === 'revise',
      });
      const generationParameters = {
        ...(response.generation_parameters && typeof response.generation_parameters === 'object'
          ? response.generation_parameters
          : {}),
        artifact_role_map: roleMap,
      };
      const passCandidates = Array.isArray(response.pass_candidates)
        ? response.pass_candidates
        : [];
      const envelope = buildAiCandidateEnvelope({
        candidateId: `multi-agent-${Date.now()}`,
        operationType: 'multi-agent-apply',
        composition: response.candidate,
        sourceFingerprint: response.source_fingerprint || sourceFingerprint,
        candidateFingerprint,
        generationParameters,
        warnings: response.warning_codes || [],
        extras: {
          agent_sequence: response.agent_sequence || [],
          stages: response.stages || [],
          recommendation: response.recommendation || null,
          artifact_log: response.artifact_log || [],
          pipeline_id: response.pipeline_id || 'agent_spine_v1',
          artifact_role_map: roleMap,
          revision_history: response.revision_history || [],
          pass_candidates: passCandidates,
          stop_reason: response.stop_reason || null,
          revision_mode: response.revision_mode || revisionMode,
          last_valid_fingerprint: response.last_valid_fingerprint || candidateFingerprint,
          usage: response.usage || null,
        },
      });
      // Discard competing session candidates when multi-agent candidate is set.
      set({
        multiAgentStatus: 'success',
        multiAgentError: '',
        multiAgentCandidate: envelope,
        multiAgentRevisionHistory: Array.isArray(response.revision_history)
          ? response.revision_history
          : [],
        multiAgentPassCandidates: passCandidates,
        multiAgentStopReason: response.stop_reason || null,
        multiAgentOperationSummary: response.operation_summary || null,
        multiAgentOperationRunId: null,
        multiAgentRevisionLoopStatus: 'idle',
        multiAgentAbortController: null,
        multiAgentComparePassIndex: 0,
        multiAgentAuditionActive: false,
        arrangementCandidates: [],
        arrangementSelectedCandidateId: null,
        developmentCandidates: [],
        developmentSelectedCandidateId: null,
        reharmonizeCandidate: null,
        reharmonizeProposalFingerprint: null,
        reharmonizeAuditionActive: false,
      });
      return envelope;
    } catch (error) {
      if (get().multiAgentRequestId !== requestId) {
        return null;
      }
      const aborted = error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED'
        || error?.name === 'AbortError';
      set({
        multiAgentStatus: aborted ? (get().multiAgentCandidate ? 'success' : 'idle') : 'error',
        multiAgentError: aborted ? '' : (error?.message || 'Multi-agent workflow preview failed'),
        multiAgentCandidate: aborted ? get().multiAgentCandidate : null,
        multiAgentRevisionLoopStatus: aborted ? 'cancelled' : 'idle',
        multiAgentAbortController: null,
        multiAgentOperationRunId: null,
        multiAgentOperationSummary: aborted ? null : get().multiAgentOperationSummary,
        multiAgentStopReason: aborted ? 'cancelled' : null,
      });
      return null;
    }
  },

  previewAutonomousComposer: async (brief) => {
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const plan = await previewAutonomousPlan(brief);
      console.debug('[musicStore] Autonomous plan preview', {
        duration_bars: plan?.constraints?.duration_bars ?? null,
        section_count: Array.isArray(plan?.sections) ? plan.sections.length : 0,
        brief_len: JSON.stringify(brief || {}).length,
      });
      set({
        autonomousPreview: plan,
        autonomousPreviewFingerprint: briefFingerprint(brief),
        autonomousStatus: 'idle',
      });
      return plan;
    } catch (error) {
      console.error('[musicStore] Autonomous preview failed', { code: error?.code || null });
      set({
        autonomousPreview: null,
        autonomousPreviewFingerprint: null,
        autonomousStatus: 'error',
        autonomousError: error?.code || 'autonomous_brief_invalid',
      });
      return null;
    }
  },

  startAutonomousComposer: async (brief, { includeRendering = false, autonomyMode = 'autonomous' } = {}) => {
    const baseline = get().workingFingerprint;
    const operationRunId = mintOperationRunId();
    const abortController = typeof globalThis.AbortController !== 'undefined'
      ? new globalThis.AbortController()
      : null;
    set({
      autonomousStatus: 'loading',
      autonomousError: '',
      autonomousRun: null,
      autonomousRunId: operationRunId,
      autonomousBaselineFingerprint: baseline,
      autonomousAbortController: abortController,
    });
    const projectId = get().currentProjectId;
    try {
      const view = await startAutonomousRun({
        brief,
        include_rendering: includeRendering,
        operation_run_id: operationRunId,
        autonomy_mode: autonomyMode,
        project_id: projectId,
        expected_working_version: projectId ? get().workingVersion : null,
        expected_head_revision_id: projectId ? get().currentRevisionId : null,
        expected_source_fingerprint: projectId ? baseline : null,
      }, abortController ? { signal: abortController.signal } : {});
      console.debug('[musicStore] Autonomous run', {
        run_id_prefix: String(view?.run_id || '').slice(0, 16),
        status: view?.status || null,
        autonomy_mode: view?.autonomy_mode || null,
        checkpoint_id: view?.checkpoint_id || null,
        stage_count: Array.isArray(view?.stages) ? view.stages.length : 0,
      });
      await adoptAutonomousView(set, get, view, baseline);
      set({ autonomousAbortController: null });
      return view;
    } catch (error) {
      const aborted = error?.name === 'CanceledError' || error?.code === 'ERR_CANCELED'
        || error?.name === 'AbortError';
      console.error('[musicStore] Autonomous start failed', {
        code: aborted ? 'operation_cancelled' : (error?.code || null),
      });
      set({
        autonomousStatus: aborted ? 'success' : 'error',
        autonomousError: aborted ? 'operation_cancelled' : (error?.code || error?.message || 'autonomous_run_invalid'),
        autonomousAbortController: null,
      });
      return null;
    }
  },

  pauseAutonomousComposer: async () => {
    const operationRunId = get().autonomousRunId;
    if (!operationRunId) return null;
    try {
      const view = await pauseAutonomousRun(operationRunId);
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous pause failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  cancelAutonomousComposer: async () => {
    const controller = get().autonomousAbortController;
    if (controller) {
      controller.abort();
    }
    const runId = get().autonomousRun?.run_id;
    if (!runId) return null;
    try {
      const view = await cancelAutonomousRun(get().autonomousRun?.run_id || runId);
      set({
        autonomousRun: view,
        autonomousStatus: view?.status === 'cancelled' ? 'success' : get().autonomousStatus,
        autonomousError: view?.failure_code || '',
      });
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous cancel failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  resumeAutonomousComposer: async () => {
    const runId = get().autonomousRun?.run_id;
    if (!runId) return null;
    const baseline = get().autonomousBaselineFingerprint;
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const view = await resumeAutonomousRun(runId);
      await adoptAutonomousView(set, get, view, baseline);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous resume failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  approveAutonomousRender: async () => {
    const runId = get().autonomousRun?.run_id;
    if (!runId) return null;
    try {
      const view = await approveAutonomousStage(runId, 'render');
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous approve failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  approveAutonomousCheckpoint: async (checkpointId) => {
    const runId = get().autonomousRun?.run_id;
    if (!runId || !checkpointId) return null;
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const view = await approveAutonomousCheckpoint(runId, checkpointId);
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous checkpoint approve failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  rejectAutonomousArrangement: async () => {
    const runId = get().autonomousRun?.run_id;
    if (!runId) return null;
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const view = await rejectAutonomousArrangement(runId);
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous reject failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  instructAutonomousStage: async (stageId, text) => {
    const runId = get().autonomousRun?.run_id;
    if (!runId || !stageId) return null;
    console.debug('[musicStore] Autonomous instruction', {
      stage_id: stageId,
      instruction_len: String(text || '').length,
    });
    try {
      const view = await instructAutonomousStage(runId, stageId, text);
      set({
        autonomousRun: view,
        autonomousStatus: 'success',
        autonomousError: '',
      });
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous instruction failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_instruction_unsafe' });
      return null;
    }
  },

  retryAutonomousStage: async (stageId) => {
    const runId = get().autonomousRun?.run_id;
    if (!runId || !stageId) return null;
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const view = await retryAutonomousStage(runId, stageId);
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous retry failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  openAutonomousStage: async (stageId) => {
    const runId = get().autonomousRun?.run_id;
    if (!runId || !stageId) return null;
    set({ autonomousStatus: 'loading', autonomousError: '' });
    try {
      const view = await openAutonomousStage(runId, stageId);
      await adoptAutonomousView(set, get, view, get().autonomousBaselineFingerprint);
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous open failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_revision_not_head' });
      return null;
    }
  },

  branchAutonomousStage: async (stageId, name) => {
    const runId = get().autonomousRun?.run_id;
    if (!runId || !stageId) return null;
    try {
      const created = await branchAutonomousStage(runId, stageId, name);
      console.debug('[musicStore] Autonomous branch', {
        run_id_prefix: String(runId).slice(0, 16),
        stage_id: stageId,
      });
      return created;
    } catch (error) {
      console.error('[musicStore] Autonomous branch failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  skipAutonomousRender: async () => {
    const runId = get().autonomousRun?.run_id;
    if (!runId) return null;
    try {
      const view = await skipAutonomousStage(runId, 'render');
      set({
        autonomousRun: view,
        autonomousStatus: 'success',
        autonomousError: '',
      });
      return view;
    } catch (error) {
      console.error('[musicStore] Autonomous skip failed', { code: error?.code || null });
      set({ autonomousStatus: 'error', autonomousError: error?.code || 'autonomous_run_invalid' });
      return null;
    }
  },

  applyMultiAgentCandidate: async ({ asNewBranch = false, branchName = '' } = {}) => {
    const state = get();
    const candidate = state.multiAgentCandidate;
    if (!candidate?.composition) {
      set({ multiAgentStatus: 'error', multiAgentError: 'No multi-agent candidate to apply' });
      return false;
    }
    const liveSource = state.editedMusicJson || state.generatedMusicJson;
    const liveSourceFp = await compositionEditFingerprint(liveSource);
    if (candidate.source_fingerprint && liveSourceFp !== candidate.source_fingerprint) {
      set({
        multiAgentStatus: 'error',
        multiAgentError: 'Base composition changed; request a new multi-agent preview',
      });
      return false;
    }
    const prepared = candidate.composition;
    const roleMap = candidate.artifact_role_map
      || candidate.generation_parameters?.artifact_role_map
      || buildArtifactRoleMapFromLog(candidate.artifact_log || [], {
        requireRevisionPlan: candidate.recommendation === 'revise',
      });
    const roleCheck = validateArtifactRoleMap(roleMap, {
      requireRevisionPlan: candidate.recommendation === 'revise',
    });
    if (!roleCheck.ok) {
      set({
        multiAgentStatus: 'error',
        multiAgentError: `Missing artifact roles for Apply: ${roleCheck.missing.join(', ')}`,
      });
      return false;
    }
    const generationParameters = {
      ...(candidate.generation_parameters && typeof candidate.generation_parameters === 'object'
        ? candidate.generation_parameters
        : {}),
      artifact_role_map: roleMap,
      artifact_envelopes: Array.isArray(candidate.artifact_log)
        ? candidate.artifact_log.slice(0, 64)
        : [],
    };
    const historySnapshot = {
      provider: candidate.provider,
      model: candidate.model,
      model_id: candidate.model_id,
      generation_parameters: generationParameters,
    };
    const localStatePatch = {
      multiAgentCandidate: null,
      multiAgentStatus: 'idle',
      multiAgentError: '',
      multiAgentRevisionHistory: [],
      multiAgentPassCandidates: [],
      multiAgentStopReason: null,
      multiAgentOperationSummary: null,
      multiAgentOperationRunId: null,
      multiAgentComparePassIndex: null,
      multiAgentAuditionActive: false,
    };
    const aiPayload = {
      provider: candidate.provider || null,
      model: candidate.model || null,
      model_id: candidate.model_id || null,
      warning_codes: toHistoryAiWarningCodes(candidate.warnings),
      generation_parameters: generationParameters,
    };

    if (!state.currentProjectId) {
      commitCompositionTransaction(set, get, {
        nextComposition: prepared,
        action: 'multi-agent-apply',
        noteSummary: null,
        historySnapshot,
        statePatch: localStatePatch,
      });
      void get().refreshMusicXmlFromEditedComposition();
      return true;
    }

    if (
      !state.activeBranchId
      || state.workingVersion == null
      || !state.currentRevisionId
      || !state.workingFingerprint
    ) {
      set({
        multiAgentStatus: 'error',
        multiAgentError: 'Missing branch CAS fields for durable multi-agent apply',
      });
      return false;
    }

    try {
      let durable;
      if (asNewBranch) {
        const name = String(branchName || '').trim();
        if (!name) {
          set({
            multiAgentStatus: 'error',
            multiAgentError: 'Branch name required for Apply as new branch',
          });
          return false;
        }
        durable = await applyAsBranchRequest(state.currentProjectId, {
          name,
          source_branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'multi-agent-apply',
          declared_scope: { ranges: [], track_ids: [] },
          ai: aiPayload,
        });
      } else {
        durable = await commitRevisionRequest(state.currentProjectId, {
          branch_id: state.activeBranchId,
          expected_active_branch_id: state.activeBranchId,
          expected_working_version: state.workingVersion,
          expected_head_revision_id: state.currentRevisionId,
          expected_source_fingerprint: state.workingFingerprint,
          composition: prepared,
          operation_type: 'multi-agent-apply',
          checkpoint_dirty_draft: true,
          declared_scope: { ranges: [], track_ids: [] },
          ai: aiPayload,
        });
      }
      if (get().currentProjectId !== state.currentProjectId) {
        return false;
      }
      installDurableHistoryResult(set, get, durable, {
        clearUndo: Boolean(asNewBranch),
        markSaved: true,
        action: 'multi-agent-apply',
      });
      set({
        ...localStatePatch,
        ...(asNewBranch ? clearedVersionHistoryState() : {}),
      });
      void get().refreshMusicXmlFromEditedComposition();
      return true;
    } catch (error) {
      set({
        multiAgentStatus: 'error',
        multiAgentError: error?.message || 'Multi-agent apply failed',
      });
      return false;
    }
  },

  discardMultiAgentCandidate: () => {
    set({
      multiAgentCandidate: null,
      multiAgentStatus: 'idle',
      multiAgentError: '',
      multiAgentRevisionHistory: [],
      multiAgentPassCandidates: [],
      multiAgentStopReason: null,
      multiAgentOperationSummary: null,
      multiAgentOperationRunId: null,
      multiAgentRevisionLoopStatus: 'idle',
      multiAgentComparePassIndex: null,
      multiAgentAuditionActive: false,
      multiAgentAbortController: null,
    });
  },

  loadAdaptiveScore: async () => {
    const projectId = get().currentProjectId;
    adaptiveScoreRequestSeq += 1;
    const requestId = adaptiveScoreRequestSeq;
    if (!projectId) {
      set({ ...initialAdaptiveScoreState });
      return null;
    }
    console.debug('[musicStore] Adaptive score load', { projectId, requestId });
    set({ adaptiveStatus: 'loading', adaptiveCommandError: '' });
    try {
      const listed = await listAdaptiveScores(projectId);
      if (get().currentProjectId !== projectId || requestId !== adaptiveScoreRequestSeq) {
        console.debug('[musicStore] Adaptive score load ignored', { projectId, requestId });
        return null;
      }
      const scores = Array.isArray(listed?.scores) ? listed.scores : [];
      if (!scores.length) {
        set({ ...initialAdaptiveScoreState, adaptiveStatus: 'empty', adaptiveScoreList: [] });
        return null;
      }
      const chosen = scores.find((item) => item.is_default) || scores[0];
      const detail = await getAdaptiveScore(projectId, chosen.id);
      if (get().currentProjectId !== projectId || requestId !== adaptiveScoreRequestSeq) {
        console.debug('[musicStore] Adaptive score load ignored', { projectId, requestId });
        return null;
      }
      const score = detail.score;
      set({
        adaptiveScoreId: score.id,
        adaptiveDocumentRevision: detail.document_revision,
        adaptiveBindingStatus: detail.binding_status,
        adaptiveScore: score,
        adaptiveScoreList: scores,
        adaptiveFindings: [],
        adaptiveSelectedStateId: score.initial_state_id || score.states?.[0]?.id || null,
        adaptiveCommandError: '',
        adaptiveStatus: 'ready',
        adaptiveScheduledTransition: null,
      });
      try {
        const pending = await getCurrentAdaptiveTransition(projectId, score.id);
        if (get().currentProjectId === projectId && requestId === adaptiveScoreRequestSeq) {
          set({ adaptiveScheduledTransition: pending });
        }
      } catch (pendingError) {
        console.warn('[musicStore] Adaptive transition current failed', {
          status: pendingError.status || null,
          code: pendingError.code || null,
        });
      }
      return detail;
    } catch (error) {
      if (get().currentProjectId !== projectId || requestId !== adaptiveScoreRequestSeq) {
        return null;
      }
      console.warn('[musicStore] Adaptive score load failed', {
        code: error.code || null,
        status: error.status || null,
      });
      set({
        adaptiveStatus: 'error',
        adaptiveCommandError: error.message || 'Adaptive score load failed',
        adaptiveFindings: error.findings || [],
      });
      return null;
    }
  },

  createEmptyAdaptiveScore: async () => {
    const projectId = get().currentProjectId;
    if (!projectId) {
      return null;
    }
    console.info('[musicStore] Adaptive score command', { op: 'create_score', scoreId: null });
    set({ adaptiveStatus: 'loading', adaptiveCommandError: '' });
    try {
      const detail = await createAdaptiveScore(projectId, {
        is_default: true,
        score: {
          schema_version: 'adaptive.score.v1',
          name: 'Exploration cue',
        },
      });
      if (get().currentProjectId !== projectId) {
        return null;
      }
      set({
        adaptiveScoreId: detail.score.id,
        adaptiveDocumentRevision: detail.document_revision,
        adaptiveBindingStatus: detail.binding_status,
        adaptiveScore: detail.score,
        adaptiveScoreList: [{ id: detail.score.id, name: detail.score.name, is_default: true }],
        adaptiveFindings: [],
        adaptiveSelectedStateId: null,
        adaptiveCommandError: '',
        adaptiveStatus: 'ready',
      });
      return detail;
    } catch (error) {
      if (get().currentProjectId !== projectId) {
        return null;
      }
      console.warn('[musicStore] Adaptive score create failed', {
        code: error.code || null,
        status: error.status || null,
      });
      set({
        adaptiveStatus: 'error',
        adaptiveCommandError: error.message || 'Could not create an adaptive score',
        adaptiveFindings: error.findings || [],
      });
      return null;
    }
  },

  scheduleAdaptiveTransition: async (toStateId, positionTick) => {
    const projectId = get().currentProjectId;
    const scoreId = get().adaptiveScoreId;
    const revision = get().adaptiveDocumentRevision;
    const fromStateId = get().adaptiveSelectedStateId;
    if (!projectId || !scoreId || !revision || !fromStateId || !toStateId) {
      return null;
    }
    const position = Number.isInteger(positionTick) && positionTick >= 0 ? positionTick : 0;
    try {
      const schedule = await scheduleAdaptiveTransition(projectId, scoreId, {
        expected_document_revision: revision,
        from_state_id: fromStateId,
        to_state_id: toStateId,
        transition_id: null,
        position_tick: position,
        runtime: { intensity: 0, flags: {}, bars_in_state: 0 },
      });
      if (get().currentProjectId !== projectId || get().adaptiveScoreId !== scoreId) {
        return null;
      }
      console.debug('[musicStore] Adaptive transition scheduled', {
        scoreId,
        quantization: schedule?.quantization || null,
        latency_ms: schedule?.latency_ms ?? null,
        boundaryTick: schedule?.boundary_tick ?? null,
      });
      set({ adaptiveScheduledTransition: schedule, adaptiveCommandError: '' });
      return schedule;
    } catch (error) {
      console.warn('[musicStore] Adaptive transition schedule failed', {
        status: error.status || null,
        code: error.code || null,
      });
      if (get().currentProjectId !== projectId) {
        return null;
      }
      set({ adaptiveCommandError: error.message || 'Could not schedule a transition' });
      return null;
    }
  },

  cancelAdaptiveTransition: async () => {
    const projectId = get().currentProjectId;
    const scoreId = get().adaptiveScoreId;
    const pending = get().adaptiveScheduledTransition;
    if (!projectId || !scoreId || !pending?.request_id) {
      return null;
    }
    try {
      await cancelAdaptiveTransition(projectId, scoreId, pending.request_id);
      if (get().currentProjectId === projectId && get().adaptiveScoreId === scoreId) {
        set({ adaptiveScheduledTransition: null });
      }
      return null;
    } catch (error) {
      console.warn('[musicStore] Adaptive transition cancel failed', {
        status: error.status || null,
        code: error.code || null,
      });
      return null;
    }
  },

  runAdaptiveScoreCommand: async (op, payload) => {
    const projectId = get().currentProjectId;
    const scoreId = get().adaptiveScoreId;
    const revision = get().adaptiveDocumentRevision;
    if (!projectId || !scoreId || !revision) {
      return null;
    }
    const previousScore = get().adaptiveScore;
    console.info('[musicStore] Adaptive score command', { op, scoreId });
    set({ adaptiveStatus: 'saving', adaptiveCommandError: '' });
    try {
      const detail = await commandAdaptiveScore(projectId, scoreId, {
        expected_document_revision: revision,
        op,
        payload,
      });
      if (get().currentProjectId !== projectId || get().adaptiveScoreId !== scoreId) {
        return null;
      }
      set({
        adaptiveScore: detail.score,
        adaptiveDocumentRevision: detail.document_revision,
        adaptiveBindingStatus: detail.binding_status,
        adaptiveFindings: detail.findings || [],
        adaptiveCommandError: '',
        adaptiveStatus: 'ready',
      });
      return detail;
    } catch (error) {
      if (get().currentProjectId !== projectId) {
        return null;
      }
      const codes = (error.findings || []).map((item) => item.code);
      console.warn('[musicStore] Adaptive score command failed', {
        code: error.code || null,
        codes,
        status: error.status || null,
      });
      if (error.status === 409 && error.code === 'adaptive_score_conflict') {
        try {
          const detail = await getAdaptiveScore(projectId, scoreId);
          if (get().currentProjectId === projectId) {
            set({
              adaptiveScore: detail.score,
              adaptiveDocumentRevision: detail.document_revision,
              adaptiveBindingStatus: detail.binding_status,
              adaptiveFindings: [],
              adaptiveCommandError: 'The adaptive score changed. Reloaded the saved graph.',
              adaptiveStatus: 'conflict',
            });
          }
        } catch (reloadError) {
          console.warn('[musicStore] Adaptive score conflict reload failed', {
            code: reloadError.code || null,
            status: reloadError.status || null,
          });
        }
        return null;
      }
      set({
        adaptiveScore: previousScore,
        adaptiveFindings: error.findings || [],
        adaptiveCommandError: error.message || 'Adaptive score command failed',
        adaptiveStatus: 'error',
      });
      return null;
    }
  },

  validateLoadedAdaptiveScore: async () => {
    const projectId = get().currentProjectId;
    const scoreId = get().adaptiveScoreId;
    if (!projectId || !scoreId) {
      return null;
    }
    try {
      const detail = await validateAdaptiveScore(projectId, scoreId);
      if (get().currentProjectId !== projectId || get().adaptiveScoreId !== scoreId) {
        return null;
      }
      const findings = detail.findings || [];
      console.debug('[musicStore] Adaptive score validated', {
        scoreId,
        errorCount: findings.filter((item) => item.severity === 'error').length,
        warningCount: findings.filter((item) => item.severity === 'warning').length,
      });
      set({
        adaptiveFindings: findings,
        adaptiveBindingStatus: detail.binding_status,
        adaptiveCommandError: '',
        adaptiveStatus: 'ready',
      });
      return detail;
    } catch (error) {
      if (get().currentProjectId !== projectId) {
        return null;
      }
      console.warn('[musicStore] Adaptive score validate failed', {
        code: error.code || null,
        codes: (error.findings || []).map((item) => item.code),
      });
      set({
        adaptiveCommandError: error.message || 'Validation failed',
        adaptiveFindings: error.findings || get().adaptiveFindings,
      });
      return null;
    }
  },

  selectAdaptiveState: (stateId) => {
    console.debug('[musicStore] Adaptive state selected', { stateId });
    set({ adaptiveSelectedStateId: stateId || null });
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
  return createDefaultTrackControl(volumeMidi);
}

function buildDefaultTrackControls(musicJson) {
  if (!isCanonicalComposition(musicJson)) {
    return {};
  }
  return buildDefaultMixerControls(musicJson);
}

function mergeTrackControls(existing, musicJson) {
  return mergeMixerControls(existing, musicJson);
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
    // Bind overlay rides undo/redo so prune / user_edited stay honest with V2 notes.
    recoveryOverlay: Array.isArray(state.recoveryOverlay)
      ? state.recoveryOverlay.map((entry) => ({ ...entry }))
      : null,
  };
}

/**
 * Sync durable recovery confidence overlay after a composition mutation.
 * Skipped when statePatch already supplies overlay (Bind) or Apply has not bound yet.
 *
 * @returns {{ recoveryOverlay?: object[] }}
 */
function recoveryOverlayPatchAfterEdit(state, nextComposition, action, statePatch) {
  if (Object.prototype.hasOwnProperty.call(statePatch, 'recoveryOverlay')) {
    return {};
  }
  if (action === 'audio-recovery-apply') {
    return {};
  }
  if (!Array.isArray(state.recoveryOverlay) || !state.recoveryOverlay.length) {
    return {};
  }
  const synced = syncOverlayAfterCompositionEdit(
    state.recoveryOverlay,
    state.editedMusicJson,
    nextComposition,
  );
  if (!synced.prunedCount && !synced.markedCount) {
    return {};
  }
  audioRecoveryLogger.info('Recovery overlay lifecycle after edit', {
    action: action || 'commit',
    prunedCount: synced.prunedCount,
    markedCount: synced.markedCount,
    remaining: synced.overlay.length,
  });
  return { recoveryOverlay: synced.overlay };
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

  const overlayLifecycle = recoveryOverlayPatchAfterEdit(
    state,
    nextComposition,
    action,
    statePatch,
  );

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
    ...overlayLifecycle,
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
  if (isScoreReadOnly(state.projectCollaboration)) {
    collaborationLogger.debug('Autosave skipped; read-only role', {
      role: state.projectCollaboration?.role || null,
    });
    return;
  }
  if (state.saveStatus === 'conflict') {
    console.warn('[musicStore] Autosave skipped; unresolved revision conflict', {
      projectId: state.currentProjectId,
      code: state.saveConflict?.code || 'project_revision_conflict',
    });
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
    branchId: state.activeBranchId,
    workingVersion: state.workingVersion,
    delayMs: AUTOSAVE_DEBOUNCE_MS,
    persistRevision: persistRevision.slice(0, 48),
  });
  autosaveTimer = setTimeout(() => {
    autosaveTimer = null;
    console.debug('[musicStore] Autosave debounce fired', {
      projectId: get().currentProjectId,
      branchId: get().activeBranchId,
    });
    get().saveCurrentProject({ reason: 'autosave' }).catch(() => {
      // error already recorded on saveStatus
    });
  }, AUTOSAVE_DEBOUNCE_MS);
}

async function adoptAutonomousView(set, get, view, baselineFingerprint) {
  const failed = view?.status === 'failed';
  console.debug('[musicStore] Autonomous view', {
    run_id_prefix: String(view?.run_id || '').slice(0, 16),
    status: view?.status || null,
    autonomy_mode: view?.autonomy_mode || null,
    checkpoint_id: view?.checkpoint_id || null,
    stage_count: Array.isArray(view?.stages) ? view.stages.length : 0,
  });
  set({
    autonomousRun: view,
    autonomousRunId: view?.run_id || get().autonomousRunId,
    autonomousStatus: failed ? 'error' : 'success',
    autonomousError: failed ? (view?.failure_code || view?.budget_code || '') : '',
  });
  if (!view?.project_id || !view?.head_revision_id) return;
  const sameProject = get().currentProjectId === view.project_id;
  const localMoved = Boolean(
    baselineFingerprint
    && get().workingFingerprint
    && get().workingFingerprint !== baselineFingerprint,
  );
  if (sameProject && localMoved) {
    console.debug('[musicStore] Autonomous reload skipped; editor moved', {
      run_id_prefix: String(view.run_id || '').slice(0, 16),
    });
    return;
  }
  if (
    get().autonomousLoadedFingerprint
    && get().autonomousLoadedFingerprint === view.composition_fingerprint
    && sameProject
  ) {
    return;
  }
  await get().openProject(view.project_id);
  set({ autonomousLoadedFingerprint: view.composition_fingerprint || null });
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
  const historyFields = historyStateFromProject(project);
  set({
    currentProjectId: project.id,
    currentProjectName: project.name,
    ...historyFields,
    saveConflict: null,
    ...clearedVersionHistoryState(),
    ...initialMusicalReferenceSessionState,
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
    generationCandidate: null,
    generationAuditionActive: false,
    generationCompareResult: null,
    generationRequestCapture: null,
    generationStatus: 'idle',
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
  ...initialAdaptiveScoreState,
    ...clearedAudioTranscriptionState(),
    ...clearedAudioRecoveryState(get),
  });

  // After clear, reload durable bound source + overlay + alignment when present.
  if (project?.id) {
    void hydrateBoundAudioRecovery(set, get, project.id);
  }
}

async function hydrateBoundAudioRecovery(set, get, projectId) {
  try {
    const discovery = await fetchBoundAudioRecovery(projectId);
    if (get().currentProjectId !== projectId) {
      audioRecoveryLogger.info('Bound hydrate aborted — project switched', {
        projectIdPrefix: String(projectId).slice(0, 8),
      });
      return;
    }
    if (!discovery?.bound || !discovery.latest) {
      audioRecoveryLogger.info('Bound hydrate miss — idle', {
        projectIdPrefix: String(projectId).slice(0, 8),
      });
      return;
    }
    const latest = discovery.latest;
    let sourceUrl = null;
    let overlay = null;
    let alignmentDocument = null;
    try {
      const fetched = await fetchAudioRecoveryAssetBlobUrl(latest.source_audio_asset_id);
      if (get().currentProjectId !== projectId) return;
      revokeRecoverySourceUrl(get);
      sourceUrl = fetched.blobUrl;
    } catch (error) {
      audioRecoveryLogger.warn('Bound hydrate source fetch failed', {
        code: error?.code || 'fetch_failed',
      });
    }
    if (latest.result_asset_id) {
      try {
        const resultDoc = await fetchAudioRecoveryAssetJson(latest.result_asset_id);
        if (get().currentProjectId !== projectId) return;
        overlay = Array.isArray(resultDoc?.overlay) ? resultDoc.overlay : null;
      } catch (error) {
        audioRecoveryLogger.warn('Bound hydrate result fetch failed', {
          code: error?.code || 'fetch_failed',
        });
      }
    }
    if (latest.alignment_asset_id) {
      try {
        alignmentDocument = await fetchAudioRecoveryAssetJson(latest.alignment_asset_id);
        if (get().currentProjectId !== projectId) return;
      } catch (error) {
        audioRecoveryLogger.warn('Bound hydrate alignment fetch failed', {
          code: error?.code || 'fetch_failed',
        });
      }
    }
    if (get().currentProjectId !== projectId) return;
    set({
      recoveryPhase: AUDIO_RECOVERY_PHASES.BOUND,
      recoveryJobId: latest.job_id,
      recoverySourceAudioAssetId: latest.source_audio_asset_id,
      recoveryResultAssetId: latest.result_asset_id,
      recoveryAlignmentAssetId: latest.alignment_asset_id || null,
      alignmentDocument,
      recoveryOverlay: overlay,
      recoverySourceObjectUrl: sourceUrl,
      roundtripProvenance: {
        schema_version: 'audio.roundtrip.provenance.v1',
        source_audio_asset_id: latest.source_audio_asset_id,
        source_sha256_prefix: latest.source_sha256_prefix || null,
        result_asset_id: latest.result_asset_id,
        alignment_asset_id: latest.alignment_asset_id || null,
        recovery_job_id: latest.job_id,
        bound_at: latest.bound_at || null,
      },
      recoveryErrorCode: null,
      recoveryErrorMessage: '',
    });
    audioRecoveryLogger.info('Bound hydrate hit', {
      projectIdPrefix: String(projectId).slice(0, 8),
      jobIdPrefix: String(latest.job_id).slice(0, 8),
      hasSourceUrl: Boolean(sourceUrl),
      hasOverlay: Boolean(overlay),
      hasAlignment: Boolean(alignmentDocument),
    });
  } catch (error) {
    audioRecoveryLogger.warn('Bound hydrate discovery failed', {
      code: error?.code || 'discovery_failed',
    });
  }
}

function syncGenerationMetaFromPrompt(set, get, { reason = 'prompt-edit' } = {}) {
  const state = get();
  if (!state.currentProjectId) {
    return;
  }
  const nextMeta = {
    provider: state.generationMeta?.provider ?? state.selectedProvider ?? null,
    model: state.generationMeta?.model ?? state.selectedModel ?? null,
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
    reharmonizeAuditionActive: false,
    reharmonizeCompareResult: null,
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
    developmentCompareResult: null,
    developmentProvider: null,
    developmentModel: null,
    developmentReferenceProvenance: null,
  };
}

function clearedVersionHistoryState() {
  return { ...initialVersionHistoryState };
}

function pruneVersionRevisionDetails(details, keepIds) {
  const next = {};
  for (const id of keepIds) {
    if (details?.[id]) {
      next[id] = details[id];
    }
  }
  return next;
}

function computeVersionRestoreBlockReason(state) {
  if (!state?.currentProjectId) {
    return 'no-project';
  }
  if (state.saveStatus === 'conflict') {
    return 'conflict';
  }
  if (state.saveStatus === 'saving' || state.saveStatus === 'unsaved') {
    return 'dirty-draft';
  }
  const persistRevision = projectPersistRevisionKey(state.editedMusicJson, state.generationMeta);
  if (persistRevision !== state.lastSavedPersistRevision) {
    return 'dirty-draft';
  }
  const headId = state.currentRevisionId;
  const headMeta = (state.versionRevisions || []).find((row) => row.id === headId);
  if (
    headMeta?.snapshot_fingerprint
    && state.workingFingerprint
    && headMeta.snapshot_fingerprint !== state.workingFingerprint
  ) {
    return 'draft-diverged';
  }
  return null;
}

function installDurableHistoryResult(set, get, durable, {
  clearUndo = false,
  markSaved = true,
  action = 'history-durable',
} = {}) {
  const historyFields = historyStateFromDurable(durable);
  const rawComposition = durable?.composition
    ? ensureCompositionNoteIds(durable.composition).composition
    : null;
  const composition = rawComposition ? prepareCompositionForStore(rawComposition) : null;
  const state = get();

  if (clearUndo || composition == null) {
    cancelAutosaveTimer();
    const undoStack = clearUndo
      ? []
      : [...(state.compositionEditUndoStack || []), snapshotCompositionEditState(state)]
        .slice(-MAX_UNDO_HISTORY);
    const persistRevision = projectPersistRevisionKey(composition, state.generationMeta);
    const revision = compositionRevisionKey(composition);
    const notationRev = notationRevisionKey(composition);
    set({
      ...historyFields,
      saveConflict: null,
      generatedMusicJson: composition,
      editedMusicJson: composition,
      musicXml: '',
      compositionRevision: revision,
      notationRevision: notationRev,
      lastSavedPersistRevision: markSaved ? persistRevision : state.lastSavedPersistRevision,
      saveStatus: markSaved ? 'saved' : 'unsaved',
      saveError: '',
      trackControls: buildDefaultTrackControls(composition),
      playbackStatus: 'idle',
      playbackSeconds: 0,
      playbackBar: 1,
      pianoRollTrackId: pickDefaultTrackId(composition),
      pianoRollNoteId: null,
      pianoRollNoteIds: [],
      editorSelectionRefs: [],
      editorSelectionPrimary: null,
      editCursorTick: 0,
      compositionEditUndoStack: undoStack,
      compositionEditRedoStack: [],
      versionAuditionActive: false,
      versionAuditionTrackControls: {},
      playbackLoop: reconcilePlaybackLoop(state.playbackLoop, composition),
      ...editorPrefsForCompositionReplace(state, composition),
      ...clearedAnalysisState(),
      ...clearedMotifUiState(),
      ...clearedReharmonizePreviewState(),
      ...clearedDevelopmentPreviewState(),
      ...clearedArrangementPreviewState(),
      ...initialHarmonyUiState,
  ...initialAdaptiveScoreState,
    });
    console.info('[musicStore] Durable history result installed', {
      action,
      clearUndo: Boolean(clearUndo),
      hasComposition: composition != null,
      undoDepth: undoStack.length,
    });
    return;
  }

  const ok = commitCompositionTransaction(set, get, {
    nextComposition: composition,
    selectedTrackId: state.pianoRollTrackId,
    selectedNoteId: state.pianoRollNoteId,
    selectedNoteIds: state.pianoRollNoteIds,
    action,
    noteSummary: null,
    statePatch: {
      ...historyFields,
      saveConflict: null,
      generatedMusicJson: composition,
      versionAuditionActive: false,
      versionAuditionTrackControls: {},
      ...clearedDevelopmentPreviewState({ preserveControls: true }),
      ...clearedArrangementPreviewState({ preserveControls: true }),
    },
  });
  if (!ok) {
    console.warn('[musicStore] Durable history install rejected by transaction', { action });
    return;
  }
  if (markSaved) {
    cancelAutosaveTimer();
    const persistRevision = projectPersistRevisionKey(get().editedMusicJson, get().generationMeta);
    set({
      lastSavedPersistRevision: persistRevision,
      saveStatus: 'saved',
      saveError: '',
    });
  }
  console.info('[musicStore] Durable history result installed', {
    action,
    clearUndo: false,
    hasComposition: true,
    undoDepth: get().compositionEditUndoStack?.length || 0,
  });
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
    arrangementCompareResult: null,
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
