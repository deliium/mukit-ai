import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  disablePlugin,
  enablePlugin,
  installPlugin,
  listPlugins,
  savePluginConfig,
} from '../api/pluginApi.js';
import { availablePluginActions, pluginHealthLabel } from '../utils/pluginLifecycleUi.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
  font-size: 1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
`;

const ErrorLine = styled.p`
  margin: 0;
  color: #9f1239;
  font-weight: 600;
  font-size: 0.9rem;
`;

const Card = styled.article`
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
`;

const Meta = styled.p`
  margin: 0;
  color: #334155;
  font-size: 0.9rem;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
`;

const Input = styled.input`
  min-height: 40px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  min-width: 140px;
`;

const Select = styled.select`
  min-height: 40px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
`;

const ACTION_LABELS = {
  install: 'Install',
  enable: 'Enable',
  disable: 'Disable',
};

const ACTION_CALLS = {
  install: installPlugin,
  enable: enablePlugin,
  disable: disablePlugin,
};

function schemaFields(schema) {
  const properties = schema && schema.properties;
  if (!properties || typeof properties !== 'object') {
    return [];
  }
  return Object.entries(properties).flatMap(([name, spec]) => {
    if (!spec || typeof spec !== 'object') {
      return [];
    }
    const type = spec.type;
    if (!['string', 'number', 'integer', 'boolean'].includes(type)) {
      return [];
    }
    return [{ name, type, enum: Array.isArray(spec.enum) ? spec.enum : null, defaultValue: spec.default }];
  });
}

function PluginCard({ plugin, busy, onAction, onSaveConfig }) {
  const fields = schemaFields(plugin.configuration_schema);
  const actions = availablePluginActions(plugin.status);
  const canConfigure = plugin.status === 'installed' || plugin.status === 'enabled' || plugin.status === 'disabled';
  const [draft, setDraft] = useState(() => {
    const initial = {};
    for (const field of fields) {
      if (field.defaultValue !== undefined) {
        initial[field.name] = field.defaultValue;
      }
    }
    return initial;
  });
  const [savedNote, setSavedNote] = useState('');

  const submitConfig = async (event) => {
    event.preventDefault();
    const payload = {};
    for (const field of fields) {
      if (draft[field.name] === undefined || draft[field.name] === '') {
        continue;
      }
      if (field.type === 'integer') {
        payload[field.name] = Number.parseInt(String(draft[field.name]), 10);
      } else if (field.type === 'number') {
        payload[field.name] = Number(draft[field.name]);
      } else if (field.type === 'boolean') {
        payload[field.name] = Boolean(draft[field.name]);
      } else {
        payload[field.name] = String(draft[field.name]);
      }
    }
    const ok = await onSaveConfig(plugin.id, payload);
    if (ok) {
      setSavedNote('The new config applies on the next enable.');
    }
  };

  return (
    <Card data-testid={`plugin-card-${plugin.id}`}>
      <Meta>
        <strong>{plugin.name || plugin.id}</strong>
        {' · '}
        {plugin.id}
        {' · '}
        {plugin.version || 'unknown version'}
      </Meta>
      <Meta>
        {plugin.category || 'uncategorized'}
        {' · '}
        {plugin.status}
        {' · '}
        {pluginHealthLabel(plugin.health)}
      </Meta>
      <Meta>
        Resources: {Array.isArray(plugin.resources) && plugin.resources.length ? plugin.resources.join(', ') : 'none'}
      </Meta>
      {plugin.code ? <ErrorLine data-testid={`plugin-code-${plugin.id}`}>{plugin.code}</ErrorLine> : null}
      <Row>
        {actions.map((action) => (
          <Button
            key={action}
            type="button"
            disabled={busy}
            onClick={() => onAction(action, plugin)}
          >
            {ACTION_LABELS[action]}
          </Button>
        ))}
      </Row>
      {canConfigure && fields.length > 0 ? (
        <form onSubmit={submitConfig}>
          {fields.map((field) => (
            <Row key={field.name}>
              <label htmlFor={`plugin-${plugin.id}-${field.name}`}>{field.name}</label>
              {field.enum ? (
                <Select
                  id={`plugin-${plugin.id}-${field.name}`}
                  value={draft[field.name] ?? ''}
                  onChange={(event) => setDraft((current) => ({ ...current, [field.name]: event.target.value }))}
                >
                  <option value="">Select</option>
                  {field.enum.map((option) => (
                    <option key={String(option)} value={String(option)}>
                      {String(option)}
                    </option>
                  ))}
                </Select>
              ) : field.type === 'boolean' ? (
                <input
                  id={`plugin-${plugin.id}-${field.name}`}
                  type="checkbox"
                  checked={Boolean(draft[field.name])}
                  onChange={(event) => setDraft((current) => ({ ...current, [field.name]: event.target.checked }))}
                />
              ) : (
                <Input
                  id={`plugin-${plugin.id}-${field.name}`}
                  type={field.type === 'string' ? 'text' : 'number'}
                  value={draft[field.name] ?? ''}
                  onChange={(event) => setDraft((current) => ({ ...current, [field.name]: event.target.value }))}
                />
              )}
            </Row>
          ))}
          <Button type="submit" disabled={busy}>Save configuration</Button>
          {savedNote ? <Hint>{savedNote}</Hint> : null}
        </form>
      ) : null}
    </Card>
  );
}

const PluginsPanel = () => {
  const [plugins, setPlugins] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [errorCode, setErrorCode] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listPlugins()
      .then((payload) => {
        if (!cancelled) {
          setPlugins(Array.isArray(payload?.plugins) ? payload.plugins : []);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorCode(error.code || 'plugin_request_failed');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onAction = async (action, plugin) => {
    setBusyId(plugin.id);
    setErrorCode(null);
    try {
      const next = await ACTION_CALLS[action](plugin.id);
      setPlugins((current) => current.map((item) => (item.id === plugin.id ? next : item)));
      if (next?.status === 'failed') {
        setErrorCode(next.code || next.health?.code || 'failed');
      }
    } catch (error) {
      setErrorCode(error.code || 'plugin_request_failed');
    } finally {
      setBusyId(null);
    }
  };

  const onSaveConfig = async (pluginId, payload) => {
    setBusyId(pluginId);
    setErrorCode(null);
    try {
      const next = await savePluginConfig(pluginId, payload);
      setPlugins((current) => current.map((item) => (item.id === pluginId ? next : item)));
      return true;
    } catch (error) {
      setErrorCode(error.code || 'plugin_request_failed');
      return false;
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Panel>
      <Title>Plugins</Title>
      <Hint>
        A directory on PLUGIN_PATHS is discovered only. Install, then enable, before any plugin code runs.
        There is no marketplace.
      </Hint>
      {errorCode ? <ErrorLine data-testid="plugin-panel-error">{errorCode}</ErrorLine> : null}
      {loading ? <Hint>Loading plugins…</Hint> : null}
      {!loading && plugins.length === 0 ? <Hint>No plugins discovered.</Hint> : null}
      {plugins.map((plugin) => (
        <PluginCard
          key={plugin.id || plugin.message}
          plugin={plugin}
          busy={busyId === plugin.id}
          onAction={onAction}
          onSaveConfig={onSaveConfig}
        />
      ))}
    </Panel>
  );
};

export default PluginsPanel;
