"""Tests for the Tel2 call event manager lifecycle."""

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from enum import Enum
import logging
import subprocess
import sys
from types import ModuleType
from typing import Any, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from hass_nabucasa import CloudError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.tel2.api import Tel2Client
from custom_components.tel2.const import (
    CONF_CLOUDHOOK_URL,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_TEL2_WEBHOOK_ID,
    CONF_TOKEN,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
)
from custom_components.tel2.coordinator import Tel2AccountCoordinator
from custom_components.tel2.runtime import Tel2RuntimeData
from custom_components.tel2.webhook import (
    ADD_RETRY_WINDOW,
    CallEventStatus,
    Tel2CallEventManager,
    async_delete_stale_registration,
    async_remove_registration,
    signal_call_event_availability,
)
from tests.fake_tel2 import NO_DATA, FakeTel2Api, RawResponse
from tests.fixture_loader import TEST_ENTRY_ID
from tests.helpers import DEFAULT_ENTRY_DATA

WEBHOOK_ID = "example-webhook-id"
WEBHOOK_SECRET = "0123456789abcdef" * 4
EXTERNAL_URL = "https://example.invalid"
REGISTERED_URL = f"{EXTERNAL_URL}/api/webhook/{WEBHOOK_ID}"
CLOUDHOOK_URL = "https://hooks.example.invalid/cloudhook/example"
REGISTRATION_NAME = f"homeassistant-tel2-{TEST_ENTRY_ID}"
ISSUE_ID = f"call_events_no_public_url_{TEST_ENTRY_ID}"
CLOUD = "homeassistant.components.cloud"


class CloudNotAvailable(HomeAssistantError):  # noqa: N818
    """Stand-in for the cloud integration's CloudNotAvailable."""


class CloudNotConnected(CloudNotAvailable):
    """Stand-in for the cloud integration's CloudNotConnected."""


class CloudConnectionState(Enum):
    """Stand-in for the cloud integration's CloudConnectionState."""

    CLOUD_CONNECTED = "cloud_connected"
    CLOUD_DISCONNECTED = "cloud_disconnected"


@dataclass(slots=True)
class CloudMocks:
    """Fake Home Assistant Cloud module functions."""

    active: MagicMock
    get_or_create: AsyncMock
    delete: AsyncMock
    listen: MagicMock
    unsubscribe: MagicMock

    def connection_callback(self) -> Callable[[CloudConnectionState], Any]:
        """Return the callback the manager subscribed with."""
        [call] = self.listen.call_args_list
        return call.args[1]  # type: ignore[no-any-return]


@pytest.fixture
def cloud(hass: HomeAssistant) -> Generator[CloudMocks]:
    # The real cloud module needs dependencies the test venv lacks, so a fake
    # module stands in and the cloud integration is marked as loaded.
    unsubscribe = MagicMock()
    mocks = CloudMocks(
        active=MagicMock(return_value=False),
        get_or_create=AsyncMock(return_value=CLOUDHOOK_URL),
        delete=AsyncMock(),
        listen=MagicMock(return_value=unsubscribe),
        unsubscribe=unsubscribe,
    )
    module = ModuleType(CLOUD)
    module.__dict__.update(
        CloudNotAvailable=CloudNotAvailable,
        CloudNotConnected=CloudNotConnected,
        CloudConnectionState=CloudConnectionState,
        async_active_subscription=mocks.active,
        async_get_or_create_cloudhook=mocks.get_or_create,
        async_delete_cloudhook=mocks.delete,
        async_listen_connection_change=mocks.listen,
    )
    hass.config.components.add("cloud")
    with patch.dict(sys.modules, {CLOUD: module}):
        yield mocks


def registrations(*rows: tuple[str, str, str]) -> dict[str, Any]:
    """Return a Get-Webhooks body with (id, name, url) rows."""
    return {
        "Result": "1",
        "Status": "Success",
        "Message": "Method Executed Successfully.",
        "Data": [
            {
                "id": tel2_id,
                "name": name,
                "url": url,
                "secretheader": "X-Tel2-Signature",
                "secrettoken": "FakeSecretTokenA",
            }
            for tel2_id, name, url in rows
        ],
    }


def ours(url: str, tel2_id: str = "7") -> dict[str, Any]:
    """Return a Get-Webhooks body holding our registration."""
    return registrations((tel2_id, REGISTRATION_NAME, url))


def add_entry(
    hass: HomeAssistant,
    factory: Callable[..., MockConfigEntry],
    **data: Any,
) -> MockConfigEntry:
    entry = factory(data={**DEFAULT_ENTRY_DATA, **data})
    entry.add_to_hass(hass)
    return entry


def make_client(hass: HomeAssistant, entry: MockConfigEntry) -> Tel2Client:
    return Tel2Client(
        async_get_clientsession(hass),
        entry.data[CONF_LOGIN],
        entry.data[CONF_PASSWORD],
        token=entry.data[CONF_TOKEN],
    )


async def start_manager(
    hass: HomeAssistant, entry: MockConfigEntry
) -> Tel2CallEventManager:
    client = make_client(hass, entry)
    account = Tel2AccountCoordinator(hass, entry, client)
    manager = Tel2CallEventManager(hass, entry, client, account)
    await manager.async_start()
    await hass.async_block_till_done()
    return manager


def no_url_issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)


def add_params(fake_tel2: FakeTel2Api) -> list[Mapping[str, Any]]:
    return [request.params for request in fake_tel2.requests_for("Add-Webhook")]


async def test_cloud_path_registers_the_cloudhook(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    cloud.active.return_value = True
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(CLOUDHOOK_URL))
    entry = add_entry(hass, mock_config_entry_factory)

    manager = await start_manager(hass, entry)

    webhook_id = entry.data[CONF_WEBHOOK_ID]
    cloud.get_or_create.assert_awaited_once_with(hass, webhook_id)
    assert entry.data[CONF_CLOUDHOOK_URL] == CLOUDHOOK_URL
    [params] = add_params(fake_tel2)
    secret = entry.data[CONF_WEBHOOK_SECRET]
    assert params["name"] == REGISTRATION_NAME
    assert params["url"] == CLOUDHOOK_URL
    assert params["secretheader"] == "X-Tel2-Signature"
    assert params["secrettoken"] == secret
    assert len(secret) == 64
    assert secret.isascii()
    assert secret.isalnum()
    assert "editid" not in params
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"
    assert manager.available is True
    assert manager.status == CallEventStatus("cloud", True, None)
    assert no_url_issue(hass) is None


async def test_persisted_cloudhook_is_reused(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    cloud.active.return_value = True
    fake_tel2.respond("Get-Webhooks", ours(CLOUDHOOK_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
        cloudhook_url=CLOUDHOOK_URL,
    )

    manager = await start_manager(hass, entry)

    cloud.get_or_create.assert_not_awaited()
    assert fake_tel2.call_count("Add-Webhook") == 0
    assert entry.data[CONF_WEBHOOK_ID] == WEBHOOK_ID
    assert entry.data[CONF_WEBHOOK_SECRET] == WEBHOOK_SECRET
    assert manager.status == CallEventStatus("cloud", True, None)


async def test_external_https_url_is_registered(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    cloud.get_or_create.assert_not_awaited()
    [params] = add_params(fake_tel2)
    assert params["url"] == REGISTERED_URL
    assert params["secrettoken"] == WEBHOOK_SECRET
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"
    assert CONF_CLOUDHOOK_URL not in entry.data
    assert manager.available is True
    assert manager.status == CallEventStatus("external", True, None)


async def test_cloud_not_connected_falls_back_to_external(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    cloud.active.return_value = True
    cloud.get_or_create.side_effect = CloudNotConnected
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    assert CONF_CLOUDHOOK_URL not in entry.data
    assert [p["url"] for p in add_params(fake_tel2)] == [REGISTERED_URL]
    assert manager.status == CallEventStatus("external", True, None)


@pytest.mark.parametrize("external_url", [None, "http://example.invalid"])
async def test_no_public_url_raises_repair_issue(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    external_url: str | None,
) -> None:
    hass.config.external_url = external_url
    entry = add_entry(hass, mock_config_entry_factory)

    manager = await start_manager(hass, entry)

    issue = no_url_issue(hass)
    assert issue is not None
    assert issue.translation_key == "call_events_no_public_url"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert manager.available is False
    assert manager.status == CallEventStatus("none", False, None)
    assert fake_tel2.call_count("Add-Webhook") == 0


async def test_cloud_connection_clears_the_issue(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    cloud.active.return_value = True
    cloud.get_or_create.side_effect = [CloudNotConnected, CLOUDHOOK_URL]
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(CLOUDHOOK_URL))
    entry = add_entry(hass, mock_config_entry_factory)
    manager = await start_manager(hass, entry)
    assert no_url_issue(hass) is not None

    await cloud.connection_callback()(CloudConnectionState.CLOUD_CONNECTED)
    await hass.async_block_till_done()

    assert no_url_issue(hass) is None
    assert manager.available is True
    assert [p["url"] for p in add_params(fake_tel2)] == [CLOUDHOOK_URL]
    assert manager.status == CallEventStatus("cloud", True, None)


async def test_reconcile_after_external_url_set_clears_the_issue(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)
    assert manager.available is False

    hass.config.external_url = EXTERNAL_URL
    await manager.async_reconcile()

    assert no_url_issue(hass) is None
    assert manager.available is True
    assert manager.status == CallEventStatus("external", True, None)


async def test_reconcile_leaves_a_matching_registration(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)
    await manager.async_reconcile()

    assert fake_tel2.call_count("Add-Webhook") == 0
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "1"
    assert manager.status == CallEventStatus("external", True, None)


async def test_reconcile_re_adds_a_missing_registration(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)

    fake_tel2.respond(
        "Get-Webhooks",
        registrations(("2", "Other app", "https://example.invalid/other-app/calls")),
        ours(REGISTERED_URL, "9"),
    )
    await manager.async_reconcile()

    [params] = add_params(fake_tel2)
    assert params["url"] == REGISTERED_URL
    assert "editid" not in params
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "9"
    assert manager.status.registered_on_tel2 is True


async def test_reconcile_updates_a_changed_url(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = "https://new.example.invalid"
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    [params] = add_params(fake_tel2)
    assert params["url"] == f"https://new.example.invalid/api/webhook/{WEBHOOK_ID}"
    assert params["editid"] == "1"
    assert fake_tel2.call_count("Get-Webhooks") == 1
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "1"
    assert manager.status == CallEventStatus("external", True, None)


@pytest.mark.parametrize(
    ("action", "response"),
    [
        ("Get-Webhooks", aiohttp.ClientError()),
        ("Get-Webhooks", "error_generic.json"),
        ("Add-Webhook", "add_webhook_rejected.json"),
    ],
)
async def test_tel2_error_during_start_is_logged_and_retried(
    *,
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
    action: str,
    response: Any,
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond(action, response)
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    assert manager.available is True
    assert manager.status == CallEventStatus("external", False, None)
    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    assert any(r.levelno == logging.WARNING for r in caplog.records)

    fake_tel2.respond("Get-Webhooks", NO_DATA, ours(REGISTERED_URL))
    fake_tel2.respond("Add-Webhook", "add_webhook.json")
    await manager.async_reconcile()

    assert manager.status == CallEventStatus("external", True, None)
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"


async def test_start_registers_the_ha_webhook_and_unload_releases_it(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    hass_ws_client: WebSocketGenerator,
) -> None:
    assert await async_setup_component(hass, "webhook", {})
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    client = await hass_ws_client(hass)

    manager = await start_manager(hass, entry)
    await client.send_json_auto_id({"type": "webhook/list"})
    registered = (await client.receive_json())["result"]
    assert [
        (hook["webhook_id"], hook["domain"], hook["local_only"]) for hook in registered
    ] == [(WEBHOOK_ID, DOMAIN, False)]
    assert registered[0]["allowed_methods"] == ["POST"]
    cloud.listen.assert_called_once()

    requests_before = len(fake_tel2.requests)
    manager.async_unload()
    await hass.async_block_till_done()

    await client.send_json_auto_id({"type": "webhook/list"})
    assert (await client.receive_json())["result"] == []
    cloud.unsubscribe.assert_called_once_with()
    assert len(fake_tel2.requests) == requests_before
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "1"


async def test_unload_clears_the_no_url_issue(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(hass, mock_config_entry_factory)
    manager = await start_manager(hass, entry)
    assert no_url_issue(hass) is not None

    manager.async_unload()

    assert no_url_issue(hass) is None


async def test_stale_registration_is_deleted_and_cleared(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(
        hass, mock_config_entry_factory, webhook_id=WEBHOOK_ID, tel2_webhook_id="1"
    )

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    [request] = fake_tel2.requests_for("Delete-Webhook")
    assert request.params == {"id": "1"}
    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    assert entry.data[CONF_WEBHOOK_ID] == WEBHOOK_ID


async def test_stale_registration_is_kept_when_tel2_fails(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Delete-Webhook", "delete_webhook_rejected.json")
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(hass, mock_config_entry_factory, tel2_webhook_id="1")

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "1"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_stale_registration_is_kept_when_the_check_after_a_refusal_fails(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Delete-Webhook", "delete_webhook_rejected.json")
    fake_tel2.respond("Get-Webhooks", aiohttp.ClientError())
    entry = add_entry(hass, mock_config_entry_factory, tel2_webhook_id="5")

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "5"
    assert fake_tel2.call_count("Get-Webhooks") == 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_stale_registration_is_kept_when_tel2_is_down(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Delete-Webhook", aiohttp.ClientError())
    entry = add_entry(hass, mock_config_entry_factory, tel2_webhook_id="1")

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "1"
    assert fake_tel2.call_count("Get-Webhooks") == 0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_stale_registration_already_gone_is_cleared_quietly(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Delete-Webhook", "delete_webhook_rejected.json")
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(hass, mock_config_entry_factory, tel2_webhook_id="5")

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_stale_registration_without_an_id_calls_nothing(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(hass, mock_config_entry_factory)

    await async_delete_stale_registration(hass, entry, make_client(hass, entry))

    assert fake_tel2.requests == []


async def test_remove_registration_deletes_on_tel2_and_the_cloudhook(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        tel2_webhook_id="1",
        cloudhook_url=CLOUDHOOK_URL,
    )

    await async_remove_registration(hass, entry, make_client(hass, entry))

    [request] = fake_tel2.requests_for("Delete-Webhook")
    assert request.params == {"id": "1"}
    cloud.delete.assert_awaited_once_with(hass, WEBHOOK_ID)


async def test_remove_registration_without_a_stored_id_deletes_ours_by_name(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    fake_tel2.respond(
        "Get-Webhooks",
        registrations(
            ("3", "Other app", "https://other.example.com/hook"),
            ("7", REGISTRATION_NAME, REGISTERED_URL),
        ),
    )
    entry = add_entry(hass, mock_config_entry_factory, webhook_id=WEBHOOK_ID)

    await async_remove_registration(hass, entry, make_client(hass, entry))

    [request] = fake_tel2.requests_for("Delete-Webhook")
    assert request.params == {"id": "7"}


async def test_remove_registration_without_a_stored_id_or_listing_deletes_nothing(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond(
        "Get-Webhooks",
        registrations(("3", "Other app", "https://other.example.com/hook")),
    )
    entry = add_entry(hass, mock_config_entry_factory, webhook_id=WEBHOOK_ID)

    await async_remove_registration(hass, entry, make_client(hass, entry))

    assert fake_tel2.call_count("Get-Webhooks") == 1
    assert fake_tel2.call_count("Delete-Webhook") == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_remove_registration_lookup_with_tel2_down_warns(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Get-Webhooks", aiohttp.ClientError())
    entry = add_entry(hass, mock_config_entry_factory, webhook_id=WEBHOOK_ID)

    await async_remove_registration(hass, entry, make_client(hass, entry))

    assert fake_tel2.call_count("Delete-Webhook") == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("may remain on the Tel2 account" in r.getMessage() for r in warnings)


async def test_remove_registration_with_tel2_down_warns_and_completes(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_tel2.respond("Delete-Webhook", aiohttp.ClientError())
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        tel2_webhook_id="1",
        cloudhook_url=CLOUDHOOK_URL,
    )

    await async_remove_registration(hass, entry, make_client(hass, entry))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("may remain on the Tel2 account" in r.getMessage() for r in warnings)
    cloud.delete.assert_awaited_once_with(hass, WEBHOOK_ID)


@pytest.mark.parametrize("error", [CloudNotAvailable, ValueError])
async def test_remove_registration_tolerates_a_missing_cloudhook(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    error: type[Exception],
) -> None:
    cloud.delete.side_effect = error
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        tel2_webhook_id="1",
        cloudhook_url=CLOUDHOOK_URL,
    )

    await async_remove_registration(hass, entry, make_client(hass, entry))

    assert fake_tel2.call_count("Delete-Webhook") == 1


async def test_remove_registration_without_cloudhook_skips_cloud(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(
        hass, mock_config_entry_factory, webhook_id=WEBHOOK_ID, tel2_webhook_id="1"
    )

    await async_remove_registration(hass, entry, make_client(hass, entry))

    cloud.delete.assert_not_awaited()
    assert fake_tel2.call_count("Delete-Webhook") == 1


async def test_without_the_cloud_integration_uses_the_external_url(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
        cloudhook_url=CLOUDHOOK_URL,
    )

    manager = await start_manager(hass, entry)
    manager.async_unload()

    assert fake_tel2.call_count("Add-Webhook") == 0
    assert manager.status == CallEventStatus("external", True, None)


async def test_remove_registration_without_the_cloud_integration_skips_cloud(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        tel2_webhook_id="1",
        cloudhook_url=CLOUDHOOK_URL,
    )

    await async_remove_registration(hass, entry, make_client(hass, entry))

    assert fake_tel2.call_count("Delete-Webhook") == 1


@pytest.mark.parametrize("error", [CloudError, TimeoutError, ValueError])
async def test_cloudhook_failure_falls_back_to_external(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    error: type[Exception],
) -> None:
    cloud.active.return_value = True
    cloud.get_or_create.side_effect = error
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    assert CONF_CLOUDHOOK_URL not in entry.data
    assert manager.status == CallEventStatus("external", True, None)


async def test_failed_start_releases_the_ha_webhook(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    cloud.active.side_effect = RuntimeError("boom")
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    with pytest.raises(RuntimeError):
        await start_manager(hass, entry)
    cloud.unsubscribe.assert_called_once_with()

    cloud.active.side_effect = None
    manager = await start_manager(hass, entry)

    assert manager.status.url_source == "none"


@pytest.mark.parametrize(
    ("url", "add_count"), [(REGISTERED_URL, 0), ("https://old.example.invalid/x", 1)]
)
async def test_reconcile_matches_the_stored_id_after_a_rename(
    *,
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    url: str,
    add_count: int,
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", registrations(("4", "renamed on Tel2", url)))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
        tel2_webhook_id="4",
    )

    manager = await start_manager(hass, entry)

    assert [p.get("editid") for p in add_params(fake_tel2)] == ["4"] * add_count
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "4"
    assert manager.status.registered_on_tel2 is True


async def test_added_registration_missing_on_re_read_is_warned(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass.config.external_url = EXTERNAL_URL
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)

    assert fake_tel2.call_count("Add-Webhook") == 1
    assert fake_tel2.call_count("Get-Webhooks") == 2
    assert manager.status.registered_on_tel2 is False
    assert CONF_TEL2_WEBHOOK_ID not in entry.data
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    message = warning.getMessage()
    assert REGISTERED_URL not in message
    assert WEBHOOK_SECRET not in message


async def test_added_registration_missing_on_re_read_keeps_the_stored_id(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
        tel2_webhook_id="9",
    )

    manager = await start_manager(hass, entry)

    assert fake_tel2.call_count("Add-Webhook") == 1
    assert manager.status.registered_on_tel2 is False
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "9"


async def test_repeated_registration_failures_warn_once_and_recovery_is_logged(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.tel2")
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond(
        "Get-Webhooks",
        ours(REGISTERED_URL),
        "error_generic.json",
        "error_generic.json",
        ours(REGISTERED_URL),
        ours(REGISTERED_URL),
        "error_generic.json",
    )
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )

    manager = await start_manager(hass, entry)
    await manager.async_reconcile()
    await manager.async_reconcile()
    await manager.async_reconcile()
    assert manager.status.registered_on_tel2 is True
    await manager.async_reconcile()
    await manager.async_reconcile()

    messages = [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == "custom_components.tel2.webhook" and r.levelno >= logging.INFO
    ]
    assert [level for level, _ in messages] == [
        logging.WARNING,
        logging.INFO,
        logging.WARNING,
    ]
    assert messages[1][1] == "Tel2 call event registration recovered"


async def test_url_source_is_none_before_start(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(hass, mock_config_entry_factory)
    client = make_client(hass, entry)
    manager = Tel2CallEventManager(
        hass, entry, client, Tel2AccountCoordinator(hass, entry, client)
    )

    assert manager.status == CallEventStatus("none", False, None)


async def test_url_source_returns_to_none_when_the_url_goes(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)
    assert manager.status.url_source == "external"

    hass.config.external_url = None
    await manager.async_reconcile()

    assert manager.status.url_source == "none"


async def test_https_internal_url_alone_is_not_public(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.internal_url = "https://internal.example.invalid"
    hass.config.external_url = None
    entry = add_entry(hass, mock_config_entry_factory)

    manager = await start_manager(hass, entry)

    assert no_url_issue(hass) is not None
    assert manager.status == CallEventStatus("none", False, None)
    assert fake_tel2.call_count("Add-Webhook") == 0


async def test_unloading_twice_unsubscribes_from_the_cloud_once(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(hass, mock_config_entry_factory)
    manager = await start_manager(hass, entry)

    manager.async_unload()
    manager.async_unload()

    cloud.unsubscribe.assert_called_once_with()


def test_availability_signal_name_is_per_entry() -> None:
    assert signal_call_event_availability("abc") == "tel2_abc_call_event_availability"


async def test_availability_signal_is_sent_only_when_it_changes(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    signals: list[None] = []
    async_dispatcher_connect(
        hass,
        signal_call_event_availability(entry.entry_id),
        lambda: signals.append(None),
    )

    manager = await start_manager(hass, entry)
    assert signals == []

    hass.config.external_url = EXTERNAL_URL
    await manager.async_reconcile()
    await manager.async_reconcile()
    await hass.async_block_till_done()
    assert (manager.available, manager.registered, len(signals)) == (True, True, 2)

    hass.config.external_url = None
    await manager.async_reconcile()
    await hass.async_block_till_done()
    assert (manager.available, manager.registered, len(signals)) == (False, True, 3)

    hass.config.external_url = EXTERNAL_URL
    await manager.async_reconcile()
    manager.async_unload()
    await hass.async_block_till_done()
    assert (manager.available, len(signals)) == (False, 5)


async def test_stored_registration_counts_as_registered_before_any_listing(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    entry = add_entry(hass, mock_config_entry_factory, tel2_webhook_id="7")
    client = make_client(hass, entry)
    manager = Tel2CallEventManager(
        hass, entry, client, Tel2AccountCoordinator(hass, entry, client)
    )

    assert manager.registered is True
    assert manager.status == CallEventStatus("none", True, None)


async def test_failed_listing_keeps_a_known_registration(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL), RawResponse("<br />"))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)
    assert manager.registered is True
    signals: list[None] = []
    async_dispatcher_connect(
        hass,
        signal_call_event_availability(entry.entry_id),
        lambda: signals.append(None),
    )

    await manager.async_reconcile()
    await hass.async_block_till_done()

    assert fake_tel2.call_count("Get-Webhooks") == 3
    assert manager.registered is True
    assert signals == []


async def test_listing_without_the_registration_clears_it_and_signals(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
) -> None:
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
        tel2_webhook_id="7",
    )
    manager = await start_manager(hass, entry)
    signals: list[None] = []
    async_dispatcher_connect(
        hass,
        signal_call_event_availability(entry.entry_id),
        lambda: signals.append(None),
    )

    fake_tel2.respond("Get-Webhooks", NO_DATA)
    await manager.async_reconcile()
    await hass.async_block_till_done()

    assert manager.registered is False
    assert len(signals) == 1


def unlisted_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "does not list it" in r.getMessage()
    ]


async def test_unlisted_add_is_not_repeated_within_the_retry_window(
    *,
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
) -> None:
    hass.config.external_url = EXTERNAL_URL
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)
    assert fake_tel2.call_count("Add-Webhook") == 1
    assert len(unlisted_warnings(caplog)) == 1

    freezer.tick(ADD_RETRY_WINDOW / 2)
    await manager.async_reconcile()

    assert fake_tel2.call_count("Add-Webhook") == 1
    assert fake_tel2.call_count("Get-Webhooks") == 3
    assert manager.registered is False
    assert len(unlisted_warnings(caplog)) == 1

    freezer.tick(ADD_RETRY_WINDOW / 2)
    await manager.async_reconcile()

    assert fake_tel2.call_count("Add-Webhook") == 2
    assert len(unlisted_warnings(caplog)) == 1

    await manager.async_reconcile()

    assert fake_tel2.call_count("Add-Webhook") == 2


async def test_unlisted_add_marker_clears_once_tel2_lists_it(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    cloud: CloudMocks,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass.config.external_url = EXTERNAL_URL
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    manager = await start_manager(hass, entry)

    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    await manager.async_reconcile()

    assert manager.registered is True
    assert entry.data[CONF_TEL2_WEBHOOK_ID] == "7"
    assert fake_tel2.call_count("Add-Webhook") == 1

    fake_tel2.respond("Get-Webhooks", NO_DATA)
    await manager.async_reconcile()

    assert fake_tel2.call_count("Add-Webhook") == 2
    assert len(unlisted_warnings(caplog)) == 2


def test_runtime_data_type_hints_resolve() -> None:
    hints = get_type_hints(Tel2RuntimeData)

    assert hints["call_events"] == Tel2CallEventManager | None


@pytest.mark.parametrize(
    "module", ["custom_components.tel2.webhook", "custom_components.tel2.runtime"]
)
def test_modules_import_first_without_a_cycle(module: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
