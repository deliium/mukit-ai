import React, { useState, useCallback } from 'react';
import styled from 'styled-components';
import { useDropzone } from 'react-dropzone';
import axios from 'axios';

const Container = styled.div`
  h2 {
    color: #333;
    margin-bottom: 20px;
    font-size: 1.5rem;
    font-weight: 600;
  }
`;

const DropzoneContainer = styled.div`
  border: 2px dashed ${props => props.isDragActive ? '#667eea' : '#ddd'};
  border-radius: 12px;
  padding: 40px 20px;
  text-align: center;
  background: ${props => props.isDragActive ? '#f8f9ff' : '#fafafa'};
  cursor: pointer;
  transition: all 0.3s ease;
  margin-bottom: 20px;

  &:hover {
    border-color: #667eea;
    background: #f8f9ff;
  }
`;

const DropzoneText = styled.div`
  color: #666;
  font-size: 1rem;
  
  .highlight {
    color: #667eea;
    font-weight: 500;
  }
`;

const FileList = styled.div`
  margin: 20px 0;
`;

const FileItem = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px;
  background: #f5f5f5;
  border-radius: 8px;
  margin-bottom: 8px;
  font-size: 0.9rem;
`;

const Button = styled.button`
  background: ${props => props.variant === 'primary' ? '#667eea' : '#6b7280'};
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
    background: ${props => props.variant === 'primary' ? '#5a67d8' : '#4b5563'};
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

const TrainingDataUploader = ({ onModelTrained }) => {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [training, setTraining] = useState(false);
  const [message, setMessage] = useState('');

  const onDrop = useCallback((acceptedFiles) => {
    const midiFiles = acceptedFiles.filter(file => 
      file.name.toLowerCase().endsWith('.mid') || 
      file.name.toLowerCase().endsWith('.midi')
    );
    
    if (midiFiles.length !== acceptedFiles.length) {
      setMessage({
        type: 'error',
        text: 'Please only upload MIDI files (.mid or .midi)'
      });
    } else {
      setFiles(prev => [...prev, ...midiFiles]);
      setMessage({
        type: 'success',
        text: `Added ${midiFiles.length} MIDI file(s)`
      });
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'audio/midi': ['.mid', '.midi']
    }
  });

  const removeFile = (index) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  const uploadFiles = async () => {
    if (files.length === 0) {
      setMessage({
        type: 'error',
        text: 'Please select MIDI files to upload'
      });
      return;
    }

    setUploading(true);
    setMessage('');

    try {
      const formData = new FormData();
      files.forEach(file => {
        formData.append('files', file);
      });

      await axios.post('/upload-training-data', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      setMessage({
        type: 'success',
        text: `Successfully uploaded ${files.length} file(s)`
      });
      setFiles([]);
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Upload failed: ${error.response?.data?.detail || error.message}`
      });
    } finally {
      setUploading(false);
    }
  };

  const trainModel = async () => {
    setTraining(true);
    setMessage('');

    try {
      await axios.post('/train-model');
      setMessage({
        type: 'success',
        text: 'Model training completed successfully!'
      });
      onModelTrained();
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Training failed: ${error.response?.data?.detail || error.message}`
      });
    } finally {
      setTraining(false);
    }
  };

  const clearModel = async () => {
    if (!window.confirm('Are you sure you want to clear the trained model? This action cannot be undone.')) {
      return;
    }

    try {
      await axios.delete('/clear-model');
      setMessage({
        type: 'success',
        text: 'Model cleared successfully! Ready for fresh training.'
      });
      // Reset model loaded state
      onModelTrained(false);
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Failed to clear model: ${error.response?.data?.detail || error.message}`
      });
    }
  };

  return (
    <Container>
      <h2>🎼 Training Data</h2>
      
      <DropzoneContainer {...getRootProps()} isDragActive={isDragActive}>
        <input {...getInputProps()} />
        <DropzoneText>
          {isDragActive ? (
            <span className="highlight">Drop MIDI files here...</span>
          ) : (
            <>
              Drag & drop MIDI files here, or <span className="highlight">click to select</span>
              <br />
              <small>Supports .mid and .midi files</small>
            </>
          )}
        </DropzoneText>
      </DropzoneContainer>

      {files.length > 0 && (
        <FileList>
          <h4>Selected Files:</h4>
          {files.map((file, index) => (
            <FileItem key={index}>
              <span>{file.name}</span>
              <button onClick={() => removeFile(index)} style={{
                background: 'none',
                border: 'none',
                color: '#ef4444',
                cursor: 'pointer',
                fontSize: '1.2rem'
              }}>
                ×
              </button>
            </FileItem>
          ))}
        </FileList>
      )}

      {message && (
        <StatusMessage className={message.type}>
          {message.text}
        </StatusMessage>
      )}

      <Button 
        onClick={uploadFiles} 
        disabled={uploading || files.length === 0}
        variant="primary"
      >
        {uploading ? 'Uploading...' : 'Upload Training Data'}
      </Button>

      <Button 
        onClick={trainModel} 
        disabled={training}
        variant="secondary"
      >
        {training ? 'Training Model...' : 'Train Model'}
      </Button>

      <Button 
        onClick={clearModel} 
        disabled={training}
        variant="secondary"
        style={{ background: '#ef4444', marginTop: '10px' }}
      >
        🗑️ Clear Model
      </Button>

      <StatusMessage className="info">
        <strong>Note:</strong> The model will use sample data if no MIDI files are uploaded. 
        For better results, upload your own MIDI training data.
      </StatusMessage>
    </Container>
  );
};

export default TrainingDataUploader;
