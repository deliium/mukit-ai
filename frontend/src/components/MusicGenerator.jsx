import React, { useState } from 'react';
import styled from 'styled-components';
import axios from 'axios';

const Container = styled.div`
  h2 {
    color: #333;
    margin-bottom: 20px;
    font-size: 1.5rem;
    font-weight: 600;
  }
`;

const FormGroup = styled.div`
  margin-bottom: 20px;
`;

const Label = styled.label`
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
  color: #374151;
`;

const Input = styled.input`
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 1rem;
  transition: border-color 0.3s ease;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Select = styled.select`
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 1rem;
  background: white;
  transition: border-color 0.3s ease;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Button = styled.button`
  background: #667eea;
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  font-size: 1rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
  width: 100%;
  margin-top: 10px;

  &:hover {
    background: #5a67d8;
    transform: translateY(-1px);
  }

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
    transform: none;
  }
`;

const StatusMessage = styled.div`
  padding: 12px;
  border-radius: 8px;
  margin: 15px 0;
  font-size: 0.9rem;
  text-align: center;
  
  &.success {
    background: #d1fae5;
    color: #065f46;
    border: 1px solid #a7f3d0;
  }
  
  &.error {
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #fca5a5;
  }
  
  &.info {
    background: #dbeafe;
    color: #1e40af;
    border: 1px solid #93c5fd;
  }
`;

const GeneratedMusic = styled.div`
  margin-top: 20px;
  padding: 20px;
  background: #f8f9ff;
  border-radius: 12px;
  border: 1px solid #e0e7ff;
`;

const DownloadButton = styled.a`
  display: inline-block;
  background: #10b981;
  color: white;
  text-decoration: none;
  padding: 10px 20px;
  border-radius: 8px;
  font-weight: 500;
  margin-top: 10px;
  transition: all 0.3s ease;

  &:hover {
    background: #059669;
    transform: translateY(-1px);
  }
`;

const ParameterGrid = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
  
  @media (max-width: 480px) {
    grid-template-columns: 1fr;
  }
`;

const MusicGenerator = ({ modelLoaded }) => {
  const [length, setLength] = useState(100);
  const [temperature, setTemperature] = useState(0.8);
  const [seedNotes, setSeedNotes] = useState('');
  const [format, setFormat] = useState('midi');
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState('');
  const [generatedFile, setGeneratedFile] = useState(null);

  const handleGenerate = async () => {
    if (!modelLoaded) {
      setMessage({
        type: 'error',
        text: 'Model not loaded. Please train the model first.'
      });
      return;
    }

    setGenerating(true);
    setMessage('');

    try {
      const requestData = {
        length: parseInt(length),
        temperature: parseFloat(temperature),
        format: format
      };

      // Parse seed notes if provided
      if (seedNotes.trim()) {
        const notes = seedNotes.split(',').map(note => {
          const trimmed = note.trim();
          return trimmed ? parseInt(trimmed) : null;
        }).filter(note => note !== null && note >= 0 && note <= 127);
        
        if (notes.length > 0) {
          requestData.seed_notes = notes;
        }
      }

      const response = await axios.post('/generate-music', requestData);
      
      setMessage({
        type: 'success',
        text: 'Music generated successfully!'
      });
      
      setGeneratedFile({
        filename: response.data.filename,
        downloadUrl: `/download/${response.data.filename}`
      });
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Generation failed: ${error.response?.data?.detail || error.message}`
      });
    } finally {
      setGenerating(false);
    }
  };

  return (
    <Container>
      <h2>🎹 Generate Music</h2>
      
      <ParameterGrid>
        <FormGroup>
          <Label htmlFor="length">Length (notes)</Label>
          <Input
            id="length"
            type="number"
            min="10"
            max="500"
            value={length}
            onChange={(e) => setLength(e.target.value)}
            placeholder="Number of notes to generate"
          />
        </FormGroup>

        <FormGroup>
          <Label htmlFor="temperature">Creativity Level</Label>
          <Select
            id="temperature"
            value={temperature}
            onChange={(e) => setTemperature(e.target.value)}
          >
            <option value="0.1">Conservative (0.1)</option>
            <option value="0.3">Low (0.3)</option>
            <option value="0.5">Medium (0.5)</option>
            <option value="0.8">High (0.8)</option>
            <option value="1.0">Very High (1.0)</option>
            <option value="1.5">Extreme (1.5)</option>
          </Select>
        </FormGroup>
      </ParameterGrid>

      <FormGroup>
        <Label htmlFor="format">Output Format</Label>
        <Select
          id="format"
          value={format}
          onChange={(e) => setFormat(e.target.value)}
        >
          <option value="midi">MIDI (.mid) - For DAWs and music software</option>
          <option value="musicxml">MusicXML (.xml) - For MuseScore and sheet music editors</option>
        </Select>
        <small style={{ color: '#6b7280', fontSize: '0.8rem' }}>
          {format === 'musicxml' 
            ? '🎼 Perfect for viewing and editing in MuseScore! Shows actual sheet music notation.'
            : '🎵 Standard MIDI format for use in digital audio workstations and music software.'
          }
        </small>
      </FormGroup>

      <FormGroup>
        <Label htmlFor="seedNotes">Seed Notes (optional)</Label>
        <Input
          id="seedNotes"
          type="text"
          value={seedNotes}
          onChange={(e) => setSeedNotes(e.target.value)}
          placeholder="e.g., 60, 64, 67 (MIDI note numbers, comma-separated)"
        />
        <small style={{ color: '#6b7280', fontSize: '0.8rem' }}>
          MIDI note numbers (0-127). Leave empty for random generation.
        </small>
      </FormGroup>

      {message && (
        <StatusMessage className={message.type}>
          {message.text}
        </StatusMessage>
      )}

      <Button 
        onClick={handleGenerate} 
        disabled={generating || !modelLoaded}
      >
        {generating ? 'Generating Music...' : 'Generate Music'}
      </Button>

      {generatedFile && (
        <GeneratedMusic>
          <h4>🎵 Generated Music</h4>
          <p>Your music has been generated successfully!</p>
          <DownloadButton 
            href={generatedFile.downloadUrl}
            download={generatedFile.filename}
          >
            📥 Download {format === 'musicxml' ? 'MusicXML File' : 'MIDI File'}
          </DownloadButton>
          {format === 'musicxml' && (
            <p style={{ fontSize: '0.9rem', color: '#6b7280', marginTop: '10px' }}>
              💡 <strong>Tip:</strong> Open this file in MuseScore to view and edit the sheet music notation!
            </p>
          )}
        </GeneratedMusic>
      )}

      {!modelLoaded && (
        <StatusMessage className="info">
          <strong>Note:</strong> Please train the model first before generating music.
        </StatusMessage>
      )}
    </Container>
  );
};

export default MusicGenerator;
