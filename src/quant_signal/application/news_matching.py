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

_MEDIA_WORDS = (
    "新聞網|新聞|日報|時報|晚報|週刊|周刊|雜誌|電子報|財經|經濟|工商|鉅亨|Yahoo|雅虎|"
    "ETtoday|MoneyDJ|Google|頻道|報導|網|報|台"
)
# " - 經濟日報" / "｜中天新聞" / "-聯合新聞網": a trailing media name.
_SUFFIX_SPACED = re.compile(r"\s+[-–—|｜]\s+[^-–—|｜]{1,30}$")
_SUFFIX_PIPE = re.compile(r"\s*[|｜]\s*[^-–—|｜]{1,30}$")
_SUFFIX_DASH_MEDIA = re.compile(rf"\s*[-–—]\s*[^-–—|｜]{{0,28}}(?:{_MEDIA_WORDS})$")
# "中天新聞：大盤震盪": a leading media name.
_PREFIX_MEDIA = re.compile(rf"^[^\s:：]{{0,12}}(?:{_MEDIA_WORDS})\s*[:：]\s*")

# Zero-width, bidi and other invisible controls; they hide text from a human reader.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")

# Units that follow a bare number that is a quantity, not a stock code.
_CODE_BAD_NEXT = "張檔倍季%點元億萬年月日"
_CODE_BAD_PREV = "$"
_RANGE_CHARS = "/~"

# Two-character names are ordinary words ("大成", "中華"); they need extra evidence.
_FINANCE_AFTER = (
    "營收", "股價", "法說", "財報", "獲利", "訂單", "漲停", "跌停", "漲", "跌", "股票",
    "配息", "配股", "除息", "財測", "公告", "擴產", "董事", "(", "（",
)
_VERB_BEFORE = (
    "看好", "看壞", "加碼", "減碼", "買超", "賣超", "外資", "投信", "自營", "法人",
    "調升", "調降", "偏多", "偏空", "及", "與", "和", "跟", "、",
)

# Short names headlines use for a company, mapped to the full symbol.
ALIASES: dict[str, str] = {"台積": "2330.TW"}


def clean_text(text: str, *, limit: int | None = None) -> str:
    """Strip invisible/control characters and collapse whitespace; optionally truncate."""
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def strip_media(headline: str) -> str:
    """Drop a trailing " - 媒體" / "｜媒體" suffix and a leading "媒體：" prefix."""
    text = unicodedata.normalize("NFKC", headline).strip()
    previous = None
    while previous != text:  # "標題 - 媒體 - Google 新聞"
        previous = text
        for pattern in (_SUFFIX_SPACED, _SUFFIX_PIPE, _SUFFIX_DASH_MEDIA):
            text = pattern.sub("", text)
        text = _PREFIX_MEDIA.sub("", text)
    return text


def headline_key(headline: str) -> str:
    """Normalized form of a headline: no media name, punctuation or whitespace."""
    text = strip_media(clean_text(headline))
    return "".join(ch for ch in text.casefold() if unicodedata.category(ch)[0] in "LN")


def headline_hash(headline: str) -> str:
    return hashlib.sha1(headline_key(headline).encode()).hexdigest()


def _code_hit(text: str, code: str) -> bool:
    pattern = re.compile(rf"(?<![\d.]){re.escape(code)}(?!\d|\.\d)")
    for match in pattern.finditer(text):
        start, end = match.start(), match.end()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        after2 = text[end + 1] if end + 1 < len(text) else ""
        spaced = text[end:].lstrip()
        unit = spaced[:1]  # "2002 張": a unit may follow after spaces
        if (after and after in _RANGE_CHARS) or (before and before in _RANGE_CHARS):
            continue
        if after == "-" and after2.isdigit():  # "2025-2027年"
            continue
        if before == "-" and start > 1 and text[start - 2].isdigit():
            continue
        if unit and unit in _CODE_BAD_NEXT:
            continue
        if unit == "股" and spaced[1:2] not in ("價", "票"):
            continue
        if before and before in _CODE_BAD_PREV:
            continue
        return True
    return False


def _is_cjk(char: str) -> bool:
    return bool(char) and bool(_CJK_CHAR.match(char))


def _short_name_ok(text: str, begin: int, end: int, code_present: bool) -> bool:
    """Is this two-character name really the company? Rule-based, deliberately strict."""
    if code_present:
        return True
    left, right = text[:begin], text[end:]
    if not _is_cjk(left[-1:]) and not _is_cjk(right[:1]):
        return True  # delimited on both sides
    if right.startswith(_FINANCE_AFTER):
        return True
    return left.endswith(_VERB_BEFORE)


def match_symbols(
    headline: str,
    names: dict[str, str],
    *,
    scope: set[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Symbols (within ``scope``) a headline is about, by stock code or company name.

    ``names`` maps *every* listed symbol (``2330.TW``) to its Chinese name: all of
    them take part in matching, longest name first, so "中華電" is consumed before
    "中華" can claim it; only then are matches filtered to ``scope``. Codes need
    digit boundaries and are rejected beside units or inside ranges. Names of three
    or more characters match as substrings; two-character names need a stock code,
    delimiters, a finance word or a trading verb next to them. The media name
    around a headline is ignored.
    """
    text = unicodedata.normalize("NFKC", strip_media(headline))
    wanted = scope if scope is not None else set(names)
    code_present: set[str] = set()
    for symbol in names:
        code = symbol.split(".")[0]
        if code.isdigit() and 4 <= len(code) <= 6 and _code_hit(text, code):
            code_present.add(symbol)

    by_name: dict[str, list[str]] = {}
    for symbol, raw_name in names.items():
        name = unicodedata.normalize("NFKC", raw_name or "").strip().replace("*", "")
        name = re.sub(r"-?KY$", "", name)
        if _CJK_RUN.match(name):
            by_name.setdefault(name, []).append(symbol)
    for alias, symbol in (ALIASES if aliases is None else aliases).items():
        by_name.setdefault(alias, [])
        if symbol not in by_name[alias]:
            by_name[alias].append(symbol)

    matched = set(code_present)
    masked = text
    for name in sorted(by_name, key=len, reverse=True):
        symbols = by_name[name]
        start = 0
        while (index := masked.find(name, start)) >= 0:
            end = index + len(name)
            start = end
            if len(name) == 2 and not _short_name_ok(
                text, index, end, any(symbol in code_present for symbol in symbols)
            ):
                continue
            matched.update(symbols)
            masked = masked[:index] + "\0" * len(name) + masked[end:]
    return sorted(matched & wanted)
