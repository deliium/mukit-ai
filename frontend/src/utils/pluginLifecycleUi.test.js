import test from 'node:test';
import assert from 'node:assert/strict';
import { availablePluginActions, pluginHealthLabel } from './pluginLifecycleUi.js';

test('install is the only action for a discovered plugin', () => {
  assert.deepEqual(availablePluginActions('discovered'), ['install']);
});

test('installed plugins can be enabled or disabled', () => {
  assert.deepEqual(availablePluginActions('installed'), ['enable', 'disable']);
});

test('enabled plugins can be disabled', () => {
  assert.deepEqual(availablePluginActions('enabled'), ['disable']);
});

test('disabled plugins can be enabled', () => {
  assert.deepEqual(availablePluginActions('disabled'), ['enable']);
});

test('failed plugins keep install, enable, and disable', () => {
  assert.deepEqual(availablePluginActions('failed'), ['install', 'enable', 'disable']);
});

test('incompatible plugins have no actions', () => {
  assert.deepEqual(availablePluginActions('incompatible'), []);
  assert.deepEqual(availablePluginActions('mystery'), []);
});

test('health labels name the status and a failure code', () => {
  assert.equal(pluginHealthLabel({ status: 'unknown', code: null }), 'Unknown');
  assert.equal(pluginHealthLabel(null), 'Unknown');
  assert.equal(pluginHealthLabel({ status: 'healthy', code: null }), 'Healthy');
  assert.equal(
    pluginHealthLabel({ status: 'unhealthy', code: 'plugin_register_failed' }),
    'Unhealthy (plugin_register_failed)',
  );
});
