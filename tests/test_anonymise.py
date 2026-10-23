"""Tests for the fixture anonymiser."""

import json
import re
from typing import Any

import pytest

from custom_components.tel2.const import SECRET_PREFS
from scripts import anonymise
from scripts.anonymise import anonymise_responses
from tests.fixture_loader import CAPTURED_DIR

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


def test_webhook_names_are_replaced_and_ours_stays_recognisable() -> None:
    result = anonymise_responses(
        {
            "w.json": {
                "Data": [
                    {"id": "9", "name": "Jane Smith mobile app", "url": URLS[0]},
                    {"id": "10", "name": "homeassistant-tel2-01K7EXAMPLE", "url": ""},
                ]
            }
        }
    )
    rows = result["w.json"]["Data"]
    assert rows[0]["name"] == "Example webhook"
    assert rows[1]["name"] == "homeassistant-tel2-example-entry"
    assert "Smith" not in json.dumps(result)


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
                "Result": "0",
                "x": "Zq7xK9mW",
                "y": "hi morwenna",
                "ok": "fine",
                "Pin": "1",
                "z": "voicemail_pin",
                "Message": "Invalid token",
            }
        }
    )
    assert paths == ["a.json.x", "a.json.y", "a.json.<key 4>", "a.json.z"]


def test_short_numeric_secret_does_not_touch_decimals() -> None:
    result = anonymise_responses(
        {
            "p.json": {"Result": "0", "voicemail_pin": "0000", "Message": "pin 0000"},
            "b.json": {"Rate": "10.0000"},
        }
    )
    assert result["b.json"]["Rate"] == "10.0000"
    assert result["p.json"]["Message"] == "pin [REDACTED]"


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
        anonymise_responses({"f.json": {"Result": "0", "Message": "Ship to ZZ1 1ZZ"}})
    assert exc.value.paths == ["f.json.Message"]
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
        {"name": "Example webhook", "secretheader": "X-Example-Header"}
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Number14021InclMins", "Number00000InclMins"),
        ("T2CallingPlan4190", "T2CallingPlan0000"),
        ("4190Plan", "0000Plan"),
        ("T2CallingPlan419", "T2CallingPlan419"),
        ("Plan 14021 minutes", "Plan 14021 minutes"),
    ],
)
def test_embedded_digit_runs_are_zeroed(raw: str, expected: str) -> None:
    result = anonymise_responses({"p.json": {"Data": [{"Alias": raw}]}})
    assert result["p.json"]["Data"][0]["Alias"] == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16T09:15:02Z", "2026-01-01T09:15:02Z"),
        ("20261016T091502Z", "20260101T091502Z"),
        ("2026-10-16T09:15:02.123456+01:00", "2026-01-01T09:15:02.123456+01:00"),
        ("2025-03-04 10:11:12", "2026-01-01 10:11:12"),
        ("17/05/1980", "01/01/2026"),
        ("1980-05-17", "2026-01-01"),
        ("Line441632960001", "Line441632960001"),
        ("Ext01632960001", "Ext01632960001"),
    ],
)
def test_timestamps_keep_their_time_and_get_a_fixed_date(
    raw: str, expected: str
) -> None:
    result = anonymise_responses({"p.json": {"Data": {"Note": raw}}})
    assert result["p.json"]["Data"]["Note"] == expected


def test_leak_check_flags_embedded_digit_runs() -> None:
    paths = anonymise.Anonymiser().find_leaks(
        {
            "a.json": {
                "a": "Number14021InclMins",
                "b": "T2CallingPlan419",
                "c": "20260101T091502Z",
                "d": "Line441632960001",
                "e": "4190Plan",
            }
        }
    )
    assert paths == ["a.json.a", "a.json.e"]


@pytest.mark.parametrize("key", ["stamp", "created", "date_created", "DateCreated"])
def test_set_up_timestamps_get_a_fixed_value(key: str) -> None:
    result = anonymise_responses({"p.json": {"Data": {key: "2025-03-04 05:06:07"}}})
    assert result["p.json"]["Data"][key] == anonymise.TIMESTAMP_PLACEHOLDER
    assert anonymise.TIMESTAMP_PLACEHOLDER == "2026-01-01 00:00:00"


@pytest.mark.parametrize("key", ["DOB", "DateOfBirth", "date_of_birth", "BirthDate"])
def test_birth_dates_are_replaced(key: str) -> None:
    result = anonymise_responses({"c.json": {"Data": {key: "1980-05-17"}}})
    assert result["c.json"]["Data"][key] == anonymise.REDACTED


def test_additional_details_are_replaced_unless_empty() -> None:
    result = anonymise_responses(
        {
            "a.json": {"Data": {"AdditionalDetails": '[{"spouse":"Jane Smith"}]'}},
            "b.json": {"Data": {"AdditionalDetails": '[{"spouse":"Jane"}]'}},
            "c.json": {"Data": {"AdditionalDetails": "[{}]"}},
            "d.json": {"Data": {"AdditionalDetails": "Jane"}},
            "e.json": {"Data": {"AdditionalDetails": '["", null, "Jane"]'}},
        }
    )
    assert "Jane" not in json.dumps(result)
    assert result["a.json"]["Data"]["AdditionalDetails"] == anonymise.REDACTED
    assert result["c.json"]["Data"]["AdditionalDetails"] == "[{}]"
    assert result["e.json"]["Data"]["AdditionalDetails"] == anonymise.REDACTED


def test_json_in_a_string_is_anonymised_and_kept_compact() -> None:
    result = anonymise_responses(
        {
            "s.json": {
                "Data": {
                    "Settings": json.dumps(
                        {
                            "spouse": "Jane Smith",
                            "forward": NUMBER_B_NATIONAL,
                            "enabled": "1",
                        }
                    )
                }
            }
        }
    )
    settings = result["s.json"]["Data"]["Settings"]
    assert " " not in settings
    parsed = json.loads(settings)
    assert parsed["spouse"] == anonymise.REDACTED
    assert re.fullmatch(r"07700900\d{3}|01632960\d{3}", parsed["forward"])
    assert parsed["enabled"] == "1"


def test_json_in_a_string_naming_a_secret_is_dropped() -> None:
    result = anonymise_responses(
        {
            "s.json": {
                "Data": {
                    "Extra": '{"pref":"voicemail_pin","value":"8642"}',
                    "Status": "1",
                }
            }
        }
    )
    assert result["s.json"]["Data"] == {"Status": "1"}
    assert "8642" not in json.dumps(result)


def test_number_billing_group_and_profile_are_replaced() -> None:
    result = anonymise_responses(
        {
            "n.json": {
                "Data": [
                    {
                        "ServiceName": NUMBER_B_NATIONAL,
                        "BillingGroup": "Smith Family",
                        "Profile": "Bob Jones",
                    },
                    {
                        "ServiceName": NUMBER_A_NATIONAL,
                        "BillingGroup": "",
                        "Profile": "",
                    },
                ]
            }
        }
    )
    rows = result["n.json"]["Data"]
    assert (rows[0]["BillingGroup"], rows[0]["Profile"]) == (
        "Example Group",
        "Example Profile",
    )
    assert (rows[1]["BillingGroup"], rows[1]["Profile"]) == ("", "")


def test_mail_fields_keep_flags_and_addresses_but_not_free_text() -> None:
    result = anonymise_responses(
        {
            "p.json": {
                "Data": [
                    {
                        "pref_uid": NUMBER_A_INTERNATIONAL,
                        "voicemail_emailcopy": "jane",
                        "voicemail_disable": "0",
                        "sendvoicemail": "no",
                        "email": f"{NUMBER_A_INTERNATIONAL}@voip",
                        "mailbox": NUMBER_A_INTERNATIONAL,
                        "missed_call_email": EMAILS[0],
                        "copy_emails": f"{EMAILS[0]}, jane",
                    }
                ]
            }
        }
    )
    row = result["p.json"]["Data"][0]
    assert row["voicemail_emailcopy"] == anonymise.REDACTED
    assert row["voicemail_disable"] == "0"
    assert row["sendvoicemail"] == "no"
    assert re.fullmatch(r"441632960\d{3}@voip", row["email"])
    assert re.fullmatch(r"441632960\d{3}", row["mailbox"])
    assert re.fullmatch(r"user\d+@example\.com", row["missed_call_email"])
    assert row["copy_emails"] == anonymise.REDACTED
    assert "jane" not in json.dumps(result)


@pytest.mark.parametrize("separator", [", ", "; ", " "])
def test_mail_field_address_lists_replace_each_address_and_keep_the_separator(
    separator: str,
) -> None:
    result = anonymise_responses(
        {"p.json": {"Data": {"copy_emails": separator.join(EMAILS)}}}
    )
    copy_emails = result["p.json"]["Data"]["copy_emails"]
    match = re.fullmatch(
        rf"user(\d+)@example\.com{re.escape(separator)}user(\d+)@example\.com",
        copy_emails,
    )
    assert match is not None
    assert match[1] != match[2]


def test_leak_check_flags_free_text_beside_placeholder_addresses_in_mail_fields() -> (
    None
):
    paths = anonymise.Anonymiser().find_leaks(
        {"a.json": {"copy_emails": "user1@example.com, ask Jane"}}
    )
    assert paths == ["a.json.copy_emails"]


def test_leak_check_flags_placeholder_address_lists_outside_mail_fields() -> None:
    paths = anonymise.Anonymiser().find_leaks(
        {"a.json": {"Note": "user1@example.com, user2@example.com"}}
    )
    assert paths == ["a.json.Note"]


def test_free_text_outside_known_fields_is_replaced() -> None:
    result = anonymise_responses(
        {
            "f.json": {
                "Data": {
                    "Note": "Jane Smith",
                    "Greeting": "Hello there",
                    "BillingDescription": "Free Trial Line - 60 mins calling to UK",
                    "Country": "United Kingdom",
                    "Forward": "0161 496 0999",
                    "Seen": "2025-03-04 10:11:12",
                    "tz": "london",
                }
            }
        }
    )
    data = result["f.json"]["Data"]
    assert data["Note"] == anonymise.REDACTED
    assert data["Greeting"] == anonymise.REDACTED
    assert data["BillingDescription"] == "Free Trial Line - 60 mins calling to UK"
    assert data["Country"] == "United Kingdom"
    assert re.fullmatch(r"01632960\d{3}", data["Forward"])
    assert data["Seen"] == "2026-01-01 10:11:12"
    assert data["tz"] == "london"


def test_data_row_message_and_status_free_text_is_replaced() -> None:
    result = anonymise_responses(
        {
            "f.json": {
                "Result": "1",
                "Status": "Success",
                "Message": "Method Executed Successfully.",
                "Data": [
                    {
                        "Message": "Missed call from Jane Smith",
                        "Status": "Called back by Jane Smith",
                    }
                ],
            }
        }
    )
    envelope = result["f.json"]
    assert envelope["Message"] == "Method Executed Successfully."
    assert envelope["Data"] == [
        {"Message": anonymise.REDACTED, "Status": anonymise.REDACTED}
    ]


def test_leak_check_flags_dates_except_the_placeholders() -> None:
    paths = anonymise.Anonymiser().find_leaks(
        {
            "a.json": {
                "a": "1980-05-17",
                "b": "17/05/1980",
                "c": anonymise.TIMESTAMP_PLACEHOLDER,
                "d": "01/01/2026",
                "e": "2026-01-01T09:15:02Z",
                "f": "19800517T101112",
            }
        }
    )
    assert paths == ["a.json.a", "a.json.b", "a.json.f"]


def test_leak_check_flags_free_text_outside_known_fields() -> None:
    paths = anonymise.Anonymiser().find_leaks(
        {
            "a.json": {
                "Result": "0",
                "Note": "Jane Smith",
                "Message": "Invalid token",
                "CustomerName": "Example Customer",
                "Name": "Line 2",
                "Seen": "2026-01-01 09:15:02",
                "Nested": '{"Note":"Jane Smith","ok":"1"}',
                "List": ["fine", "Bob Jones"],
            }
        }
    )
    assert paths == ["a.json.Note", "a.json.Nested<json>.Note", "a.json.List[1]"]


def test_leak_check_flags_free_text_in_data_row_message_and_status() -> None:
    paths = anonymise.Anonymiser().find_leaks(
        {
            "a.json": {
                "Result": "1",
                "Message": "Method Executed Successfully.",
                "Data": [
                    {
                        "Message": "Missed call from Jane Smith",
                        "Status": "Called back by Jane Smith",
                    }
                ],
            }
        }
    )
    assert paths == ["a.json.Data[0].Message", "a.json.Data[0].Status"]


def test_committed_captured_fixtures_are_unchanged_by_a_rerun() -> None:
    files = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CAPTURED_DIR.glob("*.json"))
        if path.name != "index.json"
    }
    assert files
    assert anonymise_responses(files) == files


def _probe(data: Any, message: str, **kwargs: Any) -> str:
    result = anonymise_responses(
        {"d.json": {"Data": data}, "e.json": {"Result": "0", "Message": message}},
        **kwargs,
    )
    return str(result["e.json"]["Message"])


def test_more_numbers_than_the_drama_range_holds_fails() -> None:
    anonymiser = anonymise.Anonymiser()
    placeholders = {anonymiser._phone(f"07700900{index:03d}") for index in range(1000)}
    assert placeholders == {f"01632960{index:03d}" for index in range(1000)}
    with pytest.raises(ValueError, match="drama range"):
        anonymiser._phone("+33 1 99 00 00 01")


def test_nine_digit_number_in_a_number_field_is_replaced() -> None:
    result = anonymise_responses({"f.json": {"Data": {"Mobile": "07700900"}}})
    assert re.fullmatch(r"01632960\d{3}", result["f.json"]["Data"]["Mobile"])


def test_three_character_secret_is_scrubbed_but_shorter_values_are_not() -> None:
    assert _probe({}, "code x7q ok", secrets=("x7q", "ok")) == "code [REDACTED] ok"


def test_extra_secrets_are_matched_without_surrounding_whitespace() -> None:
    assert _probe({}, "code Zq7xK9mW", secrets=(" Zq7xK9mW\n",)) == "code [REDACTED]"


def test_long_extra_secret_is_scrubbed_inside_a_longer_token() -> None:
    assert _probe({}, "keyZq7xK9mWend", secrets=("Zq7xK9mW",)) == "key[REDACTED]end"


def test_extra_secret_in_a_key_is_scrubbed() -> None:
    result = anonymise_responses(
        {"f.json": {"Data": {"Zq7xK9mW": "1"}}}, secrets=("Zq7xK9mW",)
    )
    assert result["f.json"]["Data"] == {"[REDACTED]": "1"}


def test_long_secret_value_is_scrubbed_inside_a_longer_token() -> None:
    message = _probe({"voicemail_pin": "Zq7xK9mW"}, "keyZq7xK9mWend")
    assert message == "key[REDACTED]end"


def test_secret_value_in_a_key_is_scrubbed() -> None:
    result = anonymise_responses(
        {"f.json": {"Data": {"voicemail_pin": "Zq7xK9mW", "Zq7xK9mW": "1"}}}
    )
    assert result["f.json"]["Data"] == {"[REDACTED]": "1"}


def test_secret_values_shorter_than_four_characters_are_not_scrubbed_elsewhere() -> (
    None
):
    assert _probe({"voicemail_pin": "864"}, "dial 864") == "dial 864"


@pytest.mark.parametrize(
    "value",
    ["true", "false", "none", "null", "enabled", "disabled", "active", "inactive"],
)
def test_trivial_secret_values_are_not_scrubbed_elsewhere(value: str) -> None:
    assert _probe({"voicemail_pin": value}, f"now {value}") == f"now {value}"


def test_secret_values_in_lists_and_dicts_are_scrubbed_elsewhere() -> None:
    message = _probe(
        {"voicemail_pin": ["8642", {"code": "1357"}]}, "pins 8642 and 1357"
    )
    assert message == "pins [REDACTED] and [REDACTED]"


def test_flat_list_drops_only_the_secret_pair_and_remembers_it() -> None:
    result = anonymise_responses(
        {
            "f.json": {"Data": ["voicemail_pin", "8642", "anoncall_reject", "on"]},
            "e.json": {"Result": "0", "Message": "set voicemail_pin to 8642"},
        }
    )
    assert result["f.json"]["Data"] == ["anoncall_reject", "on"]
    assert result["e.json"]["Message"] == "set [REDACTED] to [REDACTED]"


@pytest.mark.parametrize(
    "key",
    ["db_passwd", "MySecurityAnswer", "PINCode", "UserPwd", "sip_pwd", "masterpin"],
)
def test_more_secret_field_names_are_dropped(key: str) -> None:
    out = _dump({"f.json": {"Data": {key: "Zq7xK9mW"}}})
    assert "Zq7xK9mW" not in out
    assert key not in out


@pytest.mark.parametrize(
    "key", ["balance", "availablebalance", "charge", "chargeexcl", "amount", "credit"]
)
def test_money_fields_get_the_placeholder(key: str) -> None:
    result = anonymise_responses(
        {"s.json": {"Data": {key: "4321.98"}}, "n.json": {"Data": {key: -4321.98}}}
    )
    assert result["s.json"]["Data"][key] == "10.00"
    assert result["n.json"]["Data"][key] == 10.0


def test_envelope_status_and_action_naming_a_token_are_kept() -> None:
    data = {
        "Result": "-1",
        "Status": "Token_Expired",
        "Action": "Get-Token",
        "Message": "Token_Invalid",
        "Data": "",
    }
    assert anonymise_responses({"e.json": data}) == {"e.json": data}


def test_envelope_status_may_hold_free_text() -> None:
    data = {"Result": "1", "Status": "Partially Successful", "Data": ""}
    assert anonymise_responses({"e.json": data}) == {"e.json": data}


def test_webhook_row_with_only_a_secret_token_is_a_webhook_row() -> None:
    result = anonymise_responses(
        {"w.json": {"Data": [{"name": "Jane mobile", "secrettoken": "whsec-abcdef"}]}}
    )
    assert result["w.json"]["Data"] == [{"name": "Example webhook"}]


@pytest.mark.parametrize(
    "key", ["accountcode", "accountnumber", "accountno", "account", "customercode"]
)
def test_account_fields_get_the_placeholder_and_are_scrubbed_elsewhere(
    key: str,
) -> None:
    result = anonymise_responses(
        {
            "a.json": {"Data": {key: "48213977"}},
            "e.json": {"Result": "0", "Message": "Account 48213977 suspended"},
        }
    )
    assert result["a.json"]["Data"][key] == "00000000"
    assert result["e.json"]["Message"] == "Account 00000000 suspended"


def test_six_character_account_code_is_scrubbed_after_a_decimal_point() -> None:
    assert _probe({"Account_Code": "483921"}, "rate 1.483921") == "rate 1.00000000"


def test_short_account_code_does_not_touch_decimals() -> None:
    assert _probe({"Account_Code": "4821"}, "rate 10.48215") == "rate 10.48215"


@pytest.mark.parametrize(
    ("key", "placeholder"),
    [
        ("customername", "Example Customer"),
        ("contactname", "Example Customer"),
        ("fullname", "Example Customer"),
        ("companyname", "Example Ltd"),
        ("firstname", "Example"),
        ("lastname", "Customer"),
    ],
)
def test_person_fields_get_their_placeholder(key: str, placeholder: str) -> None:
    result = anonymise_responses({"p.json": {"Data": {key: "Jane Exampleton"}}})
    assert result["p.json"]["Data"][key] == placeholder


def test_three_letter_name_parts_are_scrubbed_but_not_inside_longer_words() -> None:
    message = _probe({"CustomerName": "Ann Exampleton"}, "Hi Ann, Annual Exampleton")
    assert message == "Hi Example, Annual Example"


def test_full_name_is_scrubbed_even_when_its_parts_are_glued_to_other_text() -> None:
    message = _probe({"CustomerName": "Jane Exampleton"}, "ToJane Exampleton2")
    assert message == "ToExample Customer2"


@pytest.mark.parametrize(
    "word", ["the", "and", "ltd", "limited", "plc", "llp", "inc", "company", "mrs"]
)
def test_name_stopwords_are_not_scrubbed_elsewhere(word: str) -> None:
    message = _probe({"CompanyName": f"Widgetco {word}"}, f"one {word} two")
    assert message == f"one {word} two"


def test_shorter_learned_values_do_not_split_longer_ones() -> None:
    message = _probe(
        [{"FirstName": "Jane"}, {"CustomerName": "Jane Exampleton"}],
        "Hi Jane Exampleton",
    )
    assert message == "Hi Example Customer"


@pytest.mark.parametrize("key", ["login", "username"])
def test_login_fields_get_the_placeholder_and_are_scrubbed_elsewhere(
    key: str,
) -> None:
    result = anonymise_responses(
        {
            "a.json": {"Data": {key: "portal-user-77"}},
            "e.json": {"Result": "0", "Message": "Login portal-user-77 refused"},
        }
    )
    assert result["a.json"]["Data"][key] == "example-login"
    assert result["e.json"]["Message"] == "Login example-login refused"


@pytest.mark.parametrize("key", ["useragent", "agent", "ua"])
def test_user_agent_fields_get_the_placeholder(key: str) -> None:
    result = anonymise_responses({"r.json": {"Data": {key: "Yealink SIP-T46U"}}})
    assert result["r.json"]["Data"][key] == "example-user-agent-1"


@pytest.mark.parametrize(
    "key",
    [
        "mac",
        "macaddress",
        "deviceid",
        "device",
        "serial",
        "serialnumber",
        "imei",
        "contact",
        "sipcontact",
    ],
)
def test_device_fields_get_the_placeholder(key: str) -> None:
    result = anonymise_responses({"r.json": {"Data": {key: "yealinkpbx"}}})
    assert result["r.json"]["Data"][key] == "device-1"


def test_device_value_is_scrubbed_inside_a_longer_token() -> None:
    message = _probe({"DeviceID": "yealinkpbx"}, "on yealinkpbxlounge")
    assert message == "on device-1lounge"


def test_mac_address_in_free_text_is_replaced() -> None:
    assert _probe({}, "phone 80:5e:c0:12:34:56 seen") == "phone device-1 seen"


@pytest.mark.parametrize(
    "key",
    [
        "id",
        "uniqueid",
        "userid",
        "editid",
        "originid",
        "webhookid",
        "recordid",
        "smsid",
        "cdrid",
        "customerid",
        "accountid",
        "subscriberid",
        "phoneid",
        "lineid",
        "groupid",
        "callid",
        "sessionid",
        "profileid",
        "uuid",
        "guid",
        "invoice_id",
        "InvoiceID",
        "InvoiceId",
    ],
)
def test_id_fields_are_replaced(key: str) -> None:
    result = anonymise_responses({"r.json": {"Data": {key: "webhook-7731"}}})
    assert result["r.json"]["Data"][key] == "id-1"


def test_numeric_ids_are_numbered_in_order_and_zero_is_kept() -> None:
    result = anonymise_responses(
        {"r.json": {"Data": [{"id": 9917345}, {"id": 1}, {"id": 0}, {"id": "0"}]}}
    )
    assert result["r.json"]["Data"] == [{"id": 1}, {"id": 2}, {"id": 0}, {"id": "0"}]


def test_ids_are_scrubbed_elsewhere_and_short_ones_are_left_alone() -> None:
    result = anonymise_responses(
        {
            "r.json": {"Data": [{"id": "4821"}, {"id": "7"}]},
            "s.json": {"Data": {"Ref": "4821", "Count": "7", "7": "x", "4821": "y"}},
            "e.json": {"Result": "0", "Message": "ref 4821 and 7"},
        }
    )
    assert result["s.json"]["Data"] == {"Ref": "1", "Count": "7", "7": "x", "1": "y"}
    assert result["e.json"]["Message"] == "ref 1 and 7"


def test_id_in_a_key_is_scrubbed() -> None:
    result = anonymise_responses(
        {"r.json": {"Data": {"userid": "9917345", "row_9917345": "1"}}}
    )
    assert result["r.json"]["Data"] == {"userid": "1", "row_1": "1"}


@pytest.mark.parametrize(
    "key",
    [
        "servicename",
        "aparty",
        "bparty",
        "from",
        "to",
        "line",
        "cli",
        "callerid",
        "destination",
        "BackupNumber",
        "HomePhone",
        "WorkMobile",
        "OfficeFax",
        "Landline",
    ],
)
def test_number_fields_replace_bare_international_numbers(key: str) -> None:
    result = anonymise_responses({"c.json": {"Data": {key: "33199000001"}}})
    assert result["c.json"]["Data"][key] == "441632960000"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (770090012, 1632960000),
        (447700900, 441632960000),
        (33199000001, 441632960000),
        (447700900344, 441632960000),
        (7700900344.0, 1632960000.0),
        (201, 201),
    ],
)
def test_integer_numbers_in_number_fields(raw: float, expected: float) -> None:
    result = anonymise_responses({"c.json": {"Data": {"Mobile": raw}}})
    assert result["c.json"]["Data"]["Mobile"] == expected


def test_booleans_and_fractional_numbers_are_kept() -> None:
    data = {"Data": {"Balance": False, "id": True, "Rate": 0.05, "Mobile": 0.5}}
    assert anonymise_responses({"b.json": data}) == {"b.json": data}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+447700900344", "+441632960000"),
        ("00447700900344", "00441632960000"),
        ("(07700) 900344", "01632960000"),
        ("447700900344", "441632960000"),
    ],
)
def test_number_prefix_style_is_kept(raw: str, expected: str) -> None:
    result = anonymise_responses({"c.json": {"Data": {"Mobile": raw}}})
    assert result["c.json"]["Data"]["Mobile"] == expected


def test_bare_national_digits_are_scrubbed_inside_a_token() -> None:
    message = _probe({"Mobile": "+447700900344"}, "ref7700900344 or 7700900344")
    assert message == "ref1632960000 or 1632960000"


def test_invalid_international_looking_number_is_left_alone() -> None:
    assert _probe({}, "dial +0 1234 5678") == "dial +0 1234 5678"


def test_international_number_in_a_key_is_replaced() -> None:
    result = anonymise_responses({"s.json": {"Data": {"+33 1 99 00 00 01": "1"}}})
    assert result["s.json"]["Data"] == {"+441632960000": "1"}


@pytest.mark.parametrize("key", ["linenumber", "line", "servicename"])
def test_line_rows_name_the_line(key: str) -> None:
    result = anonymise_responses(
        {
            "n.json": {
                "Data": [
                    {key: "07700900344", "Name": "Office", "Alias": "Den"},
                    {key: "07700900345", "DisplayName": "Study"},
                ]
            }
        }
    )
    assert result["n.json"]["Data"] == [
        {key: "01632960000", "Name": "Line 1", "Alias": "Line 2"},
        {key: "01632960001", "DisplayName": "Line 3"},
    ]


def test_line_name_fields_name_the_line_outside_line_rows() -> None:
    result = anonymise_responses({"n.json": {"Data": {"LineName": "Office"}}})
    assert result["n.json"]["Data"] == {"LineName": "Line 1"}


def test_line_names_are_scrubbed_elsewhere_unless_generic() -> None:
    message = _probe(
        [{"Parameter_4": "Office Desk"}, {"Parameter_4": "Line 7"}],
        "call Office Desk or Line 7",
    )
    assert message == "call Line 1 or Line 7"


def test_plain_name_outside_line_and_webhook_rows_is_kept() -> None:
    result = anonymise_responses({"n.json": {"Data": {"Name": "Office"}}})
    assert result["n.json"]["Data"] == {"Name": "Office"}


@pytest.mark.parametrize(
    "key",
    [
        "servicename",
        "prefname",
        "preferencename",
        "filename",
        "hostname",
        "typename",
        "fieldname",
        "parametername",
        "settingname",
        "headername",
        "voicename",
        "planname",
        "productname",
        "bundlename",
        "packagename",
    ],
)
def test_technical_name_fields_are_kept(key: str) -> None:
    result = anonymise_responses({"t.json": {"Data": {key: "Voice"}}})
    assert result["t.json"]["Data"] == {key: "Voice"}


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("EmailAddress", "jane@mail.test", "user1@example.com"),
        ("IPAddress", "203.0.113.160", "192.0.2.1"),
        ("IPv6Address", "2001:db8::1", "192.0.2.1"),
        ("BtMacAddress", "80:5e:c0:12:34:56", "device-1"),
        ("WebAddress", "www.widgetco.test", "host1.example.invalid"),
        ("UrlAddress", "https://widgetco.test/a", "https://example.invalid/1"),
    ],
)
def test_contact_addresses_are_not_postal_addresses(
    key: str, raw: str, expected: str
) -> None:
    result = anonymise_responses({"c.json": {"Data": {key: raw, "Status": "Active"}}})
    assert result["c.json"]["Data"] == {key: expected, "Status": "Active"}


@pytest.mark.parametrize(
    ("key", "placeholder"),
    [
        ("building", "Example House"),
        ("buildingname", "Example House"),
        ("buildingnumber", "1"),
        ("streetnumber", "1"),
        ("housenumber", "1"),
        ("city", "Exampletown"),
        ("town", "Exampletown"),
        ("postcode", "ZZ99 9ZZ"),
        ("postalcode", "ZZ99 9ZZ"),
        ("zipcode", "ZZ99 9ZZ"),
        ("homeaddress", "Example Street"),
        ("streetname", "Example Street"),
        ("suburb", "Example Suburb"),
    ],
)
def test_address_fields_get_their_placeholder(key: str, placeholder: str) -> None:
    result = anonymise_responses({"c.json": {"Data": {key: "Widgetville"}}})
    assert result["c.json"]["Data"] == {key: placeholder}


@pytest.mark.parametrize(
    ("key", "placeholder"),
    [
        ("flat", "Flat 1"),
        ("flatnumber", "Flat 1"),
        ("unit", "Flat 1"),
        ("unitnumber", "Flat 1"),
        ("apartment", "Flat 1"),
        ("district", "Example Suburb"),
        ("locality", "Example Suburb"),
        ("state", "Example County"),
        ("county", "Example County"),
        ("region", "Example County"),
        ("province", "Example County"),
        ("zip", "ZZ99 9ZZ"),
    ],
)
def test_ambiguous_address_fields_need_an_address_in_the_record(
    key: str, placeholder: str
) -> None:
    result = anonymise_responses(
        {
            "a.json": {"Data": {"Street": "Widget Road", key: "Widgetville"}},
            "b.json": {"Data": {key: "Widgetville"}},
        }
    )
    assert result["a.json"]["Data"] == {"Street": "Example Street", key: placeholder}
    assert result["b.json"]["Data"] == {key: "Widgetville"}


def test_address_parts_are_scrubbed_elsewhere() -> None:
    message = _probe(
        {"BillingAddress": "42 Widget Road, Widgetville"},
        "near Widget Road in Widgetville",
    )
    assert message == "near Example Street in Example Street"


def test_learned_words_do_not_rewrite_longer_words() -> None:
    message = _probe(
        {
            "Login": "jane",
            "BillingGroup": "Oak",
            "BillingAddress": "7 Mill, Lane",
            "Parameter_4": "Den",
            "id": "abcd",
        },
        "janet Oakley Millbrook Lanes Denver abcde",
    )
    assert message == "janet Oakley Millbrook Lanes Denver abcde"


def test_url_is_scrubbed_inside_a_longer_token() -> None:
    message = _probe(
        {"RecordingURL": "https://cdn.widgetco.test/rec/998877.mp3"},
        "see 9https://cdn.widgetco.test/rec/998877.mp3",
    )
    assert message == "see 9https://example.invalid/1"


def test_placeholder_hosts_are_kept_and_other_hosts_numbered() -> None:
    message = _probe(
        {}, "see host7.example.invalid, www.example.com and pbx.widgetco.test"
    )
    assert (
        message
        == "see host7.example.invalid, www.example.com and host1.example.invalid"
    )


def test_mail_field_keeps_a_list_of_safe_addresses() -> None:
    result = anonymise_responses(
        {"m.json": {"Data": {"copy_emails": "a@mail.test,b@mail.test"}}}
    )
    assert result["m.json"]["Data"] == {
        "copy_emails": "user1@example.com,user2@example.com"
    }


def test_list_items_get_no_record_context() -> None:
    data = {
        "Name": ["Office"],
        "State": ["Active"],
        "Message": ["Called back by Jane Exampleton"],
    }
    result = anonymise_responses({"l.json": {"Result": "1", "Data": data}})
    assert result["l.json"]["Data"] == {
        "Name": ["Office"],
        "State": ["Active"],
        "Message": ["[REDACTED]"],
    }


CALLED_BACK = {"Result": "1", "Message": "Called back by Jane Exampleton"}


@pytest.mark.parametrize(
    "data",
    [
        {"Data": {"Row": CALLED_BACK}},
        {"Data": [CALLED_BACK]},
        {"Extra": json.dumps(CALLED_BACK)},
    ],
)
def test_only_the_top_two_levels_can_be_an_envelope(data: dict[str, Any]) -> None:
    out = _dump({"f.json": data})
    assert "Jane" not in out
    assert "[REDACTED]" in out


def test_top_level_list_and_json_string_files_can_hold_envelopes() -> None:
    envelope = {"Result": "1", "Message": "Method Executed Successfully."}
    files = {"a.json": [envelope], "b.json": json.dumps(envelope)}
    result = anonymise_responses(files)
    assert result["a.json"] == [envelope]
    assert json.loads(result["b.json"]) == envelope


def test_json_in_a_string_keeps_non_ascii_text() -> None:
    result = anonymise_responses({"s.json": {"Data": {"Settings": '{"tz":"Zürich"}'}}})
    assert result["s.json"]["Data"]["Settings"] == '{"tz":"Zürich"}'


def test_additional_details_without_content_are_kept() -> None:
    result = anonymise_responses(
        {"a.json": {"Data": {"AdditionalDetails": '["", null]'}}}
    )
    assert result["a.json"]["Data"]["AdditionalDetails"] == '["",null]'


def test_time_range_is_not_free_text() -> None:
    result = anonymise_responses(
        {"t.json": {"Data": {"Seen": "2025-03-04 10:11:12 - 2025-03-05 10:11:12"}}}
    )
    assert (
        result["t.json"]["Data"]["Seen"] == "2026-01-01 10:11:12 - 2026-01-01 10:11:12"
    )


def test_leak_error_names_the_paths() -> None:
    with pytest.raises(anonymise.LeakError) as exc:
        anonymise_responses(
            {"f.json": {"Data": {"Ref": "123456789012", "Alt": "210987654321"}}}
        )
    assert str(exc.value) == (
        "Anonymised output failed the leak check at: f.json.Data.Ref, f.json.Data.Alt"
    )


def test_leak_check_flags_keys_holding_learned_contact_details() -> None:
    anonymiser = anonymise.Anonymiser(secrets=("Zq7xK9mW",))
    anonymiser.anonymise(
        {
            "Account_Code": "48213977",
            "Login": "portal-user-77",
            "DeviceID": "yealinkpbx",
            "RecordingURL": "https://www.example.com/rec/1",
            "Email": "jane@mail.test",
            "Server": "pbx.widgetco.test",
            "ContactIP": "203.0.113.160",
            "Mobile": "07700 900344",
            "userid": "9917345",
        }
    )
    keys = [
        "Zq7xK9mW",
        "48213977",
        "portal-user-77",
        "yealinkpbx",
        "https://www.example.com/rec/1",
        "jane@mail.test",
        "pbx.widgetco.test",
        "203.0.113.160",
        "07700 900344",
        "row_9917345",
    ]
    paths = anonymiser.find_leaks({"a.json": dict.fromkeys(keys, "1")})
    assert paths == [f"a.json.<key {index}>" for index in range(len(keys))]


def test_leak_check_only_treats_the_top_two_levels_as_envelopes() -> None:
    envelope = {"Result": "1", "Message": "Method Executed Successfully."}
    paths = anonymise.Anonymiser().find_leaks(
        {
            "a.json": {"Data": {"Row": CALLED_BACK}},
            "b.json": {"Data": [CALLED_BACK]},
            "c.json": {"Extra": json.dumps(CALLED_BACK)},
            "d.json": {"Data": {"Message": ["Called back by Jane Exampleton"]}},
            "e.json": [envelope],
            "f.json": json.dumps(envelope),
        }
    )
    assert paths == [
        "a.json.Data.Row.Message",
        "b.json.Data[0].Message",
        "c.json.Extra<json>.Message",
        "d.json.Data.Message[0]",
    ]


def test_bare_national_digits_in_a_key_are_replaced() -> None:
    result = anonymise_responses(
        {"s.json": {"Data": {"Mobile": "+447700900344", "ref7700900344": "1"}}}
    )
    assert result["s.json"]["Data"] == {
        "Mobile": "+441632960000",
        "ref1632960000": "1",
    }


def test_secret_pref_record_value_is_scrubbed_elsewhere() -> None:
    message = _probe([{"pref": "voicemail_pin", "value": "8642"}], "pin 8642")
    assert message == "pin [REDACTED]"


def test_redacted_free_text_is_scrubbed_elsewhere() -> None:
    message = _probe({"Note": "Jane Exampleton"}, "Hi Jane Exampleton")
    assert message == "Hi [REDACTED]"


def test_whole_address_and_numbered_parts_are_scrubbed_elsewhere() -> None:
    message = _probe(
        {"BillingAddress": "42 Widget Road, Widgetville"},
        "to 42 Widget Road, Widgetville or 42 Widget Road",
    )
    assert message == "to Example Street or Example Street"


def test_url_and_email_hosts_are_scrubbed_after_a_slash() -> None:
    message = _probe(
        {
            "RecordingURL": "https://cdn.widgetco.test/rec/1",
            "Email": "jane@mail.widgetco.test",
        },
        "mirrors files/cdn.widgetco.test and files/mail.widgetco.test",
    )
    assert message == (
        "mirrors files/host1.example.invalid and files/host2.example.invalid"
    )
