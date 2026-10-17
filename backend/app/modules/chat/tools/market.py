"""종목 도구: search_stocks, get_stock_detail(스펙 15 4절). 시세·종목 조회만 한다."""

from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, StringConstraints

from app.modules.chat.tools.common import (
    ToolInput,
    krw_compact,
    market_label,
    plain,
    price,
    rate,
    status_output,
    tool_output,
    unit,
)
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.market import service as market_service

SEARCH_LIMIT = 10

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]


class SearchInput(ToolInput):
    query: Query


class CodeInput(ToolInput):
    code: Code


def stock_href(code: str) -> str:
    return f"/stocks/{quote(code, safe='')}"


def stock_extra(market: str | None, kind: str | None) -> str | None:
    parts = [label for label in (market_label(market), "ETF" if kind == "etf" else None) if label]
    return " · ".join(parts) or None


def search_stocks(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, SearchInput)
    items = market_service.search_stocks(ctx.db, params.query, limit=SEARCH_LIMIT)
    content = {
        "summary": f"'{params.query}' 종목 검색 결과는 {len(items)}개입니다."
        + ("" if items else " 다른 이름이나 종목코드로 다시 찾아보세요."),
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "market": item.market,
                "sector": item.sector,
                "kind": item.kind,
            }
            for item in items
        ],
    }
    if not items:
        return tool_output(content)
    card = {
        "type": "stock_list",
        "title": f"'{params.query}' 종목 검색",
        "asOf": None,
        # 종목 마스터 검색이라 시세가 없다(배지 없음, 4.1).
        "source": "master",
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "price": None,
                "changeRate": None,
                "extra": stock_extra(item.market, item.kind),
            }
            for item in items
        ],
        "href": None,
    }
    return tool_output(content, card)


def _metrics(snapshot, market: str) -> list[dict]:
    if snapshot is None:
        return []
    candidates = [
        ("PER", unit(snapshot.per, "배")),
        ("PBR", unit(snapshot.pbr, "배")),
        ("ROE", unit(snapshot.roe, "%")),
        ("시가총액", krw_compact(snapshot.market_cap)),
        ("EPS", price(snapshot.eps, market)),
        ("BPS", price(snapshot.bps, market)),
        ("외국인 보유 비율", unit(snapshot.foreign_ratio, "%")),
        ("52주 최고가", price(snapshot.high_52w, market)),
        ("52주 최저가", price(snapshot.low_52w, market)),
        ("거래대금", krw_compact(snapshot.trading_value)),
    ]
    return [{"label": label, "value": value} for label, value in candidates if value is not None]


def get_stock_detail(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, CodeInput)
    detail = market_service.get_stock_detail(ctx.db, params.code)
    if detail is None:
        return status_output(
            "not_found",
            "종목을 찾을 수 없습니다. 종목코드를 모르면 search_stocks로 먼저 찾아 주세요.",
            code=params.code,
        )
    quote_info = detail.quote
    if quote_info is not None:
        change = rate(quote_info.change_rate) or "등락률 없음"
        summary = (
            f"{detail.name}({detail.code})의 현재가는 "
            f"{price(quote_info.price, detail.market)}({change})입니다."
        )
    else:
        summary = f"{detail.name}({detail.code})의 현재 시세를 가져오지 못했습니다."
    content = {
        "summary": summary,
        "code": detail.code,
        "name": detail.name,
        "market": detail.market,
        "sector": detail.sector,
        "kind": detail.kind,
        "source": detail.source,
        "quote": plain(quote_info),
        "snapshot": plain(detail.snapshot),
    }
    card = {
        "type": "stock",
        "code": detail.code,
        "name": detail.name,
        "price": price(quote_info.price, detail.market) if quote_info else None,
        "changeRate": rate(quote_info.change_rate) if quote_info else None,
        "metrics": _metrics(detail.snapshot, detail.market),
        "source": detail.source,
        "href": stock_href(detail.code),
    }
    return tool_output(content, card)


register_tool(
    ToolSpec(
        name="search_stocks",
        description=(
            "종목 이름이나 코드 일부로 종목 마스터에서 종목을 찾아 "
            "종목코드·시장·업종·종류(주식·ETF)를 최대 10개 돌려준다. 사용자가 말한 종목의 코드를 "
            "모를 때 get_stock_detail이나 add_to_watchlist 전에 쓴다. 시세는 없다. 조건(PER, "
            "시가총액 등)으로 고르는 요청에는 screen_stocks를 쓴다."
        ),
        input_model=SearchInput,
        label="종목 찾는 중",
        handler=search_stocks,
    )
)
register_tool(
    ToolSpec(
        name="get_stock_detail",
        description=(
            "종목코드 하나의 현재가·등락률(quote)과 최근 스냅샷 지표(PER·PBR·ROE·EPS·BPS·시가총액·"
            "외국인 보유 비율·52주 최고/최저가)를 확인한다. 종목코드를 모르면 search_stocks를 먼저 "
            "쓴다. source가 mock이면 샘플 데이터라 실제 시세가 아니라고 말한다. status가 "
            "not_found면 없는 종목이다. 사거나 팔라고 권하지 않고 정보만 설명한다."
        ),
        input_model=CodeInput,
        label="종목 정보 확인 중",
        handler=get_stock_detail,
    )
)
