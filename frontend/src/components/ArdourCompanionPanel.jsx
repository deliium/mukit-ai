import { useEffect, useState } from 'react';
import styled from 'styled-components';

import {
  ardourTransportLocate,
  ardourTransportPlay,
  ardourTransportStop,
  connectArdourCompanion,
  disconnectArdourCompanion,
  getArdourCompanionStatus,
  setArdourRecordArm,
  setArdourStripFader,
  setArdourStripMute,
  setArdourStripPan,
  setArdourStripSolo,
} from '../api/ardourCompanionApi.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  canControlArdour,
  formatArdourStatusBanner,
  startArdourStatusPoll,
} from '../utils/ardourCompanion/controls.js';
import { formatArdourTimecode } from '../utils/ardourCompanion/timecode.js';
import ArdourExchangePanel from './ArdourExchangePanel.jsx';

const logger = createAppLogger('ardourCompanion');

const Wrap = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 860px;
`;

const Step = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const StepTitle = styled.h3`
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  font: inherit;
  padding: 6px 12px;
  border: 1px solid #cbd5e1;
  background: ${(p) => (p.$active ? '#0f172a' : '#fff')};
  color: ${(p) => (p.$active ? '#fff' : '#0f172a')};
  cursor: ${(p) => (p.disabled ? 'not-allowed' : 'pointer')};
  opacity: ${(p) => (p.disabled ? 0.5 : 1)};
`;

const Banner = styled.p`
  margin: 0;
  padding: 8px 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: #0f172a;
  font-size: 0.9rem;
`;

const Muted = styled.p`
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #475569;
  min-width: 120px;
`;

const Input = styled.input`
  font: inherit;
  padding: 4px 6px;
`;

const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;

  th, td {
    border-bottom: 1px solid #e2e8f0;
    padding: 6px 4px;
    text-align: left;
  }
`;

const CheckLabel = styled.label`
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9rem;
  color: #0f172a;
`;

const Details = styled.details`
  border-top: 1px solid #e2e8f0;
  padding-top: 12px;
`;

const Summary = styled.summary`
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 600;
  color: #0f172a;
`;

/**
 * Ardour workflow tab: session → transport → scope → send → alternatives → return.
 * Opening never connects and never writes the score or Ardour session files.
 */
const ArdourCompanionPanel = () => {
  const [host, setHost] = useState('127.0.0.1');
  const [oscPort, setOscPort] = useState(3819);
  const [feedbackPort, setFeedbackPort] = useState(8000);
  const [controlPermission, setControlPermission] = useState(false);
  const [status, setStatus] = useState(null);
  const [locateSamples, setLocateSamples] = useState(0);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const refreshStatus = async () => {
    try {
      const next = await getArdourCompanionStatus();
      setStatus(next);
      setError(null);
      // DEBUG only — avoid INFO spam every 1000 ms poll tick.
      logger.debug('poll status', { connection_state: next?.connection_state });
    } catch (err) {
      setError(err?.message || 'Status failed');
    }
  };

  useEffect(() => {
    void refreshStatus();
    const stop = startArdourStatusPoll(refreshStatus);
    return () => {
      stop();
    };
  }, []);

  const controlsEnabled = canControlArdour({
    controlPermission: Boolean(status?.control_permission),
    connectionState: status?.connection_state,
  });

  const clock = formatArdourTimecode(status?.locate_samples, status?.sample_rate);
  const playing = status?.transport_playing === true;

  const run = async (label, action) => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      if (result?.status) {
        setStatus(result.status);
      } else if (result?.connection_state) {
        setStatus((prev) => ({ ...(prev || {}), ...result }));
      } else {
        await refreshStatus();
      }
      logger.info('ui command', { command: label });
    } catch (err) {
      setError(err?.message || `${label} failed`);
      logger.info('ui command failed', { command: label, code: err?.code || null });
    } finally {
      setBusy(false);
    }
  };

  if (status && status.enabled === false) {
    return (
      <Wrap data-testid="ardour-workflow-panel">
        <Muted>Companion disabled (`ARDOUR_COMPANION_ENABLED`).</Muted>
        <Muted>
          Export MIDI / MusicXML / WAV from Export, then import into Ardour.
          See docs/ardour-companion.md.
        </Muted>
        <ArdourExchangePanel companionStatus={status} />
      </Wrap>
    );
  }

  const strips = Array.isArray(status?.strips) ? status.strips : [];

  return (
    <Wrap data-testid="ardour-workflow-panel">
      <Muted>
        Stay in Ardour as the DAW; use AI Composer only for scoped generate / transform / return.
        Opening this tab does not connect, ingest, or apply. Mukit never edits Ardour session XML.
      </Muted>

      <Step data-testid="ardour-workflow-session">
        <StepTitle>1. Connected session</StepTitle>
        <Banner>{formatArdourStatusBanner(status)}</Banner>
        {error ? <Banner role="alert">{error}</Banner> : null}

        <Row>
          <Field>
            Host
            <Input value={host} onChange={(e) => setHost(e.target.value)} />
          </Field>
          <Field>
            OSC port
            <Input
              type="number"
              min={1}
              max={65535}
              value={oscPort}
              onChange={(e) => setOscPort(Number(e.target.value))}
            />
          </Field>
          <Field>
            Feedback port
            <Input
              type="number"
              min={1}
              max={65535}
              value={feedbackPort}
              onChange={(e) => setFeedbackPort(Number(e.target.value))}
            />
          </Field>
        </Row>

        <CheckLabel>
          <input
            type="checkbox"
            checked={controlPermission}
            onChange={(e) => setControlPermission(e.target.checked)}
          />
          Allow DAW control
        </CheckLabel>
        {status?.session_id && !status.control_permission ? (
          <Muted>
            Session connected without DAW control. Reconnect with the checkbox enabled to send
            transport/mixer commands.
          </Muted>
        ) : null}

        <Row>
          <Button
            type="button"
            disabled={busy}
            onClick={() => run('connect', () => connectArdourCompanion({
              host,
              osc_port: oscPort,
              feedback_port: feedbackPort,
              control_permission: controlPermission,
            }))}
          >
            Connect
          </Button>
          <Button
            type="button"
            disabled={busy}
            onClick={() => run('disconnect', () => disconnectArdourCompanion())}
          >
            Disconnect
          </Button>
        </Row>
      </Step>

      <Step data-testid="ardour-workflow-transport">
        <StepTitle>2. Transport + timecode</StepTitle>
        <Banner>
          {playing ? 'playing' : 'stopped'}
          {' · '}
          {clock.display}
          {status?.locate_samples != null ? ` · samples=${status.locate_samples}` : ''}
          {status?.sample_rate ? ` · ${status.sample_rate} Hz` : ''}
        </Banner>
        <Row>
          <Button
            type="button"
            disabled={busy || !controlsEnabled}
            onClick={() => run('play', () => ardourTransportPlay())}
          >
            Play
          </Button>
          <Button
            type="button"
            disabled={busy || !controlsEnabled}
            onClick={() => run('stop', () => ardourTransportStop())}
          >
            Stop
          </Button>
          <Field>
            Locate samples
            <Input
              type="number"
              min={0}
              value={locateSamples}
              onChange={(e) => setLocateSamples(Number(e.target.value))}
            />
          </Field>
          <Button
            type="button"
            disabled={busy || !controlsEnabled}
            onClick={() => run('locate', () => ardourTransportLocate({ samples: locateSamples, roll: 0 }))}
          >
            Locate
          </Button>
          <Button
            type="button"
            disabled={busy || !controlsEnabled}
            onClick={() => run('record_arm', () => setArdourRecordArm(!(status?.record_armed)))}
          >
            Record {status?.record_armed ? 'armed' : 'disarmed'}
          </Button>
        </Row>
        <Muted>
          Display clock is samples÷sample_rate (not Ardour BBT). Selected strip:{' '}
          {status?.selected_strip_name || status?.selected_ssid || '—'}
        </Muted>
      </Step>

      <ArdourExchangePanel companionStatus={status} />

      <Details data-testid="ardour-workflow-mixer">
        <Summary>Mixer (advanced)</Summary>
        <Muted>Strip fader / pan / mute / solo — secondary to the AI Composer workflow.</Muted>
        <Table>
          <thead>
            <tr>
              <th>SSID</th>
              <th>Name</th>
              <th>Fader</th>
              <th>Pan</th>
              <th>Mute</th>
              <th>Solo</th>
            </tr>
          </thead>
          <tbody>
            {strips.map((strip) => (
              <tr key={strip.ssid}>
                <td>{strip.ssid}</td>
                <td>{strip.name || '—'}</td>
                <td>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    disabled={!controlsEnabled || busy}
                    value={strip.fader ?? 0}
                    onChange={(e) => {
                      const value = Number(e.target.value);
                      void run('strip_fader', () => setArdourStripFader(strip.ssid, value));
                    }}
                  />
                </td>
                <td>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    disabled={!controlsEnabled || busy}
                    value={strip.pan ?? 0.5}
                    onChange={(e) => {
                      const value = Number(e.target.value);
                      void run('strip_pan', () => setArdourStripPan(strip.ssid, value));
                    }}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    disabled={!controlsEnabled || busy}
                    checked={Boolean(strip.mute)}
                    onChange={(e) => {
                      void run('strip_mute', () => setArdourStripMute(strip.ssid, e.target.checked ? 1 : 0));
                    }}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    disabled={!controlsEnabled || busy}
                    checked={Boolean(strip.solo)}
                    onChange={(e) => {
                      void run('strip_solo', () => setArdourStripSolo(strip.ssid, e.target.checked ? 1 : 0));
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Details>
    </Wrap>
  );
};

export default ArdourCompanionPanel;
