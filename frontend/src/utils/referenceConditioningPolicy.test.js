import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  applyConditioningPreset,
  buildConditioningRequestFields,
  collectMultiRefBorrowRows,
  emptyPolicyState,
  validateBorrowDimensionUniqueness,
  validateDisjointPartition,
} from './referenceConditioningPolicy.js';

describe('referenceConditioningPolicy', () => {
  it('accepts disjoint preserve / borrow / regenerate', () => {
    const result = validateDisjointPartition({
      preserve: ['rhythm'],
      borrow: ['texture'],
      regenerate: ['melodic_contour', 'harmony'],
    });
    assert.equal(result.ok, true);
  });

  it('rejects partition overlap', () => {
    const result = validateDisjointPartition({
      preserve: ['texture'],
      borrow: ['texture'],
      regenerate: [],
    });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'reference_conditioning_partition_overlap');
  });

  it('rejects duplicate borrow dims across refs', () => {
    const result = validateBorrowDimensionUniqueness([
      { dimensions: ['texture'] },
      { dimensions: ['texture', 'rhythm'] },
    ]);
    assert.equal(result.ok, false);
    assert.equal(result.code, 'reference_conditioning_borrow_dimension_conflict');
  });

  it('builds multi-ref AC payload (texture A + rhythm B)', () => {
    const policy = {
      ...emptyPolicyState(),
      regenerate: ['melodic_contour', 'harmony'],
      dimensionStrengths: { texture: 'strong', rhythm: 'normal' },
    };
    const built = buildConditioningRequestFields({
      policyState: policy,
      borrowRows: [
        { projectId: 'proj-a', dimensions: ['texture'], scope: { kind: 'composition' } },
        { projectId: 'proj-b', dimensions: ['rhythm'], scope: { kind: 'composition' } },
      ],
      activeProjectId: 'workspace-1',
    });
    assert.equal(built.ok, true);
    assert.ok(built.fields.style_references);
    assert.equal(built.fields.style_references.length, 2);
    assert.deepEqual(built.fields.style_references[0].dimensions, ['texture']);
    assert.deepEqual(built.fields.style_references[1].dimensions, ['rhythm']);
    assert.equal(
      built.fields.reference_conditioning_policy.dimension_strengths.texture,
      'strong',
    );
    assert.deepEqual(
      built.fields.reference_conditioning_policy.regenerate_dimensions,
      ['melodic_contour', 'harmony'],
    );
  });

  it('applies new_piece preset with A/B borrow sources', () => {
    const applied = applyConditioningPreset('new_piece_texture_rhythm');
    assert.equal(applied.ok, true);
    assert.deepEqual(applied.policy.regenerate, ['harmony', 'harmonic_rhythm', 'melodic_contour']);
    assert.equal(applied.policy.borrowSourceByDim.texture, 'A');
    assert.equal(applied.policy.borrowSourceByDim.rhythm, 'B');
  });

  it('collects multi-ref borrow rows from A/B sources', () => {
    const { rows, warnings } = collectMultiRefBorrowRows({
      enabled: true,
      dimensions: ['texture', 'rhythm'],
      borrowSourceByDim: { texture: 'A', rhythm: 'B' },
      primary: { projectId: 'proj-a', scope: { kind: 'composition' } },
      primaryComposition: { schema_version: 'composition.v2' },
      secondary: { projectId: 'proj-b', scope: { kind: 'composition' } },
      secondaryComposition: { schema_version: 'composition.v2' },
    });
    assert.equal(warnings.length, 0);
    assert.equal(rows.length, 2);
    assert.deepEqual(rows[0].dimensions, ['texture']);
    assert.equal(rows[0].projectId, 'proj-a');
    assert.deepEqual(rows[1].dimensions, ['rhythm']);
    assert.equal(rows[1].projectId, 'proj-b');
  });

  it('falls back B dims onto A when secondary missing', () => {
    const { rows, warnings } = collectMultiRefBorrowRows({
      enabled: true,
      dimensions: ['texture', 'rhythm'],
      borrowSourceByDim: { texture: 'A', rhythm: 'B' },
      primary: { projectId: 'proj-a', scope: { kind: 'composition' } },
    });
    assert.ok(warnings.includes('reference_conditioning_secondary_missing'));
    assert.equal(rows.length, 1);
    assert.deepEqual(rows[0].dimensions, ['texture', 'rhythm']);
  });
});
