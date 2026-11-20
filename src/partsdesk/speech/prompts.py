"""Prompt text for FaceTime readback. Identifiers are spelled out before synthesis."""
from __future__ import annotations

import re

MAX_PROMPT_CHARS = 400
DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
NATO = {
    "A": "Alfa", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo", "F": "Foxtrot",
    "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett", "K": "Kilo", "L": "Lima",
    "M": "Mike", "N": "November", "O": "Oscar", "P": "Papa", "Q": "Quebec", "R": "Romeo",
    "S": "Sierra", "T": "Tango", "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
}
SYMBOLS = {"-": "dash", "/": "slash", ".": "point"}

GREETING = ("Parts Desk training. Hold the part tag so the whole QR code is in the center of the frame. "
            "Keep it still for a moment.")
FRAMING_CUE = "Move closer and show the entire QR code without covering its corners."
CERTIFICATE_REQUEST = ("Tag confirmed. Please send a sharp photo of the full release certificate, "
                       "including the footer and all four corner markers, by iMessage.")
CERTIFICATE_RETAKE = ("The certificate footer was not fully readable. Please send another photo with "
                      "the whole page flat, in focus, and without glare.")


def spell(identifier: str) -> str:
    words = []
    for char in identifier.upper():
        if char.isdigit():
            words.append(DIGITS[int(char)])
        elif char in NATO:
            words.append(NATO[char])
        elif char in SYMBOLS:
            words.append(SYMBOLS[char])
    return ", ".join(words)


def tag_readback(part: str, serial: str) -> str:
    return f"Training tag. Part {spell(part)}. Serial {spell(serial)}. Say confirm, or retake."


def clean_prompt(text: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:MAX_PROMPT_CHARS]
    if not cleaned:
        raise ValueError("Prompt is empty.")
    return cleaned
