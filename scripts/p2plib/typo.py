"""Content-level typography: non-breaking spaces and the title line break. No CSS involved."""
from __future__ import annotations

import math
import re

NBSP = " "
LABEL_RE = re.compile(r"\b(Table|Figure|Fig\.|Section|Sec\.|Eq\.|Equation|Algorithm|Appendix) (?=[A-Z]?\.?\d|[IVXL]+\b)")
NUMBER_UNIT_RE = re.compile(r"(?<![\w@/.-])(\d[\d,.]*%?|[A-Z]+\d+[A-Z\d]*) (?=[A-Za-z(])")
TITLE_LINE_CHARS = 40  # characters that fit one line of the template's h1 at desktop width


def bind_numbers(text: str) -> str:
    """Keep 'Table 3', '23 images', 'W4A8 MASQuant' together."""
    text = LABEL_RE.sub(lambda m: m.group(1) + NBSP, text)
    return NUMBER_UNIT_RE.sub(lambda m: m.group(1) + NBSP, text)


def bind_tail(text: str, min_chars: int, max_words: int = 5, max_chars: int = 34) -> str:
    """Join the last words with non-breaking spaces so the last line is never one word or very short.

    At least the last two words are joined; more are added until the tail has `min_chars` characters.
    """
    words = text.split(" ")
    if len(words) < 3 or max(len(w) for w in words[-2:]) > 22:
        return text  # a long last word (a URL) must stay free to wrap on a phone
    n = 2
    while n < max_words and n < len(words) - 1 and len(" ".join(words[-n:])) < min_chars:
        n += 1
    while n > 2 and len(" ".join(words[-n:])) > max_chars:
        n -= 1
    if len(" ".join(words[-n:])) > max_chars:
        return text  # the last two words would not fit one line on a phone; joining them would split a word
    return " ".join(words[:-n]) + " " + NBSP.join(words[-n:])


def polish(text: str, min_tail: int) -> str:
    """Numbers with their units, and the tail of the text, kept together. The wording is unchanged."""
    return bind_tail(bind_numbers(text), min_tail)


def name(text: str) -> str:
    """An author name never splits across lines."""
    return text.replace(" ", NBSP)


def title_parts(title: str) -> list[str]:
    """Title lines: break right after the colon when that gives balanced lines (at most 3 on desktop)."""
    head, sep, rest = title.partition(": ")
    natural = math.ceil(len(title) / TITLE_LINE_CHARS)
    if not sep or natural < 2:
        return [title]
    with_break = 1 + math.ceil(len(rest) / TITLE_LINE_CHARS)
    if with_break <= min(3, natural) or (with_break == 2 and len(rest) <= TITLE_LINE_CHARS):
        return [head + ":", rest]
    return [title]
