"""Rule-based classification of 重大訊息 titles. Deterministic: no I/O, no LLM.

The category and polarity are coarse research hints (which kind of catalyst, and
whether the wording reads as good or bad news), not trading advice. The first
matching rule wins; polarity comes from keywords inside that rule. The ``clause``
argument is accepted for forward compatibility only: clause numbers differ between
TWSE and TPEx and say little beyond the title, so no rule reads it yet.
"""

from __future__ import annotations

import re
import unicodedata

from quant_signal.domain.models import EventCategory, EventPolarity

# Phrases containing a trigger word that are really something else.
_NOISE = re.compile(
    "合併財務報告|合併財務報表|合併營收|合併報表|合併損益|合併綜合損益|合併資產負債表"
)


def _has(text: str, words: str) -> bool:
    return any(word and word in text for word in words.split("|"))


def classify_material(title: str, clause: str | None = None) -> tuple[EventCategory, EventPolarity]:
    text = _NOISE.sub("", unicodedata.normalize("NFKC", title or ""))

    if _has(text, "澄清|媒體報導|媒體報載"):
        return "clarification", "neutral"

    if _has(text, "庫藏股|買回本公司股份"):
        if _has(text, "處分|出售|轉讓|屆滿|執行完畢|未執行"):
            return "buyback", "neutral"
        return "buyback", "positive"

    if _has(text, "股利|股息|配息|配股|盈餘分派|盈餘分配|盈餘轉增資|除息|除權"):
        if _has(text, "不分派|不發放|未分派|停發|不配發"):
            return "dividend", "negative"
        if _has(text, "基準日|除息|除權|交易日|訂定"):
            return "dividend", "neutral"
        return "dividend", "positive"

    if _has(text, "增資|私募|發行新股|可轉換公司債|轉換公司債|海外公司債"):
        if _has(text, "子公司|孫公司|轉投資|完成|定價|繳款|變更登記|取消|終止"):
            return "capital_increase", "neutral"
        return "capital_increase", "negative"

    if _has(text, "併購|購併|合併|收購|股份轉換|分割"):
        if _has(text, "終止|取消|解除"):
            return "m_and_a", "negative"
        return "m_and_a", "neutral"

    if _has(text, "訴訟|起訴|判決|裁罰|仲裁|侵權|檢調|搜索|罰鍰|偵辦|羈押|勝訴|敗訴|提告|行政處分"):
        if _has(text, "勝訴|撤銷處分|不起訴"):
            return "litigation", "positive"
        if _has(text, "和解|裁定"):
            return "litigation", "neutral"
        return "litigation", "negative"

    if re.search(
        r"(董事長|總經理|執行長|財務長|發言人|獨立董事|董事(?!會)|監察人|經理人|會計主管|稽核主管|總裁|副總)"
        r".*(異動|辭任|辭職|解任|免職|選任|就任|任命|更換|改選|卸任|離職|補選|變動|退休|請辭|聘任|委任|接任|代理)",
        text,
    ):
        return "executive_change", "negative" if _has(text, "解任|免職") else "neutral"

    if _has(text, "財測|財務預測|營運展望|預測數|財務預估|營收預估|獲利預估|營運指引"):
        if _has(text, "上修|調升|調高"):
            return "guidance", "positive"
        if _has(text, "下修|調降|調低"):
            return "guidance", "negative"
        return "guidance", "neutral"

    if not _has(text, "衍生性|受託契約|公司債|債券") and _has(
        text, "合約|契約|簽約|簽署|簽訂|合作|策略聯盟|備忘錄|MOU|標案|得標|決標|訂單|承攬|授權|供貨"
    ):
        if _has(text, "終止|解除|取消|違約"):
            return "major_contract", "negative"
        if _has(text, "得標|決標|訂單|簽署|簽訂|簽約"):
            return "major_contract", "positive"
        return "major_contract", "neutral"

    if _has(text, "重大虧損|減資|跳票|拒絕往來|停工|火災|爆炸|下市|終止上市|重整|破產|解散") or (
        _has(text, "衍生性") and _has(text, "損失")
    ):
        return "other", "negative"
    return "other", "neutral"
