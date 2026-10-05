import React from 'react';
import styled from 'styled-components';
import ComposerWorkspace from './ComposerWorkspace.jsx';
import ProjectComposerBar from './ProjectComposerBar.jsx';

const Container = styled.div`
  width: 100%;
  min-width: 0;

  h2 {
    color: #1e293b;
    margin-bottom: 12px;
    font-size: 1.35rem;
    font-weight: 650;
  }
`;

/**
 * Composer workspace shell: project bar + tabbed studio (Generate first).
 */
const MusicGenerator = () => (
  <Container>
    <ProjectComposerBar />
    <ComposerWorkspace />
  </Container>
);

export default MusicGenerator;
