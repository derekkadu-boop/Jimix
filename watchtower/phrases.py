import json
from pathlib import Path

with Path(__file__).with_suffix(".json").open(encoding="utf-8") as file:
    PHRASES = {
        signal: tuple(
            dict.fromkeys(phrase.casefold() for variants in locales.values() for phrase in variants)
        )
        for signal, locales in json.load(file).items()
    }


def contains_phrase(text: str, signal: str) -> bool:
    folded = text.casefold()
    return any(phrase in folded for phrase in PHRASES[signal])
