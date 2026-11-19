"""The API's contract (lesson 4.19): paths, methods and status codes change only on purpose.

Clients are written against these. A renamed path or a removed status code breaks them even
when every other test passes, so the current contract is compared with a committed snapshot.
After a deliberate change, refresh the snapshot and review its diff in the pull request:

    UPDATE_CONTRACT=1 uv run pytest tests/test_api_contract.py
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI

from atlas.api.app import create_app
from atlas.config import ApiSettings

SNAPSHOT = Path(__file__).parent / "api_contract.json"


def contract(app: FastAPI) -> dict[str, dict[str, list[str]]]:
    """Every documented path, its methods, and each method's documented status codes."""
    paths = app.openapi()["paths"]
    return {
        path: {method: sorted(operation["responses"]) for method, operation in methods.items()}
        for path, methods in sorted(paths.items())
    }


def test_the_api_contract_changes_only_on_purpose(api_settings: ApiSettings) -> None:
    current = contract(create_app(api_settings))
    if os.environ.get("UPDATE_CONTRACT") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")

    expected = json.loads(SNAPSHOT.read_text())

    assert current == expected
