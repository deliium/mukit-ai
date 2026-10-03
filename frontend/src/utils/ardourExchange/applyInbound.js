/**
 * Apply an exchange inbound payload via SPA ``completeImport`` (replace).
 * Never merges into an unrelated open score.
 */

export async function applyExchangeInbound(completeImport, payload) {
  if (typeof completeImport !== 'function') {
    return false;
  }
  if (!payload || !payload.composition) {
    return false;
  }
  return completeImport({
    composition: payload.composition,
    import_report: payload.import_report || null,
  });
}

export function shouldAutoIngestOnMount() {
  // Locked: opening the Ardour tab never auto-ingests.
  return false;
}
