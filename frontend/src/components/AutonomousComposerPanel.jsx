import React, { useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import {
  briefFingerprint,
  canStart,
  enabledActions,
  instructionAllowed,
  musicalBoardLine,
  musicalProgress,
} from '../utils/autonomousControl.js';

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

const INTENTS = ['sparse_opening', 'establish_theme', 'build', 'climax', 'resolve'];

const DEFAULT_LINES = [
  { intent: 'sparse_opening', text: 'cold sparse opening' },
  { intent: 'establish_theme', text: 'introduce Theme A' },
  { intent: 'build', text: 'increase tension' },
  { intent: 'climax', text: 'strong climax' },
  { intent: 'resolve', text: 'quiet transformed ending' },
];

function buildBrief(fields) {
  const instrumentation = fields.instruments
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    schema_version: 'creative.brief.v1',
    title: fields.title.trim() || null,
    duration_seconds: Number(fields.duration) || 150,
    narrative: fields.lines.map((line) => ({ intent: line.intent, text: line.text })),
    instrumentation,
    forbidden_instrument_families: fields.noDrums ? ['drums'] : [],
    opening_key: fields.openingKey,
    final_section_key: fields.finalKey || null,
    motif_label: fields.motifLabel.trim() || 'Theme A',
    motif_must_remain_recognizable: fields.themeStays,
    time_signature: fields.meter.trim() || '4/4',
    tempo_min: fields.tempoMin ? Number(fields.tempoMin) : null,
    tempo_max: fields.tempoMax ? Number(fields.tempoMax) : null,
    mood: fields.mood.trim() || null,
    genre: fields.genre.trim() || null,
  };
}

function PlanSummary({ plan }) {
  if (!plan?.constraints) return null;
  const bars = plan.constraints.duration_bars;
  const sections = Array.isArray(plan.sections) ? plan.sections : [];
  return (
    <div data-testid="autonomous-plan-summary">
      <Hint data-testid="autonomous-plan-bars">
        {bars != null ? `${bars} bars` : 'Plan ready'}
      </Hint>
      <List>
        {sections.map((section) => (
          <li key={section.id}>
            {section.label}
            {' '}
            {section.start_bar}
            {' · '}
            {section.bar_count}
          </li>
        ))}
      </List>
    </div>
  );
}

const AutonomousComposerPanel = () => {
  const status = useMusicStore((s) => s.autonomousStatus);
  const error = useMusicStore((s) => s.autonomousError);
  const run = useMusicStore((s) => s.autonomousRun);
  const runId = useMusicStore((s) => s.autonomousRunId);
  const preview = useMusicStore((s) => s.autonomousPreview);
  const previewFingerprint = useMusicStore((s) => s.autonomousPreviewFingerprint);
  const start = useMusicStore((s) => s.startAutonomousComposer);
  const review = useMusicStore((s) => s.previewAutonomousComposer);
  const cancel = useMusicStore((s) => s.cancelAutonomousComposer);
  const pause = useMusicStore((s) => s.pauseAutonomousComposer);
  const resume = useMusicStore((s) => s.resumeAutonomousComposer);
  const approveRender = useMusicStore((s) => s.approveAutonomousRender);
  const skipRender = useMusicStore((s) => s.skipAutonomousRender);
  const approveCheckpoint = useMusicStore((s) => s.approveAutonomousCheckpoint);
  const rejectArrangement = useMusicStore((s) => s.rejectAutonomousArrangement);
  const instruct = useMusicStore((s) => s.instructAutonomousStage);
  const retry = useMusicStore((s) => s.retryAutonomousStage);
  const openStage = useMusicStore((s) => s.openAutonomousStage);
  const branchStage = useMusicStore((s) => s.branchAutonomousStage);
  const checkoutBranch = useMusicStore((s) => s.checkoutVersionBranch);

  const [title, setTitle] = useState('');
  const [duration, setDuration] = useState(150);
  const [tempoMin, setTempoMin] = useState(60);
  const [tempoMax, setTempoMax] = useState(84);
  const [meter, setMeter] = useState('4/4');
  const [lines, setLines] = useState(DEFAULT_LINES);
  const [instruments, setInstruments] = useState('piano, cello, strings');
  const [openingKey, setOpeningKey] = useState('F# minor');
  const [finalKey, setFinalKey] = useState('F# major');
  const [motifLabel, setMotifLabel] = useState('Theme A');
  const [mood, setMood] = useState('');
  const [genre, setGenre] = useState('');
  const [noDrums, setNoDrums] = useState(true);
  const [themeStays, setThemeStays] = useState(true);
  const [includeRendering, setIncludeRendering] = useState(false);
  const [autonomyMode, setAutonomyMode] = useState('autonomous');
  const [instructionStage, setInstructionStage] = useState('arrangement');
  const [instructionText, setInstructionText] = useState('');
  const [branchName, setBranchName] = useState('From theme');
  const [fork, setFork] = useState(null);

  const loading = status === 'loading';
  const brief = buildBrief({
    title,
    duration,
    tempoMin,
    tempoMax,
    meter,
    lines,
    instruments,
    openingKey,
    finalKey,
    motifLabel,
    mood,
    genre,
    noDrums,
    themeStays,
  });
  const startEnabled = !loading && canStart(
    { previewedFingerprint: previewFingerprint },
    brief,
  );
  const stages = Array.isArray(run?.stages) ? run.stages : [];
  const board = musicalProgress(stages, brief.motif_label, {
    runStatus: run?.status,
    checkpointId: run?.checkpoint_id,
  });
  const actions = enabledActions(run || {});
  const awaitingRender = stages.some(
    (stage) => stage.stage_id === 'render' && stage.status === 'awaiting_approval',
  );
  const instructionOk = instructionAllowed(instructionText, {
    forbiddenFamilies: brief.forbidden_instrument_families,
  });
  const failedRetry = stages.filter((stage) => (
    stage.status === 'failed'
    && stage.failure_code !== 'operation_cancelled'
    && stage.recoverable !== false
    && stage.recoverable !== 0
  ));

  const onReview = () => {
    review(brief);
  };

  const onStart = () => {
    if (briefFingerprint(brief) !== previewFingerprint) return;
    start(brief, { includeRendering, autonomyMode });
  };

  const onInstruct = () => {
    instruct(instructionStage, instructionText);
  };

  const onBranch = async (stageId) => {
    const created = await branchStage(stageId, branchName);
    if (created?.branch_id) {
      setFork(created);
    }
  };

  return (
    <Box data-testid="autonomous-composer-panel">
      <Title>Autonomous composer</Title>
      <Hint>
        Write the brief, review the compiled plan, then start. Guided mode stops at musical checkpoints.
      </Hint>
      <Row>
        <Label>
          Title
          <Input
            data-testid="autonomous-title"
            value={title}
            disabled={loading}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Label>
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
          Tempo min
          <Input
            data-testid="autonomous-tempo-min"
            type="number"
            min={1}
            max={400}
            value={tempoMin}
            disabled={loading}
            onChange={(event) => setTempoMin(event.target.value)}
          />
        </Label>
        <Label>
          Tempo max
          <Input
            data-testid="autonomous-tempo-max"
            type="number"
            min={1}
            max={400}
            value={tempoMax}
            disabled={loading}
            onChange={(event) => setTempoMax(event.target.value)}
          />
        </Label>
        <Label>
          Meter
          <Input
            data-testid="autonomous-meter"
            value={meter}
            disabled={loading}
            onChange={(event) => setMeter(event.target.value)}
          />
        </Label>
      </Row>
      <Row>
        <Label>
          Opening key
          <Input
            data-testid="autonomous-opening-key"
            value={openingKey}
            disabled={loading}
            onChange={(event) => setOpeningKey(event.target.value)}
          />
        </Label>
        <Label>
          Final key
          <Input
            data-testid="autonomous-final-key"
            value={finalKey}
            disabled={loading}
            onChange={(event) => setFinalKey(event.target.value)}
          />
        </Label>
        <Label>
          Motif label
          <Input
            data-testid="autonomous-motif-label"
            value={motifLabel}
            disabled={loading}
            onChange={(event) => setMotifLabel(event.target.value)}
          />
        </Label>
        <Label>
          Mood
          <Input
            value={mood}
            disabled={loading}
            onChange={(event) => setMood(event.target.value)}
          />
        </Label>
        <Label>
          Genre
          <Input
            value={genre}
            disabled={loading}
            onChange={(event) => setGenre(event.target.value)}
          />
        </Label>
        <Label>
          Autonomy
          <select
            data-testid="autonomous-mode"
            value={autonomyMode}
            disabled={loading}
            onChange={(event) => setAutonomyMode(event.target.value)}
          >
            <option value="guided">Guided</option>
            <option value="balanced">Balanced</option>
            <option value="autonomous">Autonomous</option>
          </select>
        </Label>
      </Row>
      {lines.map((line, index) => (
        <Row key={`${line.intent}-${index}`}>
          <Label>
            Beat
            <select
              value={line.intent}
              disabled={loading}
              onChange={(event) => {
                const next = lines.slice();
                next[index] = { ...line, intent: event.target.value };
                setLines(next);
              }}
            >
              {INTENTS.map((intent) => (
                <option key={intent} value={intent}>{intent}</option>
              ))}
            </select>
          </Label>
          <Label>
            Text
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
          <Button
            type="button"
            disabled={loading || lines.length <= 1}
            onClick={() => setLines(lines.filter((_, item) => item !== index))}
          >
            Remove beat
          </Button>
        </Row>
      ))}
      <Button
        type="button"
        data-testid="autonomous-add-beat"
        disabled={loading || lines.length >= 8}
        onClick={() => setLines(lines.concat([{ intent: 'build', text: 'continue' }]))}
      >
        Add beat
      </Button>
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
          Theme stays recognizable
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
        <Button type="button" data-testid="autonomous-review-plan" disabled={loading} onClick={onReview}>
          Review plan
        </Button>
        <Button type="button" data-testid="autonomous-start" disabled={!startEnabled} onClick={onStart}>
          Start
        </Button>
        {(Boolean(runId) && loading) || actions.includes('pause') ? (
          <Button type="button" data-testid="autonomous-pause" disabled={!runId} onClick={pause}>
            Pause
          </Button>
        ) : null}
        <Button type="button" data-testid="autonomous-cancel" disabled={!loading && !run} onClick={cancel}>
          Cancel
        </Button>
        {actions.includes('resume') ? (
          <Button type="button" data-testid="autonomous-resume" disabled={loading} onClick={resume}>
            Resume
          </Button>
        ) : null}
        {actions.includes('approve') && run?.checkpoint_id && run.checkpoint_id !== 'render' ? (
          <Button
            type="button"
            data-testid="autonomous-approve"
            disabled={loading}
            onClick={() => approveCheckpoint(run.checkpoint_id)}
          >
            Approve
          </Button>
        ) : null}
        {actions.includes('reject') ? (
          <Button type="button" data-testid="autonomous-reject" disabled={loading} onClick={rejectArrangement}>
            Reject arrangement
          </Button>
        ) : null}
        {awaitingRender ? (
          <>
            <Button type="button" data-testid="autonomous-render-approve" onClick={approveRender}>
              Approve render
            </Button>
            <Button type="button" data-testid="autonomous-render-skip" onClick={skipRender}>
              Skip render
            </Button>
          </>
        ) : null}
      </Row>
      <PlanSummary plan={preview} />
      <PlanSummary plan={run?.plan} />
      {actions.includes('instruction') ? (
        <Row>
          <Label>
            Stage
            <select
              data-testid="autonomous-instruction-stage"
              value={instructionStage}
              onChange={(event) => setInstructionStage(event.target.value)}
            >
              <option value="arrangement">Arrangement</option>
              <option value="expression">Expression</option>
            </select>
          </Label>
          <Label>
            Instruction
            <Input
              data-testid="autonomous-instruction"
              value={instructionText}
              maxLength={500}
              onChange={(event) => setInstructionText(event.target.value)}
            />
          </Label>
          <Button
            type="button"
            data-testid="autonomous-instruction-submit"
            disabled={loading || !instructionOk}
            onClick={onInstruct}
          >
            Save instruction
          </Button>
        </Row>
      ) : null}
      {stages
        .filter((stage) => stage.stage_id === instructionStage && stage.instruction)
        .map((stage) => (
          <Hint key={`shown-${stage.stage_id}`}>
            {stage.instruction}
            {stage.warning_code ? ` (${stage.warning_code})` : ''}
          </Hint>
        ))}
      {failedRetry.map((stage) => {
        const row = board.find((item) => item.stage_ids.includes(stage.stage_id));
        return (
          <Button
            key={stage.stage_id}
            type="button"
            data-testid={`autonomous-retry-${stage.stage_id}`}
            disabled={loading}
            onClick={() => retry(stage.stage_id)}
          >
            Retry
            {' '}
            {row?.label || 'stage'}
          </Button>
        );
      })}
      <Label>
        Branch name
        <Input
          data-testid="autonomous-branch-name"
          value={branchName}
          maxLength={80}
          onChange={(event) => setBranchName(event.target.value)}
        />
      </Label>
      {stages.filter((stage) => stage.revision_id).map((stage) => {
        const row = board.find((item) => item.stage_ids.includes(stage.stage_id));
        const label = row?.label || 'stage';
        const isHead = stage.revision_id === run?.head_revision_id;
        return (
          <Row key={`rev-${stage.stage_id}`}>
            {isHead ? (
              <Button
                type="button"
                data-testid={`autonomous-open-${stage.stage_id}`}
                disabled={loading}
                onClick={() => openStage(stage.stage_id)}
              >
                Open
                {' '}
                {label}
              </Button>
            ) : null}
            <Button
              type="button"
              data-testid={`autonomous-branch-${stage.stage_id}`}
              disabled={loading}
              onClick={() => onBranch(stage.stage_id)}
            >
              Branch
              {' '}
              {label}
            </Button>
          </Row>
        );
      })}
      {fork ? (
        <Row>
          <Hint data-testid="autonomous-branch-result">
            {fork.name}
            . The run stays on its original branch.
          </Hint>
          <Button
            type="button"
            data-testid="autonomous-branch-checkout"
            onClick={() => checkoutBranch(fork.branch_id)}
          >
            Open this branch in the editor
          </Button>
        </Row>
      ) : null}
      {error ? <ErrorText>{error}</ErrorText> : null}
      <List data-testid="autonomous-stage-list">
        {board.map((row) => (
          <li key={row.step_id}>{musicalBoardLine(row)}</li>
        ))}
      </List>
    </Box>
  );
};

export default AutonomousComposerPanel;
