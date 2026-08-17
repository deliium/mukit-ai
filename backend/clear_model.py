#!/usr/bin/env python3
"""
Script to clear previously generated models and training data
"""
import os
import shutil
from pathlib import Path

def clear_models():
    """Clear all model files"""
    models_dir = Path("models")
    if models_dir.exists():
        for file in models_dir.glob("*"):
            if file.is_file():
                file.unlink()
                print(f"✅ Deleted: {file}")
        print("🧹 All model files cleared!")
    else:
        print("📁 No models directory found")

def clear_training_data():
    """Clear all training data"""
    training_dir = Path("training_data")
    if training_dir.exists():
        for file in training_dir.glob("*"):
            if file.is_file():
                file.unlink()
                print(f"✅ Deleted: {file}")
        print("🧹 All training data cleared!")
    else:
        print("📁 No training data directory found")

def clear_all():
    """Clear everything"""
    print("🗑️  Clearing all generated data...")
    clear_models()
    clear_training_data()
    print("✨ All data cleared! Ready for fresh training.")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "models":
            clear_models()
        elif command == "training":
            clear_training_data()
        elif command == "all":
            clear_all()
        else:
            print("Usage: python clear_model.py [models|training|all]")
    else:
        clear_all()
