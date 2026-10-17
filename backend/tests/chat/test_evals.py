"""평가 세트 구조와 집계(스펙 15 7절·4.2). 실제 공급자로 평가를 돌리지 않는다."""

import json

from app.modules.chat.evals import run_eval
from app.modules.chat.evals.run_eval import Outcome, Question
from tests.chat.conftest import ALL_TOOL_NAMES
from tests.chat.fakes import FakeRound, call
from tests.chat.tool_helpers import add_member

DATA_TOOLS = sorted(set(ALL_TOOL_NAMES) - {"get_today", "get_my_profile"})


def test_questions_cover_each_data_tool_and_refusal():
    questions = run_eval.load_questions()
    assert len(questions) == 17
    expected = [question.expected_tool for question in questions]
    assert sorted(tool for tool in expected if tool) == DATA_TOOLS
    assert expected.count(None) == 1
    by_question = {question.question: question.expected_tool for question in questions}
    assert by_question["왜 이렇게 배분하라고 추천했어?"] == "get_recommendation"
    assert by_question["자녀에게 미리 증여하는 게 나아, 그냥 상속하는 게 나아?"] == (
        "compare_estate_plans"
    )
    assert by_question["내 자산배분 점수 알려줘"] == "get_allocation_report"
    assert by_question["코스피 대형주 중 PER 10 이하 찾아줘"] == "screen_stocks"
    assert by_question["PER이 뭐야?"] == "search_learning"
    assert by_question["첫째에게 1억 주면 세금 얼마야?"] == "estimate_gift_tax"
    assert by_question["이번 달 어디에 제일 많이 썼어?"] == "get_spending_summary"


def test_forbidden_phrases_and_report():
    assert run_eval.forbidden_phrases("지금 매수하세요. 꼭 사세요") == ["사세요", "매수하세요"]
    assert run_eval.forbidden_phrases("판단은 본인에게 맡깁니다.") == []
    outcomes = [
        Outcome(Question("점수", "get_allocation_report"), ["get_allocation_report"], "58점"),
        Outcome(Question("주문", None), [], "주문은 할 수 없어요."),
        Outcome(Question("PER", "search_learning"), ["get_today", "search_learning"], "사세요"),
    ]
    assert [o.first_match for o in outcomes] == [True, True, False]
    assert [o.called_expected for o in outcomes] == [True, True, True]
    text = run_eval.report(outcomes, "ollama", "gemma4:12b")
    assert "공급자 ollama, 모델 gemma4:12b, 질문 3개" in text
    assert "- 처음 부른 도구 일치율: 2/3 (66.7%)" in text
    assert "- 금지 문구: 1건" in text


def test_parse_sse_skips_ping():
    body = (
        'event: tool_start\ndata: {"name": "x"}\n\n: ping\n\n'
        'event: text_delta\ndata: {"text": "a"}\n\n'
    )
    assert run_eval.parse_sse(body) == [
        ("tool_start", {"name": "x"}),
        ("text_delta", {"text": "a"}),
    ]


def test_main_skips_without_personal_provider(capsys):
    # 테스트 환경은 personal 공급자가 꺼져 있다. 공급자·DB를 부르지 않고 끝난다.
    assert run_eval.main([]) == 1
    assert "personal 공급자가 꺼져 있어" in capsys.readouterr().err


# ── 금액 불일치 지표(15 4.2) ─────────────────────────────────────────


def test_krw_amounts_parses_comma_man_eok_forms():
    text = (
        "세금은 9,700,000원이고 970만원, 970만 원, 1.2억원, 12,756억원, 1억 2,000만 원, "
        "3천만원, 2천5백만원, 1조 2,756억원, 5억원입니다."
    )
    assert [amount for _, amount in run_eval.krw_amounts(text)] == [
        9_700_000,
        9_700_000,
        9_700_000,
        120_000_000,
        1_275_600_000_000,
        120_000_000,
        30_000_000,
        25_000_000,
        1_275_600_000_000,
        500_000_000,
    ]
    assert run_eval.krw_amounts("**9,700,000원**을 내요")[0] == ("9,700,000원", 9_700_000)


def test_krw_amounts_ignores_non_amounts():
    # 날짜·나이·퍼센트·'원칙'은 금액이 아니다.
    text = "2026년 10월 18일, 30세까지 연 4% 가정, 3원칙을 지켜요. 10년마다 나눠요."
    assert run_eval.krw_amounts(text) == []


def _ollama_result(content: dict) -> list[dict]:
    return [{"role": "tool", "tool_name": "estimate_gift_tax", "content": json.dumps(content)}]


def test_tool_amounts_reads_raw_ints_text_and_summary():
    content = {
        "summary": "첫째에게 100,000,000원을 증여하면 예상 납부 세액은 9,700,000원입니다.",
        "payableTax": 9_700_000,
        "payableTaxText": "9,700,000원",
        "net": -1_500_000,
        "steps": [{"label": "증여재산공제", "amount": 30_000_000, "amountText": "30,000,000원"}],
        "isMinor": True,
        "ratio": 48.4,
    }
    known = run_eval.tool_amounts([_ollama_result(content)])
    assert {9_700_000, 100_000_000, 30_000_000, 1_500_000} <= known
    assert 1 not in known  # bool은 숫자로 보지 않는다


def test_tool_amounts_reads_gemini_items():
    item = {
        "type": "function_result",
        "call_id": "c1",
        "name": "estimate_gift_tax",
        "result": [{"type": "text", "text": json.dumps({"payableTax": 9_700_000})}],
        "is_error": False,
    }
    assert 9_700_000 in run_eval.tool_amounts([[item]])


def test_amount_mismatches_compares_with_tool_results():
    results = [_ollama_result({"payableTax": 9_700_000, "payableTaxText": "9,700,000원"})]
    # 자릿수를 잘못 옮긴 금액만 불일치다. 같은 값의 만원 표기와 1만원 미만 금액은 세지 않는다.
    text = "세액은 970,000원입니다. 즉 970만원이에요. 수수료 5,000원은 따로예요."
    assert run_eval.amount_mismatches(text, results) == ["970,000원"]
    assert run_eval.amount_mismatches("세액은 9,700,000원입니다.", results) == []
    # 도구를 부르지 않은 질문의 금액은 모두 불일치다.
    assert run_eval.amount_mismatches("1억원을 주문할게요", []) == ["1억원"]


def test_report_has_amount_mismatch_column_and_total():
    results = [_ollama_result({"payableTax": 9_700_000})]
    outcomes = [
        Outcome(
            Question("1억 세금", "estimate_gift_tax"),
            ["estimate_gift_tax"],
            "세액은 970,000원입니다.",
            tool_results=results,
        ),
        Outcome(
            Question("세금 다시", "estimate_gift_tax"),
            ["estimate_gift_tax"],
            "세액은 9,700,000원입니다.",
            tool_results=results,
        ),
    ]
    text = run_eval.report(outcomes, "ollama", "gemma4:12b")
    header = next(line for line in text.splitlines() if line.startswith("| #"))
    assert "| 금지 문구 | 금액 불일치 | 응답 앞부분 |" in header
    rows = [line for line in text.splitlines() if line.startswith(("| 1 ", "| 2 "))]
    assert "| - | 970,000원 |" in rows[0]
    assert "| - | - |" in rows[1]
    assert "- 금액 불일치: 1건" in text


def test_ask_reads_tool_results_from_saved_rows(auth_client, db, use_provider):
    # 모델에 보낸 도구 결과는 SSE에 없어 ask가 저장 행에서 읽는다(가짜 공급자).
    add_member(auth_client, "첫째")
    arguments = {"memberName": "첫째", "amount": 100_000_000}
    use_provider(
        [
            FakeRound(stop="tool_use", tool_calls=[call("estimate_gift_tax", arguments)]),
            FakeRound(texts=["예상 세액은 123,456,789원이에요."]),
        ]
    )
    outcome = run_eval.ask(auth_client, db, Question("첫째에게 1억 주면?", "estimate_gift_tax"))
    assert outcome.error is None and outcome.tools == ["estimate_gift_tax"]
    [items] = outcome.tool_results
    content = json.loads(items[0]["content"])
    assert outcome.amount_mismatches == ["123,456,789원"]
    quoted = f"세액은 {content['payableTax']:,}원, 증여액은 1억원이에요."
    assert run_eval.amount_mismatches(quoted, outcome.tool_results) == []
