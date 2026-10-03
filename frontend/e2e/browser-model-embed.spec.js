import { expect, test } from '@playwright/test';

/**
 * Optional WebGPU smoke for BrowserModelHost batched cosine.
 * Skips when navigator.gpu is missing (Firefox/Safari/headless CI).
 * Ship-1 CPU twin is covered by unit tests; this only exercises WebGPU when present.
 */
test.describe('BrowserModel WebGPU cosine (optional)', () => {
  test('batched cosine runs when navigator.gpu is available', async ({ page }) => {
    await page.goto('/');
    const hasGpu = await page.evaluate(() => Boolean(
      typeof navigator !== 'undefined'
      && navigator.gpu
      && typeof navigator.gpu.requestAdapter === 'function',
    ));
    test.skip(!hasGpu, 'navigator.gpu unavailable — CPU twin covered by unit tests');

    const result = await page.evaluate(async () => {
      const mod = await import('/src/utils/browserModels/webgpu/batchedCosine.js');
      const query = [1, 0, 0, 0];
      const matrix = [[1, 0, 0, 0], [0, 1, 0, 0]];
      // L2 not required for orthogonality smoke with unit vectors
      return mod.batchedCosineSimilarity(query, matrix);
    });

    expect(result.device === 'webgpu' || result.device === 'browser_cpu').toBeTruthy();
    expect(result.scores.length).toBe(2);
    expect(Math.abs(result.scores[0] - 1)).toBeLessThan(1e-3);
  });
});
