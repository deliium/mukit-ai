import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  loadComposerProfileSessionPreference,
  saveComposerProfileSessionPreference,
} from './composerProfileApi.js';

describe('composerProfileApi session preference', () => {
  it('round-trips selected id and strength via localStorage stub', () => {
    const store = new Map();
    const storage = {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => { store.set(key, String(value)); },
      removeItem: (key) => { store.delete(key); },
    };
    saveComposerProfileSessionPreference({ profileId: 'prof_abc', strength: 'normal' }, storage);
    const loaded = loadComposerProfileSessionPreference(storage);
    assert.equal(loaded.profileId, 'prof_abc');
    assert.equal(loaded.strength, 'normal');
  });

  it('defaults when missing or invalid', () => {
    const storage = {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    };
    const loaded = loadComposerProfileSessionPreference(storage);
    assert.equal(loaded.profileId, null);
    assert.equal(loaded.strength, 'off');
  });

  it('defaults when storage is unavailable', () => {
    const loaded = loadComposerProfileSessionPreference(null);
    assert.equal(loaded.profileId, null);
    assert.equal(loaded.strength, 'off');
  });
});
