"""매매일지 도구: get_trade_stats, get_positions(스펙 15 4절). 조회만 한다."""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.modules.chat.tools.common import (
    NO_ARGUMENTS,
    NoInput,
    ToolInput,
    day_text,
    money,
    number,
    rate,
    signed_money,
    status_output,
    tool_output,
)
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.journal import service as journal_service

CURRENCY_LABEL = {"KRW": "원화", "USD": "달러"}
NO_TRADES_MESSAGE = (
    "매매 기록이 없습니다. 매매일지 화면에서 거래를 직접 입력하거나 파일을 올리거나 "
    "증권사 체결 내역을 동기화해 주세요."
)


class TradeStatsInput(ToolInput):
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def valid_range(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError("from은 to 이전 날짜여야 합니다.")
        return self


def _performance(item) -> dict | None:
    if item is None:
        return None
    # trade_id는 모델에 보내지 않는다.
    return {
        "symbolName": item.symbol_name,
        "realizedPnl": number(item.realized_pnl),
        "tradedOn": item.traded_on.isoformat(),
    }


def _period(start: date | None, end: date | None) -> str:
    if start is None and end is None:
        return "전체 기간"
    return f"{day_text(start) or '처음'} ~ {day_text(end) or '오늘'}"


def get_trade_stats(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, TradeStatsInput)
    stats = journal_service.get_trade_stats(ctx.db, ctx.user.id, params.start, params.end)
    if not stats.by_currency:
        return status_output("no_trades", NO_TRADES_MESSAGE)
    period = _period(params.start, params.end)
    parts = [
        f"{CURRENCY_LABEL.get(item.currency, item.currency)} 실현손익 "
        f"{signed_money(item.realized_pnl, item.currency)}(매도 {item.sell_count}건)"
        for item in stats.by_currency
    ]
    content = {
        "summary": f"{period} " + ", ".join(parts) + "입니다.",
        "from": params.start.isoformat() if params.start else None,
        "to": params.end.isoformat() if params.end else None,
        "byCurrency": [
            {
                "currency": item.currency,
                "realizedPnl": number(item.realized_pnl),
                "winCount": item.win_count,
                "lossCount": item.loss_count,
                "winRate": item.win_rate,
                "sellCount": item.sell_count,
                "avgWin": number(item.avg_win),
                "avgLoss": number(item.avg_loss),
                "bestTrade": _performance(item.best_trade),
                "worstTrade": _performance(item.worst_trade),
            }
            for item in stats.by_currency
        ],
        "bySymbol": [
            {
                "symbolCode": item.symbol_code,
                "symbolName": item.symbol_name,
                "currency": item.currency,
                "realizedPnl": number(item.realized_pnl),
                "sellCount": item.sell_count,
            }
            for item in stats.by_symbol
        ],
        "monthly": [
            {
                "month": item.month,
                "currency": item.currency,
                "realizedPnl": number(item.realized_pnl),
            }
            for item in stats.monthly
        ],
    }
    card = {
        "type": "table",
        "title": f"매매 성과 ({period})",
        "columns": [
            {"key": "currency", "label": "통화"},
            {"key": "realizedPnl", "label": "실현손익", "align": "right"},
            {"key": "record", "label": "승·패", "align": "right"},
            {"key": "winRate", "label": "승률", "align": "right"},
            {"key": "sellCount", "label": "매도", "align": "right"},
        ],
        "rows": [
            {
                "currency": CURRENCY_LABEL.get(item.currency, item.currency),
                "realizedPnl": signed_money(item.realized_pnl, item.currency),
                "record": f"{item.win_count}승 {item.loss_count}패",
                "winRate": "-" if item.win_rate is None else f"{item.win_rate:.2f}%",
                "sellCount": f"{item.sell_count}건",
            }
            for item in stats.by_currency
        ],
        "footnote": "실현손익은 매도 거래 기준이며 수수료·세금을 반영했습니다.",
        "href": "/journal",
    }
    return tool_output(content, card)


def get_positions(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    positions = journal_service.get_positions(ctx.db, ctx.user.id, with_quotes=True)
    if not positions:
        return status_output(
            "no_positions",
            "보유 중인 종목이 없습니다. 매매일지에 매수 기록이 있어야 보유 종목을 계산할 수 "
            "있어요.",
        )
    content = {
        "summary": f"매매일지 기준 보유 종목은 {len(positions)}개입니다.",
        "positions": [
            {
                "market": item.market,
                "symbolCode": item.symbol_code,
                "symbolName": item.symbol_name,
                "currency": item.currency,
                "quantity": number(item.quantity),
                "avgCost": number(item.avg_cost),
                "costBasis": number(item.cost_basis),
                "realizedPnl": number(item.realized_pnl),
                "currentPrice": number(item.current_price),
                "evalAmount": number(item.eval_amount),
                "unrealizedPnl": number(item.unrealized_pnl),
                "unrealizedPnlRate": item.unrealized_pnl_rate,
                "quoteSource": item.quote_source,
                "lastTradedOn": item.last_traded_on.isoformat(),
            }
            for item in positions
        ],
    }
    footnotes = ["현재가가 없는 종목은 평가손익을 계산하지 않았습니다."]
    if any(item.quote_source == "mock" for item in positions):
        footnotes.insert(0, "현재가는 샘플 데이터입니다. 실제 시세가 아닙니다.")
    card = {
        "type": "table",
        "title": "보유 종목",
        "columns": [
            {"key": "name", "label": "종목"},
            {"key": "avgCost", "label": "평균단가", "align": "right"},
            {"key": "currentPrice", "label": "현재가", "align": "right"},
            {"key": "unrealized", "label": "평가손익", "align": "right"},
        ],
        "rows": [
            {
                "name": f"{item.symbol_name} ({item.quantity}주)",
                "avgCost": money(item.avg_cost, item.currency),
                "currentPrice": money(item.current_price, item.currency) or "-",
                "unrealized": (
                    f"{signed_money(item.unrealized_pnl, item.currency)} "
                    f"({rate(item.unrealized_pnl_rate) or '-'})"
                    if item.unrealized_pnl is not None
                    else "-"
                ),
            }
            for item in positions
        ],
        "footnote": " ".join(footnotes),
        "href": "/journal",
    }
    return tool_output(content, card)


register_tool(
    ToolSpec(
        name="get_trade_stats",
        description=(
            "사용자 매매일지의 실현손익 통계(통화별 손익·승률·매도 건수·평균 수익/손실·최고/최저 "
            "거래, 종목별·월별 손익)를 확인한다. '올해 매매 성적 어때?', '어떤 종목에서 손해 "
            "봤어?' 같은 질문에 쓴다. 입력 from·to는 매도일 범위(YYYY-MM-DD)이고 전체 기간이면 둘 "
            "다 null이다. status가 no_trades면 매매 기록이 없다. 매매를 기록하거나 주문하는 기능은 "
            "없다."
        ),
        input_model=TradeStatsInput,
        label="매매 성과 확인 중",
        handler=get_trade_stats,
    )
)
register_tool(
    ToolSpec(
        name="get_positions",
        description=(
            "사용자 매매일지 기록으로 계산한 보유 "
            "종목(수량·평균단가·매입원가·현재가·평가손익·수익률)을 확인한다. '지금 뭐 들고 "
            f"있어?', '보유 종목 수익률 알려줘' 같은 질문에 쓴다. {NO_ARGUMENTS} 현재가는 국내 "
            "종목만 있고 없으면 null이다. 사거나 팔라고 권하지 않는다."
        ),
        input_model=NoInput,
        label="보유 종목 확인 중",
        handler=get_positions,
    )
)
