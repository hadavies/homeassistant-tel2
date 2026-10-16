"""Shared fixtures for Tel2 tests."""

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from syrupy.assertion import SnapshotAssertion

from custom_components.tel2.const import DOMAIN
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import TEST_ENTRY_ID


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the custom integration in every test."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return a snapshot assertion using the Home Assistant extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def mock_config_entry_factory() -> Callable[..., MockConfigEntry]:
    """Return a factory that builds Tel2 mock config entries."""

    def _factory(**kwargs: Any) -> MockConfigEntry:
        kwargs.setdefault("domain", DOMAIN)
        kwargs.setdefault("entry_id", TEST_ENTRY_ID)
        kwargs.setdefault("title", "Tel2")
        kwargs.setdefault("data", {})
        kwargs.setdefault("options", {})
        return MockConfigEntry(**kwargs)

    return _factory


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Patch entry setup so flow tests do not load the integration."""
    with patch(
        "custom_components.tel2.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def fake_tel2(aioclient_mock: AiohttpClientMocker) -> FakeTel2Api:
    """Return a fake Tel2 API serving fixtures on the mocked HTTP client."""
    return FakeTel2Api(aioclient_mock)
