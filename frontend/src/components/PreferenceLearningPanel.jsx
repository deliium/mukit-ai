import React, { useEffect, useState } from 'react';
import styled from 'styled-components';

import {
  getPreferenceSettings,
  listPreferenceChoices,
  PreferenceApiError,
  putPreferenceSettings,
  resetPreferenceData,
} from '../api/preferenceApi.js';
import { useMusicStore } from '../store/musicStore.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
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

const Row = styled.label`
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 0.9rem;
  color: #334155;
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
  width: fit-content;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
`;

const List = styled.ul`
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const Item = styled.li`
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 0.85rem;
  color: #334155;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

function errorCode(error) {
  if (error instanceof PreferenceApiError && error.code) {
    return error.code;
  }
  return 'preference_request_failed';
}

export default function PreferenceLearningPanel() {
  const collectionEnabled = useMusicStore((state) => state.preferenceCollectionEnabled);
  const rankingEnabled = useMusicStore((state) => state.preferenceRankingEnabled);
  const featureAvailable = useMusicStore((state) => state.preferenceFeatureAvailable);
  const setPreferenceLearningFlags = useMusicStore((state) => state.setPreferenceLearningFlags);
  const [choices, setChoices] = useState([]);
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [settings, listed] = await Promise.all([
          getPreferenceSettings(),
          listPreferenceChoices(),
        ]);
        if (cancelled) {
          return;
        }
        setPreferenceLearningFlags({
          collectionEnabled: Boolean(settings.collection_enabled),
          rankingEnabled: Boolean(settings.ranking_enabled),
          featureAvailable: Boolean(settings.feature_available),
        });
        setChoices(listed);
        setStatus('ready');
        setError('');
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setStatus('error');
        setError(errorCode(loadError));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setPreferenceLearningFlags]);

  async function updateSwitch(nextCollection, nextRanking) {
    setError('');
    try {
      const saved = await putPreferenceSettings({
        collection_enabled: nextCollection,
        ranking_enabled: nextRanking,
      });
      setPreferenceLearningFlags({
        collectionEnabled: Boolean(saved.collection_enabled),
        rankingEnabled: Boolean(saved.ranking_enabled),
        featureAvailable: Boolean(saved.feature_available),
      });
    } catch (saveError) {
      setError(errorCode(saveError));
    }
  }

  async function resetChoices() {
    setError('');
    try {
      await resetPreferenceData();
      const listed = await listPreferenceChoices();
      setChoices(listed);
    } catch (resetError) {
      setError(errorCode(resetError));
    }
  }

  return (
    <Panel>
      <Title>Preference learning</Title>
      <Hint>
        A choice is stored only after you Apply a development or arrangement candidate,
        and only when both the server flag and collection are on. Ranking reorders the
        next ballot. It does not Apply.
      </Hint>
      {featureAvailable ? null : (
        <Hint>The server flag is off.</Hint>
      )}
      <Row>
        <input
          type="checkbox"
          checked={collectionEnabled}
          disabled={!featureAvailable || status === 'loading'}
          onChange={(event) => updateSwitch(event.target.checked, rankingEnabled)}
        />
        Collect choices
      </Row>
      <Row>
        <input
          type="checkbox"
          checked={rankingEnabled}
          disabled={!featureAvailable || status === 'loading'}
          onChange={(event) => updateSwitch(collectionEnabled, event.target.checked)}
        />
        Rank later ballots
      </Row>
      <Button type="button" onClick={resetChoices} disabled={status === 'loading'}>
        Reset preference data
      </Button>
      {error ? <ErrorText>{error}</ErrorText> : null}
      <List>
        {choices.map((choice) => (
          <Item key={choice.id}>
            {choice.surface}
            {' · '}
            {choice.operation}
            {' · chosen '}
            {choice.chosen_candidate_id}
            {' · '}
            {choice.candidate_count}
            {' candidates · '}
            {choice.source_fingerprint_prefix}
          </Item>
        ))}
      </List>
      {status === 'ready' && choices.length === 0 ? <Hint>No stored choices.</Hint> : null}
    </Panel>
  );
}
