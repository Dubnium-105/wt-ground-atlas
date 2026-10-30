#!/usr/bin/env python3
"""Build a standalone, lightweight, prediction-first site from local V2 arrays."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from wt_atlas.forecast.build import main

if __name__ == "__main__":
    main()
