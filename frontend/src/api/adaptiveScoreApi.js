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
  schedule: '/projects/{project_id}/adaptive-scores/{score_id}/transition-requests',
  current: '/projects/{project_id}/adaptive-scores/{score_id}/transition-requests/current',
  cancel: '/projects/{project_id}/adaptive-scores/{score_id}/transition-requests/{request_id}',
  intensity: '/projects/{project_id}/adaptive-scores/{score_id}/layer-intensity',
  preview: '/projects/{project_id}/adaptive-scores/{score_id}/layer-plans/preview',
  playback: '/projects/{project_id}/adaptive-scores/{score_id}/playback',
  playbackCommand: '/projects/{project_id}/adaptive-scores/{score_id}/playback/commands',
  context: '/projects/{project_id}/adaptive-scores/{score_id}/context',
  contextSample: '/projects/{project_id}/adaptive-scores/{score_id}/context/samples',
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

export function startAdaptivePlayback(projectId, scoreId, payload) {
  return request('post', PATHS.playback, `${scorePath(projectId, scoreId)}/playback`, payload);
}

export function getAdaptivePlayback(projectId, scoreId) {
  return request('get', PATHS.playback, `${scorePath(projectId, scoreId)}/playback`);
}

export function commandAdaptivePlayback(projectId, scoreId, command) {
  return request(
    'post',
    PATHS.playbackCommand,
    `${scorePath(projectId, scoreId)}/playback/commands`,
    command,
  );
}

export function deleteAdaptivePlayback(projectId, scoreId) {
  return request('delete', PATHS.playback, `${scorePath(projectId, scoreId)}/playback`);
}

export function startAdaptiveMusicalContext(projectId, scoreId, payload) {
  return request('post', PATHS.context, `${scorePath(projectId, scoreId)}/context`, payload);
}

export function deleteAdaptiveMusicalContext(projectId, scoreId) {
  return request('delete', PATHS.context, `${scorePath(projectId, scoreId)}/context`);
}

export function sendAdaptiveContextSample(projectId, scoreId, sample) {
  return request(
    'post',
    PATHS.contextSample,
    `${scorePath(projectId, scoreId)}/context/samples`,
    sample,
  );
}

function raiseAdaptiveError(error, pathTemplate) {
  const status = error.response?.status ?? null;
  const detail = error.response?.data?.detail;
  const message = typeof detail?.message === 'string'
    ? detail.message
    : 'Adaptive score request failed';
  const err = new Error(message);
  err.status = status;
  err.code = typeof detail?.code === 'string' ? detail.code : null;
  err.findings = findingList(error.response?.data);
  logger.debug('Adaptive score request failed', {
    path: pathTemplate,
    status,
    code: err.code,
  });
  return err;
}

export async function scheduleAdaptiveTransition(projectId, scoreId, body) {
  const endpoint = `${scorePath(projectId, scoreId)}/transition-requests`;
  try {
    const response = await axios({
      method: 'post',
      url: endpoint,
      data: body,
      headers: collaborationHeaders(),
      validateStatus: (status) => status === 200 || status === 201,
    });
    return response.data;
  } catch (error) {
    throw raiseAdaptiveError(error, PATHS.schedule);
  }
}

export async function getCurrentAdaptiveTransition(projectId, scoreId) {
  const endpoint = `${scorePath(projectId, scoreId)}/transition-requests/current`;
  try {
    const response = await axios({
      method: 'get',
      url: endpoint,
      headers: collaborationHeaders(),
      validateStatus: (status) => status === 200 || status === 204,
    });
    if (response.status === 204) {
      return null;
    }
    return response.data ?? null;
  } catch (error) {
    throw raiseAdaptiveError(error, PATHS.current);
  }
}

export function mapAdaptiveLayerIntensity(projectId, scoreId, body) {
  return request(
    'post',
    PATHS.intensity,
    `${scorePath(projectId, scoreId)}/layer-intensity`,
    body,
  );
}

export function previewAdaptiveLayerPlan(projectId, scoreId, body) {
  return request(
    'post',
    PATHS.preview,
    `${scorePath(projectId, scoreId)}/layer-plans/preview`,
    body,
  );
}

export async function cancelAdaptiveTransition(projectId, scoreId, requestId) {
  const endpoint = `${scorePath(projectId, scoreId)}/transition-requests/${encodeURIComponent(requestId)}`;
  try {
    await axios({
      method: 'delete',
      url: endpoint,
      headers: collaborationHeaders(),
      validateStatus: (status) => status === 204,
    });
    return null;
  } catch (error) {
    throw raiseAdaptiveError(error, PATHS.cancel);
  }
}
