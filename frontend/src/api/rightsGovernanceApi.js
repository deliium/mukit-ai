/**
 * Studio rights-governance registry client.
 * Opening Profiles never writes registry rows.
 */

import axios from 'axios';

export async function getRightsGovernanceEntry(sourceKind, sourceId) {
  return request(
    'get',
    `/rights-governance/entries/${encodeURIComponent(sourceKind)}/${encodeURIComponent(sourceId)}`,
  );
}

export async function putRightsGovernanceEntry(sourceKind, sourceId, body) {
  return request(
    'put',
    `/rights-governance/entries/${encodeURIComponent(sourceKind)}/${encodeURIComponent(sourceId)}`,
    body,
  );
}

export async function evaluateRightsGovernance(body) {
  return request('post', '/rights-governance/evaluate', body);
}

export async function getRightsGovernanceStatus() {
  return request('get', '/rights-governance/status');
}

async function request(method, endpoint, data) {
  try {
    const response = await axios({ method, url: endpoint, data });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const rawDetail = error.response?.data?.detail;
    const code = rawDetail && typeof rawDetail === 'object' ? rawDetail.code : null;
    if (status === 404 && code === 'rights_not_found') {
      return null;
    }
    const message =
      (rawDetail && typeof rawDetail === 'object' && rawDetail.message)
      || (typeof rawDetail === 'string' ? rawDetail : null)
      || error.message
      || 'Rights governance request failed';
    const err = new Error(message);
    err.status = status;
    err.code = code;
    throw err;
  }
}
