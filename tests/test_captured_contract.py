"""Contract tests: the client against anonymised responses captured from Tel2."""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import aiohttp
import pytest

from custom_components.tel2.api import Tel2AuthError, Tel2Client
from custom_components.tel2.const import PERSONAL_PREF_FIELDS, SECRET_PREFS
from custom_components.tel2.models import (
    AccountBalance,
    BundleUsage,
    CustomerDetails,
    PlanSummary,
)
from custom_components.tel2.switch import SWITCHES
from tests.fake_tel2 import FakeTel2Api
from tests.fixture_loader import load_captured

LOGIN = "portal-login-for-tests"
PASSWORD = "portal-password-for-tests"
LINE = "441632960001"
TOKEN_RESPONSE = {
    "Result": "1",
    "Status": "Success",
    "Message": "Token Successfully Retrieved",
    "Data": {"token": "fake-token-for-tests"},
}


def captured(name: str) -> Any:
    body = load_captured(name)
    if body is None:
        pytest.skip(f"{name} has not been captured")
    return body


@pytest.fixture
async def session(fake_tel2: FakeTel2Api) -> AsyncGenerator[aiohttp.ClientSession]:
    session = fake_tel2.create_session()
    yield session
    await session.close()


@pytest.fixture
def client(session: aiohttp.ClientSession, fake_tel2: FakeTel2Api) -> Tel2Client:
    fake_tel2.respond("Get-Token", TOKEN_RESPONSE)
    return Tel2Client(session, LOGIN, PASSWORD)


async def test_customer_details(client: Tel2Client, fake_tel2: FakeTel2Api) -> None:
    fake_tel2.respond("Get-Customer-Details", captured("get_customer_details.json"))

    assert await client.async_get_customer_details() == CustomerDetails(
        account_code="00000000",
        customer_name="Example Customer",
        company_name="Example Ltd",
        status="Active",
    )


async def test_account_balance(client: Tel2Client, fake_tel2: FakeTel2Api) -> None:
    fake_tel2.respond("Get-Account-Balance", captured("get_account_balance.json"))

    assert await client.async_get_account_balance() == AccountBalance(
        balance=Decimal("10.00"), available_balance=Decimal("10.00")
    )


async def test_current_plan_is_the_calling_plan_row(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Current-Plan-Summary", captured("get_current_plan_summary.json")
    )

    assert await client.async_get_current_plan() == PlanSummary(
        alias="T2CallingPlan419", description="Free Trial Plan"
    )


async def test_numbers_are_canonical_and_keep_tel2_id(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Numbers", captured("get_numbers.json"))
    fake_tel2.respond("Get-Line-Names", captured("get_line_names.json"))

    [number] = await client.async_get_numbers()

    assert (number.canonical, number.tel2_id) == (LINE, LINE)
    assert number.name == captured("get_line_names.json")["Data"][0]["Name"]
    assert number.extension is None


async def test_bundle_usage_per_line(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    usage = captured("get_line_bundle_usage.json")
    fake_tel2.respond("Get-Numbers", captured("get_numbers.json"))
    fake_tel2.respond("Get-Line-Names", captured("get_line_names.json"))
    [number] = await client.async_get_numbers()
    fake_tel2.respond("Get-Line-Bundle-Usage", usage[number.tel2_id])

    assert await client.async_get_line_bundle_usage(number) == BundleUsage(
        used_minutes=3, total_minutes=60
    )
    [request] = fake_tel2.requests_for("Get-Line-Bundle-Usage")
    assert request.params == {"line": LINE}


async def test_all_prefs_are_keyed_by_canonical_number_without_secrets(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", captured("get_all_pref_summary.json"))

    prefs = await client.async_get_all_prefs()

    assert list(prefs) == [LINE]
    line = prefs[LINE]
    assert not set(line) & (SECRET_PREFS | PERSONAL_PREF_FIELDS | {"pref_uid"})
    assert line["mailbox"] == LINE
    assert line["profile_recordingenabled"] == "0"
    assert line["do_not_disturb_status"] == ""


async def test_recording_switch_reads_on_from_the_captured_prefs(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", captured("get_all_pref_summary.json"))
    prefs = (await client.async_get_all_prefs())[LINE]

    recording = next(d for d in SWITCHES if d.key == "call_recording")

    assert prefs["callrecording_disabled"] == "0"
    assert recording.is_on_fn(prefs) is True


async def test_empty_subscriber_status_and_webhooks(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", captured("get_subscriber_status.json"))
    fake_tel2.respond("Get-Webhooks", captured("get_webhooks.json"))

    assert await client.async_get_subscriber_status() == frozenset()
    assert await client.async_get_webhooks() == []


async def test_empty_replies_do_not_count_as_refused_access(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Account-Balance", captured("get_account_balance.json"))
    fake_tel2.respond("Get-Subscriber-Status", captured("get_subscriber_status.json"))
    fake_tel2.respond("Get-Webhooks", captured("get_webhooks.json"))

    assert await client.async_probe_access() == set()


async def test_invalid_token_renews_once_and_retries(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    refreshed: list[str] = []
    client = Tel2Client(
        session, LOGIN, PASSWORD, token="stale", on_token_refreshed=refreshed.append
    )
    fake_tel2.respond("Get-Token", TOKEN_RESPONSE)
    fake_tel2.respond(
        "Get-Account-Balance",
        captured("error_invalid_token.json"),
        captured("get_account_balance.json"),
    )

    balance = await client.async_get_account_balance()

    assert balance.balance == Decimal("10.00")
    assert fake_tel2.call_count("Get-Token") == 1
    assert refreshed == ["fake-token-for-tests"]
    assert [r.body["token"] for r in fake_tel2.requests_for("Get-Account-Balance")] == [
        "stale",
        "fake-token-for-tests",
    ]


async def test_wrong_password_at_get_token_is_auth_error(
    session: aiohttp.ClientSession, fake_tel2: FakeTel2Api
) -> None:
    client = Tel2Client(session, LOGIN, PASSWORD)
    fake_tel2.respond("Get-Token", captured("error_wrong_password.json"))

    with pytest.raises(Tel2AuthError):
        await client.async_authenticate()

    assert fake_tel2.call_count("Get-Token") == 1
