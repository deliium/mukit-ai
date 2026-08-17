import React from 'react';
import styled from 'styled-components';

const HeaderContainer = styled.header`
  text-align: center;
  margin-bottom: 40px;
  padding: 20px 0;
`;

const Title = styled.h1`
  font-size: 3rem;
  font-weight: 700;
  color: white;
  margin-bottom: 10px;
  text-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
  
  @media (max-width: 768px) {
    font-size: 2rem;
  }
`;

const Subtitle = styled.p`
  font-size: 1.2rem;
  color: rgba(255, 255, 255, 0.9);
  font-weight: 300;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
`;

const Header = () => {
  return (
    <HeaderContainer>
      <Title>🎵 AI Music Composer</Title>
      <Subtitle>Generate beautiful music using machine learning</Subtitle>
    </HeaderContainer>
  );
};

export default Header;
