import React, { useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import {
  canCommitFilmAdapt,
  countLine,
  editOpId,
  hitLine,
  operationLine,
} from '../utils/filmScoreAdapt.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 10px;
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

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Input = styled.input`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
  width: 7rem;
`;

const Select = styled.select`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
  color: #312e81;
  background: #fff;
`;

const Button = styled.button`
  border: 1px solid #c7d2fe;
  background: ${(p) => (p.$primary ? '#4338ca' : '#fff')};
  color: ${(p) => (p.$primary ? '#fff' : '#312e81')};
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.9rem;
  cursor: pointer;

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const List = styled.ul`
  margin: 0;
  padding-left: 18px;
  font-size: 0.85rem;
  color: #1e1b4b;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

const EMPTY_EDIT = {
  kind: 'delete_span',
  start_seconds: '',
  end_seconds: '',
  at_seconds: '',
  duration_seconds: '',
  cue_id: '',
  from_seconds: '',
  to_seconds: '',
};

function numberOrNull(value) {
  if (value === '' || value == null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function editPayload(edit, index) {
  const opId = editOpId(index);
  if (edit.kind === 'insert_span') {
    const atSeconds = numberOrNull(edit.at_seconds);
    const duration = numberOrNull(edit.duration_seconds);
    if (atSeconds == null || duration == null) return null;
    return { op_id: opId, kind: 'insert_span', at_seconds: atSeconds, duration_seconds: duration };
  }
  if (edit.kind === 'move_hit') {
    const fromSeconds = numberOrNull(edit.from_seconds);
    const toSeconds = numberOrNull(edit.to_seconds);
    if (!edit.cue_id || fromSeconds == null || toSeconds == null) return null;
    return {
      op_id: opId,
      kind: 'move_hit',
      cue_id: edit.cue_id,
      from_seconds: fromSeconds,
      to_seconds: toSeconds,
    };
  }
  const start = numberOrNull(edit.start_seconds);
  const end = numberOrNull(edit.end_seconds);
  if (start == null || end == null) return null;
  return { op_id: opId, kind: 'delete_span', start_seconds: start, end_seconds: end };
}

export default function FilmScoreAdaptPanel() {
  const pictureScoring = useMusicStore((state) => state.pictureScoring);
  const baseline = useMusicStore((state) => state.filmAdaptBaseline);
  const preview = useMusicStore((state) => state.filmAdaptPreview);
  const status = useMusicStore((state) => state.filmAdaptStatus);
  const error = useMusicStore((state) => state.filmAdaptError);
  const remember = useMusicStore((state) => state.rememberFilmAdaptBaseline);
  const previewAdapt = useMusicStore((state) => state.previewFilmAdapt);
  const commitAdapt = useMusicStore((state) => state.commitFilmAdapt);
  const [edits, setEdits] = useState([{ ...EMPTY_EDIT }]);

  const proposal = preview?.proposal;
  const rememberDisabled = pictureScoring == null || status === 'loading';

  function updateEdit(index, patch) {
    setEdits((current) => current.map((row, rowIndex) => (
      rowIndex === index ? { ...row, ...patch } : row
    )));
  }

  async function onPreview() {
    if (!baseline) return;
    const payload = edits.map(editPayload);
    if (payload.some((row) => row == null)) {
      useMusicStore.setState({ filmAdaptError: 'film_adapt_invalid' });
      return;
    }
    await previewAdapt({ previous: baseline, edits: payload });
  }

  return (
    <Panel data-testid="film-score-adapt-panel">
      <Title>Film score adaptation</Title>
      <Hint>
        Declare how the picture timing changed, inspect the local repair, then commit it.
        Opening this tab does not rewrite the score.
      </Hint>
      <Row>
        <Button
          type="button"
          data-testid="film-adapt-remember"
          disabled={rememberDisabled}
          onClick={() => { void remember(); }}
        >
          Remember current picture
        </Button>
        <Button
          type="button"
          data-testid="film-adapt-preview"
          disabled={!baseline || status === 'loading'}
          onClick={() => { void onPreview(); }}
        >
          Preview
        </Button>
        <Button
          type="button"
          $primary
          data-testid="film-adapt-commit"
          disabled={!canCommitFilmAdapt(preview) || status === 'loading'}
          onClick={() => { void commitAdapt(); }}
        >
          Commit
        </Button>
      </Row>
      {edits.map((edit, index) => (
        <Row key={editOpId(index)}>
          <Select
            aria-label={`Edit ${index + 1} kind`}
            value={edit.kind}
            onChange={(event) => updateEdit(index, { kind: event.target.value })}
          >
            <option value="delete_span">Delete span</option>
            <option value="insert_span">Insert span</option>
            <option value="move_hit">Move hit</option>
          </Select>
          {edit.kind === 'delete_span' ? (
            <>
              <Input
                aria-label={`Edit ${index + 1} start seconds`}
                inputMode="decimal"
                value={edit.start_seconds}
                onChange={(event) => updateEdit(index, { start_seconds: event.target.value })}
              />
              <Input
                aria-label={`Edit ${index + 1} end seconds`}
                inputMode="decimal"
                value={edit.end_seconds}
                onChange={(event) => updateEdit(index, { end_seconds: event.target.value })}
              />
            </>
          ) : null}
          {edit.kind === 'insert_span' ? (
            <>
              <Input
                aria-label={`Edit ${index + 1} at seconds`}
                inputMode="decimal"
                value={edit.at_seconds}
                onChange={(event) => updateEdit(index, { at_seconds: event.target.value })}
              />
              <Input
                aria-label={`Edit ${index + 1} duration seconds`}
                inputMode="decimal"
                value={edit.duration_seconds}
                onChange={(event) => updateEdit(index, { duration_seconds: event.target.value })}
              />
            </>
          ) : null}
          {edit.kind === 'move_hit' ? (
            <>
              <Input
                aria-label={`Edit ${index + 1} cue id`}
                value={edit.cue_id}
                onChange={(event) => updateEdit(index, { cue_id: event.target.value })}
              />
              <Input
                aria-label={`Edit ${index + 1} from seconds`}
                inputMode="decimal"
                value={edit.from_seconds}
                onChange={(event) => updateEdit(index, { from_seconds: event.target.value })}
              />
              <Input
                aria-label={`Edit ${index + 1} to seconds`}
                inputMode="decimal"
                value={edit.to_seconds}
                onChange={(event) => updateEdit(index, { to_seconds: event.target.value })}
              />
            </>
          ) : null}
        </Row>
      ))}
      <Button
        type="button"
        disabled={edits.length >= 16}
        onClick={() => setEdits((current) => [...current, { ...EMPTY_EDIT }])}
      >
        Add edit
      </Button>
      {proposal ? (
        <List data-testid="film-adapt-summary">
          {(proposal.operations || []).map((operation) => (
            <li key={operation.op_id}>{operationLine(operation)}</li>
          ))}
          <li>{countLine(proposal.counts)}</li>
          {(proposal.hit_changes || []).map((hit) => (
            <li key={hit.cue_id}>{hitLine(hit)}</li>
          ))}
          {(proposal.warnings || []).map((code) => (
            <li key={code}>{code}</li>
          ))}
        </List>
      ) : null}
      {error ? <ErrorText role="alert">{error}</ErrorText> : null}
    </Panel>
  );
}
