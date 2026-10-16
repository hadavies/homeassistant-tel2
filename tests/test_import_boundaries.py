"""Static checks that HA-free modules never import Home Assistant."""

import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "tel2"
HA_FREE_MODULES = ("const.py", "models.py", "numbering.py")


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize("module", HA_FREE_MODULES)
def test_module_has_no_homeassistant_import(module: str) -> None:
    tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
    offending = {
        name
        for name in _imported_modules(tree)
        if name == "homeassistant" or name.startswith("homeassistant.")
    }
    assert not offending


def test_import_scan_detects_homeassistant() -> None:
    tree = ast.parse(
        "from homeassistant.core import HomeAssistant\nimport homeassistant"
    )
    assert {"homeassistant.core", "homeassistant"} <= _imported_modules(tree)
