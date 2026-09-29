import { expect, test } from '@playwright/test';

import { assertSchemaV2, backendBaseUrl, createProjectAndGenerateExpressive, getStoreSnapshot } from './helpers.js';

function decisionScore() {
  const layers = [
    ['layer-pad', 'ambient', 'track-pad', 0, 1, null, 0, 'cut', 'cut', 0, 0],
    ['layer-piano', 'harmony', 'track-piano', 0, 1, null, 0, 'cut', 'cut', 0, 0],
    ['layer-bass', 'bass', 'track-bass', 0.5, 1, null, 0, 'linear', 'linear', 400, 400],
    ['layer-strings', 'strings', 'track-strings', 0.5, 1, null, 0, 'linear', 'linear', 400, 400],
    ['layer-perc', 'percussion', 'track-perc', 0.8, 1, null, 0, 'cut', 'cut', 0, 0],
    ['layer-brass-hint', 'brass', 'track-brass', 0.9, 1, 'orchestration', 0, 'bar', 'bar', 0, 0],
    ['layer-orch', 'brass', 'track-orch', 1, 1, 'orchestration', 1, 'bar', 'bar', 0, 0],
  ];
  return {
    schema_version: 'adaptive.score.v1',
    name: 'exploration_suspense_combat_victory',
    initial_state_id: 'state-exploration',
    default_state_id: 'state-exploration',
    states: [
      {
        id: 'state-exploration',
        name: 'Exploration',
        intensity: 0.2,
        material: { kind: 'section', section_id: 'section-exploration' },
        loop: { enabled: true, start_bar: 1, end_bar: 4 },
        transition_ids: ['tr-explore-suspense'],
      },
      {
        id: 'state-suspense',
        name: 'Suspense',
        intensity: 0.4,
        material: { kind: 'section', section_id: 'section-suspense' },
        transition_ids: ['tr-suspense-combat'],
      },
      {
        id: 'state-combat',
        name: 'Combat',
        intensity: 0.8,
        material: { kind: 'section', section_id: 'section-combat' },
        loop: { enabled: true, start_bar: 9, end_bar: 12 },
        transition_ids: ['tr-combat-victory'],
      },
      {
        id: 'state-victory',
        name: 'Victory',
        intensity: 0.6,
        material: { kind: 'section', section_id: 'section-victory' },
        transition_ids: [],
      },
    ],
    transitions: [
      {
        id: 'tr-explore-suspense',
        from_state_id: 'state-exploration',
        to_state_id: 'state-suspense',
        quantization: 'loop_end',
        priority: 0,
        conditions: [],
        realization: { kind: 'cut' },
      },
      {
        id: 'tr-suspense-combat',
        from_state_id: 'state-suspense',
        to_state_id: 'state-combat',
        quantization: 'next_exit',
        priority: 0,
        conditions: [],
        realization: {
          kind: 'phrase',
          phrase_material: { kind: 'bar_range', start_bar: 9, end_bar: 9 },
        },
      },
      {
        id: 'tr-combat-victory',
        from_state_id: 'state-combat',
        to_state_id: 'state-victory',
        quantization: 'loop_end',
        priority: 0,
        conditions: [],
        realization: { kind: 'stinger', stinger_id: 'stinger-victory' },
      },
    ],
    layers: layers.map(([
      id, role, trackId, low, high, group, priority, inPolicy, outPolicy, fadeIn, fadeOut,
    ]) => ({
      id,
      name: id,
      state_id: null,
      role,
      material: { kind: 'track_range', track_ids: [trackId], start_bar: 1, end_bar: 16 },
      intensity_min: low,
      intensity_max: high,
      mix_hint: 'bed',
      exclusive_group: group,
      priority,
      fade: {
        in_policy: inPolicy,
        out_policy: outPolicy,
        fade_in_ms: fadeIn,
        fade_out_ms: fadeOut,
      },
    })),
    stingers: [{
      id: 'stinger-victory',
      name: 'Victory sting',
      material: { kind: 'track_range', track_ids: ['track-stinger'], start_bar: 13, end_bar: 13 },
      interrupt_policy: 'overlay',
      quantization: 'bar',
      retrigger: 'once',
    }],
  };
}

function decisionComposition() {
  const types = ['verse', 'chorus', 'bridge', 'outro'];
  const ids = ['section-exploration', 'section-suspense', 'section-combat', 'section-victory'];
  const roles = ['pad', 'harmony', 'bass', 'other', 'percussion', 'other', 'other', 'other'];
  const tracks = [
    'track-pad', 'track-piano', 'track-bass', 'track-strings',
    'track-perc', 'track-brass', 'track-orch', 'track-stinger',
  ];
  return {
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 30720,
    sections: ids.map((id, index) => ({
      id,
      type: types[index],
      start_bar: 1 + index * 4,
      bar_count: 4,
      start_tick: index * 7680,
      duration_ticks: 7680,
    })),
    tracks: tracks.map((id, index) => ({
      id,
      name: id,
      instrument: 'piano',
      role: roles[index],
      midi_program: 0,
      channel: index + 1,
      events: [{
        type: 'note',
        pitch: 'C4',
        start_tick: 0,
        duration_ticks: 480,
        velocity: 80,
      }],
    })),
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
  };
}

test('Adaptive playback keeps the transport after a missing state and arms Suspense', async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const created = await request.post(`${backendBaseUrl()}/projects`, {
    data: { name: 'Adaptive playback', composition: decisionComposition() },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const project = await created.json();
  const posted = await request.post(`${backendBaseUrl()}/projects/${project.id}/adaptive-scores`, {
    data: { is_default: true, score: decisionScore() },
  });
  expect(posted.ok(), await posted.text()).toBeTruthy();

  const opened = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(opened);
  await page.evaluate((projectId) => window.__MUKIT_MUSIC_STORE__.getState().openProject(projectId), project.id);
  await expect.poll(async () => (await getStoreSnapshot(page))?.projectId).toBe(project.id);

  await page.getByTestId('composer-tab-adaptive').click();
  await expect(page.getByTestId('adaptive-score-panel')).toBeVisible();
  await page.getByTestId('adaptive-state-state-combat').click();
  await expect(page.getByTestId('adaptive-current-state')).toContainText('Combat');

  await page.getByTestId('adaptive-playback-play').click();
  await expect(page.getByTestId('adaptive-playback-transport')).toHaveText('playing');
  await expect(page.getByTestId('adaptive-current-state')).toContainText('Combat');

  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().requestAdaptivePlaybackState('state-missing'));
  await expect(page.getByTestId('adaptive-playback-transport')).toHaveText('playing');

  await page.getByTestId('adaptive-playback-request-state-suspense').click();
  await expect(page.getByTestId('adaptive-playback-pending')).toContainText('7680');
  await expect(page.getByTestId('adaptive-current-state')).toContainText('Combat');
  await expect.poll(async () => page.evaluate(
    () => window.__MUKIT_PLAYBACK_ENGINE__?.getTransportState?.() ?? null,
  )).toBe('started');

  await page.getByTestId('composer-tab-piano').click();
  await expect(page.getByTestId('piano-roll-grid')).toBeVisible();
  await page.getByTestId('composer-tab-adaptive').click();
  await page.getByTestId('adaptive-playback-stop').click();
  await expect(page.getByTestId('adaptive-playback-transport')).toHaveText('stopped');
  await expect.poll(async () => page.evaluate(
    () => window.__MUKIT_PLAYBACK_ENGINE__?.getTransportState?.() ?? null,
  )).toBe('stopped');
});
