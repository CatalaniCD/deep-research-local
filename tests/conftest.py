import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TAVILY_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
# Keep model construction offline + valid for every role during tests.
os.environ.setdefault("MODEL", "gpt-4.1-mini")
# Ensure OpenRouter path stays inert in tests unless explicitly set.
os.environ.pop("OPENROUTER_API_KEY", None)
