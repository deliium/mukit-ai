from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
import os
from pathlib import Path
from .models.music_composer import MusicComposer
from .schemas import MusicGenerationRequest, MusicGenerationResponse
import tempfile
import shutil
from contextlib import asynccontextmanager

# Initialize the music composer model
composer = MusicComposer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the model on startup"""
    try:
        await composer.load_model()
        print("Music composer model loaded successfully!")
    except Exception as e:
        print(f"Error loading model: {e}")
        # Create a new model if none exists
        await composer.create_and_train_model()
    yield


app = FastAPI(title="Music Composer API", version="1.0.0", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Music Composer API is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "model_loaded": composer.model is not None}

@app.post("/upload-training-data")
async def upload_training_data(files: list[UploadFile] = File(...)):
    """Upload MIDI files for training the model"""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    # Create training data directory if it doesn't exist
    training_dir = Path("training_data")
    training_dir.mkdir(exist_ok=True)
    
    uploaded_files = []
    for file in files:
        if not file.filename.endswith(('.mid', '.midi')):
            raise HTTPException(status_code=400, detail=f"File {file.filename} is not a MIDI file")
        
        file_path = training_dir / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        uploaded_files.append(file.filename)
    
    return {"message": f"Uploaded {len(uploaded_files)} files", "files": uploaded_files}

@app.post("/train-model")
async def train_model():
    """Train the music composer model with uploaded MIDI files"""
    try:
        await composer.create_and_train_model()
        return {"message": "Model training completed successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Training failed: {str(e)}")

@app.delete("/clear-model")
async def clear_model():
    """Clear the trained model and start fresh"""
    try:
        # Clear model files
        for model_path in (
            Path("models/music_composer_model.pkl"),
            Path("models/music_composer_model.keras"),
            Path("models/tokenizer.pkl"),
        ):
            if model_path.exists():
                model_path.unlink()
        
        # Reset composer model
        composer.model = None
        
        return {"message": "Model cleared successfully! Ready for fresh training."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear model: {str(e)}")

@app.post("/generate-music", response_model=MusicGenerationResponse)
async def generate_music(request: MusicGenerationRequest):
    """Generate new music based on the specified parameters"""
    if composer.model is None:
        raise HTTPException(status_code=400, detail="Model not loaded. Please train the model first.")
    
    # Validate format
    if request.format not in ["midi", "musicxml"]:
        raise HTTPException(status_code=400, detail="Format must be 'midi' or 'musicxml'")
    
    try:
        # Generate music
        generated_music = await composer.generate_music(
            length=request.length,
            temperature=request.temperature,
            seed_notes=request.seed_notes
        )
        
        # Save generated music to temporary file based on format
        if request.format == "musicxml":
            suffix = ".xml"
            media_type = "application/vnd.recordare.musicxml+xml"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                generated_music.write('musicxml', fp=tmp_file.name)
                tmp_file_path = tmp_file.name
        else:  # midi
            suffix = ".mid"
            media_type = "audio/midi"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                generated_music.write('midi', fp=tmp_file.name)
                tmp_file_path = tmp_file.name
        
        return MusicGenerationResponse(
            message=f"Music generated successfully in {request.format.upper()} format!",
            filename=os.path.basename(tmp_file_path),
            file_path=tmp_file_path
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Music generation failed: {str(e)}")

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download generated music file (MIDI or MusicXML)"""
    file_path = f"/tmp/{filename}"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Determine media type based on file extension
    if filename.endswith('.xml'):
        media_type = "application/vnd.recordare.musicxml+xml"
    else:  # .mid or .midi
        media_type = "audio/midi"
    
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
