import pytest

from partsdesk.speech.prompts import clean_prompt, spell, tag_readback


def test_digits_are_read_individually_with_leading_zeros():
    assert spell("0072") == "zero, zero, seven, two"


def test_letters_use_phonetic_alphabet():
    assert spell("1856M94P01") == "one, eight, five, six, Mike, nine, four, Papa, zero, one"


def test_separators_are_spoken():
    assert spell("A-1") == "Alfa, dash, one"


def test_readback_names_part_and_serial_and_asks_for_confirmation():
    text = tag_readback("1856M94P01", "00482")
    assert "zero, zero, four, eight, two" in text
    assert "confirm" in text.lower() and "retake" in text.lower()


def test_clean_prompt_strips_control_characters_and_bounds_length():
    assert clean_prompt("hello\x00\x1b world") == "hello world"
    assert len(clean_prompt("x" * 5000)) == 400
    with pytest.raises(ValueError):
        clean_prompt("   ")
