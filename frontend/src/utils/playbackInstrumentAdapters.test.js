import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  createInstrumentProfileCache,
  createToneSamplerAdapter,
  createToneSynthAdapter,
} from './playbackInstrumentAdapters.js';
import {
  buildSampleUrlMap,
  clearBrowserPlaybackManifest,
  prepareTrackInstrumentAdapters,
  setBrowserPlaybackManifest,
} from './playbackInstrumentLoader.js';
import { validateBrowserPlaybackManifest } from './browserPlaybackAssets.js';

const manifestPath = new URL('../../public/audio/dev-tone-fixtures-v1/manifest.json', import.meta.url);
const FIXTURE_MANIFEST = JSON.parse(readFileSync(manifestPath, 'utf8'));

function createFakeTone({ failSampler = false } = {}) {
  class FakeSynth {
    constructor(options = {}) {
      this.options = options;
      this.attacks = [];
      this.releases = [];
      this.connections = [];
      this.disposed = false;
      this.maxPolyphony = options.maxPolyphony;
    }

    connect(node) {
      this.connections.push(node);
      return node;
    }

    disconnect() {}

    triggerAttack(note, time, velocity) {
      this.attacks.push({ note, time, velocity });
    }

    triggerRelease(note, time) {
      this.releases.push({ note, time });
    }

    releaseAll(time) {
      this.releases.push({ all: true, time });
    }

    dispose() {
      this.disposed = true;
    }
  }

  class FakeSampler extends FakeSynth {
    constructor(options = {}) {
      super(options);
      this.urls = options.urls;
      this.baseUrl = options.baseUrl;
      this.loaded = !failSampler;
      if (failSampler) {
        Promise.resolve().then(() => options.onerror?.(new Error('decode_failed')));
      } else {
        Promise.resolve().then(() => options.onload?.());
      }
    }
  }

  return {
    Synth: FakeSynth,
    PolySynth: FakeSynth,
    MonoSynth: FakeSynth,
    MembraneSynth: FakeSynth,
    Sampler: FakeSampler,
  };
}

test('dev fixture manifest validates and maps piano/bass/strings', () => {
  const result = validateBrowserPlaybackManifest(FIXTURE_MANIFEST);
  assert.equal(result.ok, true);
  assert.equal(result.manifest.packId, 'dev-tone-fixtures-v1');
  clearBrowserPlaybackManifest();
  const installed = setBrowserPlaybackManifest(FIXTURE_MANIFEST);
  assert.equal(installed.ok, true);
  const urls = buildSampleUrlMap(
    {
      sampled: true,
      sampleRefs: result.manifest.instruments[0].samples,
    },
    result.manifest,
  );
  assert.ok(urls.C4.startsWith('/audio/dev-tone-fixtures-v1/'));
  clearBrowserPlaybackManifest();
});

test('Tone synth adapter satisfies contract and tracks note ids', async () => {
  const Tone = createFakeTone();
  const adapter = createToneSynthAdapter({
    Tone,
    track: { id: 't1', instrument: 'piano', midi_program: 0 },
  });
  const status = await adapter.prepare();
  assert.equal(status.ready, true);
  assert.equal(status.fallback, true);
  adapter.attack('n1', 'C4', 0.8, 0);
  adapter.release('n1', 0.5, 'C4');
  adapter.releaseAll(1);
  assert.ok(adapter.getToneNode());
  adapter.dispose();
  assert.equal(adapter.getStatus().reasonCode, 'disposed');
});

test('Tone sampler adapter falls back to synth when load fails', async () => {
  const Tone = createFakeTone({ failSampler: true });
  const adapter = createToneSamplerAdapter({
    Tone,
    profileId: 'piano_fixture',
    urls: { C4: '/audio/dev-tone-fixtures-v1/piano/c4.wav' },
    fallbackTrack: { instrument: 'piano', midi_program: 0 },
  });
  const status = await adapter.prepare();
  assert.equal(status.ready, true);
  assert.equal(status.fallback, true);
  assert.equal(status.engine, 'tone_synth_fallback');
  adapter.dispose();
});

test('instrument cache deduplicates concurrent loads', async () => {
  const cache = createInstrumentProfileCache();
  let builds = 0;
  const factory = async () => {
    builds += 1;
    return createToneSynthAdapter({
      Tone: createFakeTone(),
      track: { instrument: 'bass', midi_program: 33 },
      profileId: 'bass_synth',
    });
  };
  const [a, b] = await Promise.all([
    cache.load('bass_synth', factory),
    cache.load('bass_synth', factory),
  ]);
  assert.equal(a, b);
  assert.equal(builds, 1);
  cache.clear();
});

test('prepareTrackInstrumentAdapters uses synth fallback without pack', async () => {
  clearBrowserPlaybackManifest();
  const adapters = await prepareTrackInstrumentAdapters({
    Tone: createFakeTone(),
    tracks: [
      { id: 'p', instrument: 'piano', midi_program: 0 },
      { id: 'b', instrument: 'electric_bass', midi_program: 33 },
    ],
  });
  assert.equal(adapters.size, 2);
  assert.equal(adapters.get('p').getStatus().fallback, true);
  assert.equal(adapters.get('b').getStatus().profileId, 'bass_synth');
});
