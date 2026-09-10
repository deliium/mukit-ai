import assert from 'node:assert/strict';
import test from 'node:test';

import {
  barToStartTick,
  clampEditCursorTick,
  fitCompositionZoom,
  gotoNextBar,
  gotoNextSection,
  gotoPrevBar,
  gotoPrevSection,
  gotoSection,
  listSectionsForNavigation,
  scrollLeftForCenterTick,
  selectionZoomWindow,
  stepZoom,
  tickToBar,
} from './editorNavigation.js';
import { compileTimeline } from './compositionTimeline.js';

function baseComposition(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 8,
    duration_ticks: 8 * 1920,
    sections: [
      {
        id: 'intro',
        type: 'intro',
        label: 'Opening',
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
      {
        type: 'verse',
        start_bar: 3,
        bar_count: 4,
        start_tick: 3840,
        duration_ticks: 7680,
      },
      {
        type: 'unsectioned',
        start_bar: 7,
        bar_count: 2,
        start_tick: 11520,
        duration_ticks: 3840,
      },
    ],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        pan: 0,
        events: [
          { type: 'note', id: 'n1', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { type: 'note', id: 'n2', pitch: 'E4', start_tick: 4000, duration_ticks: 960, velocity: 88 },
        ],
      },
    ],
    harmony: [],
    ...overrides,
  };
}

function variableMeterComposition() {
  // 4 bars: 4/4, 4/4, 3/4, 3/4 → 1920+1920+1440+1440 = 6720
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 6720,
    time_signature_changes: [{ tick: 3840, time_signature: '3/4' }],
    sections: [
      {
        id: 'a',
        type: 'intro',
        start_bar: 1,
        bar_count: 2,
        start_tick: 0,
        duration_ticks: 3840,
      },
      {
        id: 'b',
        type: 'bridge',
        start_bar: 3,
        bar_count: 2,
        start_tick: 3840,
        duration_ticks: 2880,
      },
    ],
    tracks: [{
      id: 't1',
      name: 'T',
      instrument: 'piano',
      role: 'melody',
      midi_program: 0,
      channel: 1,
      is_drum: false,
      volume: 100,
      pan: 0,
      events: [],
    }],
    harmony: [],
  };
}

test('clampEditCursorTick clamps to duration_ticks', () => {
  const composition = baseComposition();
  assert.equal(clampEditCursorTick(composition, -10), 0);
  assert.equal(clampEditCursorTick(composition, 100), 100);
  assert.equal(clampEditCursorTick(composition, 999999), composition.duration_ticks);
  assert.equal(clampEditCursorTick(composition, 100.6), 101);
  assert.equal(clampEditCursorTick(null, 50), 50);
});

test('tickToBar accepts composition or compiled timeline', () => {
  const composition = baseComposition();
  const timeline = compileTimeline(composition);
  assert.equal(tickToBar(composition, 0), 1);
  assert.equal(tickToBar(timeline, 1920), 2);
  assert.equal(tickToBar(composition, composition.duration_ticks), 8);
  assert.equal(tickToBar(null, 0), null);
});

test('barToStartTick and bar prev/next respect constant meter', () => {
  const composition = baseComposition();
  assert.equal(barToStartTick(composition, 1), 0);
  assert.equal(barToStartTick(composition, 3), 3840);
  assert.equal(barToStartTick(composition, 99), null);

  assert.equal(gotoPrevBar(composition, 100), 0);
  assert.equal(gotoPrevBar(composition, 0), 0);
  assert.equal(gotoPrevBar(composition, 1920), 0);
  assert.equal(gotoNextBar(composition, 0), 1920);
  assert.equal(gotoNextBar(composition, 100), 1920);
  assert.equal(gotoNextBar(composition, composition.duration_ticks), composition.duration_ticks);
});

test('variable-meter bar jumps use compiled boundaries', () => {
  const composition = variableMeterComposition();
  assert.equal(barToStartTick(composition, 1), 0);
  assert.equal(barToStartTick(composition, 3), 3840);
  assert.equal(barToStartTick(composition, 4), 5280);
  assert.equal(tickToBar(composition, 4000), 3);
  assert.equal(gotoNextBar(composition, 3840), 5280);
  assert.equal(gotoPrevBar(composition, 5280), 3840);
  assert.equal(gotoPrevBar(composition, 4000), 3840);
});

test('listSectionsForNavigation labels unlabeled and unsectioned', () => {
  const composition = baseComposition();
  const sections = listSectionsForNavigation(composition);
  assert.equal(sections.length, 3);
  assert.equal(sections[0].label, 'Opening');
  assert.equal(sections[0].key, 'id:intro');
  assert.equal(sections[0].startBar, 1);
  assert.equal(sections[0].endBar, 2);
  assert.match(sections[1].label, /verse/);
  assert.match(sections[1].key, /^idx:/);
  assert.equal(sections[2].label, 'Unsectioned 3');
  assert.equal(sections[2].startTick, 11520);
  assert.equal(sections[2].endTick, 15360);
});

test('listSectionsForNavigation synthesizes coverage when sections empty', () => {
  const composition = baseComposition({ sections: [] });
  const sections = listSectionsForNavigation(composition);
  assert.equal(sections.length, 1);
  assert.equal(sections[0].label, 'Unsectioned');
  assert.equal(sections[0].startTick, 0);
  assert.equal(sections[0].endTick, composition.duration_ticks);
  assert.equal(sections[0].endBar, 8);
});

test('section navigation prev/next/goto', () => {
  const composition = baseComposition();
  const sections = listSectionsForNavigation(composition);

  assert.equal(gotoSection(composition, 'id:intro'), 0);
  assert.equal(gotoSection(composition, sections[1].key), 3840);
  assert.equal(gotoSection(composition, 'missing'), 0);

  // Mid-section → snap to current start
  assert.equal(gotoPrevSection(composition, 1000), 0);
  // At start → previous section
  assert.equal(gotoPrevSection(composition, 3840), 0);
  assert.equal(gotoNextSection(composition, 0), 3840);
  assert.equal(gotoNextSection(composition, 3840), 11520);
  assert.equal(gotoNextSection(composition, 11520), 11520);
});

test('variable-meter section boundaries', () => {
  const composition = variableMeterComposition();
  const sections = listSectionsForNavigation(composition);
  assert.equal(sections[0].endTick, 3840);
  assert.equal(sections[1].startTick, 3840);
  assert.equal(sections[1].endTick, 6720);
  assert.equal(gotoNextSection(composition, 0), 3840);
  assert.equal(gotoPrevSection(composition, 5000), 3840);
});

test('fitCompositionZoom clamps into min/max', () => {
  const fitted = fitCompositionZoom({
    durationTicks: 10000,
    clientWidth: 500,
    minZoom: 0.01,
    maxZoom: 0.25,
  });
  assert.equal(fitted.pixelsPerTick, 0.05);
  assert.equal(fitted.clamped, false);

  const clampedHigh = fitCompositionZoom({
    durationTicks: 100,
    clientWidth: 500,
    minZoom: 0.01,
    maxZoom: 0.25,
  });
  assert.equal(clampedHigh.pixelsPerTick, 0.25);
  assert.equal(clampedHigh.clamped, true);

  const clampedLow = fitCompositionZoom({
    durationTicks: 100000,
    clientWidth: 200,
    minZoom: 0.01,
    maxZoom: 0.25,
  });
  assert.equal(clampedLow.pixelsPerTick, 0.01);
  assert.equal(clampedLow.clamped, true);
});

test('selectionZoomWindow returns scroll and optional zoom', () => {
  const composition = baseComposition();
  const empty = selectionZoomWindow(composition, [], {
    pixelsPerTick: 0.05,
    clientWidth: 400,
  });
  assert.equal(empty.ok, false);

  const windowed = selectionZoomWindow(
    composition,
    [{ trackId: 'melody-1', eventId: 'n2' }],
    {
      pixelsPerTick: 0.05,
      clientWidth: 400,
      paddingTicks: 0,
      adjustZoom: true,
      minZoom: 0.01,
      maxZoom: 0.25,
    },
  );
  assert.equal(windowed.ok, true);
  assert.ok(windowed.pixelsPerTick >= 0.01);
  assert.ok(windowed.pixelsPerTick <= 0.25);
  assert.ok(Number.isFinite(windowed.scrollLeft));
  assert.ok(windowed.centerTick >= windowed.startTick);
});

test('stepZoom and scrollLeftForCenterTick', () => {
  assert.ok(stepZoom(0.05, 1) > 0.05);
  assert.ok(stepZoom(0.05, -1) < 0.05);
  assert.equal(stepZoom(0.25, 1), 0.25);
  assert.equal(stepZoom(0.01, -1), 0.01);

  const centered = scrollLeftForCenterTick({
    centerTick: 5000,
    pixelsPerTick: 0.1,
    clientWidth: 200,
    durationTicks: 15360,
  });
  assert.equal(centered.centerTick, 5000);
  assert.equal(centered.scrollLeft, 5000 * 0.1 - 100);
});
