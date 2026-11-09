import pytest

from app.domain.order_number import checksum, generate_order_number, is_valid_order_number


def test_spec_valid_example():
    assert is_valid_order_number("ORD-2587965432159-229")


def test_spec_invalid_example():
    assert not is_valid_order_number("ORD-2587965432159-458")


def test_checksum_is_zero_padded():
    assert checksum(997) == "000"
    assert checksum(998) == "001"
    assert checksum(1993) == "996"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ORD-2587965432159",
        "ORD-258796543215-229",
        "ORD-25879654321590-229",
        "ord-2587965432159-229",
        "ORD-2587965432159-229 ",
        " ORD-2587965432159-229",
        "ORD-2587965432159-229\n",
        "ORD-๒587965432159-229",
    ],
)
def test_malformed_numbers_are_rejected(value):
    assert not is_valid_order_number(value)


def test_generated_numbers_are_valid_and_13_digits():
    for _ in range(2000):
        number = generate_order_number()
        assert is_valid_order_number(number)
        assert len(number.split("-")[1]) == 13


def test_generated_numbers_are_not_sequential():
    values = [int(generate_order_number().split("-")[1]) for _ in range(200)]
    assert values != sorted(values)
    assert len(set(values)) == len(values)
