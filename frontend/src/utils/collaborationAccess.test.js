import assert from 'node:assert/strict';
import test from 'node:test';

import {
  collaborationHeaders,
  isScoreMutationShortcut,
  isScoreReadOnly,
  roleAllows,
  setCollaborationActorId,
} from './collaborationAccess.js';

test('collaborationHeaders omits the actor until one is selected', () => {
  setCollaborationActorId('');
  assert.deepEqual(collaborationHeaders(), {});
  setCollaborationActorId('actor-b');
  assert.deepEqual(collaborationHeaders(), { 'X-Mukit-Actor': 'actor-b' });
  setCollaborationActorId('');
});

test('roleAllows matches comment, share, and approve', () => {
  assert.equal(roleAllows('commenter', 'comment'), true);
  assert.equal(roleAllows('commenter', 'write_score'), false);
  assert.equal(roleAllows('viewer', 'comment'), false);
  assert.equal(roleAllows('viewer', 'read'), true);
  assert.equal(roleAllows('editor', 'review_decide'), false);
  assert.equal(roleAllows('editor', 'share'), false);
  assert.equal(roleAllows('editor', 'write_score'), true);
  assert.equal(roleAllows('owner', 'share'), true);
  assert.equal(roleAllows('owner', 'review_decide'), true);
});

test('read-only roles block note-editing shortcuts and keep navigation', () => {
  assert.equal(isScoreMutationShortcut('delete'), true);
  assert.equal(isScoreMutationShortcut('cut'), true);
  assert.equal(isScoreMutationShortcut('paste'), true);
  assert.equal(isScoreMutationShortcut('undo'), true);
  assert.equal(isScoreMutationShortcut('nudgeLeft'), true);
  assert.equal(isScoreMutationShortcut('transposeOctaveUp'), true);
  assert.equal(isScoreMutationShortcut('copy'), false);
  assert.equal(isScoreMutationShortcut('navNextBar'), false);
  assert.equal(isScoreMutationShortcut('zoomIn'), false);
  assert.equal(isScoreMutationShortcut('transportToggle'), false);
});

test('isScoreReadOnly leaves a null collaboration block writable', () => {
  assert.equal(isScoreReadOnly(null), false);
  assert.equal(isScoreReadOnly(undefined), false);
  assert.equal(isScoreReadOnly({ role: 'editor' }), false);
  assert.equal(isScoreReadOnly({ role: 'commenter' }), true);
  assert.equal(isScoreReadOnly({ role: 'viewer' }), true);
});
