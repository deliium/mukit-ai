import os
import pickle
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Optional

import music21


REST_NOTE = 128


class MusicComposer:
    def __init__(self):
        self.model = None
        self.order = 3
        self.model_path = "models/music_composer_model.pkl"
        self.training_data_path = "training_data"

        os.makedirs("models", exist_ok=True)

    async def load_model(self):
        """Load a previously trained statistical model if it exists."""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError("No trained model found")

        with open(self.model_path, "rb") as f:
            self.model = pickle.load(f)

        self.order = self.model.get("order", self.order)
        print("Music composer model loaded successfully!")

    def preprocess_midi_files(self) -> List[int]:
        """Extract note and rest events from MIDI files."""
        notes = []
        training_dir = Path(self.training_data_path)

        if not training_dir.exists():
            return self._create_sample_training_data()

        midi_files = list(training_dir.glob("*.mid")) + list(training_dir.glob("*.midi"))

        if not midi_files:
            return self._create_sample_training_data()

        print(f"Processing {len(midi_files)} MIDI files...")

        for midi_file in midi_files:
            try:
                stream = music21.converter.parse(str(midi_file))

                for element in stream.flatten().notesAndRests:
                    if isinstance(element, music21.note.Note):
                        notes.append(element.pitch.midi)
                    elif isinstance(element, music21.chord.Chord):
                        notes.append(max(pitch.midi for pitch in element.pitches))
                    elif isinstance(element, music21.note.Rest):
                        notes.append(REST_NOTE)
            except Exception as e:
                print(f"Error processing {midi_file}: {e}")

        if len(notes) < 20:
            return self._create_sample_training_data()

        print(f"Extracted {len(notes)} note events from MIDI files")
        return notes

    def _create_sample_training_data(self) -> List[int]:
        """Create sample training data for demonstration."""
        print("Creating sample training data...")
        c_major = [60, 62, 64, 65, 67, 69, 71, 72]
        sample_notes = []

        for _ in range(50):
            for note in c_major:
                sample_notes.append(note)
                if random.random() < 0.1:
                    sample_notes.append(REST_NOTE)

        for _ in range(100):
            sample_notes.append(random.randint(60, 79))

        return sample_notes

    def _build_ngram_model(self, notes: List[int]) -> dict:
        """Build a compact n-gram transition model from note events."""
        transitions = defaultdict(Counter)
        fallback_counts = Counter(notes)

        for index in range(len(notes) - self.order):
            context = tuple(notes[index:index + self.order])
            next_note = notes[index + self.order]
            transitions[context][next_note] += 1

        return {
            "order": self.order,
            "transitions": dict(transitions),
            "fallback_counts": fallback_counts,
            "notes": notes,
        }

    async def create_and_train_model(self):
        """Train and persist the statistical music model."""
        print("Starting model training...")
        notes = self.preprocess_midi_files()

        if len(notes) <= self.order:
            raise ValueError(f"Not enough training data. Need more than {self.order} notes.")

        self.model = self._build_ngram_model(notes)

        with open(self.model_path, "wb") as f:
            pickle.dump(self.model, f)

        print("Model training completed and saved!")
        return self.model

    def _weighted_choice(self, counts: Counter, temperature: float) -> int:
        items = list(counts.items())
        notes = [note for note, _ in items]
        weights = [count for _, count in items]

        if temperature != 1.0:
            exponent = 1.0 / temperature
            weights = [weight ** exponent for weight in weights]

        return random.choices(notes, weights=weights, k=1)[0]

    def _closest_available_note(self, note: int) -> int:
        available_notes = self.model["fallback_counts"].keys()
        return min(available_notes, key=lambda available: abs(available - note))

    async def generate_music(
        self,
        length: int = 100,
        temperature: float = 0.8,
        seed_notes: Optional[List[int]] = None,
    ) -> music21.stream.Stream:
        """Generate new music using the trained statistical model."""
        if self.model is None:
            raise ValueError("Model not trained. Please train the model first.")
        if length <= 0:
            raise ValueError("Length must be positive")
        if temperature <= 0:
            raise ValueError("Temperature must be positive")

        source_notes = self.model["notes"]
        if seed_notes:
            current_sequence = [
                note if note in self.model["fallback_counts"] else self._closest_available_note(note)
                for note in seed_notes
            ]
        else:
            start = random.randint(0, max(0, len(source_notes) - self.order))
            current_sequence = source_notes[start:start + self.order]

        while len(current_sequence) < self.order:
            current_sequence.append(self._weighted_choice(self.model["fallback_counts"], temperature))

        generated_notes = []
        transitions = self.model["transitions"]

        for _ in range(length):
            context = tuple(current_sequence[-self.order:])
            counts = transitions.get(context) or self.model["fallback_counts"]
            next_note = self._weighted_choice(counts, temperature)
            generated_notes.append(next_note)
            current_sequence.append(next_note)

        return self._create_stream(generated_notes, length)

    def _create_stream(self, midi_notes: List[int], length: int) -> music21.stream.Stream:
        stream = music21.stream.Stream()
        stream.metadata = music21.metadata.Metadata()
        stream.metadata.title = f"AI Generated Music (Length: {length})"
        stream.metadata.composer = "AI Music Composer"
        stream.insert(0, music21.meter.TimeSignature("4/4"))
        stream.insert(0, music21.key.Key("C"))

        for index, note_value in enumerate(midi_notes):
            if note_value == REST_NOTE:
                stream.append(music21.note.Rest(quarterLength=0.5))
                continue

            note = music21.note.Note(note_value, quarterLength=0.5)
            note.volume.velocity = 80 if index % 4 == 0 else 60
            stream.append(note)

        return stream
