/**
 * Plugin catalog client. Does not log configuration bodies.
 */

import axios from 'axios';

export async function listPlugins() {
  return request('get', '/plugins');
}

export async function getPlugin(pluginId) {
  return request('get', `/plugins/${encodeURIComponent(pluginId)}`);
}

export async function installPlugin(pluginId) {
  return request('post', `/plugins/${encodeURIComponent(pluginId)}/install`);
}

export async function enablePlugin(pluginId) {
  return request('post', `/plugins/${encodeURIComponent(pluginId)}/enable`);
}

export async function disablePlugin(pluginId) {
  return request('post', `/plugins/${encodeURIComponent(pluginId)}/disable`);
}

export async function savePluginConfig(pluginId, config) {
  return request('put', `/plugins/${encodeURIComponent(pluginId)}/config`, config);
}

async function request(method, endpoint, data) {
  try {
    const response = await axios({ method, url: endpoint, data });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    const message =
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
      || (typeof rawDetail === 'string' ? rawDetail : null)
      || error.message
      || 'Plugin request failed';
    const err = new Error(message);
    err.status = status;
    err.code = code;
    err.detail = rawDetail;
    throw err;
  }
}
