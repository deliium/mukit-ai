import test from 'node:test';
import assert from 'node:assert/strict';
import { autonomousStageText } from './autonomousStageText.js';

test('missing stage renders nothing', () => {
  assert.equal(autonomousStageText(null), '');
  assert.equal(autonomousStageText(undefined), '');
  assert.equal(autonomousStageText({}), '');
});

test('status line includes stage id and status', () => {
  assert.equal(
    autonomousStageText({ stage_id: 'symbolic', status: 'completed' }),
    'symbolic completed',
  );
  assert.equal(
    autonomousStageText({ stage_id: 'render', status: 'awaiting_approval' }),
    'render awaiting_approval',
  );
});
