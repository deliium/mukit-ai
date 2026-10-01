import React, { useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import {
  canCommitFilmScore,
  filmScoreRequestBody,
  hitStatusLines,
  sectionLines,
  tempoChangeLines,
} from '../utils/filmScorePlan.js';

const EMPTY_MOTIFS = [];

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

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #312e81;
`;

const Group = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #312e81;
`;

const Input = styled.input`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
`;

const Select = styled.select`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
  color: #312e81;
  background: #fff;
`;

const Area = styled.textarea`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
  min-height: 64px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
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

function FilmScoreSummary({ preview }) {
  const plan = preview?.plan;
  if (!plan) return null;
  const changes = tempoChangeLines(plan);
  return (
    <div data-testid="film-score-summary">
      <List>
        {sectionLines(plan).map((section) => (
          <li key={section.id}>
            {section.label} bars {section.startBar}+{section.barCount} · {section.bpm} bpm · {section.density}
          </li>
        ))}
      </List>
      <Hint>
        {changes.length === 0
          ? 'Tempo strategy: no changes'
          : `Tempo strategy: ${changes.map((change) => `tick ${change.tick} → ${change.bpm}`).join(', ')}`}
      </Hint>
      <List>
        {hitStatusLines(plan).map((hit) => (
          <li key={hit.cueId}>
            {hit.cueId}: {hit.status}
            {hit.tempoChangeAdded ? ' · tempo change' : ''}
          </li>
        ))}
      </List>
      <Hint>
        Recommendation: {preview.recommendation || 'none'}
        {(plan.warnings || []).length ? ` · ${plan.warnings.join(', ')}` : ''}
      </Hint>
    </div>
  );
}

export default function FilmScorePanel() {
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const preview = useMusicStore((state) => state.filmScorePreview);
  const status = useMusicStore((state) => state.filmScoreStatus);
  const errorCode = useMusicStore((state) => state.filmScoreError);
  const previewFilmScore = useMusicStore((state) => state.previewFilmScore);
  const commitFilmScore = useMusicStore((state) => state.commitFilmScore);
  const motifs = useMusicStore((state) => {
    const composition = state.editedMusicJson || state.generatedMusicJson;
    return Array.isArray(composition?.motifs) ? composition.motifs : EMPTY_MOTIFS;
  });
  const [brief, setBrief] = useState('');
  const [instruments, setInstruments] = useState('acoustic_grand_piano');
  const [profileId, setProfileId] = useState('');
  const [profileStrength, setProfileStrength] = useState('off');
  const [motifIds, setMotifIds] = useState([]);
  const [targetDuration, setTargetDuration] = useState('');
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [formError, setFormError] = useState('');
  const busy = status === 'loading';
  const commitEnabled = canCommitFilmScore(preview) && !busy;

  function toggleMotif(id) {
    setMotifIds((current) => {
      if (current.includes(id)) {
        return current.filter((item) => item !== id);
      }
      if (current.length >= 16) {
        return current;
      }
      return [...current, id];
    });
  }

  async function onPreview() {
    const prepared = filmScoreRequestBody({
      brief,
      instruments,
      profileId,
      profileStrength,
      motifIds,
      targetDurationSeconds: targetDuration,
      replaceExisting,
    });
    if (prepared.error) {
      console.debug('[FIX] film score preview rejected duration', { code: prepared.error });
      setFormError(prepared.error);
      return;
    }
    setFormError('');
    console.debug('[FIX] film score preview fields', {
      instrumentCount: prepared.body.instruments.length,
      motifCount: prepared.body.motif_ids.length,
      profileStrength: prepared.body.profile_strength,
      hasDuration: prepared.body.target_duration_seconds != null,
    });
    await previewFilmScore(prepared.body);
  }

  return (
    <Panel data-testid="film-score-panel">
      <Title>Film score</Title>
      <Hint>
        Preview builds an inspectable film score plan from the stored spotting cues.
        Commit writes the candidate. Opening this tab does not preview.
      </Hint>
      <Field>
        Brief
        <Area
          data-testid="film-score-brief"
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
        />
      </Field>
      <Field>
        Instruments
        <Input
          data-testid="film-score-instruments"
          value={instruments}
          onChange={(event) => setInstruments(event.target.value)}
        />
      </Field>
      <Field>
        Profile id
        <Input
          data-testid="film-score-profile-id"
          value={profileId}
          maxLength={80}
          onChange={(event) => setProfileId(event.target.value)}
        />
      </Field>
      <Field>
        Profile strength
        <Select
          data-testid="film-score-profile-strength"
          value={profileStrength}
          onChange={(event) => setProfileStrength(event.target.value)}
        >
          <option value="off">Off</option>
          <option value="light">Light</option>
          <option value="normal">Normal</option>
          <option value="strong">Strong</option>
        </Select>
      </Field>
      <Group>
        Motifs
        {motifs.length === 0 ? (
          <Hint>This composition has no motif ids.</Hint>
        ) : (
          motifs.map((motif) => (
            <label key={motif.id}>
              <input
                type="checkbox"
                data-testid={`film-score-motif-${motif.id}`}
                checked={motifIds.includes(motif.id)}
                onChange={() => toggleMotif(motif.id)}
              />
              {' '}
              {motif.label || motif.id}
            </label>
          ))
        )}
      </Group>
      <Field>
        Target duration seconds
        <Input
          data-testid="film-score-duration"
          value={targetDuration}
          inputMode="decimal"
          onChange={(event) => setTargetDuration(event.target.value)}
        />
      </Field>
      <Row>
        <label>
          <input
            type="checkbox"
            checked={replaceExisting}
            onChange={(event) => setReplaceExisting(event.target.checked)}
          />
          {' '}
          Replace existing notes
        </label>
        <Button type="button" $primary disabled={busy || !currentProjectId || !brief.trim()} onClick={onPreview}>
          Preview film score
        </Button>
        <Button
          type="button"
          data-testid="film-score-commit"
          disabled={!commitEnabled}
          onClick={() => commitFilmScore({ replaceExisting })}
        >
          Commit
        </Button>
      </Row>
      {formError || errorCode ? (
        <ErrorText data-testid="film-score-error">{formError || errorCode}</ErrorText>
      ) : null}
      <FilmScoreSummary preview={preview} />
    </Panel>
  );
}
