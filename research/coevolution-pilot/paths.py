"""Separate the published evidence from the writable directory for a new run."""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('PILOT_ROOT', CODE_ROOT)).resolve()
