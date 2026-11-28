"""Official industry classification (產業別) for listed and OTC companies.

Sources (open data, no key):
- TWSE OpenAPI ``t187ap03_L`` 上市公司基本資料, field ``產業別``.
- TPEx OpenAPI ``mopsfin_t187ap03_O`` 上櫃公司基本資料, field ``SecuritiesIndustryCode``.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

import httpx

from quant_signal.domain.models import MarketIndustry

TWSE_INDUSTRY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_INDUSTRY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# TWSE/TPEx 產業別 codes (the two exchanges share one code table).
INDUSTRY_NAMES: dict[str, str] = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "07": "化學生技醫療",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "13": "電子工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
    "80": "管理股票",
    "91": "存託憑證",
}
_COMMON_STOCK = re.compile(r"^\d{4}$")


def industry_name(code: str) -> str:
    return INDUSTRY_NAMES.get(code, f"其他({code})")


def parse_twse_industries(payload: Any, *, available_at: datetime) -> list[MarketIndustry]:
    return _parse(
        payload,
        code_key="公司代號",
        industry_key="產業別",
        market="TW",
        source="twse_openapi",
        suffix=".TW",
        available_at=available_at,
    )


def parse_tpex_industries(payload: Any, *, available_at: datetime) -> list[MarketIndustry]:
    return _parse(
        payload,
        code_key="SecuritiesCompanyCode",
        industry_key="SecuritiesIndustryCode",
        market="TWO",
        source="tpex_openapi",
        suffix=".TWO",
        available_at=available_at,
    )


def _parse(
    payload: Any,
    *,
    code_key: str,
    industry_key: str,
    market: str,
    source: str,
    suffix: str,
    available_at: datetime,
) -> list[MarketIndustry]:
    if not isinstance(payload, list):
        raise ValueError("industry payload must be a list")
    rows: dict[str, MarketIndustry] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get(code_key, "")).strip()
        industry = str(item.get(industry_key, "")).strip()
        if not _COMMON_STOCK.match(code) or not industry:
            continue
        rows[code + suffix] = MarketIndustry(
            symbol=code + suffix,
            market=market,  # type: ignore[arg-type]
            industry_code=industry,
            industry_name=industry_name(industry),
            source=source,
            available_at=available_at,
        )
    return list(rows.values())


async def fetch_json_list(
    client: httpx.AsyncClient, url: str, *, attempts: int = 4
) -> list[Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("expected a JSON array")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            await asyncio.sleep(min(1.5 * (2**attempt), 15))
    raise RuntimeError(f"industry request failed after retries: {url}") from last_error
