/** Shared helpers for V1 Playwright journeys. */

export async function getStoreSnapshot(page) {
  return page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__;
    if (!store) {
      return null;
    }
    const state = store.getState();
    const composition = state.editedMusicJson;
    const eventCount = Array.isArray(composition?.tracks)
      ? composition.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0)
      : 0;
    return {
      projectId: state.currentProjectId,
      projectName: state.currentProjectName,
      barCount: composition?.bar_count ?? null,
      trackCount: composition?.tracks?.length ?? 0,
      eventCount,
      playbackStatus: state.playbackStatus,
      saveStatus: state.saveStatus,
      selectedProvider: state.selectedProvider,
      selectedModel: state.selectedModel,
      melodyFirstPitch: composition?.tracks?.find((t) => t.id === 'melody-1')?.events?.[0]?.pitch ?? null,
    };
  });
}

export async function waitForCompositionNotes(page, { minEvents = 1, timeout = 60_000 } = {}) {
  await page.waitForFunction(
    (minimum) => {
      const store = window.__MUKIT_MUSIC_STORE__;
      if (!store) return false;
      const composition = store.getState().editedMusicJson;
      if (!composition?.tracks?.length) return false;
      const events = composition.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0);
      return events >= minimum;
    },
    minEvents,
    { timeout },
  );
}

export async function createProjectAndGenerate(page) {
  await page.goto('/');
  await page.getByTestId('new-project').click();
  await page.getByTestId('llm-model-select').waitFor({ state: 'visible', timeout: 30_000 });

  // Prefer fake provider when listed.
  const options = page.getByTestId('llm-model-select').locator('option');
  const values = await options.evaluateAll((nodes) => nodes.map((node) => node.value));
  const fake = values.find((value) => value.startsWith('fake:'));
  if (fake) {
    await page.getByTestId('llm-model-select').selectOption(fake);
  }

  await page.locator('#durationBars').fill('16');
  await page.getByTestId('generate-music').click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 90_000 });
  return getStoreSnapshot(page);
}

/** Piano-roll note edit via the same Zustand actions the UI uses (avoids brittle grid hit-testing). */
export async function editMelodyNoteViaStore(page) {
  return page.evaluate(() => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const track = (state.editedMusicJson?.tracks || []).find((item) => item.id === 'melody-1')
      || (state.editedMusicJson?.tracks || [])[0];
    if (!track?.events?.length) {
      return { ok: false, reason: 'no melody events' };
    }
    const note = track.events[0];
    const nextPitch = note.pitch === 'C5' ? 'D5' : 'C5';
    const updated = state.updateNote(track.id, note.id, { pitch: nextPitch });
    const after = api.getState();
    return {
      ok: Boolean(updated),
      trackId: track.id,
      noteId: note.id,
      beforePitch: note.pitch,
      afterPitch: after.editedMusicJson?.tracks
        ?.find((item) => item.id === track.id)
        ?.events?.find((event) => String(event.id) === String(note.id))
        ?.pitch,
      eventCount: (after.editedMusicJson?.tracks || []).reduce(
        (sum, item) => sum + (item.events?.length || 0),
        0,
      ),
    };
  });
}
