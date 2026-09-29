import re

from django.core.exceptions import ValidationError

_PLACEHOLDERS = {"n/a", "na", "none", "nil", "tbc", "unknown", "test", "x", "xx", "xxx"}


def looks_like_street_address(value):
    """Light sanity check (not a postal lookup): needs a street number and some letters."""
    value = (value or "").strip()
    if value.lower() in _PLACEHOLDERS:
        return False
    letters = len(re.findall(r"[A-Za-z]", value))
    return len(value) >= 5 and bool(re.search(r"\d", value)) and letters >= 3


def validate_street_address(value):
    if value and not looks_like_street_address(value):
        raise ValidationError(
            "Enter a street address with a number and street name, e.g. 12 George St.")
