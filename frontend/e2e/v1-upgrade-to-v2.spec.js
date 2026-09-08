import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  compareNoteSequences,
  getNoteSequencesFromStore,
  getStoreSnapshot,
  loadRepoFixture,
  seedV1ProjectViaApi,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

test('V1 project seed opens as composition.v2 with identical note sequences per track', async ({ page, request }) => {
  const v1Fixture = loadRepoFixture('backend/tests/fixtures/composition_v1_16bar_multitrack.json');
  expect(v1Fixture.schema_version).toBe('composition.v1');

  const seeded = await seedV1ProjectViaApi(request, {
    name: 'V1 Upgrade Acceptance',
    fixture: v1Fixture,
  });
  expect(seeded.fixtureSchemaVersion).toBe('composition.v1');
  expect(seeded.sourceNoteSequences['melody-1']?.length).toBeGreaterThan(0);

  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  const snapshot = await getStoreSnapshot(page);
  assertSchemaV2(snapshot);
  expect(snapshot.projectId).toBe(seeded.projectId);
  expect(snapshot.barCount).toBe(v1Fixture.bar_count);

  const openedSequences = await getNoteSequencesFromStore(page);
  expect(compareNoteSequences(seeded.sourceNoteSequences, openedSequences)).toBe(true);
});
