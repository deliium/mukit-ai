import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { applyExchangeInbound, shouldAutoIngestOnMount } from './applyInbound.js';

describe('ardourExchange applyInbound', () => {
  it('never auto-ingests on mount', () => {
    assert.equal(shouldAutoIngestOnMount(), false);
  });

  it('wires completeImport with composition replace payload', async () => {
    const calls = [];
    const completeImport = async (payload) => {
      calls.push(payload);
      return true;
    };
    const ok = await applyExchangeInbound(completeImport, {
      composition: { schema_version: 'composition.v2' },
      import_report: { note_count: 1 },
    });
    assert.equal(ok, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].composition.schema_version, 'composition.v2');
    assert.equal(calls[0].import_report.note_count, 1);
  });

  it('refuses missing composition', async () => {
    assert.equal(await applyExchangeInbound(async () => true, {}), false);
  });
});
