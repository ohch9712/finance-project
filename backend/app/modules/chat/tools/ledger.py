"""가계부 도구: get_spending_summary(스펙 15 4절)."""

import re
from datetime import date

from pydantic import BaseModel, field_validator

from app.modules.chat.tools.common import ToolInput, krw, share, status_output, tool_output
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.ledger import service as ledger_service

TOP_CATEGORY_LIMIT = 5
MONTH_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class SpendingInput(ToolInput):
    month: str | None = None  # "YYYY-MM". null이면 이번 달(KST)

    @field_validator("month")
    @classmethod
    def valid_month(cls, value: str | None) -> str | None:
        if value is not None and not MONTH_PATTERN.match(value):
            raise ValueError("month는 YYYY-MM 형식이어야 합니다.")
        return value


def _month_start(month: str | None, today: date) -> date:
    if month is None:
        return today.replace(day=1)
    year, number = MONTH_PATTERN.match(month).groups()
    return date(int(year), int(number), 1)


def _month_title(start: date) -> str:
    return f"{start.year}년 {start.month}월"


def get_spending_summary(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, SpendingInput)
    start = _month_start(params.month, ctx.today)
    summary = ledger_service.get_monthly_summary(ctx.db, ctx.user.id, start)
    title = _month_title(start)
    if summary.income_total == 0 and summary.expense_total == 0 and not summary.by_category:
        return status_output(
            "no_entries",
            f"{title} 가계부 내역이 없습니다. 가계부 화면에서 수입·지출을 입력하거나 "
            "카드 이용내역 파일을 올려 주세요.",
            month=summary.month,
        )
    expenses = [item for item in summary.by_category if item.kind == "expense"]
    top = expenses[:TOP_CATEGORY_LIMIT]
    # summary의 금액은 format_krw 전체 자릿수로 쓴다(만원·억원 축약 없음, 4.2).
    sentence = f"{title} 수입 {krw(summary.income_total)}, 지출 {krw(summary.expense_total)}입니다."
    if top:
        sentence += f" 지출이 가장 많은 카테고리는 {top[0].name}({krw(top[0].amount)})입니다."
    content = {
        "summary": sentence,
        "month": summary.month,
        "incomeTotal": summary.income_total,
        "expenseTotal": summary.expense_total,
        "net": summary.net,
        "previousMonth": {
            "month": summary.previous.month,
            "incomeTotal": summary.previous.income_total,
            "expenseTotal": summary.previous.expense_total,
        },
        "expenseChangeRate": summary.expense_change_rate,  # 지난달 대비 지출 증감(%)
        "byCategory": [
            {"name": item.name, "kind": item.kind, "amount": item.amount, "ratio": item.ratio}
            for item in summary.by_category
        ],
        "topMerchants": [
            {"merchant": item.merchant, "amount": item.amount, "count": item.count}
            for item in summary.top_merchants
        ],
    }
    card = {
        "type": "spending",
        "month": title,
        "incomeTotal": krw(summary.income_total),
        "expenseTotal": krw(summary.expense_total),
        "net": krw(summary.net),
        "topCategories": [
            {"name": item.name, "amount": krw(item.amount), "ratio": share(item.ratio)}
            for item in top
        ],
        "href": f"/ledger?month={summary.month}",
    }
    return tool_output(content, card)


register_tool(
    ToolSpec(
        name="get_spending_summary",
        description=(
            "사용자 가계부의 한 달 수입·지출 합계, 카테고리별 금액과 비율(ratio, 같은 종류 안의 "
            "%), 지출이 많은 가맹점, 지난달 대비 지출 증감률을 확인한다. '이번 달 어디에 제일 많이 "
            "썼어?', '지난달 지출 요약해줘' 같은 질문에 쓴다. 입력 month는 'YYYY-MM'이다. 이번 "
            "달이면 get_today 없이 바로 month를 null로 부른다(도구가 오늘 날짜로 이번 달을 "
            "정한다). 지난달처럼 상대적인 달만 get_today로 날짜를 먼저 확인한다. "
            "status가 no_entries면 그 달 내역이 없다는 뜻이니 입력 방법을 안내한다. 가계부를 "
            "기록하거나 고치는 기능은 없다."
        ),
        input_model=SpendingInput,
        label="가계부 확인 중",
        handler=get_spending_summary,
    )
)
