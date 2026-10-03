/** Musical scope card helpers for the Ardour workflow surface.

Prefers preview/manifest alignment when present; otherwise OSC strip + locate.
Does not import video scoring modules.
*/

import { formatArdourTimecode } from '../ardourCompanion/timecode.js';
import { createAppLogger } from '../appLogger.js';

const logger = createAppLogger('ardourExchange.scopeSummary');

/**
 * @param {{
 *   companionStatus?: object|null,
 *   exchangePreview?: object|null,
 *   connected?: boolean,
 * }} input
 */
export function buildArdourScopeSummary({
  companionStatus = null,
  exchangePreview = null,
  connected = false,
} = {}) {
  const status = companionStatus || {};
  const connectionState = status.connection_state || (connected ? 'connected' : 'disconnected');
  const isConnected = connectionState === 'connected' || connectionState === 'awaiting_feedback';
  const stripName =
    status.selected_strip_name
    || (status.selected_ssid != null ? `Strip ${status.selected_ssid}` : null);
  const sampleRate = status.sample_rate ?? exchangePreview?.manifest?.sample_rate ?? null;
  const locate = formatArdourTimecode(status.locate_samples, sampleRate);
  const playing = status.transport_playing === true;

  const alignment = exchangePreview?.alignment || null;
  const manifest = exchangePreview?.manifest || null;
  const hasPreviewAlignment = Boolean(
    alignment
    && Number.isFinite(Number(alignment.start_bar))
    && Number.isFinite(Number(alignment.bar_count)),
  );

  let barsLabel = null;
  let tempoBpm = null;
  let timeSignature = null;
  let trackName = null;
  let source = 'osc';

  if (hasPreviewAlignment) {
    const startBar = Number(alignment.start_bar);
    const barCount = Number(alignment.bar_count);
    const endBar = startBar + barCount - 1;
    barsLabel = `bars ${startBar}–${endBar}`;
    tempoBpm = alignment.tempo_bpm ?? manifest?.tempo_bpm ?? null;
    timeSignature = alignment.time_signature ?? manifest?.time_signature ?? null;
    trackName = manifest?.track_name || null;
    source = 'manifest';
  } else {
    logger.debug('scope without preview alignment', {
      connection_state: connectionState,
      has_strip: Boolean(stripName),
    });
  }

  const cta = hasPreviewAlignment
    ? null
    : 'Export the selected MIDI region via Ardour Lua, then Send to AI Composer.';

  return {
    connectionState,
    connected: isConnected,
    playing,
    stripName,
    locateDisplay: locate.display,
    locateMode: locate.mode,
    barsLabel,
    tempoBpm,
    timeSignature,
    trackName,
    source,
    exportSelectionCta: cta,
    hasPreviewAlignment,
  };
}
