#!/usr/bin/env python3
"""
Compatibility check for the Python 3.14 backend runtime.

The backend now uses a lightweight statistical composer instead of TensorFlow,
so GPU acceleration is not required.
"""
import sys


def main():
    print(f"Python version: {sys.version.split()[0]}")

    try:
        import fastapi
        import music21
        import uvicorn
    except ImportError as exc:
        print(f"Import error: {exc}")
        return 1

    print(f"FastAPI version: {fastapi.__version__}")
    print(f"music21 version: {music21.__version__}")
    print(f"uvicorn version: {uvicorn.__version__}")
    print("Backend runtime dependencies are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
