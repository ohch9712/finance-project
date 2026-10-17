"""가계부·매매일지 도구(스펙 15 4절·5절): get_spending_summary, get_trade_stats, get_positions."""

from datetime import date

import pytest

from app.modules.journal import service as journal_service
from app.modules.ledger import service as ledger_service
from tests.chat.tool_helpers import Spy, add_entry, add_trade, run, tool_ctx

EXPENSES = [
    ("식비", 300_000),
    ("교통", 120_000),
    ("쇼핑", 90_000),
    ("통신", 60_000),
    ("문화·여가", 40_000),
    ("의료·건강", 10_000),
]


def seed_month(client, month: str = "2026-10"):
    add_entry(client, "income", "급여", 3_000_000, f"{month}-10")
    for name, amount in EXPENSES:
        add_entry(client, "expense", name, amount, f"{month}-05", merchant=f"{name} 가게")


# ── get_spending_summary ────────────────────────────────────────────


def test_spending_this_month_by_default(auth_client, db, monkeypatch):
    seed_month(auth_client)
    spy = Spy(monkeypatch, ledger_service, "get_monthly_summary")
    ctx = tool_ctx(db)
    result = run(ctx, "get_spending_summary", {"month": None})
    assert spy.calls == [((db, ctx.user.id, date(2026, 10, 1)), {})]

    content = result.content
    assert content["month"] == "2026-10"
    assert content["incomeTotal"] == 3_000_000
    assert content["expenseTotal"] == 620_000
    assert content["net"] == 2_380_000
    assert content["summary"].startswith("2026년 10월 수입 3,000,000원, 지출 620,000원입니다.")
    assert "식비(300,000원)" in content["summary"]
    # 원화 금액 옆에 형식화 문자열을 함께 둔다(15 4.2).
    salary = {
        "name": "급여",
        "kind": "income",
        "amount": 3_000_000,
        "amountText": "3,000,000원",
        "ratio": 100.0,
    }
    assert salary in content["byCategory"]
    assert (content["incomeTotalText"], content["netText"]) == ("3,000,000원", "2,380,000원")

    card = result.card
    assert set(card) == {
        "type",
        "month",
        "incomeTotal",
        "expenseTotal",
        "net",
        "topCategories",
        "href",
    }
    assert card["type"] == "spending"
    assert card["month"] == "2026년 10월"
    assert (card["incomeTotal"], card["expenseTotal"], card["net"]) == (
        "3,000,000원",
        "620,000원",
        "2,380,000원",
    )
    # 지출 카테고리만 앞 5개(4.1)
    assert [item["name"] for item in card["topCategories"]] == [n for n, _ in EXPENSES[:5]]
    assert card["topCategories"][0] == {"name": "식비", "amount": "300,000원", "ratio": "48.4%"}
    assert card["href"] == "/ledger?month=2026-10"


def test_spending_specific_month(auth_client, db, monkeypatch):
    seed_month(auth_client, "2026-08")
    spy = Spy(monkeypatch, ledger_service, "get_monthly_summary")
    result = run(tool_ctx(db), "get_spending_summary", {"month": "2026-08"})
    assert spy.calls[0][0][2] == date(2026, 8, 1)
    assert result.content["expenseTotal"] == 620_000
    assert result.card["href"] == "/ledger?month=2026-08"


@pytest.mark.parametrize("month", ["2026-13", "2026/10", "202610", "26-10"])
def test_spending_month_format_is_validated(auth_client, db, month):
    result = run(tool_ctx(db), "get_spending_summary", {"month": month})
    assert result.is_error
    assert "INVALID_INPUT" in result.content


def test_spending_empty_month_is_status(auth_client, other_auth_client, db):
    seed_month(other_auth_client)  # 다른 사용자 데이터는 섞이지 않는다
    result = run(tool_ctx(db), "get_spending_summary", {"month": None})
    assert not result.is_error
    assert result.content["status"] == "no_entries"
    assert "가계부 화면" in result.content["message"]
    assert result.card is None


# ── get_trade_stats ─────────────────────────────────────────────────


def seed_trades(client):
    add_trade(client, "buy", "10", "70000", "2026-03-02")
    add_trade(client, "sell", "5", "80000", "2026-05-02")
    add_trade(client, "sell", "5", "60000", "2026-09-01")


def test_trade_stats_range_and_card(auth_client, db, monkeypatch):
    seed_trades(auth_client)
    spy = Spy(monkeypatch, journal_service, "get_trade_stats")
    ctx = tool_ctx(db)
    result = run(ctx, "get_trade_stats", {"from": "2026-01-01", "to": "2026-06-30"})
    assert spy.calls == [((db, ctx.user.id, date(2026, 1, 1), date(2026, 6, 30)), {})]
    content = result.content
    krw = content["byCurrency"][0]
    assert krw["currency"] == "KRW"
    assert krw["realizedPnl"] == 50_000 and krw["sellCount"] == 1
    assert krw["bestTrade"] == {
        "symbolName": "삼성전자",
        "realizedPnl": 50_000,
        "realizedPnlText": "50,000원",  # 원화 금액 옆 형식화 문자열(15 4.2)
        "tradedOn": "2026-05-02",
    }
    assert "tradeId" not in krw["bestTrade"]
    card = result.card
    assert card["type"] == "table"
    assert card["title"] == "매매 성과 (2026.01.01 ~ 2026.06.30)"
    assert card["href"] == "/journal"
    assert card["rows"] == [
        {
            "currency": "원화",
            "realizedPnl": "+50,000원",
            "record": "1승 0패",
            "winRate": "100.00%",
            "sellCount": "1건",
        }
    ]
    assert [column["key"] for column in card["columns"]] == list(card["rows"][0])


def test_trade_stats_whole_period(auth_client, db):
    seed_trades(auth_client)
    result = run(tool_ctx(db), "get_trade_stats", {"from": None, "to": None})
    assert result.content["byCurrency"][0]["realizedPnl"] == 0
    assert result.card["title"] == "매매 성과 (전체 기간)"


def test_trade_stats_input_validation(auth_client, db):
    for arguments in (
        {"start": "2026-01-01"},
        {"from": "2026-06-01", "to": "2026-01-01"},
        {"from": "어제", "to": None},
    ):
        result = run(tool_ctx(db), "get_trade_stats", arguments)
        assert result.is_error and "INVALID_INPUT" in result.content


def test_trade_stats_without_trades_is_status(auth_client, other_auth_client, db):
    seed_trades(other_auth_client)
    result = run(tool_ctx(db), "get_trade_stats", {"from": None, "to": None})
    assert result.content["status"] == "no_trades"
    assert result.card is None


# ── get_positions ───────────────────────────────────────────────────


def test_positions_with_quotes(auth_client, db, market, monkeypatch):
    add_trade(auth_client, "buy", "10", "70000", "2026-03-02")
    spy = Spy(monkeypatch, journal_service, "get_positions")
    ctx = tool_ctx(db)
    result = run(ctx, "get_positions")
    assert spy.calls == [((db, ctx.user.id), {"with_quotes": True})]
    position = result.content["positions"][0]
    assert position["symbolCode"] == "005930" and position["quantity"] == 10
    assert position["avgCost"] == 70_000
    assert position["currentPrice"] == 102_800  # MockBroker 시세
    assert position["quoteSource"] == "mock"
    card = result.card
    assert card["type"] == "table" and card["href"] == "/journal"
    row = card["rows"][0]
    assert row["name"] == "삼성전자 (10주)"
    assert [column["key"] for column in card["columns"]] == list(row)
    assert row["avgCost"] == "70,000원" and row["currentPrice"] == "102,800원"
    assert row["unrealized"] == "+328,000원 (+46.86%)"
    assert card["footnote"].startswith("현재가는 샘플 데이터입니다.")


def test_positions_empty_is_status(auth_client, other_auth_client, db):
    add_trade(other_auth_client, "buy", "10", "70000", "2026-03-02")
    result = run(tool_ctx(db), "get_positions")
    assert result.content["status"] == "no_positions"
    assert result.card is None
