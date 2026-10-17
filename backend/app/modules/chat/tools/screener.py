"""조건검색 도구: screen_stocks(스펙 15 4절). 모델이 13의 ScreenFilter를 직접 만든다."""

from pydantic import BaseModel

from app.modules.chat.tools.common import (
    ToolInput,
    market_label,
    plain,
    price,
    rate,
    tool_output,
    unit,
)
from app.modules.chat.tools.market import stock_extra
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.screener import service as screener_service
from app.modules.screener.schemas import ScreenFilter

SHOWN_LIMIT = 10  # 모델·카드에는 상위 몇 개만 보내고 전체는 조건검색 화면에서 본다(6절).
NO_SNAPSHOT_MESSAGE = (
    "종목 스냅샷이 아직 없어 조건검색을 할 수 없습니다. 관리자가 종목 스냅샷을 갱신한 뒤 다시 "
    "시도해 주세요."
)


class ScreenInput(ToolInput):
    filter: ScreenFilter


def _extra(item) -> str | None:
    parts = [stock_extra(item.market, item.kind)]
    if item.per is not None:
        parts.append(f"PER {unit(item.per, '배')}")
    return " · ".join(part for part in parts if part) or None


def screen_stocks(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, ScreenInput)
    screen_filter = params.filter
    result = screener_service.run_screen(ctx.db, screen_filter)
    if result.as_of is None:
        return tool_output({"status": "no_snapshot", "message": NO_SNAPSHOT_MESSAGE})
    shown = result.items[:SHOWN_LIMIT]
    href = f"/stocks/screener?f={screener_service.encode_filter(screen_filter)}"
    summary = f"{result.as_of.isoformat()} 스냅샷 기준 조건에 맞는 종목은 {result.total}개입니다."
    if result.excluded_for_missing_data:
        summary += f" 지표가 없어 제외한 종목은 {result.excluded_for_missing_data}개입니다."
    content = {
        "summary": summary,
        "asOf": result.as_of.isoformat(),
        "source": result.source,
        "total": result.total,
        "excludedForMissingData": result.excluded_for_missing_data,
        "shownCount": len(shown),
        "items": [
            {
                **plain(item),
                "marketLabel": market_label(item.market),
            }
            for item in shown
        ],
        "screenerHref": href,
    }
    card = {
        "type": "stock_list",
        "title": f"조건검색 결과 {result.total}개",
        "asOf": result.as_of.strftime("%Y.%m.%d"),
        "source": result.source or "unknown",
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "price": price(item.price, item.market),
                "changeRate": rate(item.change_rate),
                "extra": _extra(item),
            }
            for item in shown
        ],
        "href": href,
    }
    return tool_output(content, card)


DESCRIPTION = (
    "국내 주식·ETF를 지표 조건으로 골라 낸다(최신 종목 스냅샷 기준). '코스피 대형주 중 PER 10 이하 "
    "찾아줘', '오늘 5% 넘게 오른 ETF' 같은 조건검색 요청에 쓴다. 사용자의 문장을 아래 기준에 맞춰 "
    "filter로 직접 만든다. filter의 필드: markets(KOSPI·KOSDAQ 목록, 시장 제한 없으면 null), "
    "kinds(stock·etf 목록, 말하지 않으면 null=주식만), sectors(업종명 목록 또는 null), "
    "conditions(field·op·value·valueTo 목록, 모두 AND, 최대 10개, op는 gt·gte·lt·lte·between, "
    "valueTo는 between에만 쓰고 나머지는 null, 값은 숫자), sort(field·direction 또는 null="
    "시가총액 내림차순), limit(1~100, 보통 30). 결과의 total(전체 개수)과 상위 몇 종목만 말하고 "
    "카드 링크로 전체를 보게 한다. status가 no_snapshot이면 스냅샷이 없다. 종목을 사라고 권하지 "
    "않는다.\n\n필드·단위·용어 기준(조건검색 해석 기준과 같다):\n"
    + screener_service.get_filter_guide()
)

register_tool(
    ToolSpec(
        name="screen_stocks",
        description=DESCRIPTION,
        input_model=ScreenInput,
        label="조건검색 실행 중",
        handler=screen_stocks,
    )
)
