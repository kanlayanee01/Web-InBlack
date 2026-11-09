"""Order numbers: ORD-<13 random digits>-<checksum>, checksum = digits mod 997, zero padded."""

import re
import secrets

CHECKSUM_MODULUS = 997
_LOWEST_13_DIGIT = 10**12
_COUNT_13_DIGIT = 9 * 10**12

# [0-9] rather than \d: in Python \d also matches non-ASCII digits such as Thai numerals.
_PATTERN = re.compile(r"ORD-([0-9]{13})-([0-9]{3})")


def checksum(digits: int) -> str:
    return f"{digits % CHECKSUM_MODULUS:03d}"


def generate_order_number() -> str:
    # Comes from the OS CSPRNG, so it cannot be predicted from time or from earlier numbers.
    digits = _LOWEST_13_DIGIT + secrets.randbelow(_COUNT_13_DIGIT)
    return f"ORD-{digits}-{checksum(digits)}"


def is_valid_order_number(value: str) -> bool:
    match = _PATTERN.fullmatch(value)
    if match is None:
        return False
    return checksum(int(match.group(1))) == match.group(2)
