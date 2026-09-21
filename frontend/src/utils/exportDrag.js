/**
 * HTML5 drag helpers for DAW handoff of exported MIDI / MusicXML blobs.
 * Never log composition JSON — only format, size, and filename.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('exportDrag');

export const MIDI_MIME_TYPES = Object.freeze(['audio/midi', 'audio/mid', 'application/octet-stream']);
export const MUSICXML_MIME = 'application/vnd.recordare.musicxml+xml';

/**
 * Build a File suitable for DataTransfer from an export blob.
 * @param {Blob} blob
 * @param {string} filename
 * @param {string} [mimeType]
 * @returns {File}
 */
export function blobToExportFile(blob, filename, mimeType) {
  if (!blob || typeof filename !== 'string' || !filename.trim()) {
    throw new Error('blob and filename are required for export drag');
  }
  const type = (mimeType || blob.type || 'application/octet-stream').split(';')[0].trim()
    || 'application/octet-stream';
  const safeName = filename.trim();
  log.debug('Built export File for drag', {
    filename: safeName,
    byteSize: blob.size,
    mimeType: type,
  });
  return new File([blob], safeName, { type, lastModified: Date.now() });
}

/**
 * Prefer MIME for MIDI drag; fall back to octet-stream when browsers reject audio/midi.
 * @param {string} format 'midi' | 'musicxml'
 * @param {string} [contentType]
 * @returns {string}
 */
export function resolveExportDragMime(format, contentType) {
  if (format === 'musicxml') {
    return (contentType || MUSICXML_MIME).split(';')[0].trim() || MUSICXML_MIME;
  }
  const normalized = (contentType || '').split(';')[0].trim().toLowerCase();
  if (normalized === 'audio/midi' || normalized === 'audio/mid') {
    return normalized;
  }
  return 'audio/midi';
}

/**
 * Attach a File to a drag event for desktop DAW / file-manager drops.
 * @param {DataTransfer} dataTransfer
 * @param {File} file
 * @returns {boolean} true when the browser accepted the File item
 */
export function attachExportFileToDataTransfer(dataTransfer, file) {
  if (!dataTransfer || !file) {
    return false;
  }
  try {
    dataTransfer.effectAllowed = 'copy';
    if (typeof dataTransfer.items?.add === 'function') {
      dataTransfer.items.add(file);
      log.debug('Drag DataTransfer item added', {
        filename: file.name,
        byteSize: file.size,
        mimeType: file.type,
      });
      return true;
    }
  } catch (error) {
    log.warn('DataTransfer.items.add failed', {
      filename: file.name,
      errorName: error?.name,
    });
  }
  // Legacy DownloadURL (Chromium) — requires a resolvable URL, not a blob: in all hosts.
  try {
    if (typeof dataTransfer.setData === 'function') {
      dataTransfer.setData('text/plain', file.name);
    }
  } catch {
    // ignore
  }
  return false;
}

/**
 * Create a dragstart handler that uses a pre-fetched export File.
 * @param {() => File | null | undefined} getFile
 * @param {{ format?: string }} [meta]
 * @returns {(event: DragEvent) => void}
 */
export function createExportDragStartHandler(getFile, meta = {}) {
  return (event) => {
    const file = typeof getFile === 'function' ? getFile() : null;
    if (!file) {
      log.debug('Dragstart blocked — export file not ready', { format: meta.format });
      event.preventDefault();
      return;
    }
    const ok = attachExportFileToDataTransfer(event.dataTransfer, file);
    if (!ok) {
      log.warn('Dragstart could not attach file', {
        format: meta.format,
        filename: file.name,
        byteSize: file.size,
      });
      event.preventDefault();
      return;
    }
    log.info('Export drag started', {
      format: meta.format,
      filename: file.name,
      byteSize: file.size,
    });
  };
}
