"""Tests for the fixture anonymiser."""

import json
import re
from typing import Any

import pytest

from custom_components.tel2.const import SECRET_PREFS
from scripts import anonymise
from scripts.anonymise import anonymise_responses

NUMBER_A_NATIONAL = "01134960847"
NUMBER_A_INTERNATIONAL = "441134960847"
NUMBER_B_NATIONAL = "07700900344"
NUMBER_C_PLUS = "+442079460567"
FORWARD_SPACED = "0161 496 0999"
ACCOUNT_CODE = "48213977"
CUSTOMER_NAME = "Morwenna Pryce"
COMPANY_NAME = "Pryce Widgets Limited"
ADDRESS = {
    "BuildingName": "Rosemount House",
    "FlatNumber": "Flat 7b",
    "StreetNumber": "42",
    "StreetName": "Larkspur Avenue",
    "Suburb": "Ambridge",
    "City": "Oxfordshire Town",
    "State": "Oxonshire",
    "PostCode": "ZZ1 1ZZ",
}
EMAILS = ("morwenna.pryce@mail.test", "lounge-line@pryce-widgets.test")
IPS = ("203.0.113.160", "2001:db8:abcd::1234")
URLS = (
    "https://hooks.pryce-widgets.test/tel2/abc123",
    "https://cdn.tel2.test/rec/998877.mp3",
)
USER_AGENT = "Yealink SIP-T46U 198.51.100.20"
DEVICE_MAC = "80:5e:c0:12:34:56"
IDS = ("9917345", "webhook-7731", "1760600123.4455", "88123")
LINE_NAMES = ("Lounge Phone", "Office Desk")
LINE_PASSWORD = "Sup3rS3cretSipPass"
TOKEN = "f0e1d2c3b4a5968778695a4b3c2d1e0f"
SECRET_VALUES = {
    pref: f"Zv{index}q8Lm{index}x" for index, pref in enumerate(sorted(SECRET_PREFS))
}


def _responses() -> dict[str, Any]:
    return {
        "get_token.json": {"Result": "1", "Data": {"token": TOKEN}},
        "get_customer_details.json": {
            "Result": "1",
            "Status": "Success",
            "Message": f"Details for {CUSTOMER_NAME}",
            "Data": {
                "Account_Code": ACCOUNT_CODE,
                "CustomerName": CUSTOMER_NAME,
                "CompanyName": COMPANY_NAME,
                "Email": EMAILS[0],
                "Phone_Number": NUMBER_A_NATIONAL,
                "Status": "Active",
                **ADDRESS,
            },
        },
        "get_numbers.json": {
            "Result": "1",
            "Data": [
                {
                    "ServiceName": NUMBER_A_NATIONAL,
                    "Alias": NUMBER_A_NATIONAL,
                    "Parameter_1": LINE_PASSWORD,
                    "Parameter_4": LINE_NAMES[0],
                    "ExtensionNumber": "201",
                    "LineEmail": EMAILS[1],
                    "GroupID": "0",
                    "userid": IDS[0],
                },
                {
                    "ServiceName": NUMBER_B_NATIONAL,
                    "Parameter_1": "AnotherSipPass99",
                    "Parameter_4": LINE_NAMES[1],
                    "userid": IDS[3],
                },
            ],
        },
        "get_line_names.json": {
            "Result": "1",
            "Data": [
                {"ServiceName": NUMBER_A_NATIONAL, "Name": LINE_NAMES[0]},
                {"ServiceName": NUMBER_B_NATIONAL, "Name": LINE_NAMES[1]},
            ],
        },
        "get_subscriber_status.json": {
            "Result": "1",
            "Data": {NUMBER_A_NATIONAL: "1", NUMBER_C_PLUS: "1"},
        },
        "get_all_pref_summary.json": {
            "Result": "1",
            "Data": {
                NUMBER_A_NATIONAL: {
                    "do_not_disturb_status": "off",
                    "callforward_always_number": FORWARD_SPACED,
                    "missed_call_email_address": EMAILS[1],
                    **SECRET_VALUES,
                },
                NUMBER_B_NATIONAL: [
                    {"pref": "voicemail_pin", "value": "8642"},
                    {"pref": "anoncall_reject", "value": "on"},
                ],
            },
        },
        "get_webhooks.json": {
            "Result": "1",
            "Data": [
                {
                    "id": IDS[1],
                    "url": URLS[0],
                    "phonenumber": NUMBER_A_INTERNATIONAL,
                    "secrettoken": "whsec-abcdef",
                },
            ],
        },
        "get_registrations.json": {
            "Result": "1",
            "Data": {
                "uniqueid": IDS[2],
                "UserAgent": USER_AGENT,
                "DeviceID": DEVICE_MAC,
                "ContactIP": IPS[0],
                "RegisteredFrom": f"[{IPS[1]}]:5061",
                "Contact": f"sip:{NUMBER_A_INTERNATIONAL}@[{IPS[1]}]:5060",
                "RecordingURL": URLS[1],
                "Note": f"call from +{NUMBER_A_INTERNATIONAL[:2]} {NUMBER_A_INTERNATIONAL[2:]}",
            },
        },
    }


@pytest.fixture
def result() -> dict[str, Any]:
    return anonymise_responses(_responses())


@pytest.fixture
def dumped(result: dict[str, Any]) -> str:
    return json.dumps(result)


def test_no_input_number_survives_in_either_format(dumped: str) -> None:
    survivors = [
        raw
        for raw in (
            NUMBER_A_NATIONAL,
            NUMBER_A_INTERNATIONAL,
            NUMBER_B_NATIONAL,
            "447700900344",
            NUMBER_C_PLUS[1:],
            "01614960999",
            "0161 496 0999",
            "113 496 0847",
            "1134960847",
        )
        if raw in dumped
    ]
    assert survivors == []


def test_national_and_international_forms_share_one_placeholder(
    result: dict[str, Any],
) -> None:
    national = result["get_customer_details.json"]["Data"]["Phone_Number"]
    international = result["get_webhooks.json"]["Data"][0]["phonenumber"]
    assert re.fullmatch(r"01632960\d{3}", national)
    assert re.fullmatch(r"441632960\d{3}", international)
    assert national[-3:] == international[-3:]


def test_plus_prefix_and_dict_keys_are_replaced(result: dict[str, Any]) -> None:
    keys = list(result["get_subscriber_status.json"]["Data"])
    assert re.fullmatch(r"01632960\d{3}", keys[0])
    assert re.fullmatch(r"\+441632960\d{3}", keys[1])


def test_spaced_number_in_number_field_is_replaced(result: dict[str, Any]) -> None:
    prefs = next(iter(result["get_all_pref_summary.json"]["Data"].values()))
    assert re.fullmatch(r"01632960\d{3}", prefs["callforward_always_number"])


def test_extension_numbers_are_kept(result: dict[str, Any]) -> None:
    assert result["get_numbers.json"]["Data"][0]["ExtensionNumber"] == "201"


def test_account_code_does_not_survive(result: dict[str, Any], dumped: str) -> None:
    assert ACCOUNT_CODE not in dumped
    assert result["get_customer_details.json"]["Data"]["Account_Code"] == "00000000"


def test_customer_and_company_names_do_not_survive(
    result: dict[str, Any], dumped: str
) -> None:
    assert CUSTOMER_NAME not in dumped
    assert COMPANY_NAME not in dumped
    details = result["get_customer_details.json"]["Data"]
    assert details["CustomerName"] == "Example Customer"
    assert details["CompanyName"] == "Example Ltd"


def test_no_address_field_survives(dumped: str) -> None:
    assert [value for value in ADDRESS.values() if value in dumped] == []


def test_line_names_become_line_n(result: dict[str, Any], dumped: str) -> None:
    assert [name for name in LINE_NAMES if name in dumped] == []
    names = [row["Name"] for row in result["get_line_names.json"]["Data"]]
    assert names == ["Line 1", "Line 2"]
    assert result["get_numbers.json"]["Data"][0]["Parameter_4"] == "Line 1"


def test_no_email_survives(dumped: str) -> None:
    assert [email for email in EMAILS if email in dumped] == []
    assert re.search(r"user\d+@example\.com", dumped)


def test_no_ip_address_survives(dumped: str) -> None:
    assert [ip for ip in IPS if ip in dumped] == []
    assert "192.0.2." in dumped


def test_no_url_survives(dumped: str) -> None:
    assert [url for url in URLS if url in dumped] == []
    assert "https://example.invalid/" in dumped


def test_no_record_id_survives(dumped: str) -> None:
    assert [record_id for record_id in IDS if record_id in dumped] == []


def test_user_agent_and_device_identifier_do_not_survive(dumped: str) -> None:
    assert USER_AGENT not in dumped
    assert DEVICE_MAC not in dumped


def test_no_secret_survives(result: dict[str, Any], dumped: str) -> None:
    assert LINE_PASSWORD not in dumped
    assert "Parameter_1" not in dumped
    assert [value for value in SECRET_VALUES.values() if value in dumped] == []
    assert [pref for pref in SECRET_PREFS if pref in dumped] == []
    assert "8642" not in dumped
    assert "whsec-abcdef" not in dumped


def test_token_is_dropped(result: dict[str, Any], dumped: str) -> None:
    assert TOKEN not in dumped
    assert "token" not in result["get_token.json"]["Data"]


def test_extra_secrets_are_scrubbed() -> None:
    out = anonymise_responses(
        {"error.json": {"Message": "Login portal-user-77 refused"}},
        secrets=("portal-user-77",),
    )
    assert "portal-user-77" not in json.dumps(out)


def test_non_sensitive_values_are_kept(result: dict[str, Any]) -> None:
    prefs = next(iter(result["get_all_pref_summary.json"]["Data"].values()))
    assert prefs["do_not_disturb_status"] == "off"
    assert result["get_customer_details.json"]["Data"]["Status"] == "Active"


def test_input_is_not_mutated() -> None:
    responses = _responses()
    before = json.dumps(responses)
    anonymise_responses(responses)
    assert json.dumps(responses) == before


def test_deterministic_within_and_across_runs(result: dict[str, Any]) -> None:
    assert anonymise_responses(_responses()) == result
    numbers = [row["ServiceName"] for row in result["get_numbers.json"]["Data"]]
    names = [row["ServiceName"] for row in result["get_line_names.json"]["Data"]]
    assert numbers == names
    assert len(set(numbers)) == 2


def _dump(responses: dict[str, Any], **kwargs: Any) -> str:
    return json.dumps(anonymise_responses(responses, **kwargs))


@pytest.mark.parametrize(
    "key",
    [
        "SipPassword",
        "sip_password",
        "WebhookSecret",
        "ApiKey",
        "api_key",
        "AuthToken",
        "remote_access_pin",
        "web_password",
        "PinNumber",
        "Security_Answer",
        "HASH",
        "CHASH",
        "password_hash",
    ],
)
def test_unknown_secret_field_names_are_dropped(key: str) -> None:
    out = _dump({"f.json": {"Data": {key: "Zq7xK9mW", "Note": "echo Zq7xK9mW"}}})
    assert "Zq7xK9mW" not in out
    assert key not in out


def test_secret_pref_in_list_of_pairs_is_dropped() -> None:
    out = _dump(
        {"f.json": {"Data": [["voicemail_pin", "8642"], ["anoncall_reject", "on"]]}}
    )
    assert "8642" not in out
    assert "voicemail_pin" not in out
    assert "anoncall_reject" in out


def test_secret_pref_in_flat_composite_key_is_dropped() -> None:
    out = _dump(
        {
            "f.json": {
                "Data": {
                    f"{NUMBER_A_NATIONAL}_voicemail_pin": "8642",
                    f"{NUMBER_A_NATIONAL}_do_not_disturb_status": "off",
                }
            }
        }
    )
    assert "8642" not in out
    assert "voicemail_pin" not in out
    assert NUMBER_A_NATIONAL not in out
    assert "_do_not_disturb_status" in out


def test_pin_inside_a_number_cannot_split_it() -> None:
    result = anonymise_responses(
        {"f.json": {"Data": {"01184960753": {"voicemail_pin": "9607"}}}}
    )
    keys = list(result["f.json"]["Data"])
    assert re.fullmatch(r"01632960\d{3}", keys[0])
    assert "96075" not in json.dumps(result)


def test_secret_pref_with_dict_value_is_scrubbed_elsewhere() -> None:
    out = _dump(
        {"f.json": {"Data": {"voicemail_pin": {"value": "8642"}, "Note": "pin 8642"}}}
    )
    assert "8642" not in out


def test_integer_in_number_field_is_replaced() -> None:
    result = anonymise_responses({"f.json": {"ServiceName": 1134960847}})
    assert result["f.json"]["ServiceName"] == 1632960000


@pytest.mark.parametrize(
    "raw",
    [
        "+44 (0)113 496 0847",
        "(0113) 496 0847",
        "line_01134960847",
        "Line01134960847",
    ],
)
def test_bracketed_and_prefixed_numbers_in_free_text_are_replaced(raw: str) -> None:
    out = _dump({"f.json": {"Data": {"Note": f"call {raw} now", raw: "1"}}})
    assert "0847" not in out


def test_non_uk_numbers_in_free_text_are_replaced() -> None:
    result = anonymise_responses(
        {
            "f.json": {
                "Mobile": "+12025550147",
                "do_not_disturb_forward": "+12025550183",
                "Note": "forward to 0012025550183",
            }
        }
    )
    out = json.dumps(result)
    assert "5550147" not in out
    assert "5550183" not in out
    assert re.fullmatch(r"\+441632960\d{3}", result["f.json"]["do_not_disturb_forward"])


def test_float_record_id_is_replaced() -> None:
    assert "1760600123" not in _dump({"f.json": {"uniqueid": 1760600123.4455}})


def test_name_parts_used_alone_are_scrubbed() -> None:
    out = _dump(
        {
            "f.json": {
                "CustomerName": "Morwenna Pryce",
                "Greeting": "Hello Morwenna",
                "Sign": "PRYCE",
            }
        }
    )
    assert "morwenna" not in out.lower()
    assert "pryce" not in out.lower()


def test_bare_hostnames_are_scrubbed() -> None:
    out = _dump(
        {
            "f.json": {
                "Server": "pbx.pryce-widgets.test",
                "Note": "see pbx.pryce-widgets.test/status",
            }
        }
    )
    assert "pryce-widgets" not in out


def test_account_code_inside_a_longer_token_is_scrubbed() -> None:
    out = _dump(
        {"f.json": {"Account_Code": ACCOUNT_CODE, "Invoice": f"INV{ACCOUNT_CODE}-01"}}
    )
    assert ACCOUNT_CODE not in out


def test_plan_and_non_address_fields_are_kept() -> None:
    data = {
        "Data": [
            {
                "ServiceType": "Voice",
                "Alias": "Business Unlimited",
                "BillingDescription": "Unlimited UK calls",
                "FlatRate": "9.99",
                "UnitCharge": "0.05",
                "State": "Active",
            }
        ]
    }
    assert anonymise_responses({"plan.json": data}) == {"plan.json": data}


def test_webhook_name_is_kept() -> None:
    result = anonymise_responses(
        {
            "w.json": {
                "Data": [{"name": "Home Assistant", "phonenumber": "441134960847"}]
            }
        }
    )
    assert result["w.json"]["Data"][0]["name"] == "Home Assistant"


def test_unscrubbable_digit_run_fails_closed_without_the_value() -> None:
    with pytest.raises(anonymise.LeakError) as exc:
        anonymise_responses({"f.json": {"Data": {"Reference": "123456789012"}}})
    assert exc.value.paths == ["f.json.Data.Reference"]
    assert "123456789012" not in str(exc.value)


def test_leak_check_flags_learned_values_and_secret_names() -> None:
    anonymiser = anonymise.Anonymiser(secrets=("Zq7xK9mW",))
    anonymiser.anonymise({"CustomerName": "Morwenna Pryce"})
    paths = anonymiser.find_leaks(
        {
            "a.json": {
                "x": "Zq7xK9mW",
                "y": "hi morwenna",
                "ok": "fine",
                "Pin": "1",
                "z": "voicemail_pin",
                "Message": "Invalid token",
            }
        }
    )
    assert paths == ["a.json.x", "a.json.y", "a.json.<key 3>", "a.json.z"]


def test_short_numeric_secret_does_not_touch_decimals() -> None:
    result = anonymise_responses(
        {
            "p.json": {"voicemail_pin": "0000", "Note": "pin 0000"},
            "b.json": {"Rate": "10.0000"},
        }
    )
    assert result["b.json"]["Rate"] == "10.0000"
    assert result["p.json"]["Note"] == "pin [REDACTED]"


def test_address_field_without_an_anchor_is_replaced() -> None:
    out = _dump(
        {"f.json": {"Data": {"BillingAddress": "42 Larkspur Avenue, Ambridge"}}}
    )
    assert "Larkspur" not in out
    assert "Ambridge" not in out


def test_address_field_next_to_a_postcode_is_replaced() -> None:
    out = _dump(
        {
            "f.json": {
                "Data": {
                    "Postcode": "ZZ1 1ZZ",
                    "BillingAddress": "42 Larkspur Avenue, Ambridge",
                    "Note": "Delivered to Larkspur Avenue",
                }
            }
        }
    )
    assert "Larkspur" not in out
    assert "ZZ1 1ZZ" not in out


def test_prefixed_postcode_field_is_replaced() -> None:
    result = anonymise_responses({"f.json": {"Data": {"DeliveryPostCode": "ZZ1 1ZZ"}}})
    assert result["f.json"]["Data"]["DeliveryPostCode"] == "ZZ99 9ZZ"


def test_postcode_in_free_text_fails_closed_without_the_value() -> None:
    with pytest.raises(anonymise.LeakError) as exc:
        anonymise_responses({"f.json": {"Data": {"Note": "Ship to ZZ1 1ZZ"}}})
    assert exc.value.paths == ["f.json.Data.Note"]
    assert "ZZ1" not in str(exc.value)


@pytest.mark.parametrize(
    "data",
    [
        {"Data": {"status": "voicemail_pin", "value": "8642"}},
        {"Data": {"Message": ["voicemail_pin", "8642"]}},
        {"Message": ["voicemail_pin", "8642"]},
    ],
)
def test_free_text_exemption_only_covers_the_envelope(data: dict[str, Any]) -> None:
    out = _dump({"f.json": data})
    assert "8642" not in out
    assert "voicemail_pin" not in out


def test_envelope_message_naming_a_token_is_kept() -> None:
    data = {"Result": "-1", "Status": "Error", "Message": "Token_Invalid", "Data": ""}
    assert anonymise_responses({"e.json": data}) == {"e.json": data}


def test_leak_check_flags_nested_status_naming_a_secret() -> None:
    anonymiser = anonymise.Anonymiser()
    paths = anonymiser.find_leaks(
        {
            "a.json": {
                "Result": "-1",
                "Message": "Token_Invalid",
                "Data": {"status": "voicemail_pin"},
            }
        }
    )
    assert paths == ["a.json.Data.status"]


def test_nested_secret_header_value_is_replaced() -> None:
    result = anonymise_responses(
        {"w.json": {"Data": [{"name": "HA", "secretheader": "X-Tel2-Secret"}]}}
    )
    assert result["w.json"]["Data"] == [
        {"name": "HA", "secretheader": "X-Example-Header"}
    ]


@pytest.mark.parametrize("key", ["AccountName", "TradingName", "BillingName"])
def test_name_fields_outside_line_rows_are_people(key: str) -> None:
    out = _dump({"f.json": {"Data": {key: "Morwenna Pryce", "Note": "Hi Morwenna"}}})
    assert "morwenna" not in out.lower()
    assert "pryce" not in out.lower()


def test_keys_are_not_renamed_by_learned_names() -> None:
    result = anonymise_responses(
        {
            "n.json": {"Data": [{"ServiceName": NUMBER_A_NATIONAL, "Name": "Status"}]},
            "o.json": {"Data": {"Status": "Active", "status": "1"}},
        }
    )
    assert result["o.json"]["Data"] == {"Status": "Active", "status": "1"}
    assert result["n.json"]["Data"][0]["Name"] == "Line 1"


def test_anchored_ambiguous_address_value_is_not_scrubbed_elsewhere() -> None:
    result = anonymise_responses(
        {
            "c.json": {"Data": {"BillingAddress": "7 Mill Lane", "State": "Active"}},
            "o.json": {"Data": {"Status": "Active"}},
        }
    )
    assert result["c.json"]["Data"]["State"] == "Example County"
    assert result["o.json"]["Data"]["Status"] == "Active"


@pytest.mark.parametrize(
    "data",
    [
        {"Balance": "-4321.98", "AvailableBalance": "8765.43"},
        {"Balance": -4321.98, "AvailableBalance": 8765.43},
        {"Charge": "0.83", "ChargeExcl": "0.69"},
    ],
)
def test_money_values_are_replaced(data: dict[str, Any]) -> None:
    out = _dump({"f.json": {"Data": data}})
    for value in data.values():
        assert str(value) not in out
