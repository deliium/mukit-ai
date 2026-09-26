/** Local actor selector and role checks. The header is not a credential. */

const ROLE_ACTIONS = {
  viewer: ['read'],
  commenter: ['read', 'comment'],
  editor: ['read', 'comment', 'write_score', 'write_audio', 'review_open'],
  owner: [
    'read',
    'comment',
    'write_score',
    'write_audio',
    'review_open',
    'review_decide',
    'share',
    'delete_project',
  ],
};

let selectedActorId = '';

export function setCollaborationActorId(actorId) {
  selectedActorId = typeof actorId === 'string' ? actorId.trim() : '';
}

export function collaborationHeaders() {
  if (!selectedActorId) {
    return {};
  }
  return { 'X-Mukit-Actor': selectedActorId };
}

export function roleAllows(role, action) {
  const allowed = ROLE_ACTIONS[role];
  if (!allowed) {
    return false;
  }
  return allowed.includes(action);
}

/** Flag-off projects send collaboration null and stay editable. */
export function isScoreReadOnly(collaboration) {
  if (collaboration == null) {
    return false;
  }
  return !roleAllows(collaboration.role, 'write_score');
}

const SCORE_MUTATION_SHORTCUTS = new Set([
  'cut',
  'paste',
  'duplicate',
  'delete',
  'undo',
  'redo',
  'nudgeLeft',
  'nudgeRight',
  'nudgeUp',
  'nudgeDown',
  'transposeOctaveUp',
  'transposeOctaveDown',
]);

/** Commands that rewrite note events. Navigation, zoom, and selection stay available. */
export function isScoreMutationShortcut(command) {
  return SCORE_MUTATION_SHORTCUTS.has(command);
}
