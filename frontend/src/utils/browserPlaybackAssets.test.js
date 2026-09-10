import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BROWSER_PLAYBACK_MANIFEST_SCHEMA,
  buildAssetCacheKey,
  mapTrackToAssetProfile,
  resolveLocalAssetUrl,
  validateBrowserPlaybackManifest,
} from './browserPlaybackAssets.js';

function sampleDigest(n = 1) {
  const hex = `${String(n).padStart(2, '0')}${'a'.repeat(62)}`;
  return `sha256:${hex}`;
}

function validManifest(overrides = {}) {
  return {
    schema: BROWSER_PLAYBACK_MANIFEST_SCHEMA,
    packId: 'core-v1',
    version: '1.0.0',
    basePath: '/audio/core-v1/',
    provenance: {
      sourceUrl: 'https://example.invalid/source',
      creator: 'fixture',
      license: 'CC0-1.0',
      sourceHash: sampleDigest(1),
      outputHash: sampleDigest(2),
      redistributionApproved: true,
    },
    instruments: [
      {
        profileId: 'piano_sampled',
        fallbackPresetId: 'piano_keyboard',
        instrumentNames: ['piano', 'acoustic_grand_piano'],
        gmPrograms: [0, 1],
        roleHints: ['piano', 'keyboard'],
        provenance: {
          sourceUrl: 'https://example.invalid/piano',
          creator: 'fixture',
          license: 'CC0-1.0',
          sourceHash: sampleDigest(3),
          redistributionApproved: true,
        },
        samples: [
          {
            path: 'piano/c4.ogg',
            bytes: 1024,
            digest: sampleDigest(4),
            format: 'audio/ogg',
            pitch: 'C4',
          },
        ],
      },
      {
        profileId: 'bass_sampled',
        fallbackPresetId: 'bass_synth',
        instrumentNames: ['bass', 'acoustic_bass'],
        gmPrograms: [32, 33],
        roleHints: ['bass'],
        provenance: {
          sourceUrl: 'https://example.invalid/bass',
          creator: 'fixture',
          license: 'CC0-1.0',
          sourceHash: sampleDigest(5),
          redistributionApproved: true,
        },
        samples: [
          {
            path: 'bass/c2.ogg',
            bytes: 2048,
            digest: sampleDigest(6),
            format: 'audio/ogg',
            pitch: 'C2',
          },
        ],
      },
    ],
    ...overrides,
  };
}

test('validates a versioned allowlisted manifest', () => {
  const result = validateBrowserPlaybackManifest(validManifest());
  assert.equal(result.ok, true);
  assert.equal(result.manifest.packId, 'core-v1');
  assert.equal(result.manifest.instruments.length, 2);
  assert.ok(result.manifest.totalBytes > 0);
});

test('rejects remote and traversal sample paths', () => {
  const bad = validManifest({
    instruments: [
      {
        profileId: 'bad',
        fallbackPresetId: 'piano_keyboard',
        provenance: {
          sourceUrl: 'https://example.invalid/x',
          creator: 'x',
          license: 'CC0-1.0',
          sourceHash: sampleDigest(7),
          redistributionApproved: true,
        },
        samples: [
          {
            path: 'https://cdn.example/piano.ogg',
            bytes: 10,
            digest: sampleDigest(8),
            format: 'audio/ogg',
          },
        ],
      },
    ],
  });
  const result = validateBrowserPlaybackManifest(bad);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((code) => code.includes('unsafe_path')));
});

test('resolveLocalAssetUrl stays same-origin under pack prefix', () => {
  const { manifest } = validateBrowserPlaybackManifest(validManifest());
  const ok = resolveLocalAssetUrl(manifest, 'piano/c4.ogg', { origin: 'http://localhost:5173' });
  assert.equal(ok.ok, true);
  assert.equal(ok.url, 'http://localhost:5173/audio/core-v1/piano/c4.ogg');
  assert.equal(ok.cacheKey, buildAssetCacheKey('core-v1', '1.0.0', 'piano/c4.ogg'));

  const rejected = resolveLocalAssetUrl(manifest, '../secret.ogg');
  assert.equal(rejected.ok, false);
  assert.equal(rejected.reason, 'unsafe_relative_path');
});

test('maps tracks to sampled profiles or synth fallback', () => {
  const { manifest } = validateBrowserPlaybackManifest(validManifest());
  const piano = mapTrackToAssetProfile({ id: 't1', instrument: 'piano', midi_program: 0 }, manifest);
  assert.equal(piano.sampled, true);
  assert.equal(piano.profileId, 'piano_sampled');
  assert.equal(piano.fallbackPresetId, 'piano_keyboard');

  const bassByProgram = mapTrackToAssetProfile({ id: 't2', instrument: '', midi_program: 33 }, manifest);
  assert.equal(bassByProgram.sampled, true);
  assert.equal(bassByProgram.profileId, 'bass_sampled');

  const unknown = mapTrackToAssetProfile({ id: 't3', instrument: 'theremin', midi_program: 120 }, manifest);
  assert.equal(unknown.sampled, false);
  assert.equal(unknown.fallbackPresetId, 'piano_keyboard');
  assert.equal(unknown.reason, 'no_profile_match');
});
