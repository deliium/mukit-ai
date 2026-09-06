FROM python:3.14-slim

WORKDIR /app

# Install system dependencies including FluidSynth CLI and a legally
# redistributable General MIDI SoundFont (Debian fluid-soundfont-gm /
# FluidR3_GM). Used only for server-side WAV export; see docs for license.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libsndfile1 \
    fluidsynth \
    fluid-soundfont-gm \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories (models, training data, and durable project DB mount)
RUN mkdir -p models training_data /data

# Expose port
EXPOSE 8888

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888"]
