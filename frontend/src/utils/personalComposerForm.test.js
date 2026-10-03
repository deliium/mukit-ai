import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  composerOptionFromJob,
  formatPersonalComposerRefuseMessage,
  formatReferenceRightsRefuseMessage,
  hydrateRightsDraftFromRegistry,
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

  it('rejects reference_only registry use_policy even when legacy status looks trainable', () => {
    const rights = {
      [etude]: {
        status: 'verified_redistributable',
        license: 'CC-BY-4.0',
        source_reference: 'score-1',
        use_policy: 'reference_only',
        ownership_class: 'licensed',
        verification_status: 'verified',
      },
    };
    assert.equal(provenanceEligible(rights[etude]), false);
    assert.equal(selectionEligible([etude], rights, 'MyComposer-v1'), false);
  });

  it('hydrates draft fields from a registry entry', () => {
    const draft = hydrateRightsDraftFromRegistry(undefined, {
      use_policy: 'reference_only',
      ownership_class: 'licensed',
      verification_status: 'verified',
      license: 'CC-BY-4.0',
      license_spdx: 'CC-BY-4.0',
      source_reference: 'ref-1',
      legacy_status: 'verified_redistributable',
    });
    assert.equal(draft.use_policy, 'reference_only');
    assert.equal(draft.ownership_class, 'licensed');
    assert.equal(draft.status, 'verified_redistributable');
    assert.equal(draft.license, 'CC-BY-4.0');
  });

  it('maps train and reference refuse codes', () => {
    assert.equal(
      formatPersonalComposerRefuseMessage({ code: 'personal_rights_refused' }),
      'That project is not eligible to train on.',
    );
    assert.equal(
      formatPersonalComposerRefuseMessage({ code: 'rights_train_refused' }),
      'That project is not eligible to train on.',
    );
    assert.equal(
      formatReferenceRightsRefuseMessage({ code: 'rights_reference_refused' }),
      'This reference cannot be used (rights refuse). Attest ownership or pick a permitted source.',
    );
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
