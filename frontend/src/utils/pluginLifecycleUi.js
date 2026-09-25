/**
 * Plugin lifecycle action map and health labels.
 * Pure helpers: no network, no plugin config values.
 */

const ACTIONS = {
  discovered: ['install'],
  installed: ['enable', 'disable'],
  enabled: ['disable'],
  disabled: ['enable'],
  failed: ['install', 'enable', 'disable'],
  incompatible: [],
};

/**
 * @param {string | null | undefined} status
 * @returns {string[]}
 */
export function availablePluginActions(status) {
  return ACTIONS[status] ? [...ACTIONS[status]] : [];
}

/**
 * @param {{ status?: string, code?: string | null } | null | undefined} health
 * @returns {string}
 */
export function pluginHealthLabel(health) {
  const status = health && health.status;
  if (status === 'healthy') {
    return 'Healthy';
  }
  if (status === 'unhealthy') {
    return health.code ? `Unhealthy (${health.code})` : 'Unhealthy';
  }
  return 'Unknown';
}
