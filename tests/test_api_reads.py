"""Tests for the Tel2 client read operations and secret stripping."""

from collections.abc import AsyncGenerator, Iterator
import dataclasses
from decimal import Decimal
from typing import Any

import aiohttp
import pytest

from custom_components.tel2.api import (
    Tel2ApiError,
    Tel2AuthError,
    Tel2Client,
    Tel2ConnectionError,
)
from custom_components.tel2.const import PERSONAL_PREF_FIELDS, SECRET_PREFS
from custom_components.tel2.models import (
    AccountBalance,
    BundleUsage,
    CustomerDetails,
    PlanSummary,
    Tel2Number,
    WebhookRegistration,
)
from tests.fake_tel2 import NO_DATA, FakeTel2Api
from tests.fixture_loader import load_fixture

LOGIN = "portal-login-for-tests"
PASSWORD = "portal-password-for-tests"
LINE_1 = "441632960001"
LINE_2 = "441632960002"
PREF_FIXTURES = (
    "get_all_pref_summary.json",
    "get_all_pref_summary_all_on.json",
    "get_all_pref_summary_number_added.json",
)
NUMBER_FIXTURES = (
    "get_numbers.json",
    "get_numbers_extension_only.json",
    "get_numbers_number_added.json",
    "get_numbers_number_removed.json",
)


def success(data: Any) -> dict[str, Any]:
    return {
        "Result": "1",
        "Status": "Success",
        "Message": "Method Executed Successfully.",
        "Data": data,
    }


def number(canonical: str, tel2_id: str) -> Tel2Number:
    return Tel2Number(canonical=canonical, tel2_id=tel2_id, name=None, extension=None)


@pytest.fixture
async def session(fake_tel2: FakeTel2Api) -> AsyncGenerator[aiohttp.ClientSession]:
    session = fake_tel2.create_session()
    yield session
    await session.close()


@pytest.fixture
def client(session: aiohttp.ClientSession) -> Tel2Client:
    return Tel2Client(session, LOGIN, PASSWORD)


async def test_customer_details(client: Tel2Client, fake_tel2: FakeTel2Api) -> None:
    assert await client.async_get_customer_details() == CustomerDetails(
        account_code="00000000",
        customer_name="Example Customer",
        company_name="Example Ltd",
        status="Active",
    )
    assert fake_tel2.requests_for("Get-Customer-Details")[0].context == "account"


async def test_customer_details_without_company(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    data = load_fixture("get_customer_details.json")["Data"] | {"CompanyName": ""}
    fake_tel2.respond("Get-Customer-Details", success(data))

    details = await client.async_get_customer_details()

    assert details.company_name is None


async def test_customer_details_no_data_is_api_error(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Customer-Details", NO_DATA)

    with pytest.raises(Tel2ApiError) as err:
        await client.async_get_customer_details()

    assert err.value.context == "account"


async def test_account_balance(client: Tel2Client) -> None:
    balance = await client.async_get_account_balance()

    assert balance == AccountBalance(
        balance=Decimal("12.34"), available_balance=Decimal("10.00")
    )
    assert isinstance(balance.balance, Decimal)


@pytest.mark.parametrize(
    "data",
    [
        {"Balance": "lots", "AvailableBalance": "10.00"},
        {"AvailableBalance": "10.00"},
        "not a mapping",
    ],
    ids=["non-numeric", "missing-field", "not-a-mapping"],
)
async def test_unreadable_balance_is_connection_error(
    client: Tel2Client, fake_tel2: FakeTel2Api, data: Any
) -> None:
    fake_tel2.respond("Get-Account-Balance", success(data))

    with pytest.raises(Tel2ConnectionError):
        await client.async_get_account_balance()


async def test_current_plan_falls_back_to_the_first_row(client: Tel2Client) -> None:
    assert await client.async_get_current_plan() == PlanSummary(
        alias="Example Plan", description="Example business voice plan"
    )


async def test_current_plan_prefers_the_calling_plan_row(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Current-Plan-Summary",
        success(
            [
                {
                    "ServiceType": "Broadband",
                    "Alias": "Fibre",
                    "BillingDescription": "B",
                },
                {
                    "ServiceType": "CallingPlan",
                    "Alias": "Calls",
                    "BillingDescription": "Unlimited UK calls",
                },
            ]
        ),
    )

    assert await client.async_get_current_plan() == PlanSummary(
        alias="Calls", description="Unlimited UK calls"
    )


async def test_current_plan_no_data_is_none(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Current-Plan-Summary", NO_DATA)

    assert await client.async_get_current_plan() is None


async def test_numbers_are_merged_with_line_names(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    numbers = await client.async_get_numbers()

    assert numbers == [
        Tel2Number(LINE_1, "01632960001", "Line 1", "201"),
        Tel2Number(LINE_2, "01632960002", "Line 2", "202"),
    ]
    (request,) = fake_tel2.requests_for("Get-Numbers")
    assert request.context == "voice"
    assert request.params == {
        "orderby": "",
        "orderdirection": "",
        "searchby": "",
        "searchterm": "",
    }
    assert fake_tel2.call_count("Get-Line-Names") == 1


async def test_extension_only_entries_are_excluded(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Numbers", "get_numbers_extension_only.json")

    numbers = await client.async_get_numbers()

    assert [n.canonical for n in numbers] == [LINE_1, LINE_2]
    assert all(n.extension != "203" for n in numbers)


async def test_line_name_used_when_numbers_have_no_alias(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_numbers.json")["Data"]
    for row in rows:
        row.update({"Alias": "", "Parameter_4": "", "ExtensionNumber": ""})
    fake_tel2.respond("Get-Numbers", success(rows))
    fake_tel2.respond(
        "Get-Line-Names",
        success([{"ServiceName": "+44 1632 960001", "Name": "Kitchen"}]),
    )

    numbers = await client.async_get_numbers()

    assert numbers == [
        Tel2Number(LINE_1, "01632960001", "Kitchen", None),
        Tel2Number(LINE_2, "01632960002", None, None),
    ]


async def test_parameter_4_used_when_alias_is_empty(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_numbers.json")["Data"]
    rows[0].update({"Alias": "", "Parameter_4": "Front desk"})
    fake_tel2.respond("Get-Numbers", success(rows))

    numbers = await client.async_get_numbers()

    assert numbers[0].name == "Front desk"


async def test_international_tel2_id_is_kept_verbatim(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_numbers.json")["Data"][:1]
    rows[0]["ServiceName"] = "441632960001"
    fake_tel2.respond("Get-Numbers", success(rows))

    (only,) = await client.async_get_numbers()

    assert (only.canonical, only.tel2_id, only.name) == (
        LINE_1,
        "441632960001",
        "Line 1",
    )


async def test_numbers_without_a_dialable_length_are_skipped(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_numbers_extension_only.json")["Data"]
    rows[2]["ServiceName"] = "203"
    rows.append(rows[0] | {"ServiceName": "4416329600011234567"})
    fake_tel2.respond("Get-Numbers", success(rows))

    numbers = await client.async_get_numbers()

    assert [n.canonical for n in numbers] == [LINE_1, LINE_2]


@pytest.mark.parametrize(
    "response",
    ["error_generic.json", NO_DATA],
    ids=["api-error", "no-data"],
)
async def test_numbers_proceed_without_line_names(
    client: Tel2Client, fake_tel2: FakeTel2Api, response: Any
) -> None:
    rows = load_fixture("get_numbers.json")["Data"]
    rows[1].update({"Alias": "", "Parameter_4": ""})
    fake_tel2.respond("Get-Numbers", success(rows))
    fake_tel2.respond("Get-Line-Names", response)

    numbers = await client.async_get_numbers()

    assert [(n.canonical, n.name) for n in numbers] == [
        (LINE_1, "Line 1"),
        (LINE_2, None),
    ]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ("error_invalid_token.json", Tel2AuthError),
        (TimeoutError(), Tel2ConnectionError),
    ],
    ids=["auth", "connection"],
)
async def test_line_names_auth_and_connection_errors_propagate(
    client: Tel2Client,
    fake_tel2: FakeTel2Api,
    response: Any,
    error: type[Exception],
) -> None:
    fake_tel2.respond("Get-Line-Names", response)

    with pytest.raises(error):
        await client.async_get_numbers()


async def test_numbers_no_data_is_empty(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Numbers", NO_DATA)
    fake_tel2.respond("Get-Line-Names", NO_DATA)

    assert await client.async_get_numbers() == []


async def test_bundle_usage_for_a_line_with_a_bundle(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    usage = await client.async_get_line_bundle_usage(number(LINE_1, "01632960001"))

    assert usage == BundleUsage(used_minutes=37, total_minutes=1000)
    (request,) = fake_tel2.requests_for("Get-Line-Bundle-Usage")
    assert request.context == "account"
    assert request.params == {"line": "01632960001"}


async def test_bundle_usage_without_a_bundle_is_none(client: Tel2Client) -> None:
    assert (
        await client.async_get_line_bundle_usage(number(LINE_2, "01632960002")) is None
    )


async def test_subscriber_status_registered(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status_registered.json")

    assert await client.async_get_subscriber_status() == frozenset({LINE_1})


async def test_subscriber_status_ignores_values_other_than_registered(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Subscriber-Status",
        success({"01632960001": "1", "01632960002": "0", "+44 1632 960003": 1}),
    )

    assert await client.async_get_subscriber_status() == frozenset(
        {LINE_1, "441632960003"}
    )


async def test_subscriber_status_no_data_is_empty(client: Tel2Client) -> None:
    assert await client.async_get_subscriber_status() == frozenset()


async def test_all_prefs_keyed_by_canonical_number(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", "get_all_pref_summary_all_on.json")

    prefs = await client.async_get_all_prefs()

    assert set(prefs) == {LINE_1, LINE_2}
    line_1 = prefs[LINE_1]
    assert line_1["do_not_disturb_status"] == "1"
    assert line_1["callforward_always_status"] == "1"
    assert line_1["callforward_always_number"] == "07700900123"
    assert line_1["anoncall_reject"] == "1"
    assert line_1["profile_recordingenabled"] == "1"
    assert line_1["accountsetting"] == "0"
    assert "pref_uid" not in line_1
    assert all(isinstance(value, str) for value in line_1.values())


async def test_all_prefs_omit_null_values(client: Tel2Client) -> None:
    prefs = await client.async_get_all_prefs()

    assert "callforward_always_number" not in prefs[LINE_1]
    assert "blacklist" not in prefs[LINE_1]
    assert prefs[LINE_1]["callforward_always_status"] == "0"


async def test_all_prefs_drop_keys_that_look_secret(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_all_pref_summary.json")["Data"][:1]
    rows[0].update(
        {
            "new_feature_pin": "fake-new-pin",
            "SIP_Password": "fake-new-password",
            "api_token": "fake-new-token",
            "webhook_secret": "fake-new-secret",
            "access_code_extra": "fake-new-access-code",
            "nested_setting": {"a": "b"},
        }
    )
    fake_tel2.respond("Get-All-Pref-Summary", success(rows))

    prefs = (await client.async_get_all_prefs())[LINE_1]

    for key in (
        "new_feature_pin",
        "SIP_Password",
        "api_token",
        "webhook_secret",
        "access_code_extra",
        "nested_setting",
    ):
        assert key not in prefs
    assert prefs["do_not_disturb_status"] == "0"


async def test_all_prefs_drop_external_numbers_and_emails(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_all_pref_summary_all_on.json")["Data"][:1]
    external = {
        "callforward_busy_number": "07700900201",
        "callforward_always_number2": "07700900202",
        "simultaneous_ring1_number": "07700900203",
        "callrecording_allowed_numbers": "07700900204,07700900205",
        "voicemail_forwardingnumber": "07700900206",
        "blacklist": "07700900207",
        "do_not_disturb_forward": "07700900208",
        "missed_call_email_address": "missed@example.com",
        "Voicemail_EmailCopy": "copy@example.com",
    }
    rows[0].update(external)
    fake_tel2.respond("Get-All-Pref-Summary", success(rows))

    prefs = (await client.async_get_all_prefs())[LINE_1]

    assert not set(external) & set(prefs)
    assert not set(external.values()) & set(prefs.values())
    assert prefs["callforward_always_number"] == "07700900123"
    assert prefs["mailbox"] == "01632960001"
    assert prefs["voicemailmailbox"] == "01632960001"


async def test_all_prefs_drop_mailboxes_of_other_numbers(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_all_pref_summary.json")["Data"][:1]
    rows[0].update({"mailbox": "07700900301", "voicemailmailbox": "07700900302"})
    fake_tel2.respond("Get-All-Pref-Summary", success(rows))

    prefs = (await client.async_get_all_prefs())[LINE_1]

    assert "mailbox" not in prefs
    assert "voicemailmailbox" not in prefs


async def test_all_prefs_skip_rows_without_a_number(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    rows = load_fixture("get_all_pref_summary.json")["Data"]
    rows[1]["pref_uid"] = ""
    fake_tel2.respond("Get-All-Pref-Summary", success(rows))

    assert set(await client.async_get_all_prefs()) == {LINE_1}


async def test_all_prefs_no_data_is_empty(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-All-Pref-Summary", NO_DATA)

    assert await client.async_get_all_prefs() == {}


async def test_webhooks_populated(client: Tel2Client, fake_tel2: FakeTel2Api) -> None:
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")

    assert await client.async_get_webhooks() == [
        WebhookRegistration(
            tel2_id="1",
            name="homeassistant-tel2-tel2_test_entry",
            url="https://example.invalid/api/webhook/example-webhook-id",
        ),
        WebhookRegistration(
            tel2_id="2",
            name="Other app",
            url="https://example.invalid/other-app/calls",
        ),
    ]
    assert fake_tel2.requests_for("Get-Webhooks")[0].context == "api"


async def test_webhooks_accept_upper_case_id(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond(
        "Get-Webhooks",
        success([{"ID": 7, "Name": "x", "URL": "https://example.invalid/x"}, {}]),
    )

    assert await client.async_get_webhooks() == [
        WebhookRegistration(tel2_id="7", name="x", url="https://example.invalid/x")
    ]


async def test_webhooks_no_data_is_empty(client: Tel2Client) -> None:
    assert await client.async_get_webhooks() == []


async def test_probe_access_all_granted(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    assert await client.async_probe_access() == set()
    assert [(r.action, r.context) for r in fake_tel2.requests[1:]] == [
        ("Get-Account-Balance", "account"),
        ("Get-Subscriber-Status", "voice"),
        ("Get-Webhooks", "api"),
    ]


@pytest.mark.parametrize(
    ("action", "fixture", "context"),
    [
        ("Get-Account-Balance", "error_access_refused_account.json", "account"),
        ("Get-Subscriber-Status", "error_access_refused_voice.json", "voice"),
        ("Get-Webhooks", "error_access_refused_api.json", "api"),
    ],
)
async def test_probe_access_reports_each_refused_context(
    client: Tel2Client,
    fake_tel2: FakeTel2Api,
    action: str,
    fixture: str,
    context: str,
) -> None:
    fake_tel2.respond(action, fixture)

    assert await client.async_probe_access() == {context}


async def test_probe_access_reports_every_refused_context(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Account-Balance", "error_access_refused_account.json")
    fake_tel2.respond("Get-Subscriber-Status", "error_access_refused_voice.json")
    fake_tel2.respond("Get-Webhooks", "error_access_refused_api.json")

    assert await client.async_probe_access() == {"account", "voice", "api"}


async def test_probe_access_no_data_counts_as_granted(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Account-Balance", NO_DATA)

    assert await client.async_probe_access() == set()


async def test_probe_access_auth_error_propagates(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", "error_wrong_password.json")

    with pytest.raises(Tel2AuthError):
        await client.async_probe_access()


async def test_probe_access_token_api_error_is_not_a_refused_context(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    fake_tel2.respond("Get-Token", "error_generic.json")

    with pytest.raises(Tel2ApiError) as err:
        await client.async_probe_access()

    assert err.value.context is None


def _walk(value: Any) -> Iterator[Any]:
    """Yield every key, value and field reachable from a returned object."""
    yield value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(key)
            yield from _walk(item)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from _walk(item)


def _fixture_secret_values() -> set[str]:
    values: set[str] = set()
    for name in NUMBER_FIXTURES:
        values |= {row["Parameter_1"] for row in load_fixture(name)["Data"]}
    for name in PREF_FIXTURES:
        for row in load_fixture(name)["Data"]:
            values |= {
                str(row[key])
                for key in SECRET_PREFS | {"email", "imapuser", "pager"}
                if row.get(key) is not None
            }
    values |= {
        row["secrettoken"]
        for row in load_fixture("get_webhooks_populated.json")["Data"]
    }
    return values


async def _everything_returned(client: Tel2Client, fake_tel2: FakeTel2Api) -> list[Any]:
    returned: list[Any] = [
        await client.async_get_customer_details(),
        await client.async_get_account_balance(),
        await client.async_get_current_plan(),
    ]
    for numbers_fixture in NUMBER_FIXTURES:
        fake_tel2.respond("Get-Numbers", numbers_fixture)
        numbers = await client.async_get_numbers()
        returned.append(numbers)
        returned.extend(
            [await client.async_get_line_bundle_usage(line) for line in numbers]
        )
    fake_tel2.respond("Get-Subscriber-Status", "get_subscriber_status_registered.json")
    returned.append(await client.async_get_subscriber_status())
    for prefs_fixture in PREF_FIXTURES:
        fake_tel2.respond("Get-All-Pref-Summary", prefs_fixture)
        returned.append(await client.async_get_all_prefs())
    fake_tel2.respond("Get-Webhooks", "get_webhooks_populated.json")
    returned.append(await client.async_get_webhooks())
    returned.append(await client.async_probe_access())
    return returned


async def test_no_secret_or_personal_field_is_returned(
    client: Tel2Client, fake_tel2: FakeTel2Api
) -> None:
    secret_values = _fixture_secret_values()
    forbidden_keys = (
        SECRET_PREFS | PERSONAL_PREF_FIELDS | {"Parameter_1", "secrettoken"}
    )
    assert any(value.startswith("fake-sip-password") for value in secret_values)
    assert "fake-voicemail-pin-1" in secret_values
    assert "FakeSecretTokenA" in secret_values

    returned = await _everything_returned(client, fake_tel2)
    reachable = list(_walk(returned))
    strings = {item for item in reachable if isinstance(item, str)}
    dict_keys = {key for item in reachable if isinstance(item, dict) for key in item}

    assert strings
    assert not strings & secret_values
    assert not dict_keys & forbidden_keys
    assert not any("@" in item for item in strings)
