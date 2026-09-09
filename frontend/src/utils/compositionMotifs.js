/**
 * Pure motif-reference helpers for canonical composition.v2.
 * Motifs store identity/provenance only — never playable note payloads.
 */

import { barAtTick, compileTimeline } from './compositionTimeline.js';

export const MOTIF_MIN_EVENT_REFS = 3;
export const MOTIF_MAX_EVENT_REFS = 32;
export const MOTIF_MAX_AUTHORING_BAR_SPAN = 2;
export const MOTIF_AUTHORING_WARNING_INCOMPLETE_TIE = 'motif_incomplete_tie_chain';
export const MOTIF_AUTHORING_WARNING_PERCUSSION = 'motif_percussion_source';
export const MOTIF_AUTHORING_WARNING_BAR_SPAN = 'motif_bar_span_exceeded';
export const MOTIF_AUTHORING_WARNING_COUNT = 'motif_event_count_out_of_range';
export const MOTIF_AUTHORING_WARNING_TRACK = 'motif_track_invalid';
export const MOTIF_AUTHORING_WARNING_UNRESOLVED = 'motif_event_unresolved';
export const MOTIF_RELATIONSHIP_KINDS = Object.freeze([
  'original',
  'repeat',
  'transpose',
  'rhythmic_variation',
  'melodic_variation',
  'inversion',
  'augmentation',
  'diminution',
  'sequence',
  'answer',
  'counterphrase',
]);
export const MOTIF_MECHANICAL_OPERATIONS = Object.freeze([
  'repeat',
  'transpose',
  'inversion',
  'augmentation',
  'diminution',
  'sequence',
]);
export const MOTIF_CREATIVE_OPERATIONS = Object.freeze([
  'rhythmic_variation',
  'melodic_variation',
  'answer',
  'counterphrase',
]);
export const MOTIF_APPLY_OPERATIONS = Object.freeze([
  ...MOTIF_MECHANICAL_OPERATIONS,
  ...MOTIF_CREATIVE_OPERATIONS,
]);
export const MOTIF_CREATIVE_OPERATION_SET = new Set(MOTIF_CREATIVE_OPERATIONS);
export const MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED = 'motif_occurrence_pruned';
export const MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED = 'motif_definition_removed';

const RELATIONSHIP_SET = new Set(MOTIF_RELATIONSHIP_KINDS);

function invalid(message, code = 'motif_invalid') {
  return { valid: false, message, code };
}

function valid(message = 'Motif references are valid.') {
  return { valid: true, message };
}

export function isMotifRelationshipKind(value) {
  return typeof value === 'string' && RELATIONSHIP_SET.has(value);
}

export function indexEventsById(composition) {
  const indexed = new Map();
  if (!composition || !Array.isArray(composition.tracks)) {
    return indexed;
  }
  for (const track of composition.tracks) {
    if (!track || !Array.isArray(track.events)) {
      continue;
    }
    for (const event of track.events) {
      if (event && typeof event.id === 'string' && event.id.trim()) {
        indexed.set(event.id, { track, event });
      }
    }
  }
  return indexed;
}

export function sortEventsChronologically(events) {
  return [...(events || [])].toSorted((a, b) => {
    if (a.start_tick !== b.start_tick) {
      return a.start_tick - b.start_tick;
    }
    if (a.duration_ticks !== b.duration_ticks) {
      return a.duration_ticks - b.duration_ticks;
    }
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

export function collectSelectedEventIdsInOrder(composition, trackId, selectedEventIds) {
  const ids = Array.isArray(selectedEventIds)
    ? selectedEventIds.filter((id) => typeof id === 'string' && id.trim())
    : [];
  if (!ids.length) {
    return [];
  }
  const indexed = indexEventsById(composition);
  const resolved = [];
  for (const eventId of ids) {
    const located = indexed.get(eventId);
    if (!located || located.track.id !== trackId) {
      continue;
    }
    resolved.push(located.event);
  }
  return sortEventsChronologically(resolved).map((event) => event.id);
}

export function isPitchedMotifSourceTrack(track) {
  if (!track || typeof track !== 'object') {
    return false;
  }
  if (track.is_drum || track.role === 'drums' || track.role === 'percussion') {
    return false;
  }
  return true;
}

export function occurrenceIncludesCompleteTieChains(track, eventIds) {
  const referenced = new Set(eventIds || []);
  const indexed = new Map((track?.events || []).map((event) => [event.id, event]));
  for (const eventId of referenced) {
    const event = indexed.get(eventId);
    if (!event?.tie?.group_id) {
      continue;
    }
    const members = tieGroupMembers(track, event);
    const memberIds = members.map((member) => member.id);
    if (!memberIds.every((id) => referenced.has(id))) {
      return false;
    }
  }
  return true;
}

export function findSectionContainingTick(composition, tick) {
  const sections = Array.isArray(composition?.sections) ? composition.sections : [];
  const value = Number(tick);
  if (!Number.isFinite(value)) {
    return null;
  }
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index];
    const start = Number(section?.start_tick);
    const end = start + Number(section?.duration_ticks);
    if (Number.isFinite(start) && Number.isFinite(end) && value >= start && value < end) {
      return { section, index };
    }
  }
  return null;
}

export function resolveOccurrenceLocationLabels(composition, occurrence) {
  const span = deriveMotifOccurrenceSpan(composition, occurrence);
  const timeline = compileTimeline(composition);
  const startBar = timeline ? barAtTick(timeline, span.startTick) : null;
  const attackEndTick = Math.max(
    ...(occurrence.event_ids || []).map((eventId) => {
      const located = indexEventsById(composition).get(eventId);
      return located ? located.event.start_tick : span.startTick;
    }),
  );
  const endBar = timeline ? barAtTick(timeline, attackEndTick) : null;
  const sectionMatch = findSectionContainingTick(composition, span.startTick);
  const section = sectionMatch?.section ?? null;
  return {
    startTick: span.startTick,
    endTick: span.endTick,
    startBar,
    endBar,
    sectionId: typeof section?.id === 'string' ? section.id : null,
    sectionType: typeof section?.type === 'string' ? section.type : null,
    sectionIndex: sectionMatch?.index ?? null,
    sectionLabel: section
      ? (typeof section.label === 'string' && section.label.trim()
        ? section.label.trim()
        : (typeof section.type === 'string' ? section.type : null))
      : null,
  };
}

export function validateMotifAuthoringSelection(composition, { trackId, eventIds } = {}) {
  const normalizedTrackId = typeof trackId === 'string' ? trackId.trim() : '';
  if (!normalizedTrackId) {
    return invalid('A track must be selected for motif authoring.', MOTIF_AUTHORING_WARNING_TRACK);
  }
  const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
  const track = tracks.find((item) => item?.id === normalizedTrackId);
  if (!track) {
    return invalid('Selected track was not found.', MOTIF_AUTHORING_WARNING_TRACK);
  }
  if (!isPitchedMotifSourceTrack(track)) {
    return invalid(
      'Motif source must be a pitched, non-percussion track.',
      MOTIF_AUTHORING_WARNING_PERCUSSION,
    );
  }

  const orderedIds = collectSelectedEventIdsInOrder(composition, normalizedTrackId, eventIds);
  if (orderedIds.length !== (Array.isArray(eventIds) ? eventIds.length : 0)) {
    return invalid('Selection references unresolved or cross-track events.', MOTIF_AUTHORING_WARNING_UNRESOLVED);
  }
  if (orderedIds.length < MOTIF_MIN_EVENT_REFS || orderedIds.length > MOTIF_MAX_EVENT_REFS) {
    return invalid(
      `Motif selection must contain ${MOTIF_MIN_EVENT_REFS}–${MOTIF_MAX_EVENT_REFS} notes.`,
      MOTIF_AUTHORING_WARNING_COUNT,
    );
  }
  if (!occurrenceIncludesCompleteTieChains(track, orderedIds)) {
    return invalid(
      'Motif selection must include complete tie chains.',
      MOTIF_AUTHORING_WARNING_INCOMPLETE_TIE,
    );
  }

  const timeline = compileTimeline(composition);
  if (!timeline) {
    return invalid('Unable to compile composition timeline for motif validation.', 'motif_timeline_unavailable');
  }
  const attacks = orderedIds.map((eventId) => indexEventsById(composition).get(eventId).event.start_tick);
  const firstAttack = Math.min(...attacks);
  const lastAttack = Math.max(...attacks);
  const startBar = barAtTick(timeline, firstAttack);
  const endBar = barAtTick(timeline, lastAttack);
  const barSpan = endBar - startBar + 1;
  if (barSpan > MOTIF_MAX_AUTHORING_BAR_SPAN) {
    return invalid(
      `Motif note attacks must span at most ${MOTIF_MAX_AUTHORING_BAR_SPAN} bars.`,
      MOTIF_AUTHORING_WARNING_BAR_SPAN,
    );
  }

  const location = resolveOccurrenceLocationLabels(composition, {
    track_id: normalizedTrackId,
    event_ids: orderedIds,
  });

  console.debug('[compositionMotifs] Motif authoring selection validated', {
    trackId: normalizedTrackId,
    eventCount: orderedIds.length,
    startBar,
    endBar,
    barSpan,
  });

  return {
    valid: true,
    message: 'Motif selection is valid for authoring.',
    trackId: normalizedTrackId,
    eventIds: orderedIds,
    startBar,
    endBar,
    barSpan,
    ...location,
  };
}

export function applyMotifReconciliation(composition, removedEventIds) {
  const { motifs, warnings } = reconcileMotifsForRemovedEventIds(
    composition?.motifs,
    removedEventIds,
  );
  return {
    composition: {
      ...composition,
      motifs,
    },
    warnings,
  };
}

function canonicalUsageKey(trackId, eventIds) {
  return `${trackId}:${(eventIds || []).join('\0')}`;
}

export function projectMotifUsagesForDisplay(composition, {
  analysisReport = null,
  detectedUsages = [],
  includeDetected = true,
} = {}) {
  const usages = [];
  const seenCanonical = new Set();
  const motifs = Array.isArray(composition?.motifs) ? composition.motifs : [];

  for (const motif of motifs) {
    for (const occurrence of motif.occurrences || []) {
      let location;
      try {
        location = resolveOccurrenceLocationLabels(composition, occurrence);
      } catch (error) {
        console.warn('[compositionMotifs] Skipping unresolved canonical occurrence', {
          motifId: motif.id,
          occurrenceId: occurrence.id,
          message: error.message,
        });
        continue;
      }
      const key = canonicalUsageKey(occurrence.track_id, occurrence.event_ids);
      seenCanonical.add(key);
      usages.push({
        key: `canonical:${motif.id}:${occurrence.id}`,
        source: 'canonical',
        motifId: motif.id,
        motifLabel: motif.label,
        occurrenceId: occurrence.id,
        familyId: null,
        trackId: occurrence.track_id,
        eventIds: [...occurrence.event_ids],
        relationship: occurrence.relationship,
        identityScore: occurrence.relationship === 'original' ? 1 : null,
        stale: false,
        truncated: false,
        ...location,
      });
    }
  }

  if (includeDetected && Array.isArray(detectedUsages) && detectedUsages.length) {
    for (const detected of detectedUsages) {
      const key = canonicalUsageKey(detected.trackId, detected.eventIds);
      if (seenCanonical.has(key)) {
        continue;
      }
      usages.push({
        key: detected.key,
        source: 'detected',
        motifId: detected.familyId,
        motifLabel: detected.label,
        occurrenceId: detected.occurrenceId,
        familyId: detected.familyId,
        trackId: detected.trackId,
        eventIds: detected.eventIds,
        relationship: detected.relationship,
        identityScore: detected.identityScore,
        stale: Boolean(detected.stale),
        truncated: Boolean(detected.truncated),
        startTick: detected.startTick,
        endTick: detected.endTick,
        startBar: detected.startBar,
        endBar: detected.endBar,
        sectionId: detected.sectionId,
        sectionType: detected.sectionType,
        sectionIndex: detected.sectionIndex,
        sectionLabel: detected.sectionLabel,
        staleReason: detected.staleReason || null,
      });
    }
  }

  console.debug('[compositionMotifs] Projected motif usages for display', {
    canonicalCount: motifs.reduce((total, motif) => total + (motif.occurrences?.length || 0), 0),
    detectedCount: usages.filter((item) => item.source === 'detected').length,
    totalCount: usages.length,
    analysisStatus: analysisReport?.status || null,
  });

  return usages;
}

export function deriveMotifOccurrenceSpan(composition, occurrence) {
  const indexed = indexEventsById(composition);
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const eventId of occurrence.event_ids) {
    const located = indexed.get(eventId);
    if (!located) {
      throw new Error(`Unresolved motif event id: ${eventId}`);
    }
    if (located.track.id !== occurrence.track_id) {
      throw new Error(`Event ${eventId} is not on track ${occurrence.track_id}`);
    }
    start = Math.min(start, located.event.start_tick);
    end = Math.max(end, located.event.start_tick + located.event.duration_ticks);
  }
  return { startTick: start, endTick: end };
}

function tieGroupMembers(track, event) {
  if (!event.tie || !event.tie.group_id) {
    return [event];
  }
  const groupId = event.tie.group_id;
  return (track.events || [])
    .filter((item) => item.tie && item.tie.group_id === groupId)
    .toSorted((a, b) => {
      if (a.start_tick !== b.start_tick) {
        return a.start_tick - b.start_tick;
      }
      if (a.duration_ticks !== b.duration_ticks) {
        return a.duration_ticks - b.duration_ticks;
      }
      return String(a.id || '').localeCompare(String(b.id || ''));
    });
}

export function validateMotifDefinitions(composition) {
  const motifs = composition?.motifs;
  if (motifs == null) {
    return valid('Motifs default empty.');
  }
  if (!Array.isArray(motifs)) {
    return invalid('motifs must be an array.');
  }
  if (motifs.length === 0) {
    return valid('Motifs collection is empty.');
  }

  const motifIds = new Set();
  const motifLabels = new Set();
  const occurrenceIds = new Set();
  const tracksById = new Map(
    (composition.tracks || []).filter(Boolean).map((track) => [track.id, track]),
  );
  const indexed = indexEventsById(composition);

  console.debug('[compositionMotifs] Validating motif definitions', {
    motifCount: motifs.length,
  });

  for (const motif of motifs) {
    if (!motif || typeof motif !== 'object' || Array.isArray(motif)) {
      return invalid('Each motif must be an object.');
    }
    if (motif.pitch != null || motif.events != null || motif.notes != null) {
      return invalid('Motif definitions must not embed note payloads.', 'motif_note_payload');
    }
    if (typeof motif.id !== 'string' || !motif.id.trim()) {
      return invalid('Motif id must be a non-empty string.');
    }
    if (typeof motif.label !== 'string' || !motif.label.trim()) {
      return invalid('Motif label must be a non-empty string.');
    }
    if (motifIds.has(motif.id)) {
      return invalid('Motif ids must be unique.');
    }
    if (motifLabels.has(motif.label)) {
      return invalid('Motif labels must be unique.');
    }
    motifIds.add(motif.id);
    motifLabels.add(motif.label);

    if (!Array.isArray(motif.occurrences) || motif.occurrences.length < 1) {
      return invalid('Each motif requires at least one occurrence.');
    }

    let originalCount = 0;
    for (const occurrence of motif.occurrences) {
      if (!occurrence || typeof occurrence !== 'object') {
        return invalid('Motif occurrence must be an object.');
      }
      if (
        occurrence.pitch != null ||
        occurrence.pitches != null ||
        occurrence.notes != null ||
        occurrence.events != null
      ) {
        return invalid('Motif occurrences must not embed note payloads.', 'motif_note_payload');
      }
      if (typeof occurrence.id !== 'string' || !occurrence.id.trim()) {
        return invalid('Motif occurrence id must be a non-empty string.');
      }
      if (occurrenceIds.has(occurrence.id)) {
        return invalid('Motif occurrence ids must be unique.');
      }
      occurrenceIds.add(occurrence.id);

      if (typeof occurrence.track_id !== 'string' || !occurrence.track_id.trim()) {
        return invalid('Motif occurrence track_id must be a non-empty string.');
      }
      if (!isMotifRelationshipKind(occurrence.relationship)) {
        return invalid('Motif occurrence relationship is unsupported.');
      }
      if (occurrence.relationship === 'original') {
        originalCount += 1;
        if (occurrence.transform != null) {
          return invalid('original motif occurrence must not carry transform provenance.');
        }
      } else if (occurrence.transform != null) {
        if (
          !occurrence.transform ||
          typeof occurrence.transform !== 'object' ||
          occurrence.transform.operation !== occurrence.relationship
        ) {
          return invalid('transform.operation must match occurrence relationship.');
        }
        if (
          occurrence.transform.variation_strength != null &&
          (typeof occurrence.transform.variation_strength !== 'number' ||
            !Number.isFinite(occurrence.transform.variation_strength) ||
            occurrence.transform.variation_strength < 0 ||
            occurrence.transform.variation_strength > 1)
        ) {
          return invalid('variation_strength must be a finite float in 0..1.');
        }
      }

      if (!Array.isArray(occurrence.event_ids)) {
        return invalid('Motif occurrence event_ids must be an array.');
      }
      if (
        occurrence.event_ids.length < MOTIF_MIN_EVENT_REFS ||
        occurrence.event_ids.length > MOTIF_MAX_EVENT_REFS
      ) {
        return invalid(
          `Motif occurrence event_ids must contain ${MOTIF_MIN_EVENT_REFS}–${MOTIF_MAX_EVENT_REFS} ids.`,
        );
      }
      if (new Set(occurrence.event_ids).size !== occurrence.event_ids.length) {
        return invalid('Motif occurrence event_ids must be duplicate-free.');
      }

      const track = tracksById.get(occurrence.track_id);
      if (!track) {
        return invalid('Motif occurrence references unknown track.', 'motif_track_missing');
      }
      if (track.is_drum || track.role === 'drums' || track.role === 'percussion') {
        return invalid(
          'Motif occurrence must reference a non-percussion pitched track.',
          'motif_percussion_source',
        );
      }

      const resolved = [];
      for (const eventId of occurrence.event_ids) {
        if (typeof eventId !== 'string' || !eventId.trim()) {
          return invalid('Motif occurrence event_ids must be non-empty strings.');
        }
        const located = indexed.get(eventId);
        if (!located) {
          return invalid(
            'Motif occurrence references unresolved event id.',
            'motif_event_unresolved',
          );
        }
        if (located.track.id !== occurrence.track_id) {
          return invalid(
            'Motif occurrence event belongs to another track.',
            'motif_event_track_mismatch',
          );
        }
        resolved.push(located.event);
      }

      const orderedIds = resolved
        .toSorted((a, b) => {
          if (a.start_tick !== b.start_tick) {
            return a.start_tick - b.start_tick;
          }
          if (a.duration_ticks !== b.duration_ticks) {
            return a.duration_ticks - b.duration_ticks;
          }
          return String(a.id || '').localeCompare(String(b.id || ''));
        })
        .map((event) => event.id);
      if (orderedIds.join('\0') !== occurrence.event_ids.join('\0')) {
        return invalid('Motif occurrence event_ids must be chronological.');
      }

      const referenced = new Set(occurrence.event_ids);
      for (const event of resolved) {
        if (!event.tie) {
          continue;
        }
        const members = tieGroupMembers(track, event);
        const memberIds = members.map((member) => member.id);
        if (memberIds.some((id) => typeof id !== 'string' || !id)) {
          return invalid('Motif occurrence references a tie chain with missing ids.');
        }
        if (!memberIds.every((id) => referenced.has(id))) {
          return invalid('Motif occurrence must include complete tie chains.');
        }
      }
    }

    if (originalCount !== 1) {
      return invalid('Each motif definition requires exactly one original occurrence.');
    }
  }

  console.debug('[compositionMotifs] Motif validation completed', {
    motifCount: motifs.length,
    occurrenceCount: occurrenceIds.size,
  });
  return valid();
}

/**
 * Count note events on a track overlapping [startTick, endTick).
 */
export function countDestinationOverlapEvents(composition, trackId, startTick, endTick) {
  const track = (composition?.tracks || []).find((item) => item?.id === trackId);
  if (!track || !Array.isArray(track.events)) {
    return 0;
  }
  const start = Number(startTick);
  const end = Number(endTick);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return 0;
  }
  let count = 0;
  for (const event of track.events) {
    if (!event || (event.type && event.type !== 'note')) {
      continue;
    }
    const eventStart = Number(event.start_tick);
    const eventEnd = eventStart + Number(event.duration_ticks);
    if (eventStart < end && eventEnd > start) {
      count += 1;
    }
  }
  return count;
}

export function resolveMotifOccurrenceBarSpan(composition, occurrence) {
  if (!occurrence || typeof occurrence !== 'object') {
    return { valid: false, startBar: null, endBar: null, barSpan: null };
  }
  try {
    const location = resolveOccurrenceLocationLabels(composition, occurrence);
    const startBar = Number(location.startBar);
    const endBar = Number(location.endBar);
    if (!Number.isFinite(startBar) || !Number.isFinite(endBar)) {
      return { valid: false, startBar: null, endBar: null, barSpan: null };
    }
    return {
      valid: true,
      startBar,
      endBar,
      barSpan: endBar - startBar + 1,
      ...location,
    };
  } catch (error) {
    console.warn('[compositionMotifs] Unable to resolve occurrence bar span', {
      occurrenceId: occurrence.id || null,
      message: error.message,
    });
    return { valid: false, startBar: null, endBar: null, barSpan: null };
  }
}

export function nextMotifLabel(existingMotifs = []) {
  const used = new Set(
    (existingMotifs || [])
      .map((motif) => (typeof motif?.label === 'string' ? motif.label.trim() : ''))
      .filter(Boolean),
  );
  let index = 0;
  while (index < 26 * 26) {
    const letter = String.fromCharCode(65 + (index % 26));
    const cycle = Math.floor(index / 26);
    const label = cycle === 0 ? `Motif ${letter}` : `Motif ${letter}${cycle + 1}`;
    if (!used.has(label)) {
      return label;
    }
    index += 1;
  }
  return `Motif ${existingMotifs.length + 1}`;
}

export function reconcileMotifsForRemovedEventIds(motifs, removedEventIds) {
  const removed = removedEventIds instanceof Set ? removedEventIds : new Set(removedEventIds || []);
  if (!Array.isArray(motifs) || motifs.length === 0 || removed.size === 0) {
    return { motifs: Array.isArray(motifs) ? motifs : [], warnings: [] };
  }

  console.debug('[compositionMotifs] Reconciling motif references', {
    motifCount: motifs.length,
    removedEventIdCount: removed.size,
  });

  const nextMotifs = [];
  const warnings = [];

  for (const motif of motifs) {
    const surviving = [];
    let originalRemoved = false;
    for (const occurrence of motif.occurrences || []) {
      const hit = (occurrence.event_ids || []).some((eventId) => removed.has(eventId));
      if (hit) {
        warnings.push({
          code: MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
          motifId: motif.id,
          occurrenceId: occurrence.id,
        });
        console.warn('[compositionMotifs] Pruned motif occurrence', {
          code: MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
          motifId: motif.id,
          occurrenceId: occurrence.id,
        });
        if (occurrence.relationship === 'original') {
          originalRemoved = true;
        }
        continue;
      }
      surviving.push(occurrence);
    }

    if (originalRemoved || surviving.length === 0) {
      warnings.push({
        code: MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
        motifId: motif.id,
        occurrenceId: null,
      });
      console.warn('[compositionMotifs] Removed motif definition', {
        code: MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
        motifId: motif.id,
      });
      continue;
    }

    nextMotifs.push({ ...motif, occurrences: surviving });
  }

  console.debug('[compositionMotifs] Reconciliation completed', {
    motifCountBefore: motifs.length,
    motifCountAfter: nextMotifs.length,
    warningCount: warnings.length,
  });
  return { motifs: nextMotifs, warnings };
}
