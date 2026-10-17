"""관심종목 도구: get_watchlist, add_to_watchlist(스펙 15 4절).

add_to_watchlist는 이 챗봇의 유일한 쓰기 도구다. 12의 add_item이 함수 안에서 커밋(실패 경로는
커밋 또는 롤백)한다. 09의 TurnWriter가 행마다 커밋하므로 도구 실행 시점에 미커밋 변경이 없다(4.1).
"""

from pydantic import BaseModel

from app.core.errors import AppError
from app.modules.chat.tools.common import (
    NO_ARGUMENTS,
    NoInput,
    error_status,
    plain,
    price,
    rate,
    tool_output,
)
from app.modules.chat.tools.market import CodeInput
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.watchlist import service as watchlist_service

SHOWN_LIMIT = 20
ADD_STATUSES = {"NOT_FOUND": "not_found", "CONFLICT": "conflict"}


def get_watchlist(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    items = watchlist_service.list_items(ctx.db, ctx.user.id)
    if not items:
        return tool_output(
            {
                "status": "empty",
                "message": (
                    "관심종목이 비어 있습니다. 관심종목 화면이나 종목 상세에서 담을 수 있어요."
                ),
            }
        )
    content = {
        "summary": f"관심종목은 {len(items)}개입니다.",
        "source": items[0].source,
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "market": item.market,
                "sector": item.sector,
                "kind": item.kind,
                "groupName": item.group_name,
                "memo": item.memo,
                "quote": plain(item.quote),
                "metrics": plain(item.metrics),
            }
            for item in items
        ],
    }
    card = {
        "type": "stock_list",
        "title": f"관심종목 {len(items)}개",
        "asOf": None,
        "source": items[0].source,
        "items": [
            {
                "code": item.code,
                "name": item.name,
                "price": price(item.quote.price, item.market) if item.quote else None,
                "changeRate": rate(item.quote.change_rate) if item.quote else None,
                "extra": item.group_name,
            }
            for item in items[:SHOWN_LIMIT]
        ],
        "href": "/stocks/watchlist",
    }
    return tool_output(content, card)


def add_to_watchlist(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, CodeInput)
    try:
        item = watchlist_service.add_item(ctx.db, ctx.user.id, params.code)
    except AppError as exc:
        # 없는 종목 404, 이미 담김·100개 상한 409는 상태로 바꾼다(4.1).
        return error_status(exc, ADD_STATUSES)
    content = {
        "summary": (
            f"{item.name}({item.code}) 종목을 관심종목 '{item.group_name}' 그룹에 담았습니다."
        ),
        "code": item.code,
        "name": item.name,
        "groupName": item.group_name,
    }
    card = {
        "type": "link",
        "title": f"관심종목에 담았어요: {item.name}",
        "description": f"'{item.group_name}' 그룹 · 관심종목 화면에서 확인하세요.",
        "href": "/stocks/watchlist",
    }
    return tool_output(content, card)


register_tool(
    ToolSpec(
        name="get_watchlist",
        description=(
            "사용자의 관심종목 목록(종목코드·이름·그룹·메모·현재가·등락률·PER·PBR·시가총액)을 "
            f"확인한다. '내 관심종목 어때?' 같은 질문에 쓴다. {NO_ARGUMENTS} source가 mock이면 "
            "샘플 시세다. status가 empty면 관심종목이 없다."
        ),
        input_model=NoInput,
        label="관심종목 확인 중",
        handler=get_watchlist,
    )
)
register_tool(
    ToolSpec(
        name="add_to_watchlist",
        description=(
            "종목 하나를 사용자의 관심종목(기본 그룹)에 담는다. 사용자가 관심종목에 담아 달라고 "
            "분명히 말했을 때만 쓴다. 추천하거나 묻지 않은 종목을 스스로 담지 않는다. 종목코드를 "
            "모르면 search_stocks로 먼저 찾는다. status가 conflict면 이미 담겨 있거나 100개 "
            "상한이고, not_found면 없는 종목이다. 주문·매매 기능이 아니다."
        ),
        input_model=CodeInput,
        label="관심종목에 담는 중",
        handler=add_to_watchlist,
    )
)
