# 🎵 AI Music Composer

A full-stack application that composes music from MIDI training data. The system analyzes MIDI files and generates new musical compositions using a lightweight statistical note-transition model compatible with Python 3.14.

## 🚀 Features

- **AI-Powered Music Generation**: Learns note-transition patterns and generates new compositions
- **MIDI File Processing**: Upload your own MIDI files for training or use built-in sample data
- **Interactive Web Interface**: Beautiful React frontend with drag-and-drop file upload
- **Customizable Parameters**: Control music length, creativity level, and starting notes
- **Real-time Generation**: Generate and download MIDI files instantly

## 🏗️ Architecture

- **Backend**: FastAPI with Python
- **Frontend**: React with styled-components
- **Music Model**: Python 3.14-compatible statistical note-transition model
- **Music Processing**: music21 library for MIDI handling

## 📋 Prerequisites

- Python 3.14+
- Node.js 16+
- npm or yarn

## 🛠️ Installation

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Start the FastAPI server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888
```

The API will be available at `http://localhost:8888`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the React development server:
```bash
npm start
```

The frontend will be available at `http://localhost:3000`

## 🎼 Usage

### 1. Training the Model

1. **Upload Training Data**: 
   - Drag and drop MIDI files (.mid or .midi) into the upload area
   - Or click to select files from your computer
   - The system will use sample data if no files are uploaded

2. **Train the Model**:
   - Click "Train Model" to start training
   - Training typically takes 5-10 minutes depending on your hardware
   - The model will be automatically saved after training

### 2. Generating Music

1. **Set Parameters**:
   - **Length**: Number of notes to generate (10-500)
   - **Creativity Level**: Controls randomness (0.1 = conservative, 1.5 = very creative)
   - **Seed Notes**: Optional starting notes (MIDI numbers 0-127, comma-separated)

2. **Generate**: Click "Generate Music" to create new compositions

3. **Download**: Download the generated MIDI file to use in your music software

## 🎹 MIDI Note Reference

Common MIDI note numbers for reference:
- C4 (Middle C): 60
- D4: 62
- E4: 64
- F4: 65
- G4: 67
- A4: 69
- B4: 71
- C5: 72

## 🔧 API Endpoints

- `GET /` - API status
- `GET /health` - Health check
- `POST /upload-training-data` - Upload MIDI files for training
- `POST /train-model` - Train the music composer model
- `POST /generate-music` - Generate new music
- `GET /download/{filename}` - Download generated MIDI files

## 📁 Project Structure

```
mukit-ai/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application
│   │   ├── schemas.py           # Pydantic models
│   │   └── models/
│   │       ├── __init__.py
│   │       └── music_composer.py # Music model implementation
│   ├── requirements.txt
│   ├── training_data/           # Uploaded MIDI files
│   └── models/                  # Saved model files
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Header.js
│   │   │   ├── MusicGenerator.js
│   │   │   └── TrainingDataUploader.js
│   │   ├── App.js
│   │   ├── index.js
│   │   └── index.css
│   └── package.json
└── README.md
```

## 🧠 Model Architecture

The music composer uses a persisted statistical note-transition model:

- **Input**: MIDI notes and rests extracted from training files
- **Training**: N-gram transition counts over note sequences
- **Generation**: Weighted random sampling with temperature control
- **Output**: MIDI or MusicXML stream generated through music21

## 🎯 Training Process

1. **Data Preprocessing**: MIDI files are parsed and converted to note sequences
2. **Sequence Creation**: Sliding window approach creates input-target pairs
3. **Model Training**: The composer learns weighted next-note transitions
4. **Generation**: The trained model generates new sequences note by note

## 🔍 Troubleshooting

### Common Issues

1. **Model not loading**: Ensure the model has been trained first
2. **Training fails**: Check that MIDI files are valid and not corrupted
3. **Generation errors**: Verify model is loaded and parameters are valid
4. **CORS errors**: Ensure backend is running on port 8888

### Performance Tips

- Use GPU acceleration for faster training (if available)
- Limit training data size for initial testing
- Adjust sequence length based on your music style
- Use appropriate creativity levels for your use case

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- [music21](https://web.mit.edu/music21/) for MIDI processing
- [FastAPI](https://fastapi.tiangolo.com/) for the backend API
- [React](https://reactjs.org/) for the frontend interface

## 🔮 Future Enhancements

- Support for multiple instruments
- Real-time audio playback
- Advanced music theory constraints
- Style transfer between different musical genres
- Collaborative composition features
- Mobile app version

---

**Happy Composing! 🎵**
