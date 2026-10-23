"""Load Tel2 test fixtures: synthetic by default, captured only on request."""

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CAPTURED_DIR = FIXTURES_DIR / "captured"
TEST_ENTRY_ID = "tel2_test_entry"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixture(name: str) -> Any:
    """Return the parsed synthetic fixture, ignoring any captured file."""
    return _read(FIXTURES_DIR / name)


def load_captured(name: str) -> Any | None:
    """Return the parsed captured fixture, or None when it has not been captured."""
    path = CAPTURED_DIR / name
    return _read(path) if path.is_file() else None
