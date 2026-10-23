"""Every translation key the integration code references exists in en.json."""

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import homeassistant.components.homeassistant as ha_core_component
import pytest

from custom_components.tel2 import PLATFORMS

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "tel2"
TRANSLATIONS = PACKAGE_DIR / "translations" / "en.json"
HA_TRANSLATIONS = Path(ha_core_component.__file__).parent / "translations" / "en.json"
PLATFORM_MODULES = frozenset(platform.value for platform in PLATFORMS)
EXCEPTION_TYPES = frozenset(
    {
        "HomeAssistantError",
        "ServiceValidationError",
        "UpdateFailed",
        "ConfigEntryAuthFailed",
        "ConfigEntryError",
        "ConfigEntryNotReady",
    }
)
EXTERNAL_DOMAINS = {"HOMEASSISTANT_DOMAIN": "homeassistant"}
IMPLICIT_ABORTS = {
    "_abort_if_unique_id_configured": ("error", "already_configured"),
    "_abort_if_unique_id_mismatch": ("reason", "unique_id_mismatch"),
    "_async_abort_entries_match": (None, "already_configured"),
}


@dataclass(frozen=True, order=True)
class KeyRef:
    """A translation key used in the code and the en.json path it must exist at."""

    path: tuple[str, ...]
    where: str
    external: bool = False


@dataclass
class Scan:
    """Translation key references found in a package, and anything unresolvable."""

    refs: list[KeyRef] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _keyword_strings(tree: ast.Module, keyword: str) -> list[str]:
    return [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == keyword
        and isinstance(kw.value, ast.Constant)
        and isinstance(kw.value.value, str)
    ]


def _returned_strings_of(tree: ast.Module, keyword: str) -> list[str]:
    functions = {
        kw.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == keyword and isinstance(kw.value, ast.Name)
    }
    return [
        ret.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in functions
        for ret in ast.walk(node)
        if isinstance(ret, ast.Return)
        and isinstance(ret.value, ast.Constant)
        and isinstance(ret.value.value, str)
    ]


# A key computed at runtime must be listed here with where its values come from.
DYNAMIC_KEYS: dict[tuple[str, str], Callable[[ast.Module], list[str]]] = {
    ("button.py", "self.entity_description.error_key"): lambda tree: _keyword_strings(
        tree, "error_key"
    ),
    ("switch.py", "refusal"): lambda tree: _returned_strings_of(
        tree, "precondition_fn"
    ),
}


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign | ast.Assign) and (
            isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _Collector(ast.NodeVisitor):
    def __init__(
        self, module: str, tree: ast.Module, constants: dict[str, str], scan: Scan
    ) -> None:
        self.module = module
        self.tree = tree
        self.constants = constants
        self.scan = scan
        self.classes: list[str] = []
        self.functions: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "errors"
            ):
                self._add(node, node.value, lambda key: (self._flow, "error", key))
            if _call_name(target) == "_attr_translation_key":
                self._add_entity(node, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if "translation_key" in kwargs:
            self._translation_key_call(node, name, kwargs)
        if name in ("async_abort", "async_update_reload_and_abort"):
            self._abort_call(node, name, kwargs)
        elif name == "AbortFlow" and node.args:
            self._add(node, node.args[0], self._abort_path)
        elif name in IMPLICIT_ABORTS:
            keyword, default = IMPLICIT_ABORTS[name]
            reason = kwargs.get(keyword) if keyword else None
            self._add(node, reason or ast.Constant(default), self._abort_path)
        self.generic_visit(node)

    def _translation_key_call(
        self, node: ast.Call, name: str | None, kwargs: dict[str, ast.expr]
    ) -> None:
        key = kwargs["translation_key"]
        external = self._external(node, kwargs.get("translation_domain"))
        if external is None:
            return
        if name in EXCEPTION_TYPES:
            self._add(node, key, lambda k: ("exceptions", k, "message"), external)
        elif name == "async_create_issue":
            self._add(node, key, lambda k: ("issues", k, "title"), external)
        elif name is not None and name.endswith("Description"):
            self._add_entity(node, key)
        else:
            self.scan.problems.append(
                f"{self._where(node)}: translation_key passed to {name}, "
                "which this test does not know how to check"
            )

    def _abort_call(
        self, node: ast.Call, name: str, kwargs: dict[str, ast.expr]
    ) -> None:
        external = self._external(node, kwargs.get("translation_domain"))
        if external is None:
            return
        reason = kwargs.get("reason")
        if reason is None and name == "async_update_reload_and_abort":
            in_reconfigure = any("reconfigure" in f for f in self.functions)
            default = (
                "reconfigure_successful" if in_reconfigure else "reauth_successful"
            )
            reason = ast.Constant(default)
        if reason is None:
            self.scan.problems.append(f"{self._where(node)}: abort without a reason")
            return
        self._add(node, reason, self._abort_path, external)

    def _external(self, node: ast.Call, domain: ast.expr | None) -> bool | None:
        if domain is None or (isinstance(domain, ast.Name) and domain.id == "DOMAIN"):
            return False
        if isinstance(domain, ast.Name) and domain.id in EXTERNAL_DOMAINS:
            return True
        self.scan.problems.append(
            f"{self._where(node)}: unknown translation domain {ast.unparse(domain)}"
        )
        return None

    @property
    def _flow(self) -> str:
        return "options" if any("OptionsFlow" in c for c in self.classes) else "config"

    def _abort_path(self, key: str) -> tuple[str, ...]:
        return (self._flow, "abort", key)

    def _add_entity(self, node: ast.AST, key: ast.expr) -> None:
        platform = self.module.removesuffix(".py")
        if platform not in PLATFORM_MODULES:
            self.scan.problems.append(
                f"{self._where(node)}: entity translation key outside a platform module"
            )
            return
        self._add(node, key, lambda k: ("entity", platform, k))

    def _add(
        self,
        node: ast.AST,
        key: ast.expr,
        path: Callable[[str], tuple[str, ...]],
        external: bool = False,
    ) -> None:
        values = self._resolve(key)
        if values is None:
            self.scan.problems.append(
                f"{self._where(node)}: can't resolve translation key "
                f"{ast.unparse(key)}; add it to DYNAMIC_KEYS"
            )
            return
        if not values:
            self.scan.problems.append(
                f"{self._where(node)}: {ast.unparse(key)} resolved to no keys"
            )
        self.scan.refs.extend(
            KeyRef(path(value), self._where(node), external) for value in values
        )

    def _resolve(self, key: ast.expr) -> list[str] | None:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return [key.value]
        if isinstance(key, ast.Name) and key.id in self.constants:
            return [self.constants[key.id]]
        resolver = DYNAMIC_KEYS.get((self.module, ast.unparse(key)))
        return resolver(self.tree) if resolver else None

    def _where(self, node: ast.AST) -> str:
        return f"{self.module}:{getattr(node, 'lineno', '?')}"


def scan_package(package: Path) -> Scan:
    """Collect every translation key reference in the package's modules."""
    trees = {
        path.name: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(package.glob("*.py"))
    }
    shared: dict[str, str] = {}
    for tree in trees.values():
        shared |= _module_constants(tree)
    scan = Scan()
    for module, tree in trees.items():
        constants = shared | _module_constants(tree)
        _Collector(module, tree, constants, scan).visit(tree)
    return scan


def _lookup(strings: Any, path: tuple[str, ...]) -> Any:
    node = strings
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def missing_keys(scan: Scan, ours: Any, core: Any) -> list[KeyRef]:
    """Return the references whose key has no non-empty translation."""
    return [
        ref
        for ref in scan.refs
        if not _lookup(core if ref.external else ours, ref.path)
    ]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SCAN = scan_package(PACKAGE_DIR)


def _ref_id(ref: KeyRef) -> str:
    return f"{'.'.join(ref.path)}@{ref.where}"


def _sections(refs: list[KeyRef]) -> Iterator[tuple[str, ...]]:
    for ref in refs:
        yield ref.path[:2] if ref.path[0] in ("config", "entity") else ref.path[:1]


def test_every_reference_is_resolved() -> None:
    assert SCAN.problems == []


def test_scan_reaches_every_section() -> None:
    assert set(_sections(SCAN.refs)) >= {
        ("exceptions",),
        ("issues",),
        ("config", "error"),
        ("config", "abort"),
        *(("entity", platform) for platform in PLATFORM_MODULES),
    }


@pytest.mark.parametrize(
    "path",
    [
        ("exceptions", "resync_failed", "message"),
        ("exceptions", "reboot_failed", "message"),
        ("exceptions", "forward_destination_required", "message"),
        ("issues", "call_events_no_public_url", "title"),
        ("config", "abort", "already_configured"),
        ("config", "abort", "wrong_account"),
        ("config", "abort", "reauth_successful"),
    ],
)
def test_indirect_keys_are_found(path: tuple[str, ...]) -> None:
    assert path in {ref.path for ref in SCAN.refs}


@pytest.mark.parametrize("ref", sorted(set(SCAN.refs)), ids=_ref_id)
def test_referenced_key_is_translated(ref: KeyRef) -> None:
    source = HA_TRANSLATIONS if ref.external else TRANSLATIONS
    assert _lookup(_load(source), ref.path), f"{_ref_id(ref)} missing in {source}"


SCRATCH_SENSOR = """
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN

GHOST = SensorEntityDescription(key="ghost", translation_key="ghost")
BALANCE = SensorEntityDescription(key="balance", translation_key="balance")


def fail(key):
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_such_error")


def update(key):
    raise UpdateFailed(translation_domain=DOMAIN, translation_key=key)


class Flow:
    async def async_step_user(self, user_input=None):
        errors = {}
        errors["base"] = "not_translated"
        errors["base"] = "invalid_auth"
        self._abort_if_unique_id_configured()
        return self.async_abort(reason="vanished")


class Thing:
    _attr_translation_key = "phantom"
"""


def test_scanner_reports_missing_and_unresolved_keys(tmp_path: Path) -> None:
    (tmp_path / "sensor.py").write_text(SCRATCH_SENSOR, encoding="utf-8")

    scan = scan_package(tmp_path)

    assert {ref.path for ref in missing_keys(scan, _load(TRANSLATIONS), {})} == {
        ("entity", "sensor", "ghost"),
        ("entity", "sensor", "phantom"),
        ("exceptions", "no_such_error", "message"),
        ("config", "error", "not_translated"),
        ("config", "abort", "vanished"),
    }
    assert [problem.partition(": ")[2] for problem in scan.problems] == [
        "can't resolve translation key key; add it to DYNAMIC_KEYS"
    ]
