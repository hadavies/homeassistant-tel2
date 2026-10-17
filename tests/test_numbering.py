"""Tests for Tel2 number normalisation."""

import pytest

from custom_components.tel2.numbering import (
    canonical_number,
    display_number,
    is_valid_destination,
)


@pytest.mark.parametrize(
    "raw",
    [
        "01632960123",
        "01632 960123",
        "01632-960-123",
        "(01632) 960123",
        "441632960123",
        "+441632960123",
        "+44 1632 960123",
        "+44 (0)1632 960123",
        "+44 01632 960123",
        "0044 01632 960123",
        "4401632960123",
        "00441632960123",
        "0044 1632 960123",
    ],
)
def test_forms_of_one_number_share_a_canonical_value(raw: str) -> None:
    assert canonical_number(raw) == "441632960123"


def test_national_number_uses_given_country_code() -> None:
    assert canonical_number("092420000", country_code="64") == "6492420000"


def test_trunk_zero_is_kept_after_a_foreign_country_code() -> None:
    assert canonical_number("+64 09 242 0000") == "64092420000"


def test_foreign_international_number_is_kept() -> None:
    assert canonical_number("+64 9 242 0000") == "6492420000"
    assert canonical_number("006492420000") == "6492420000"


def test_display_number_prefixes_plus() -> None:
    assert display_number("441632960123") == "+441632960123"


def test_display_round_trips_through_canonical() -> None:
    canonical = canonical_number("01632 960123")
    assert canonical_number(display_number(canonical)) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "+",
        "00",
        "0",
        "-()",
        "01632 96012x",
        "abc",
        "+44+1632960123",
        "+01632960123",
        "+0044 1632 960123",
    ],
)
def test_invalid_input_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError, match="number"):
        canonical_number(raw)


@pytest.mark.parametrize("canonical", ["", "+441632960123", "4416329601x3"])
def test_display_number_rejects_non_canonical(canonical: str) -> None:
    with pytest.raises(ValueError, match="number"):
        display_number(canonical)


@pytest.mark.parametrize(
    "raw",
    [
        "01632 960123",
        "+44 1632 960123",
        "00441632960123",
        "1234567",
        "+123456789012345",
    ],
)
def test_valid_destinations(raw: str) -> None:
    assert is_valid_destination(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "123456",
        "+1234567890123456",
        "01632 96012x",
        "not a number",
        "+",
        "+01632960123",
        "+0044 1632 960123",
    ],
)
def test_invalid_destinations(raw: str) -> None:
    assert not is_valid_destination(raw)
