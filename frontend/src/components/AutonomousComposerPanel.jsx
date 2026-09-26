import React, { useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { autonomousStageText } from '../utils/autonomousStageText.js';

const Box = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
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

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Label = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
`;

const Input = styled.input`
  font-size: 0.85rem;
  padding: 4px 6px;
`;

const Button = styled.button`
  font-size: 0.85rem;
`;

const List = styled.ul`
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.85rem;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

const DEFAULT_LINES = [
  { intent: 'sparse_opening', text: 'cold sparse opening' },
  { intent: 'establish_theme', text: 'introduce Theme A' },
  { intent: 'build', text: 'increase tension' },
  { intent: 'climax', text: 'strong climax' },
  { intent: 'resolve', text: 'quiet transformed ending' },
];

const AutonomousComposerPanel = () => {
  const status = useMusicStore((s) => s.autonomousStatus);
  const error = useMusicStore((s) => s.autonomousError);
  const run = useMusicStore((s) => s.autonomousRun);
  const start = useMusicStore((s) => s.startAutonomousComposer);
  const cancel = useMusicStore((s) => s.cancelAutonomousComposer);
  const resume = useMusicStore((s) => s.resumeAutonomousComposer);
  const approve = useMusicStore((s) => s.approveAutonomousRender);
  const skip = useMusicStore((s) => s.skipAutonomousRender);

  const [duration, setDuration] = useState(150);
  const [lines, setLines] = useState(DEFAULT_LINES);
  const [instruments, setInstruments] = useState('piano, cello, strings');
  const [openingKey, setOpeningKey] = useState('F# minor');
  const [finalKey, setFinalKey] = useState('F# major');
  const [noDrums, setNoDrums] = useState(true);
  const [themeStays, setThemeStays] = useState(true);
  const [includeRendering, setIncludeRendering] = useState(false);

  const loading = status === 'loading';
  const stages = Array.isArray(run?.stages) ? run.stages : [];
  const awaitingRender = stages.some(
    (stage) => stage.stage_id === 'render' && stage.status === 'awaiting_approval',
  );

  const onStart = () => {
    const instrumentation = instruments.split(',').map((item) => item.trim()).filter(Boolean);
    const brief = {
      schema_version: 'creative.brief.v1',
      duration_seconds: Number(duration) || 150,
      narrative: lines.map((line) => ({ intent: line.intent, text: line.text })),
      instrumentation,
      forbidden_instrument_families: noDrums ? ['drums'] : [],
      opening_key: openingKey,
      final_section_key: finalKey || null,
      motif_label: 'Theme A',
      motif_must_remain_recognizable: themeStays,
    };
    start(brief, { includeRendering });
  };

  return (
    <Box data-testid="autonomous-composer-panel">
      <Title>Autonomous composer</Title>
      <Hint>
        One brief builds a multi-section project. It does not run the spine preview.
      </Hint>
      <Row>
        <Label>
          Duration (seconds)
          <Input
            data-testid="autonomous-duration"
            type="number"
            min={30}
            max={600}
            value={duration}
            disabled={loading}
            onChange={(event) => setDuration(event.target.value)}
          />
        </Label>
        <Label>
          Opening key
          <Input
            value={openingKey}
            disabled={loading}
            onChange={(event) => setOpeningKey(event.target.value)}
          />
        </Label>
        <Label>
          Final key
          <Input
            value={finalKey}
            disabled={loading}
            onChange={(event) => setFinalKey(event.target.value)}
          />
        </Label>
      </Row>
      {lines.map((line, index) => (
        <Label key={line.intent}>
          {line.intent}
          <Input
            value={line.text}
            disabled={loading}
            onChange={(event) => {
              const next = lines.slice();
              next[index] = { ...line, text: event.target.value };
              setLines(next);
            }}
          />
        </Label>
      ))}
      <Label>
        Instruments
        <Input
          value={instruments}
          disabled={loading}
          onChange={(event) => setInstruments(event.target.value)}
        />
      </Label>
      <Row>
        <label>
          <input
            type="checkbox"
            checked={noDrums}
            disabled={loading}
            onChange={(event) => setNoDrums(event.target.checked)}
          />
          {' '}
          No drums
        </label>
        <label>
          <input
            type="checkbox"
            checked={themeStays}
            disabled={loading}
            onChange={(event) => setThemeStays(event.target.checked)}
          />
          {' '}
          Theme A stays recognizable
        </label>
        <label>
          <input
            type="checkbox"
            checked={includeRendering}
            disabled={loading}
            onChange={(event) => setIncludeRendering(event.target.checked)}
          />
          {' '}
          Include rendering
        </label>
      </Row>
      <Row>
        <Button type="button" data-testid="autonomous-start" disabled={loading} onClick={onStart}>
          Start
        </Button>
        <Button type="button" data-testid="autonomous-cancel" disabled={!loading && !run} onClick={cancel}>
          Cancel
        </Button>
        <Button
          type="button"
          data-testid="autonomous-resume"
          disabled={loading || !run}
          onClick={resume}
        >
          Resume
        </Button>
        {awaitingRender ? (
          <>
            <Button type="button" data-testid="autonomous-render-approve" onClick={approve}>
              Approve render
            </Button>
            <Button type="button" data-testid="autonomous-render-skip" onClick={skip}>
              Skip render
            </Button>
          </>
        ) : null}
      </Row>
      {error ? <ErrorText>{error}</ErrorText> : null}
      <List data-testid="autonomous-stage-list">
        {stages.map((stage) => (
          <li key={stage.stage_id}>{autonomousStageText(stage)}</li>
        ))}
      </List>
    </Box>
  );
};

export default AutonomousComposerPanel;
