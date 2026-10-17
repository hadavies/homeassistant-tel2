"""Tests for inbound Tel2 call events on the HA webhook."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import Any

from aiohttp.test_utils import TestClient
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.tel2.const import CallEventType
from custom_components.tel2.coordinator import Tel2AccountCoordinator
from custom_components.tel2.models import (
    AccountBalance,
    AccountState,
    CallEvent,
    CustomerDetails,
    Tel2Number,
)
from custom_components.tel2.webhook import (
    SECRET_HEADER,
    Tel2CallEventManager,
    signal_call_event,
)
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import TEST_ENTRY_ID, load_fixture
from tests.helpers import LINE_1, LINE_2
from tests.test_webhook_manager import (
    EXTERNAL_URL,
    REGISTERED_URL,
    WEBHOOK_ID,
    WEBHOOK_SECRET,
    add_entry,
    make_client,
    ours,
)

CALLER = "447700900123"
UNKNOWN_LINE = "441632960099"
START = datetime(2026, 10, 16, 9, 15, 2, tzinfo=UTC)
WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_ID}"


def account_state(*numbers: str) -> AccountState:
    return AccountState(
        details=CustomerDetails("00000000", "Example", None, "Active"),
        balance=AccountBalance(Decimal(0), Decimal(0)),
        plan=None,
        numbers={
            number: Tel2Number(number, str(index), None, None)
            for index, number in enumerate(numbers)
        },
        bundles={},
    )


@dataclass(slots=True)
class Harness:
    """A started manager, a client for its webhook and the published events."""

    entry: MockConfigEntry
    account: Tel2AccountCoordinator
    manager: Tel2CallEventManager
    client: TestClient[Any, Any]
    events: list[CallEvent]

    async def post(
        self,
        payload: Any,
        *,
        secret: str | None = WEBHOOK_SECRET,
        header: str = SECRET_HEADER,
    ) -> int:
        """POST a payload signed with the given secret and return the status."""
        headers = {} if secret is None else {header: secret}
        if isinstance(payload, str | bytes):
            response = await self.client.post(
                WEBHOOK_PATH, data=payload, headers=headers
            )
        else:
            response = await self.client.post(
                WEBHOOK_PATH, json=payload, headers=headers
            )
        return response.status


@pytest.fixture
async def harness(
    hass: HomeAssistant,
    fake_tel2: FakeTel2Api,
    mock_config_entry_factory: Callable[..., MockConfigEntry],
    hass_client_no_auth: ClientSessionGenerator,
) -> Harness:
    assert await async_setup_component(hass, "webhook", {})
    hass.config.external_url = EXTERNAL_URL
    fake_tel2.respond("Get-Webhooks", ours(REGISTERED_URL))
    entry = add_entry(
        hass,
        mock_config_entry_factory,
        webhook_id=WEBHOOK_ID,
        webhook_secret=WEBHOOK_SECRET,
    )
    client = make_client(hass, entry)
    account = Tel2AccountCoordinator(hass, entry, client)
    account.async_set_updated_data(account_state(LINE_1, LINE_2))
    manager = Tel2CallEventManager(hass, entry, client, account)
    await manager.async_start()
    await hass.async_block_till_done()

    events: list[CallEvent] = []

    @callback
    def capture(event: CallEvent) -> None:
        events.append(event)

    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_call_event(TEST_ENTRY_ID), capture)
    )
    return Harness(entry, account, manager, await hass_client_no_auth(), events)


def test_signal_name_is_per_entry() -> None:
    assert signal_call_event("abc") == "tel2_abc_call_event"


@pytest.mark.parametrize(
    ("fixture", "event_type", "start_time", "duration"),
    [
        ("webhook_inbound_ringing.json", CallEventType.RINGING, None, None),
        ("webhook_inbound_answered.json", CallEventType.ANSWERED, None, None),
        ("webhook_inbound_ended.json", CallEventType.ENDED, START, 42),
        ("webhook_inbound_missed.json", CallEventType.MISSED, START, None),
        ("webhook_inbound_blocked.json", CallEventType.BLOCKED, START, None),
    ],
)
async def test_inbound_events_are_published_for_the_called_number(
    *,
    hass: HomeAssistant,
    harness: Harness,
    fixture: str,
    event_type: CallEventType,
    start_time: datetime | None,
    duration: int | None,
) -> None:
    payload = load_fixture(fixture)

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    assert harness.events == [
        CallEvent(
            number=LINE_1,
            event_type=event_type,
            direction="inbound",
            from_number=CALLER,
            to_number=LINE_1,
            call_id=payload["id"],
            origin_id=payload["originid"],
            start_time=start_time,
            duration=duration,
        )
    ]


@pytest.mark.parametrize(
    ("fixture", "event_type", "start_time", "duration"),
    [
        ("webhook_outbound_ringing.json", CallEventType.RINGING, None, None),
        ("webhook_outbound_answered.json", CallEventType.ANSWERED, None, None),
        ("webhook_outbound_ended.json", CallEventType.ENDED, START, 42),
        ("webhook_outbound_busy.json", CallEventType.BUSY, START, None),
        ("webhook_outbound_noanswer.json", CallEventType.NO_ANSWER, START, None),
        ("webhook_outbound_rejected.json", CallEventType.REJECTED, START, None),
        ("webhook_outbound_invalid.json", CallEventType.INVALID, START, None),
        (
            "webhook_outbound_notavailable.json",
            CallEventType.NOT_AVAILABLE,
            START,
            None,
        ),
        ("webhook_outbound_blocked.json", CallEventType.BLOCKED, START, None),
    ],
)
async def test_outbound_events_are_published_for_the_calling_number(
    *,
    hass: HomeAssistant,
    harness: Harness,
    fixture: str,
    event_type: CallEventType,
    start_time: datetime | None,
    duration: int | None,
) -> None:
    payload = load_fixture(fixture)

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    assert harness.events == [
        CallEvent(
            number=LINE_1,
            event_type=event_type,
            direction="outbound",
            from_number=LINE_1,
            to_number=CALLER,
            call_id=payload["id"],
            origin_id=payload["originid"],
            start_time=start_time,
            duration=duration,
        )
    ]


async def test_e164_numbers_are_canonicalised(
    hass: HomeAssistant, harness: Harness
) -> None:
    payload = {
        **load_fixture("webhook_inbound_ended.json"),
        "from": f"+{CALLER}",
        "to": f"+{LINE_2}",
        "duration": "42",
    }

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    [event] = harness.events
    assert (event.number, event.from_number, event.to_number) == (
        LINE_2,
        CALLER,
        LINE_2,
    )
    assert event.duration == 42


async def test_secret_header_name_is_case_insensitive(
    hass: HomeAssistant, harness: Harness
) -> None:
    payload = load_fixture("webhook_inbound_ringing.json")

    assert await harness.post(payload, header=SECRET_HEADER.lower()) == 200
    await hass.async_block_till_done()

    assert len(harness.events) == 1


@pytest.mark.parametrize(
    ("optional", "value"),
    [
        ("start_time", "16/10/2026 09:15"),
        ("start_time", 1760606102),
        ("duration", "forty two"),
        ("duration", True),
        ("duration", -1),
        pytest.param("duration", "9" * 10, id="duration-10-digits"),
        pytest.param("duration", "9" * 5000, id="duration-5000-digits"),
        pytest.param("duration", 10**10, id="duration-huge-int"),
    ],
)
async def test_unreadable_optional_fields_become_none(
    hass: HomeAssistant, harness: Harness, optional: str, value: Any
) -> None:
    payload = {**load_fixture("webhook_inbound_ended.json"), optional: value}

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    [event] = harness.events
    assert getattr(event, optional) is None


def without(fixture: str, key: str) -> dict[str, Any]:
    payload: dict[str, Any] = load_fixture(fixture)
    del payload[key]
    return payload


def replaced(fixture: str, key: str, value: Any) -> dict[str, Any]:
    return {**load_fixture(fixture), key: value}


@pytest.mark.parametrize(
    ("payload", "from_number", "to_number"),
    [
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "anonymous"),
            "anonymous",
            LINE_1,
            id="withheld caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", ""),
            "unknown",
            LINE_1,
            id="empty caller",
        ),
        pytest.param(
            without("webhook_inbound_missed.json", "from"),
            "unknown",
            LINE_1,
            id="missing caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "   "),
            "unknown",
            LINE_1,
            id="blank caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", 447700900123),
            "unknown",
            LINE_1,
            id="non-string caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "  Withheld \n"),
            "Withheld",
            LINE_1,
            id="padded caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "x" * 100),
            "x" * 32,
            LINE_1,
            id="long caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "With\nheld"),
            "Withheld",
            LINE_1,
            id="newline in caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "\x1b[2JWith\x07held"),
            "[2JWithheld",
            LINE_1,
            id="ansi escape and bel in caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "\u202edlehhtiW"),
            "dlehhtiW",
            LINE_1,
            id="bidi override in caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "\x1b\x07\u202e\r\n"),
            "unknown",
            LINE_1,
            id="control-only caller",
        ),
        pytest.param(
            replaced("webhook_inbound_missed.json", "from", "\x00" * 40 + "Withheld"),
            "Withheld",
            LINE_1,
            id="controls before truncation",
        ),
        pytest.param(
            replaced("webhook_outbound_ended.json", "to", "voicemail"),
            LINE_1,
            "voicemail",
            id="non-numeric destination",
        ),
        pytest.param(
            without("webhook_outbound_ended.json", "to"),
            LINE_1,
            "unknown",
            id="missing destination",
        ),
    ],
)
async def test_other_party_falls_back_to_the_raw_value(
    hass: HomeAssistant,
    harness: Harness,
    payload: dict[str, Any],
    from_number: str,
    to_number: str,
) -> None:
    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    [event] = harness.events
    assert (event.number, event.from_number, event.to_number) == (
        LINE_1,
        from_number,
        to_number,
    )


async def test_missing_origin_id_falls_back_to_the_call_id(
    hass: HomeAssistant, harness: Harness
) -> None:
    payload = without("webhook_inbound_ringing.json", "originid")

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    [event] = harness.events
    assert event.origin_id == event.call_id == payload["id"]


@pytest.mark.parametrize(
    ("call_id", "origin_id", "expected_call_id", "expected_origin_id"),
    [
        pytest.param(
            "call\x1b[0m-1\u202e",
            "origin\n-1",
            "call[0m-1",
            "origin-1",
            id="controls removed",
        ),
        pytest.param("c" * 100, "o" * 100, "c" * 64, "o" * 64, id="long ids"),
        pytest.param(" call-1 ", "\x07\n", "call-1", "call-1", id="empty origin"),
    ],
)
async def test_call_ids_are_printable_and_bounded(
    *,
    hass: HomeAssistant,
    harness: Harness,
    call_id: str,
    origin_id: str,
    expected_call_id: str,
    expected_origin_id: str,
) -> None:
    payload = {
        **load_fixture("webhook_inbound_ringing.json"),
        "id": call_id,
        "originid": origin_id,
    }

    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    [event] = harness.events
    assert (event.call_id, event.origin_id) == (expected_call_id, expected_origin_id)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(load_fixture("webhook_aianalysis.json"), id="aianalysis"),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "state", "transferred"),
            id="unknown state",
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "to", UNKNOWN_LINE),
            id="unknown inbound number",
        ),
        pytest.param(
            replaced("webhook_outbound_ringing.json", "from", UNKNOWN_LINE),
            id="unknown outbound number",
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "state", ["ringing"]),
            id="non-string state",
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "type", "forwarded"),
            id="unknown type",
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "to", "anonymous"),
            id="invalid inbound to",
        ),
        pytest.param(
            replaced("webhook_outbound_ringing.json", "from", "anonymous"),
            id="invalid outbound from",
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "to", 441632960001),
            id="non-string to",
        ),
        *(
            pytest.param(
                without("webhook_inbound_ringing.json", key), id=f"missing {key}"
            )
            for key in ("state", "id", "to", "type")
        ),
        pytest.param(
            replaced("webhook_inbound_ringing.json", "id", "\x1b\x07\u202e "),
            id="control-only id",
        ),
        pytest.param(
            without("webhook_outbound_ringing.json", "from"),
            id="missing outbound from",
        ),
        pytest.param([load_fixture("webhook_inbound_ringing.json")], id="json list"),
        pytest.param("{not json", id="invalid json"),
        pytest.param(b"\xff\xfe", id="not utf-8"),
        pytest.param("", id="empty body"),
    ],
)
async def test_unusable_posts_publish_nothing(
    hass: HomeAssistant,
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
    payload: Any,
) -> None:
    assert await harness.post(payload) == 200
    await hass.async_block_till_done()

    assert harness.events == []
    assert harness.manager.status.last_event_at is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "secret",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("0" * 64, id="wrong"),
        pytest.param(WEBHOOK_SECRET[:-1], id="truncated"),
        pytest.param(WEBHOOK_SECRET + "0", id="extended"),
    ],
)
async def test_posts_without_the_secret_publish_nothing(
    hass: HomeAssistant, harness: Harness, secret: str | None
) -> None:
    payload = load_fixture("webhook_inbound_ringing.json")

    assert await harness.post(payload, secret=secret) == 200
    await hass.async_block_till_done()

    assert harness.events == []
    assert harness.manager.status.last_event_at is None


async def test_events_before_the_first_account_refresh_are_dropped(
    hass: HomeAssistant, harness: Harness
) -> None:
    harness.account.data = None  # type: ignore[assignment]

    assert await harness.post(load_fixture("webhook_inbound_ringing.json")) == 200
    await hass.async_block_till_done()

    assert harness.events == []


async def test_last_event_at_records_accepted_events_only(
    hass: HomeAssistant, harness: Harness, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-17 10:00:00+00:00")
    accepted_at = dt_util.utcnow()
    assert await harness.post(load_fixture("webhook_inbound_ringing.json")) == 200
    await hass.async_block_till_done()
    assert harness.manager.status.last_event_at == accepted_at

    freezer.move_to("2026-10-17 10:05:00+00:00")
    assert await harness.post(load_fixture("webhook_aianalysis.json")) == 200
    assert (
        await harness.post(load_fixture("webhook_inbound_ringing.json"), secret="x")
        == 200
    )
    await hass.async_block_till_done()

    assert harness.manager.status.last_event_at == accepted_at
    assert len(harness.events) == 1


async def test_drop_logs_leak_neither_secret_nor_payload(
    hass: HomeAssistant, harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.tel2")
    bad_secret = "f" * 64
    payload = replaced("webhook_inbound_ringing.json", "to", UNKNOWN_LINE)

    await harness.post(load_fixture("webhook_inbound_ringing.json"), secret=bad_secret)
    await harness.post(payload)
    await harness.post(replaced("webhook_inbound_ringing.json", "to", "anonymous"))
    await harness.post(load_fixture("webhook_aianalysis.json"))
    await harness.post("{not json")
    await hass.async_block_till_done()

    drops = [
        record
        for record in caplog.records
        if record.name == "custom_components.tel2.webhook"
        and "call event" in record.getMessage()
    ]
    assert len(drops) == 5
    for secret in (WEBHOOK_SECRET, bad_secret):
        assert secret not in caplog.text
    for value in (UNKNOWN_LINE, CALLER, "anonymous", "example-call", "transcript"):
        assert value not in caplog.text
