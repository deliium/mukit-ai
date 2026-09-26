/**
 * One-line stage label. No network and no composition.
 */

export function autonomousStageText(stage) {
  if (!stage || typeof stage !== 'object') return '';
  const stageId = String(stage.stage_id || '').trim();
  const status = String(stage.status || '').trim();
  if (!stageId && !status) return '';
  return `${stageId} ${status}`.trim();
}
