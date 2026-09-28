import axios from 'axios';

import { createAppLogger } from '../utils/appLogger.js';
import { collaborationHeaders } from '../utils/collaborationAccess.js';

const logger = createAppLogger('adaptiveScoreApi');

const PATHS = Object.freeze({
  list: '/projects/{project_id}/adaptive-scores',
  create: '/projects/{project_id}/adaptive-scores',
  get: '/projects/{project_id}/adaptive-scores/{score_id}',
  command: '/projects/{project_id}/adaptive-scores/{score_id}/commands',
  validate: '/projects/{project_id}/adaptive-scores/{score_id}/validate',
});

function projectScoresPath(projectId) {
  return `/projects/${encodeURIComponent(projectId)}/adaptive-scores`;
}

function scorePath(projectId, scoreId) {
  return `${projectScoresPath(projectId)}/${encodeURIComponent(scoreId)}`;
}

function findingList(data) {
  const findings = data?.detail?.details?.findings;
  if (!Array.isArray(findings)) {
    return [];
  }
  return findings.flatMap((item) => {
    if (!item || typeof item !== 'object' || typeof item.code !== 'string') {
      return [];
    }
    return [{
      code: item.code,
      severity: item.severity === 'warning' ? 'warning' : 'error',
      target_id: typeof item.target_id === 'string' ? item.target_id : null,
      message: typeof item.message === 'string' ? item.message : '',
    }];
  });
}

async function request(method, pathTemplate, endpoint, data) {
  logger.debug('Adaptive score request', { method, path: pathTemplate });
  try {
    const response = await axios({
      method,
      url: endpoint,
      data,
      headers: collaborationHeaders(),
    });
    logger.debug('Adaptive score request', {
      method,
      path: pathTemplate,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const detail = error.response?.data?.detail;
    const findings = findingList(error.response?.data);
    logger.debug('Adaptive score request failed', {
      method,
      path: pathTemplate,
      status,
      codes: findings.map((item) => item.code),
    });
    const message = typeof detail?.message === 'string'
      ? detail.message
      : 'Adaptive score request failed';
    const err = new Error(message);
    err.status = status;
    err.code = typeof detail?.code === 'string' ? detail.code : null;
    err.findings = findings;
    throw err;
  }
}

export function listAdaptiveScores(projectId) {
  return request('get', PATHS.list, projectScoresPath(projectId));
}

export function createAdaptiveScore(projectId, payload) {
  return request('post', PATHS.create, projectScoresPath(projectId), payload);
}

export function getAdaptiveScore(projectId, scoreId) {
  return request('get', PATHS.get, scorePath(projectId, scoreId));
}

export function commandAdaptiveScore(projectId, scoreId, command) {
  return request('post', PATHS.command, `${scorePath(projectId, scoreId)}/commands`, command);
}

export function validateAdaptiveScore(projectId, scoreId) {
  return request('post', PATHS.validate, `${scorePath(projectId, scoreId)}/validate`);
}
