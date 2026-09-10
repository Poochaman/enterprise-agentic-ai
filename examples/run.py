"""Convenient launcher: python examples/run.py (from any working directory)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from examples.server import main

if __name__ == "__main__":
    main()
