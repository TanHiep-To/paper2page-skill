"""Plain-text facts taken from the PDF without an LLM: page text, abstract, numbers."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?!\w)")
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‐": "-", "‑": "-",
                        "−": "-", "­": "", " ": " "})
# The stamp arXiv prints in the left margin of page 1: "arXiv:2006.11239v2  [cs.LG]  16 Dec 2020".
# It is not part of the paper, and the text layer can place it in the middle of the abstract.
_STAMP = r"arXiv:[\w.\-/]+[ \t]+\[[\w.\-]+\][ \t]+\d{1,2}[ \t]+[A-Z][a-z]{2}[ \t]+\d{4}"
ARXIV_STAMP = re.compile(rf"(?m)^[ \t]*{_STAMP}[ \t]*(?:\n|$)|[ \t]*{_STAMP}[ \t]*")


def strip_arxiv_stamp(text: str) -> str:
    """Remove the stamp: its whole line when it stands alone, otherwise the stamp with one space left."""
    return ARXIV_STAMP.sub(lambda m: "" if m.group().endswith("\n") or m.start() == 0 or text[m.start() - 1] == "\n" else " ", text)
_ABSTRACT_END = re.compile(r"^(keywords?\b|index terms\b|(1|i)\.?\s+introduction\b|introduction$|ccs concepts\b)",
                           re.I)


@dataclass
class PdfText:
    pages: list[str]

    @property
    def full(self) -> str:
        return "\n".join(self.pages)

    @property
    def numbers(self) -> set[str]:
        return numbers_in(self.full)


def load(pdf: Path) -> PdfText:
    with fitz.open(pdf) as doc:
        return PdfText([clean(page.get_text("text")) for page in doc])


def clean(text: str) -> str:
    """Normalise composed characters, ligatures and typographic punctuation; keep line breaks.
    The arXiv margin stamp is removed."""
    text = strip_arxiv_stamp(unicodedata.normalize("NFC", text))
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    return text.translate(_PUNCT)


def squash(text: str) -> str:
    """Single-line form used for 'is this string in the PDF' checks."""
    return re.sub(r"\s+", " ", clean(text)).strip()


def fuzzy_key(text: str) -> str:
    """Comparison key that ignores case, whitespace and (line-break) hyphenation."""
    return re.sub(r"\s+", " ", clean(text).replace("-", "")).strip().lower()


def loose_key(text: str) -> str:
    """Key for label lookups: letters and digits only, so line breaks and hyphenation do not matter."""
    return re.sub(r"[^0-9a-zÀ-￿]+", "", clean(text).lower())


def numbers_in(text: str) -> set[str]:
    return {m.group().replace(",", "").rstrip(".") for m in NUM_RE.finditer(clean(text))}


def extract_abstract(pdf: PdfText) -> str | None:
    """Text between the 'Abstract' heading and Keywords / Introduction, across page breaks."""
    lines = "\n".join(pdf.pages[:3]).splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s*abstract\b[\s.:—-]*", ln, re.I)), None)
    if start is None:
        return None
    first = re.sub(r"^\s*abstract\b[\s.:—-]*", "", lines[start], flags=re.I)
    body = [first] if first.strip() else []
    for ln in lines[start + 1:]:
        if _ABSTRACT_END.match(ln.strip()):
            break
        if ln.strip().isdigit():  # page number
            continue
        body.append(ln)
    text = dehyphenate(squash("\n".join(body)), pdf.full)
    return text or None


def dehyphenate(text: str, corpus: str) -> str:
    """Rejoin words split at line ends ('multi- layered'). The hyphen is kept only when the paper
    writes the hyphenated form unbroken elsewhere more often than the joined form."""
    low = corpus.lower()

    def join(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        hyph, plain = f"{a}-{b}", f"{a}{b}"
        return hyph if low.count(hyph.lower()) > low.count(plain.lower()) else plain

    return re.sub(r"(\w+)- (\w+)", join, text)


def find_table_pages(pdf: PdfText, number: int) -> list[int]:
    """0-based indices of the page holding the 'Table N' caption, plus the next page (continued tables)."""
    pat = re.compile(rf"^\s*Table\s+{number}\s*[:.|]", re.M)
    for i, text in enumerate(pdf.pages):
        if pat.search(text):
            return [i] + ([i + 1] if i + 1 < len(pdf.pages) else [])
    return []
