import axios from 'axios';

import { createAppLogger } from '../utils/appLogger.js';
import { collaborationHeaders } from '../utils/collaborationAccess.js';

const logger = createAppLogger('performanceApi');

function plansPath(projectId) {
  return `/projects/${encodeURIComponent(projectId)}/performance-plans`;
}

function planPath(projectId, planId) {
  return `${plansPath(projectId)}/${encodeURIComponent(planId)}`;
}

async function request(method, pathTemplate, endpoint, data) {
  logger.debug('Performance plan request', { method, path: pathTemplate });
  try {
    const response = await axios({
      method,
      url: endpoint,
      data,
      headers: collaborationHeaders(),
    });
    logger.debug('Performance plan request ok', {
      method,
      path: pathTemplate,
      status: response.status,
    });
    return response.data ?? null;
  } catch (error) {
    const status = error.response?.status ?? null;
    const detail = error.response?.data?.detail;
    const code = typeof detail?.code === 'string' ? detail.code : null;
    logger.debug('Performance plan request failed', {
      method,
      path: pathTemplate,
      status,
      code,
    });
    const err = new Error(
      typeof detail?.message === 'string' ? detail.message : (error.message || 'Request failed'),
    );
    err.status = status;
    err.code = code;
    err.details = detail?.details ?? null;
    throw err;
  }
}

export async function listPerformancePlans(projectId) {
  return request('GET', 'list', plansPath(projectId));
}

export async function listPerformancePresets(projectId) {
  return request('GET', 'presets', `${plansPath(projectId)}/presets`);
}

export async function createPerformancePlan(projectId, body) {
  return request('POST', 'create', plansPath(projectId), body);
}

export async function getPerformancePlan(projectId, planId) {
  return request('GET', 'get', planPath(projectId, planId));
}

export async function updatePerformancePlan(projectId, planId, body) {
  return request('PUT', 'put', planPath(projectId, planId), body);
}

export async function deletePerformancePlan(projectId, planId) {
  return request('DELETE', 'delete', planPath(projectId, planId));
}

export async function realizePerformancePlan(projectId, planId, composition) {
  return request('POST', 'realize', `${planPath(projectId, planId)}/realize`, {
    composition,
  });
}

export async function comparePerformancePlan(projectId, planId, composition) {
  return request('POST', 'compare', `${planPath(projectId, planId)}/compare`, {
    composition,
  });
}
