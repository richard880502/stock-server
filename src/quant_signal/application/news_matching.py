"""Text helpers for the news pipeline: headline dedupe keys and symbol matching.

Pure functions, no I/O, so the false-positive cases can be unit-tested directly.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

CJK = "㐀-䶿一-鿿"
_CJK_CHAR = re.compile(f"[{CJK}]")
_CJK_RUN = re.compile(f"^[{CJK}]{{2,}}$")
# " - 經濟日報", " | 鉅亨網", " – Yahoo奇摩股市": a trailing media suffix.
_MEDIA_SUFFIX = re.compile(r"\s+[-–—|｜]\s+[^-–—|｜]{1,30}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
# What may follow / precede a bare number without it being a stock code (years, prices).
_CODE_BAD_NEXT = "年月日元點%股億萬"
_CODE_BAD_PREV = "$"


def clean_text(text: str, *, limit: int | None = None) -> str:
    """Strip control characters and collapse whitespace; optionally truncate."""
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def headline_key(headline: str) -> str:
    """Normalized form of a headline: no media suffix, punctuation or whitespace."""
    text = unicodedata.normalize("NFKC", headline).strip()
    previous = None
    while previous != text:  # "標題 - 媒體 - Google 新聞"
        previous = text
        text = _MEDIA_SUFFIX.sub("", text)
    return "".join(ch for ch in text.casefold() if unicodedata.category(ch)[0] in "LN")


def headline_hash(headline: str) -> str:
    return hashlib.sha1(headline_key(headline).encode()).hexdigest()


def _code_hit(text: str, code: str) -> bool:
    pattern = re.compile(rf"(?<![\d.]){re.escape(code)}(?!\d|\.\d)")
    for match in pattern.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if after and after in _CODE_BAD_NEXT:
            continue
        if before and before in _CODE_BAD_PREV:
            continue
        return True
    return False


def _name_spans(text: str, name: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while (index := text.find(name, start)) >= 0:
        end = index + len(name)
        start = end
        if len(name) == 2:
            # Two-character names are common words ("大成", "中華"): accept only when at
            # least one side is not another Chinese character ("擴大成長" is not 大成).
            left = text[index - 1] if index > 0 else ""
            right = text[end] if end < len(text) else ""
            if left and right and _CJK_CHAR.match(left) and _CJK_CHAR.match(right):
                continue
        spans.append((index, end))
    return spans


def match_symbols(headline: str, names: dict[str, str]) -> list[str]:
    """Symbols a headline is about, by stock code or company name.

    ``names`` maps symbol (``2330.TW``) to its Chinese name. Codes need digit
    boundaries and are rejected beside units (``2024年``, ``2330元``); names need at
    least two CJK characters, and the longest name wins over names it contains.
    """
    text = unicodedata.normalize("NFKC", headline)
    matched: set[str] = set()
    for symbol in names:
        code = symbol.split(".")[0]
        if code.isdigit() and 4 <= len(code) <= 6 and _code_hit(text, code):
            matched.add(symbol)
    candidates: list[tuple[str, str]] = []
    for symbol, raw_name in names.items():
        name = unicodedata.normalize("NFKC", raw_name or "").strip().replace("*", "")
        name = re.sub(r"-?KY$", "", name)
        if _CJK_RUN.match(name):
            candidates.append((name, symbol))
    candidates.sort(key=lambda pair: len(pair[0]), reverse=True)
    masked = text
    for name, symbol in candidates:
        spans = _name_spans(masked, name)
        if spans:
            matched.add(symbol)
            for begin, end in spans:
                masked = masked[:begin] + "\0" * (end - begin) + masked[end:]
    return sorted(matched)
