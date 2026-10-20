import re
import unicodedata
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from workbench.models import Box, Evidence, FieldKind, Page, Word

# USD, en-US. Require consistent grouping and a maximum of two fractional digits.
MONEY = re.compile(r"[+-]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", re.ASCII)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})", re.ASCII)
IDENTIFIER = re.compile(r"[a-zA-Z0-9*•×-]{3,40}")


def clean(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("−", "-").split())


def normalize(kind: FieldKind, value: str) -> str | None:
    value = clean(value)
    if kind == "money":
        if not value or len(value) > 40:
            return None
        negative = value.startswith("(") and value.endswith(")")
        if negative:
            value = value[1:-1].strip()
        suffix = re.search(r"\s*(CR|DR)$", value, flags=re.I)
        if suffix:
            value = value[: suffix.start()].strip()
        value = re.sub(r"(?<=\$)\s+", "", value)
        if not MONEY.fullmatch(value) or (
            (negative or suffix) and (value.startswith(("+", "-")) or (negative and suffix))
        ):
            return None
        amount = Decimal(value.replace(",", "").replace("$", ""))
        if amount.copy_abs() > Decimal("999999999999999.99"):
            return None
        if negative or (suffix and suffix[1].upper() == "DR"):
            amount = -amount
        return f"{amount or Decimal(0):.2f}"
    if kind == "date":
        try:
            if ISO_DATE.fullmatch(value):
                return date.fromisoformat(value).isoformat()
            if match := US_DATE.fullmatch(value):
                month, day, year = map(int, match.groups())
                return date(year, month, day).isoformat()
        except ValueError:
            return None
        return None
    if kind == "id":
        return value.upper() if IDENTIFIER.fullmatch(value) else None
    return value if 0 < len(value) <= 300 else None


def evidence(page: Page, words: list[Word]) -> Evidence:
    return Evidence(
        page=page.number,
        text=" ".join(word.text for word in words),
        score=min(word.score for word in words),
        box=Box(
            left=min(word.box.left for word in words),
            top=min(word.box.top for word in words),
            right=max(word.box.right for word in words),
            bottom=max(word.box.bottom for word in words),
        ),
    )


def ground(kind: FieldKind, value: str, page: Page, lines: list[list[Word]]) -> Evidence | None:
    target = normalize(kind, value)
    if target is None:
        return None
    target_words = len(target.split())
    widths = (target_words,) if kind == "text" else (1, 2, 3, 4)
    best: Evidence | None = None
    best_rank = (0.0, 0, 0.0)
    for words in lines:
        for start in range(len(words)):
            for width in widths:
                if start + width > len(words):
                    continue
                window = words[start : start + width]
                candidate = normalize(kind, " ".join(word.text for word in window))
                if candidate is None:
                    continue
                similarity = float(candidate == target)
                if kind == "text" and len(target) >= 4:
                    similarity = SequenceMatcher(
                        None, target.casefold(), candidate.casefold(), autojunk=False
                    ).ratio()
                if similarity >= 0.85:
                    found = evidence(page, window)
                    rank = (similarity, width, found.score)
                    if rank > best_rank:
                        best, best_rank = found, rank
    return best
