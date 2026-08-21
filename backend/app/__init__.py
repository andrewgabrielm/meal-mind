"""MealMind backend package.

Puts backend/ on sys.path so `scripts.ingredient_tables` (the tuning tables)
is importable from services regardless of the working directory."""
import sys
from pathlib import Path

_backend = str(Path(__file__).resolve().parents[1])
if _backend not in sys.path:
    sys.path.insert(0, _backend)
