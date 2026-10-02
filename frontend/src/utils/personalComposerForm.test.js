import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  composerOptionFromJob,
  provenanceEligible,
  rightsForRequest,
  selectionEligible,
} from './personalComposerForm.js';

const etude = 'project-etude';
const sketch = 'project-sketch';

describe('personalComposerForm', () => {
  it('rejects an empty selection', () => {
    assert.equal(selectionEligible([], {}, 'MyComposer-v1'), false);
  });

  it('rejects unknown provenance', () => {
    const rights = { [etude]: rightsForRequest({ status: 'unknown' }) };
    assert.equal(provenanceEligible(rights[etude]), false);
    assert.equal(selectionEligible([etude], rights, 'MyComposer-v1'), false);
  });

  it('rejects verified_redistributable without a license', () => {
    const rights = {
      [etude]: rightsForRequest({
        status: 'verified_redistributable',
        source_reference: 'score-1',
      }),
    };
    assert.equal(provenanceEligible(rights[etude]), false);
    assert.equal(selectionEligible([etude], rights, 'MyComposer-v1'), false);
  });

  it('accepts attested user-owned Etude and Sketch as MyComposer-v1', () => {
    const owned = rightsForRequest({ status: 'user_owned', user_owned_attested: true });
    const rights = { [etude]: owned, [sketch]: owned };
    assert.equal(selectionEligible([etude, sketch], rights, 'MyComposer-v1'), true);
  });

  it('maps a complete job to a display-name option', () => {
    const option = composerOptionFromJob({
      status: 'complete',
      display_name: 'MyComposer-v1',
      registry_model_id: 'personal:pcomp_0123456789abcdef',
    });
    assert.deepEqual(option, {
      label: 'MyComposer-v1',
      value: 'personal:pcomp_0123456789abcdef',
    });
  });
});
