"""도구 결과의 원화 금액 문자열과 시스템 프롬프트 금액 인용 문장(스펙 15 4.2).

로컬 Ollama 평가에서 모델이 payableTax 9700000을 "970,000원"으로 옮겼다. 원화 금액 필드 옆에
format_krw 문자열(`…Text`)을 함께 두고, summary는 전체 자릿수로 쓰는지 고정한다.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.format import format_krw
from app.modules.chat import prompts
from app.modules.chat.tools import common
from app.modules.chat.tools.common import KRW_FIELDS, with_krw_text
from tests.chat.tool_helpers import (
    add_entry,
    add_gift,
    add_holding,
    add_member,
    add_trade,
    assess,
    run,
    tool_ctx,
)

SCREEN_FILTER = {
    "markets": ["KOSPI"],
    "kinds": None,
    "sectors": None,
    "conditions": [{"field": "per", "op": "gt", "value": 0, "valueTo": None}],
    "sort": None,
    "limit": 30,
}

# 모델용 content의 숫자 중 원화 금액이 아닌 필드(퍼센트·비율·수량·개수·점수·나이 등).
# 새 숫자 필드는 KRW_FIELDS나 여기 중 한 곳에 넣어야 한다.
NON_AMOUNT_FIELDS = frozenset(
    {
        "score",
        "scoreDelta",
        "actualWeight",
        "targetWeight",
        "weight",
        "baseWeights",
        "targetWeights",
        "priority",
        "expenseChangeRate",
        "ratio",
        "count",
        "winCount",
        "lossCount",
        "winRate",
        "sellCount",
        "quantity",
        "unrealizedPnlRate",
        "changeRate",
        "volume",
        "per",
        "pbr",
        "roe",
        "foreignRatio",
        "total",
        "excludedForMissingData",
        "shownCount",
        "age",
        "priorGiftCount",
        "horizonYears",
        "annualGrowthRate",
        "childCount",
        "grandchildCount",
        "giftCount",
        "targetAge",
        "annualReturnRate",
        "level",
    }
)
ABBREVIATED_KRW = re.compile(r"\d\s*[만억]\s*원")


def expected_text(value):
    if value is None:
        return None
    return format_krw(int(Decimal(str(value)).to_integral_value()))


# ── 공용 도우미 ─────────────────────────────────────────────────────


def test_with_krw_text_adds_text_next_to_amounts():
    content = {
        "summary": "요약",
        "payableTax": 9_700_000,
        "score": 58,
        "ratio": 48.4,
        "isMinor": True,
        "deduction": None,
        "net": -1_500_000,
        "avgCost": 70_000.6,
        "steps": [{"label": "증여재산공제", "amount": 50_000_000, "note": None}],
        "targetAmounts": {"stock": 1_000_000, "bond": None},
    }
    result = with_krw_text(content)
    assert result["payableTax"] == 9_700_000  # 원값은 그대로 둔다
    assert result["payableTaxText"] == "9,700,000원"
    assert result["netText"] == "-1,500,000원"
    assert result["avgCostText"] == "70,001원"  # 카드(money)와 같게 반올림
    assert result["deductionText"] is None
    assert result["steps"] == [
        {"label": "증여재산공제", "amount": 50_000_000, "amountText": "50,000,000원", "note": None}
    ]
    assert result["targetAmountsText"] == {"stock": "1,000,000원", "bond": None}
    assert not {"summaryText", "scoreText", "ratioText", "isMinorText"} & set(result)
    assert "payableTaxText" not in content  # 입력을 바꾸지 않는다


def test_with_krw_text_skips_foreign_currency_and_markets():
    content = {
        "byCurrency": [
            {"currency": "USD", "realizedPnl": 12.5, "bestTrade": {"realizedPnl": 10}},
            {"currency": "KRW", "realizedPnl": 50_000, "bestTrade": {"realizedPnl": 30_000}},
        ],
        "items": [
            {"market": "NASDAQ", "quote": {"price": 180}},
            {"market": "KOSPI", "quote": {"price": 70_000}},
            {"market": None, "quote": {"price": 5_000}},
        ],
    }
    usd, krw = with_krw_text(content)["byCurrency"]
    assert "realizedPnlText" not in usd and "realizedPnlText" not in usd["bestTrade"]
    assert krw["realizedPnlText"] == "50,000원"
    assert krw["bestTrade"]["realizedPnlText"] == "30,000원"
    nasdaq, kospi, unknown = with_krw_text(content)["items"]
    assert "priceText" not in nasdaq["quote"]
    assert kospi["quote"]["priceText"] == "70,000원"
    assert unknown["quote"]["priceText"] == "5,000원"  # 시장을 모르면 원화(카드 price와 같다)


def test_with_krw_text_keeps_existing_text():
    assert with_krw_text({"amount": 1000, "amountText": "그대로"}) == {
        "amount": 1000,
        "amountText": "그대로",
    }


def test_module_tools_build_results_with_tool_output():
    # 모듈 데이터 도구는 tool_output(또는 status_output·error_status)으로만 결과를 만든다.
    folder = Path(common.__file__).parent
    for path in sorted(folder.glob("*.py")):
        if path.name in {"__init__.py", "registry.py", "basic.py", "common.py"}:
            continue
        assert "ToolOutput(" not in path.read_text(encoding="utf-8"), path.name


# ── 도구 16개의 실제 결과 ───────────────────────────────────────────


def _check_amounts(value, krw: bool, problems: list[str], path: str = "content") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_amounts(item, krw, problems, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    krw = common._is_krw(value, krw)
    for key, item in value.items():
        where = f"{path}.{key}"
        if key.endswith("Text") and key.removesuffix("Text") in KRW_FIELDS:
            continue
        if key in KRW_FIELDS:
            text = value.get(f"{key}Text", "(없음)")
            if not krw:
                if f"{key}Text" in value:
                    problems.append(f"{where}: 외화인데 Text가 있다")
            elif isinstance(item, dict):
                if text != {k: expected_text(v) for k, v in item.items()}:
                    problems.append(f"{where}: {text!r}")
            elif text != expected_text(item):
                problems.append(f"{where}: {item!r} → {text!r}")
            continue
        if key in NON_AMOUNT_FIELDS:
            continue
        if isinstance(item, int | float) and not isinstance(item, bool):
            problems.append(f"{where}: 금액인지 분류하지 않은 숫자 필드")
        else:
            _check_amounts(item, krw, problems, where)


@pytest.fixture
def seeded(auth_client, admin_client, db, market):
    """도구 16개가 모두 데이터를 돌려주는 사용자(원화·달러 매매 포함)."""
    assess(auth_client)
    add_holding(auth_client, "stock", 30_000_000)
    add_holding(auth_client, "bond", 15_000_000)
    add_holding(auth_client, "cash", 10_000_000)
    add_holding(auth_client, "other", 400_000_000, include=False)
    add_entry(auth_client, "income", "급여", 4_000_000, "2026-10-01")
    add_entry(auth_client, "expense", "식비", 450_000, "2026-10-02", merchant="식당")
    add_trade(auth_client, "buy", "10", "70000", "2026-03-02")
    add_trade(auth_client, "sell", "5", "80000", "2026-05-02")
    us = {"market": "US", "symbolCode": "AAPL", "symbolName": "애플", "currency": "USD"}
    add_trade(auth_client, "buy", "2", "180.50", "2026-03-03", **us)
    add_trade(auth_client, "sell", "1", "190.25", "2026-06-03", **us)
    first = add_member(auth_client, "첫째", birthDate="2015-04-01")
    add_member(auth_client, "배우자", relation="spouse")
    add_gift(auth_client, first["id"], 20_000_000, "2025-01-10")
    response = admin_client.post(
        "/api/learn/admin/posts",
        json={
            "kind": "glossary",
            "slug": "per-zz42",
            "title": "PER 주가수익비율 zz42",
            "summary": "주가를 주당순이익으로 나눈 값",
            "bodyMd": "본문",
            "term": "PER zz42",
            "published": True,
        },
    )
    assert response.status_code == 201, response.text
    return tool_ctx(db)


TOOL_CALLS = [
    ("get_allocation_report", {}),
    ("get_recommendation", {}),
    ("get_risk_profile", {}),
    ("get_spending_summary", {"month": None}),
    ("get_trade_stats", {"from": None, "to": None}),
    ("get_positions", {}),
    ("search_stocks", {"query": "삼성"}),
    ("get_stock_detail", {"code": "005930"}),
    ("screen_stocks", {"filter": SCREEN_FILTER}),
    ("add_to_watchlist", {"code": "005930"}),
    ("get_watchlist", {}),
    ("get_gift_deductions", {"memberName": None}),
    ("estimate_gift_tax", {"memberName": "첫째", "amount": 100_000_000}),
    ("compare_estate_plans", {}),
    ("plan_gifts", {"memberName": "첫째"}),
    ("search_learning", {"query": "zz42"}),
]


def _keys(value) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value))
    if isinstance(value, dict):
        return set(value).union(*(_keys(item) for item in value.values()))
    return set()


def test_every_tool_result_has_text_for_krw_amounts(seeded):
    problems: list[str] = []
    for name, arguments in TOOL_CALLS:
        result = run(seeded, name, arguments)
        assert not result.is_error, (name, result.content)
        # 데이터 없음 상태가 아니라 실제 결과로 확인한다.
        assert result.content.get("status", "ready") == "ready", (name, result.content)
        _check_amounts(result.content, True, problems, name)
        # summary는 전체 자릿수로 쓴다(만원·억원 축약 없음).
        assert not ABBREVIATED_KRW.search(result.content.get("summary", "")), name
        # 카드는 바꾸지 않는다(표시 값은 서버에서 형식화한 문자열 그대로).
        assert not any(key.endswith("Text") for key in _keys(result.card)), name
    assert problems == []


def test_estimate_gift_tax_quotes_full_digits(seeded):
    result = run(seeded, "estimate_gift_tax", {"memberName": "첫째", "amount": 100_000_000})
    content = result.content
    assert content["payableTaxText"] == format_krw(content["payableTax"])
    assert f"예상 납부 세액은 {content['payableTaxText']}입니다" in content["summary"]
    assert "100,000,000원을 증여하면" in content["summary"]
    assert all(step["amountText"] == format_krw(step["amount"]) for step in content["steps"])
    # 카드 표시 값은 그대로다.
    assert result.card["payableTax"] == format_krw(content["payableTax"])


def test_trade_stats_texts_only_for_krw(seeded):
    content = run(seeded, "get_trade_stats", {"from": None, "to": None}).content
    by_currency = {item["currency"]: item for item in content["byCurrency"]}
    assert by_currency["KRW"]["realizedPnlText"] == "50,000원"
    assert "realizedPnlText" not in by_currency["USD"]
    assert "$" in content["summary"]  # 달러는 money() 문자열 그대로


def test_plan_gifts_and_ledger_texts(seeded):
    plan = run(seeded, "plan_gifts", {"memberName": "첫째"}).content
    assert plan["totalGiftedText"] == format_krw(plan["totalGifted"])
    assert f"총 {plan['totalGiftedText']}, 예상 세금 {plan['totalTaxText']}" in plan["summary"]
    assert plan["lumpSum"]["payableTaxText"] == format_krw(plan["lumpSum"]["payableTax"])
    assert all("amountText" in item and "taxText" in item for item in plan["schedule"])
    ledger = run(seeded, "get_spending_summary", {"month": None}).content
    assert ledger["summary"].startswith("2026년 10월 수입 4,000,000원, 지출 450,000원입니다.")
    assert ledger["byCategory"][0]["amountText"] == format_krw(ledger["byCategory"][0]["amount"])
    assert ledger["previousMonth"]["expenseTotalText"] == "0원"


# ── 시스템 프롬프트 ─────────────────────────────────────────────────


AMOUNT_SENTENCE = (
    "- 금액은 도구 결과에서 이름이 Text로 끝나는 값(예: payableTaxText)이나 summary에 적힌"
    " 그대로 옮긴다. 만원·억원으로 바꾸거나 자릿수를 다시 세지 않는다."
)


def test_system_prompt_has_amount_quoting_sentence_once():
    lines = prompts.SYSTEM_PROMPT.split("\n")
    assert lines.count(AMOUNT_SENTENCE) == 1
    # 15가 더한 도구 사용 절에 둔다(09의 고정 규칙은 그대로).
    assert lines.index(AMOUNT_SENTENCE) > lines.index("도구 사용")
    assert "- 숫자는 도구 결과에 있는 값만 인용하고 단위(원, %)를 붙인다." in lines
