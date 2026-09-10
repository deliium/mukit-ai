import React, { useMemo, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../../store/musicStore.js';
import { DYNAMIC_LEVELS } from '../../utils/musicJsonValidation.js';
import { ARTICULATION_VALUES, SNAP_VALUES } from '../../utils/pianoRollEvents.js';

const Shell = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #e0e7ff;
  border-radius: 8px;
  background: #f8fafc;
`;

const Group = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
`;

const Label = styled.span`
  font-size: 0.75rem;
  font-weight: 600;
  color: #4338ca;
  min-width: 4.5rem;
`;

const TinyButton = styled.button`
  padding: 4px 8px;
  border: none;
  border-radius: 6px;
  background: ${(props) => (props.$danger ? '#ef4444' : '#4f46e5')};
  color: white;
  font-size: 0.75rem;
  font-weight: 500;
  cursor: pointer;

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }

  &:focus-visible {
    outline: 2px solid #6366f1;
    outline-offset: 1px;
  }
`;

const Chip = styled.button`
  padding: 3px 7px;
  border-radius: 999px;
  border: 1px solid ${(props) => (props.$active ? '#4338ca' : '#c7d2fe')};
  background: ${(props) => (props.$active ? '#eef2ff' : '#ffffff')};
  color: #312e81;
  font-size: 0.7rem;
  cursor: pointer;

  &:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }

  &:focus-visible {
    outline: 2px solid #6366f1;
    outline-offset: 1px;
  }
`;

const Field = styled.label`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  color: #374151;
`;

const Select = styled.select`
  padding: 3px 6px;
  border: 1px solid #c7d2fe;
  border-radius: 4px;
  background: white;
  font-size: 0.75rem;
`;

const NumberInput = styled.input`
  width: 56px;
  padding: 3px 6px;
  border: 1px solid #c7d2fe;
  border-radius: 4px;
  background: white;
  font-size: 0.75rem;
`;

const Hint = styled.div`
  font-size: 0.75rem;
  color: #92400e;
`;

/**
 * Compact multi-note transform / articulation / dynamics inspector.
 */
export default function PianoRollSelectionInspector({
  selectedCount = 0,
  selectedNote = null,
  hasSelection = false,
}) {
  const pianoRollSnap = useMusicStore((state) => state.pianoRollSnap);
  const setPianoRollSnap = useMusicStore((state) => state.setPianoRollSnap);
  const transposeSelection = useMusicStore((state) => state.transposeSelection);
  const quantizeSelection = useMusicStore((state) => state.quantizeSelection);
  const setSelectionVelocity = useMusicStore((state) => state.setSelectionVelocity);
  const deltaSelectionVelocity = useMusicStore((state) => state.deltaSelectionVelocity);
  const setSelectionNoteLength = useMusicStore((state) => state.setSelectionNoteLength);
  const nudgeSelectionNoteLength = useMusicStore((state) => state.nudgeSelectionNoteLength);
  const legatoSelection = useMusicStore((state) => state.legatoSelection);
  const humanizeSelection = useMusicStore((state) => state.humanizeSelection);
  const duplicateSelection = useMusicStore((state) => state.duplicateSelection);
  const setSelectionArticulation = useMusicStore((state) => state.setSelectionArticulation);
  const upsertDynamicMark = useMusicStore((state) => state.upsertDynamicMark);
  const removeDynamicMark = useMusicStore((state) => state.removeDynamicMark);
  const editorCommandFeedback = useMusicStore((state) => state.editorCommandFeedback);
  const editCursorTick = useMusicStore((state) => state.editCursorTick);
  const aiEditStartBar = useMusicStore((state) => state.aiEditStartBar);
  const lockedTrackIds = useMusicStore((state) => state.lockedTrackIds);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);

  const [quantizeMode, setQuantizeMode] = useState('start');
  const [quantizeStrength, setQuantizeStrength] = useState(100);
  const [velocityValue, setVelocityValue] = useState(
    () => (Number.isFinite(selectedNote?.velocity) ? selectedNote.velocity : 90),
  );
  const [humanizeTiming, setHumanizeTiming] = useState(10);
  const [humanizeVelocity, setHumanizeVelocity] = useState(8);
  const [dynamicLevel, setDynamicLevel] = useState('mf');
  const [dynamicAt, setDynamicAt] = useState('cursor');

  const trackLocked = useMemo(
    () => (lockedTrackIds || []).map(String).includes(String(pianoRollTrackId)),
    [lockedTrackIds, pianoRollTrackId],
  );
  const disabled = !hasSelection;

  const activeArticulations = Array.isArray(selectedNote?.articulations)
    ? selectedNote.articulations
    : [];

  return (
    <Shell data-testid="piano-roll-selection-inspector" aria-label="Selection transform inspector">
      <Group>
        <Label>Pitch</Label>
        <TinyButton
          type="button"
          data-testid="piano-roll-transpose-down"
          disabled={disabled}
          aria-label="Transpose selection down one semitone"
          onClick={() => transposeSelection(-1)}
        >
          −1
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-transpose-up"
          disabled={disabled}
          aria-label="Transpose selection up one semitone"
          onClick={() => transposeSelection(1)}
        >
          +1
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-transpose-octave-down"
          disabled={disabled}
          aria-label="Transpose selection down one octave"
          onClick={() => transposeSelection(-12)}
        >
          −8ve
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-transpose-octave-up"
          disabled={disabled}
          aria-label="Transpose selection up one octave"
          onClick={() => transposeSelection(12)}
        >
          +8ve
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-duplicate"
          disabled={disabled}
          aria-label="Duplicate selection"
          onClick={() => duplicateSelection()}
        >
          Duplicate
        </TinyButton>
      </Group>

      <Group>
        <Label>Quantize</Label>
        <Field htmlFor="piano-roll-quantize-mode">
          Mode
          <Select
            id="piano-roll-quantize-mode"
            data-testid="piano-roll-quantize-mode"
            value={quantizeMode}
            aria-label="Quantize mode"
            onChange={(event) => setQuantizeMode(event.target.value)}
          >
            <option value="start">Start</option>
            <option value="start_end">Start + end</option>
          </Select>
        </Field>
        <Field htmlFor="piano-roll-quantize-grid">
          Grid
          <Select
            id="piano-roll-quantize-grid"
            data-testid="piano-roll-quantize-grid"
            value={pianoRollSnap}
            aria-label="Quantize grid snap"
            onChange={(event) => setPianoRollSnap(event.target.value)}
          >
            {SNAP_VALUES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </Select>
        </Field>
        <Field htmlFor="piano-roll-quantize-strength">
          Strength
          <NumberInput
            id="piano-roll-quantize-strength"
            data-testid="piano-roll-quantize-strength"
            type="number"
            min="0"
            max="100"
            value={quantizeStrength}
            aria-label="Quantize strength percent"
            onChange={(event) => setQuantizeStrength(Number(event.target.value))}
          />
        </Field>
        <TinyButton
          type="button"
          data-testid="piano-roll-quantize-apply"
          disabled={disabled}
          aria-label="Apply quantize to selection"
          onClick={() => quantizeSelection({
            mode: quantizeMode,
            snapValue: pianoRollSnap,
            strength: quantizeStrength,
          })}
        >
          Apply
        </TinyButton>
      </Group>

      <Group>
        <Label>Velocity</Label>
        <Field htmlFor="piano-roll-velocity-value">
          Set
          <NumberInput
            id="piano-roll-velocity-value"
            data-testid="piano-roll-velocity-value"
            type="number"
            min="1"
            max="127"
            value={velocityValue}
            aria-label="Selection velocity value"
            onChange={(event) => setVelocityValue(Number(event.target.value))}
          />
        </Field>
        <TinyButton
          type="button"
          data-testid="piano-roll-velocity-set"
          disabled={disabled}
          aria-label="Set selection velocity"
          onClick={() => setSelectionVelocity(velocityValue)}
        >
          Set
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-velocity-delta-down"
          disabled={disabled}
          aria-label="Decrease selection velocity by 5"
          onClick={() => deltaSelectionVelocity(-5)}
        >
          −5
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-velocity-delta-up"
          disabled={disabled}
          aria-label="Increase selection velocity by 5"
          onClick={() => deltaSelectionVelocity(5)}
        >
          +5
        </TinyButton>
      </Group>

      <Group>
        <Label>Length</Label>
        <TinyButton
          type="button"
          data-testid="piano-roll-length-to-grid"
          disabled={disabled}
          aria-label="Set selection note length to grid"
          onClick={() => setSelectionNoteLength({ toGrid: true })}
        >
          Set to grid
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-length-nudge-down"
          disabled={disabled}
          aria-label="Shorten selection by one snap step"
          onClick={() => nudgeSelectionNoteLength(-1)}
        >
          −snap
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-length-nudge-up"
          disabled={disabled}
          aria-label="Lengthen selection by one snap step"
          onClick={() => nudgeSelectionNoteLength(1)}
        >
          +snap
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-length-quantize-ends"
          disabled={disabled}
          aria-label="Quantize selection note ends"
          onClick={() => setSelectionNoteLength({
            quantizeEnds: true,
            strength: quantizeStrength,
          })}
        >
          Quantize end
        </TinyButton>
        <TinyButton
          type="button"
          data-testid="piano-roll-length-legato"
          disabled={disabled}
          aria-label="Legato selection note lengths"
          onClick={() => legatoSelection()}
        >
          Legato
        </TinyButton>
      </Group>

      <Group>
        <Label>Humanize</Label>
        <Field htmlFor="piano-roll-humanize-timing">
          Timing
          <NumberInput
            id="piano-roll-humanize-timing"
            data-testid="piano-roll-humanize-timing"
            type="number"
            min="0"
            max="120"
            value={humanizeTiming}
            aria-label="Humanize timing amount in ticks"
            onChange={(event) => setHumanizeTiming(Number(event.target.value))}
          />
        </Field>
        <Field htmlFor="piano-roll-humanize-velocity">
          Velocity
          <NumberInput
            id="piano-roll-humanize-velocity"
            data-testid="piano-roll-humanize-velocity"
            type="number"
            min="0"
            max="40"
            value={humanizeVelocity}
            aria-label="Humanize velocity amount"
            onChange={(event) => setHumanizeVelocity(Number(event.target.value))}
          />
        </Field>
        <TinyButton
          type="button"
          data-testid="piano-roll-humanize-apply"
          disabled={disabled}
          aria-label="Apply humanize to selection"
          onClick={() => humanizeSelection({
            timingAmount: Math.max(0, Math.round(humanizeTiming) || 0),
            velocityAmount: Math.max(0, Math.round(humanizeVelocity) || 0),
          })}
        >
          Apply
        </TinyButton>
      </Group>

      <Group aria-label="Articulation controls">
        <Label>Artics</Label>
        {ARTICULATION_VALUES.map((articulation) => {
          const active = activeArticulations.includes(articulation);
          return (
            <Chip
              key={articulation}
              type="button"
              $active={active}
              data-testid={`articulation-${articulation}`}
              disabled={disabled}
              aria-label={`Toggle ${articulation} on selection`}
              aria-pressed={active}
              onClick={() => setSelectionArticulation(articulation, 'toggle')}
            >
              {articulation}
            </Chip>
          );
        })}
        {selectedCount > 1 && (
          <span style={{ fontSize: '0.7rem', color: '#64748b' }}>
            {selectedCount} notes
          </span>
        )}
      </Group>

      <Group aria-label="Dynamics controls">
        <Label>Dynamics</Label>
        <Field htmlFor="piano-roll-dynamic-level">
          Level
          <Select
            id="piano-roll-dynamic-level"
            data-testid="piano-roll-dynamic-level"
            value={dynamicLevel}
            aria-label="Dynamic level"
            onChange={(event) => setDynamicLevel(event.target.value)}
          >
            {DYNAMIC_LEVELS.map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
          </Select>
        </Field>
        <Field htmlFor="piano-roll-dynamic-at">
          At
          <Select
            id="piano-roll-dynamic-at"
            data-testid="piano-roll-dynamic-at"
            value={dynamicAt}
            aria-label="Dynamics placement"
            onChange={(event) => setDynamicAt(event.target.value)}
          >
            <option value="cursor">Edit cursor ({editCursorTick})</option>
            <option value="bar">
              Selected bar start{aiEditStartBar ? ` (bar ${aiEditStartBar})` : ''}
            </option>
          </Select>
        </Field>
        <TinyButton
          type="button"
          data-testid="piano-roll-dynamic-upsert"
          disabled={trackLocked}
          aria-label="Create or update dynamic mark"
          onClick={() => upsertDynamicMark({ level: dynamicLevel, at: dynamicAt })}
        >
          Set mark
        </TinyButton>
        <TinyButton
          type="button"
          $danger
          data-testid="piano-roll-dynamic-remove"
          disabled={trackLocked}
          aria-label="Remove dynamic mark at placement"
          onClick={() => removeDynamicMark({ at: dynamicAt })}
        >
          Remove
        </TinyButton>
      </Group>

      {editorCommandFeedback?.skippedCount > 0 && (
        <Hint data-testid="piano-roll-articulation-skip-hint">
          {editorCommandFeedback.message || `${editorCommandFeedback.skippedCount} note(s) skipped`}
        </Hint>
      )}
    </Shell>
  );
}
