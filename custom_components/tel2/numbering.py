"""Canonical and display phone number normalisation for Tel2."""

from .const import TEL2_COUNTRY_CODE

_SEPARATORS = str.maketrans("", "", " -()\t")
_MIN_DESTINATION_DIGITS = 7
_MAX_DESTINATION_DIGITS = 15


def canonical_number(raw: str, country_code: str = TEL2_COUNTRY_CODE) -> str:
    """Return the number as international digits without a leading plus."""
    # "+44 (0)1632 ..." carries a trunk zero that must not survive into the digits.
    digits = raw.replace("(0)", "").translate(_SEPARATORS)
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country_code + digits[1:]
    if (
        not digits.isdecimal()
        or not digits.isascii()
        or digits.startswith("0")
        or digits == country_code
    ):
        raise ValueError(f"Not a phone number: {raw!r}")
    return digits


def display_number(canonical: str) -> str:
    """Return a canonical number formatted for display."""
    if not canonical.isdecimal() or not canonical.isascii():
        raise ValueError(f"Not a canonical phone number: {canonical!r}")
    return f"+{canonical}"


def is_valid_destination(raw: str) -> bool:
    """Return whether the input is usable as a forward destination."""
    try:
        digits = canonical_number(raw)
    except ValueError:
        return False
    return _MIN_DESTINATION_DIGITS <= len(digits) <= _MAX_DESTINATION_DIGITS
