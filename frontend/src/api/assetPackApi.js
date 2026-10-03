import axios from 'axios';

import { collaborationHeaders } from '../utils/collaborationAccess.js';

export class AssetPackApiError extends Error {
  constructor(code, status) {
    super(code || 'asset_pack_invalid');
    this.name = 'AssetPackApiError';
    this.code = code || 'asset_pack_invalid';
    this.status = status ?? null;
  }
}

export async function previewAssetPackPlan(brief) {
  return request('post', '/asset-packs/plan/preview', { brief });
}

export async function createAssetPack(payload) {
  return request('post', '/asset-packs', payload);
}

export async function listAssetPacks() {
  return request('get', '/asset-packs');
}

export async function getAssetPack(packId) {
  return request('get', `/asset-packs/${encodeURIComponent(packId)}`);
}

export async function listAssetPackSlots(packId) {
  return request('get', `/asset-packs/${encodeURIComponent(packId)}/slots`);
}

export async function generateAssetPack(packId, payload) {
  return request('post', `/asset-packs/${encodeURIComponent(packId)}/generate`, payload);
}

export async function regenerateAssetPackSlots(packId, payload) {
  return request('post', `/asset-packs/${encodeURIComponent(packId)}/regenerate`, payload);
}

async function request(method, url, data) {
  try {
    const response = await axios({
      method,
      url,
      data,
      headers: collaborationHeaders(),
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const detail = error.response?.data?.detail;
    const code =
      (typeof detail === 'object' && detail?.code) ||
      error.response?.data?.code ||
      'asset_pack_invalid';
    throw new AssetPackApiError(code, status);
  }
}
