from __future__ import annotations

import argparse
import json

from app.db import get_session
from app.ingestion.fixtures import load_fixture_event
from app.services.events import ingest_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest recognition event fixture into vigilante-api slice")
    parser.add_argument("--fixture", required=True, help="Path to recognition event fixture JSON")
    args = parser.parse_args()

    event = load_fixture_event(args.fixture)
    with get_session() as session:
        result = ingest_event(session, event)

    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
