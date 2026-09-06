import axios from 'axios';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { downloadBlob, filenameFromContentDisposition } from '../utils/downloadFile.js';

export async function getHealth() {
  return request('get', '/health');
}

export async function getLlmModels() {
  return request('get', '/llm/models');
}

export async function generateLlmMusicJson(payload) {
  const response = await request('post', '/llm/generate-music-json', payload);
  const validation = validateMusicJson(response.music);
  console.debug('[musicApi] LLM music response validation completed', {
    valid: validation.valid,
    schemaVersion: response.music?.schema_version || 'legacy',
    canonical: isCanonicalComposition(response.music),
    warningCount: response.warnings?.length || 0,
  });
  if (!validation.valid) {
    console.error('[musicApi] LLM music response failed validation', { message: validation.message });
    throw new Error(validation.message);
  }
  return response;
}

export async function exportMusicXml(composition) {
  return exportComposition(composition, {
    endpoint: '/export/musicxml',
    format: 'musicxml',
    fallbackFilename: 'composition.musicxml',
    expectedType: 'application/vnd.recordare.musicxml+xml',
  });
}

export async function renderMusicXmlPreview(composition) {
  const validation = validateMusicJson(composition);
  const eventCount = Array.isArray(composition?.tracks)
    ? composition.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
    : 0;
  console.debug('[musicApi] MusicXML preview request started', {
    schemaVersion: composition?.schema_version || 'legacy',
    trackCount: composition?.tracks?.length || 0,
    eventCount,
    valid: validation.valid,
  });
  if (!validation.valid || !isCanonicalComposition(composition)) {
    console.warn('[musicApi] MusicXML preview rejected invalid composition', {
      message: validation.message || 'Preview requires composition.v1 JSON',
    });
    throw new Error(validation.message || 'Preview requires canonical composition.v1 JSON');
  }

  try {
    const response = await axios.post('/export/musicxml/preview', composition, {
      responseType: 'text',
      headers: { Accept: 'application/vnd.recordare.musicxml+xml, application/xml, text/xml, text/plain' },
    });
    const musicxml = typeof response.data === 'string' ? response.data : String(response.data || '');
    console.debug('[musicApi] MusicXML preview request completed', {
      schemaVersion: composition.schema_version,
      eventCount,
      musicXmlLength: musicxml.length,
    });
    return musicxml;
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || 'Unknown MusicXML preview failure';
    const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    console.error('[musicApi] MusicXML preview request failed', {
      status: error.response?.status,
      detail: message,
    });
    throw new Error(message);
  }
}

export async function exportMidi(composition) {
  return exportComposition(composition, {
    endpoint: '/export/midi',
    format: 'midi',
    fallbackFilename: 'composition.mid',
    expectedType: 'audio/midi',
  });
}

async function exportComposition(composition, { endpoint, format, fallbackFilename, expectedType }) {
  const validation = validateMusicJson(composition);
  const eventCount = Array.isArray(composition?.tracks)
    ? composition.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
    : 0;
  console.debug('[musicApi] Export request started', {
    format,
    schemaVersion: composition?.schema_version || 'legacy',
    trackCount: composition?.tracks?.length || 0,
    eventCount,
    valid: validation.valid,
  });
  if (!validation.valid || !isCanonicalComposition(composition)) {
    console.error('[musicApi] Export rejected invalid composition', {
      format,
      message: validation.message || 'Export requires composition.v1 JSON',
    });
    throw new Error(validation.message || 'Export requires canonical composition.v1 JSON');
  }

  try {
    const response = await axios.post(endpoint, composition, { responseType: 'blob' });
    const blob = response.data;
    const contentType = response.headers?.['content-type'] || blob.type || expectedType;
    const filename = filenameFromContentDisposition(
      response.headers?.['content-disposition'],
      fallbackFilename,
    );
    console.debug('[musicApi] Export request completed', {
      format,
      schemaVersion: composition.schema_version,
      trackCount: composition.tracks.length,
      eventCount,
      blobSize: blob.size,
      contentType,
      filename,
    });
    downloadBlob(blob, filename);
    return { blob, filename, contentType };
  } catch (error) {
    const detail = await extractBlobErrorDetail(error);
    console.error('[musicApi] Export request failed', {
      format,
      status: error.response?.status,
      detail,
    });
    throw new Error(detail);
  }
}

async function extractBlobErrorDetail(error) {
  const data = error.response?.data;
  if (data instanceof Blob) {
    try {
      const text = await data.text();
      const parsed = JSON.parse(text);
      if (parsed?.detail) {
        return typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail);
      }
      return text || error.message || 'Unknown export failure';
    } catch {
      return error.message || 'Unknown export failure';
    }
  }
  return error.response?.data?.detail || error.message || 'Unknown export failure';
}

async function request(method, endpoint, data, config = {}) {
  console.debug('[musicApi] Request started', { method, endpoint });
  try {
    const response = await axios({ method, url: endpoint, data, ...config });
    console.debug('[musicApi] Request completed', { method, endpoint, status: response.status });
    return response.data;
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || 'Unknown request failure';
    console.error('[musicApi] Request failed', {
      method,
      endpoint,
      status: error.response?.status,
      detail,
    });
    throw new Error(detail);
  }
}
