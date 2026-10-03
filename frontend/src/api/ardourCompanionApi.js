/** Ardour companion HTTP client. Never logs OSC payloads. */

import axios from 'axios';

import { createAppLogger } from '../utils/appLogger.js';

const logger = createAppLogger('ardourCompanion');

export class ArdourCompanionApiError extends Error {
  constructor(message, { code = null, status = null } = {}) {
    super(message);
    this.name = 'ArdourCompanionApiError';
    this.code = code;
    this.status = status;
  }
}

function failure(error, route) {
  const detail = error?.response?.data?.detail;
  const code = detail && typeof detail === 'object' && typeof detail.code === 'string'
    ? detail.code
    : null;
  const status = error?.response?.status ?? null;
  logger.debug('request failed', { route, code, status });
  const message = detail && typeof detail === 'object' && typeof detail.message === 'string'
    ? detail.message
    : 'Ardour companion request failed';
  return new ArdourCompanionApiError(message, { code, status });
}

export async function getArdourCompanionStatus() {
  try {
    const response = await axios.get('/ardour/companion/status');
    logger.debug('status', { connection_state: response.data?.connection_state });
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/companion/status');
  }
}

export async function connectArdourCompanion(body) {
  try {
    const response = await axios.post('/ardour/companion/connect', {
      schema_version: 'ardour.companion.connect.v1',
      host: String(body.host || '').trim(),
      osc_port: Number(body.osc_port),
      feedback_port: Number(body.feedback_port),
      control_permission: Boolean(body.control_permission),
    });
    logger.info('connect', { connection_state: response.data?.connection_state });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/connect');
  }
}

export async function disconnectArdourCompanion() {
  try {
    const response = await axios.post('/ardour/companion/disconnect');
    logger.info('disconnect', { connection_state: response.data?.status?.connection_state });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/disconnect');
  }
}

export async function ardourTransportPlay() {
  try {
    const response = await axios.post('/ardour/companion/transport/play');
    logger.info('command', { command: 'play' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/transport/play');
  }
}

export async function ardourTransportStop() {
  try {
    const response = await axios.post('/ardour/companion/transport/stop');
    logger.info('command', { command: 'stop' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/transport/stop');
  }
}

export async function ardourTransportLocate({ samples, roll = 0 }) {
  try {
    const response = await axios.post('/ardour/companion/transport/locate', {
      samples: Number(samples),
      roll: Number(roll) ? 1 : 0,
    });
    logger.info('command', { command: 'locate' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/transport/locate');
  }
}

export async function getArdourRecordArm() {
  try {
    const response = await axios.get('/ardour/companion/record');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/companion/record');
  }
}

export async function setArdourRecordArm(desired) {
  try {
    const response = await axios.post('/ardour/companion/record', {
      desired: Boolean(desired),
    });
    logger.info('command', { command: 'record_arm' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/record');
  }
}

export async function getArdourStrips() {
  try {
    const response = await axios.get('/ardour/companion/strips');
    return response.data;
  } catch (error) {
    throw failure(error, 'GET /ardour/companion/strips');
  }
}

export async function setArdourStripFader(ssid, value) {
  try {
    const response = await axios.post(`/ardour/companion/strips/${ssid}/fader`, { value });
    logger.info('command', { command: 'strip_fader' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/strips/fader');
  }
}

export async function setArdourStripPan(ssid, value) {
  try {
    const response = await axios.post(`/ardour/companion/strips/${ssid}/pan`, { value });
    logger.info('command', { command: 'strip_pan' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/strips/pan');
  }
}

export async function setArdourStripMute(ssid, value) {
  try {
    const response = await axios.post(`/ardour/companion/strips/${ssid}/mute`, { value });
    logger.info('command', { command: 'strip_mute' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/strips/mute');
  }
}

export async function setArdourStripSolo(ssid, value) {
  try {
    const response = await axios.post(`/ardour/companion/strips/${ssid}/solo`, { value });
    logger.info('command', { command: 'strip_solo' });
    return response.data;
  } catch (error) {
    throw failure(error, 'POST /ardour/companion/strips/solo');
  }
}
