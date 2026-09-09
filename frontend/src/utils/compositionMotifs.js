/**
 * Pure motif-reference helpers for canonical composition.v2.
 * Motifs store identity/provenance only — never playable note payloads.
 */

export const MOTIF_MIN_EVENT_REFS = 3;
export const MOTIF_MAX_EVENT_REFS = 32;
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
