from __future__ import annotations

import json
from pathlib import Path

from app.services.events import RecognitionEventEnvelope


def load_fixture_event(path: str) -> RecognitionEventEnvelope:
    fixture_path = Path(path)
    if not fixture_path.is_absolute():
        fixture_path = Path.cwd() / fixture_path
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    return RecognitionEventEnvelope.model_validate(data)
