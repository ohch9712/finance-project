"""모듈 데이터 도구의 공통 도우미(스펙 15 4절).

- 모델에 보내는 content는 숫자를 형식화하지 않은 JSON과 요약 문장(`summary`)이다. 원화 금액
  필드 옆에는 `<이름>Text`(format_krw 전체 자릿수)를 함께 둔다(4.2, `tool_output`).
- 카드에 넣는 값은 화면에 그대로 보일 문자열로 서버에서 형식화한다(스펙 15 5절).
- 예상한 AppError는 `{"status", "message"}` 정상 결과로 바꾼다(스펙 15 4.1). 그 밖의 예외는
  잡지 않는다(09의 execute_tool이 "도구 실행 실패"로 바꾼다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.core.errors import AppError
from app.core.format import format_krw, format_krw_compact
from app.modules.chat.tools.registry import ToolOutput

KRX_MARKETS = frozenset({"KOSPI", "KOSDAQ", "KONEX"})
MARKET_LABEL = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KONEX": "코넥스"}
NO_ARGUMENTS = "인자 없이 빈 객체({})로 부른다."

# 모델용 content의 원화 금액 필드 이름(스펙 15 4.2). 퍼센트·비율·수량·개수·점수는 넣지 않는다.
KRW_FIELDS = frozenset(
    {
        # 자산배분(02): targetAmounts는 자산군별 금액 맵이다.
        "totalAmount",
        "excludedAmount",
        "actualAmount",
        "targetAmount",
        "diffAmount",
        "targetAmounts",
        # 자산배분 방향·가계부 항목·상속·증여 단계·증여 일정
        "amount",
        # 가계부(03)
        "incomeTotal",
        "expenseTotal",
        "net",
        # 매매일지(05): 원화 거래만
        "realizedPnl",
        "avgWin",
        "avgLoss",
        "avgCost",
        "costBasis",
        "currentPrice",
        "evalAmount",
        "unrealizedPnl",
        # 시세·지표(08·12·13): 국내 시장만
        "price",
        "change",
        "marketCap",
        "tradingValue",
        "eps",
        "bps",
        "high52w",
        "low52w",
        # 상속·증여(06)
        "limit",
        "usedWithin10y",
        "used",
        "available",
        "taxableValue",
        "deduction",
        "marriageBirthDeduction",
        "taxBase",
        "calculatedTax",
        "surcharge",
        "priorTaxCredit",
        "filingCredit",
        "payableTax",
        "financialAssets",
        "realEstate",
        "otherAssets",
        "debts",
        "totalGifted",
        "giftTaxTotal",
        "giftsValueAtHorizon",
        "estateAtHorizon",
        "inheritanceTax",
        "totalTax",
        "familyReceives",
        "giftTax",
        "tax",
        "futureValue",
    }
)
DOMESTIC_MARKETS = KRX_MARKETS | {"KRX"}  # 매매일지는 국내 시장을 KRX로 적는다
_NOT_AMOUNT = object()


class ToolInput(BaseModel):
    """도구 입력 모델의 기반. 키는 camelCase만 받고 모르는 키는 거부한다."""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=False)


class NoInput(ToolInput):
    pass


# ── 모델용 값(형식화하지 않음) ─────────────────────────────────────────


def number(value: Decimal | int | float | str | None) -> int | float | None:
    """Decimal·문자열 decimal을 JSON 숫자로 바꾼다. 정수 값은 int(뒤의 .000000을 없앤다)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("bool은 숫자로 바꾸지 않는다.")
    if isinstance(value, int):
        return value
    decimal = Decimal(str(value))
    if decimal == decimal.to_integral_value():
        return int(decimal)
    return float(decimal.normalize())


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def plain(value: Any) -> Any:
    """서비스 결과를 json.dumps 가능한 값으로 바꾼다(Decimal은 숫자, 날짜는 ISO 문자열)."""
    if value is None or isinstance(value, bool | str | int | float):
        return value
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, BaseModel):
        return plain(value.model_dump(by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    raise TypeError(f"도구 결과로 바꿀 수 없는 값입니다: {type(value).__name__}")


# ── 카드용 문자열(서버에서 형식화) ─────────────────────────────────────


def krw(value: int | Decimal | str | None) -> str | None:
    if value is None:
        return None
    return format_krw(int(Decimal(str(value))))


def krw_compact(value: int | None) -> str | None:
    """`5,670만원`, `1.2억원`, `12,756억원`(프론트 formatKRWCompact와 같은 모양)"""
    if value is None:
        return None
    return format_krw_compact(value)


def money(value: Decimal | str | None, currency: str) -> str | None:
    """원화는 `1,234원`, 달러는 `$12.34`(매매일지 화면과 같은 규칙)."""
    if value is None:
        return None
    decimal = Decimal(str(value))
    if currency == "USD":
        return f"${decimal:,.2f}" if decimal >= 0 else f"-${-decimal:,.2f}"
    return format_krw(int(decimal.to_integral_value()))


def signed_money(value: Decimal | str | None, currency: str) -> str | None:
    text = money(value, currency)
    if text is None:
        return None
    return f"+{text}" if Decimal(str(value)) > 0 else text


def price(value: Decimal | str | None, market: str | None) -> str | None:
    """국내 시장은 원, 그 밖의 시장은 달러로 보여 준다."""
    if value is None:
        return None
    return money(value, "KRW" if market is None or market in KRX_MARKETS else "USD")


def _fixed(value: float | Decimal | int, digits: int) -> Decimal:
    """소수 digits자리 사사오입(ROUND_HALF_UP). -0은 0으로 둔다."""
    rounded = Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP)
    return rounded.copy_abs() if rounded == 0 else rounded


def rate(value: float | Decimal | None) -> str | None:
    """등락률·수익률: `+1.11%`, `-1.11%`, `0.00%`"""
    if value is None:
        return None
    rounded = _fixed(value, 2)
    return f"+{rounded}%" if rounded > 0 else f"{rounded}%"


def share(value: float | Decimal | None) -> str | None:
    """비중: `35.2%`"""
    return None if value is None else f"{_fixed(value, 1)}%"


def unit(value: Decimal | int | float | None, suffix: str, digits: int = 2) -> str | None:
    """PER `12.34배`, ROE `15.20%`처럼 고정 소수 자릿수와 단위를 붙인다."""
    return None if value is None else f"{_fixed(value, digits):,}{suffix}"


def day_text(value: date | None) -> str | None:
    """`2026.10.16`(프론트 formatDate와 같은 모양)"""
    return None if value is None else value.strftime("%Y.%m.%d")


def market_label(market: str | None) -> str | None:
    if market is None:
        return None
    return MARKET_LABEL.get(market, market)


# ── 모델용 금액 문자열(4.2) ────────────────────────────────────────────


def _is_krw(item: Mapping, inherited: bool) -> bool:
    """같은 객체의 currency(우선)나 market으로 원화 여부를 정한다. 둘 다 없으면 바깥을 따른다."""
    if "currency" in item:
        return item["currency"] == "KRW"
    if "market" in item:
        return item["market"] is None or item["market"] in DOMESTIC_MARKETS
    return inherited


def _krw_text(value: Any) -> Any:
    """금액 하나는 `9,700,000원`, 자산군별 금액 맵은 같은 키의 문자열 맵, null은 null."""
    if value is None:
        return None
    if isinstance(value, bool):
        return _NOT_AMOUNT
    if isinstance(value, int | float):
        return money(value, "KRW")  # 정수가 아닌 원화(평균단가 등)는 카드와 같게 반올림한다
    if isinstance(value, Mapping) and value:
        texts = {str(key): _krw_text(item) for key, item in value.items()}
        if all(text is None or isinstance(text, str) for text in texts.values()):
            return texts
    return _NOT_AMOUNT


def with_krw_text(value: Any, krw: bool = True) -> Any:
    """content의 원화 금액 필드(KRW_FIELDS) 옆에 `<이름>Text`를 더한 사본을 돌려준다.

    목록 안의 금액도 같다. 원값은 지우지 않는다. 외화(currency가 KRW가 아니거나 market이 해외
    시장인 객체와 그 안쪽)는 건너뛴다.
    """
    if isinstance(value, list | tuple):
        return [with_krw_text(item, krw) for item in value]
    if not isinstance(value, Mapping):
        return value
    krw = _is_krw(value, krw)
    result: dict[str, Any] = {}
    for key, item in value.items():
        result[key] = with_krw_text(item, krw)
        text_key = f"{key}Text"
        if krw and key in KRW_FIELDS and text_key not in value:
            text = _krw_text(item)
            if text is not _NOT_AMOUNT:
                result[text_key] = text
    return result


def tool_output(content: dict, card: dict | None = None) -> ToolOutput:
    """모듈 도구의 결과. content의 원화 금액에 `…Text`를 더한다(4.2). 카드는 그대로 둔다."""
    return ToolOutput(with_krw_text(content), card)


# ── 상태 결과 ──────────────────────────────────────────────────────────


def status_output(status: str, message: str, **extra: Any) -> ToolOutput:
    """데이터 없음·예상 오류를 오류가 아닌 정상 결과로 돌려준다(카드 없음)."""
    return tool_output({"status": status, "message": message, **plain(extra)})


def error_status(exc: AppError, statuses: Mapping[str, str]) -> ToolOutput:
    """예상한 AppError(code 기준)를 상태 결과로 바꾼다. 목록에 없으면 다시 던진다."""
    status = statuses.get(exc.code)
    if status is None:
        raise exc
    extra: dict[str, Any] = {}
    details = [
        {"field": detail.field, "message": detail.message}
        for detail in exc.details
        if detail.message
    ]
    if details:
        extra["details"] = details
    return status_output(status, exc.message, **extra)
