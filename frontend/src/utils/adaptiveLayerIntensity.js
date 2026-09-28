/** Format a layer row from adaptive.layer.intensity.v1. Does not decide active. */

export function formatAdaptiveLayerStatus(row) {
  if (!row || typeof row !== 'object') {
    return '';
  }
  const active = row.active === true ? 'active' : 'inactive';
  const audible = row.audible === true ? 'audible' : 'silent';
  const fadeEnd = Number.isInteger(row.fade_end_tick) ? row.fade_end_tick : 0;
  return `${row.layer_id || ''} ${row.role || 'other'} ${active} ${audible} ${row.reason || ''} fade ${fadeEnd}`.trim();
}
