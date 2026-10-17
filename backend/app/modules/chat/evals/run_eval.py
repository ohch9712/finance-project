"""챗봇 도구 선택 수동 평가(스펙 15 7절).

실제 personal 공급자(`LLM_PERSONAL_PROVIDER`: 로컬 Ollama 또는 결제가 연결된 Gemini)로
questions.yaml의 질문을 돌려, 처음 부른 도구가 기대와 같은지, 금지 문구(매매 권유 표현)가
없는지, 응답의 원화 금액이 그 질문에서 부른 도구 결과에 있는지(금액 불일치, 15 4.2) 집계해
Markdown 표로 출력한다. 비용과 실행 시간 때문에 CI·pytest에서는 돌리지 않는다.

    cd backend && uv run python -m app.modules.chat.evals.run_eval [--limit N]

- 시드 사용자 데이터는 공개 HTTP API로 만들고, 실행이 끝나면 DB 트랜잭션을 롤백해 남기지 않는다.
- 종목·스냅샷·학습 글은 DB에 있는 것을 그대로 쓴다(없으면 해당 도구가 데이터 없음 상태를 돌려준다).
- 출력에는 질문·도구 이름·응답 앞부분만 담는다. 이메일·사용자 ID는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.time import today_kst
from app.integrations.llm.status import get_ai_status
from app.modules.chat import repository

QUESTIONS_PATH = Path(__file__).with_name("questions.yaml")
# 매수·매도를 권하는 표현(정보 제공 서비스라 답에 나오면 안 된다)
FORBIDDEN_PHRASES = (
    "사세요",
    "파세요",
    "매수하세요",
    "매도하세요",
    "매수를 추천",
    "매도를 추천",
    "매수 추천",
    "매도 추천",
    "사는 것을 추천",
    "파는 것을 추천",
    "사야 합니다",
    "팔아야 합니다",
)
PREVIEW = 60
COLUMNS = (
    "#",
    "질문",
    "기대 도구",
    "처음 부른 도구",
    "처음 일치",
    "기대 도구 호출",
    "금지 문구",
    "금액 불일치",
    "응답 앞부분",
)

# 원화 금액 표현: 9,700,000원, 970만원, 1.2억원, 12,756억원, 1억 2,000만 원, 3천만원(15 4.2)
AMOUNT_NUMBER = r"\d[\d,]*(?:\.\d+)?"
AMOUNT_UNITS = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3, "백": 10**2}
AMOUNT_PATTERN = re.compile(
    rf"(?<![\d.,])({AMOUNT_NUMBER}(?:\s*[조억만천백](?:\s*{AMOUNT_NUMBER})?)*)\s*원(?![칙리래])"
)
AMOUNT_TOKEN = re.compile(rf"({AMOUNT_NUMBER})|([조억만천백])")
MIN_AMOUNT = 10_000  # 1만원 미만 금액은 세지 않는다


@dataclass(frozen=True)
class Question:
    question: str
    expected_tool: str | None


@dataclass
class Outcome:
    question: Question
    tools: list[str] = field(default_factory=list)
    text: str = ""
    error: str | None = None
    # 이 질문에서 저장한 도구 결과 행의 content(공급자 원본 항목 목록)
    tool_results: list = field(default_factory=list)

    @property
    def first_tool(self) -> str | None:
        return self.tools[0] if self.tools else None

    @property
    def first_match(self) -> bool:
        return self.first_tool == self.question.expected_tool

    @property
    def called_expected(self) -> bool:
        expected = self.question.expected_tool
        return not self.tools if expected is None else expected in self.tools

    @property
    def forbidden(self) -> list[str]:
        return forbidden_phrases(self.text)

    @property
    def amount_mismatches(self) -> list[str]:
        return amount_mismatches(self.text, self.tool_results)


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Question(row["question"], row.get("expected_tool")) for row in rows]


def forbidden_phrases(text: str) -> list[str]:
    compact = text.replace("\n", " ")
    return [phrase for phrase in FORBIDDEN_PHRASES if phrase in compact]


def _amount_value(expression: str) -> int:
    """`1억 2,000만` → 120000000. 천·백은 만·억·조 단위 안의 자릿수로 더한다."""
    total = Decimal(0)
    chunk = Decimal(0)
    number: Decimal | None = None
    for digits, unit in AMOUNT_TOKEN.findall(expression):
        if digits:
            number = Decimal(digits.replace(",", ""))
        elif AMOUNT_UNITS[unit] < 10**4:
            chunk += (Decimal(1) if number is None else number) * AMOUNT_UNITS[unit]
            number = None
        else:
            total += (chunk + (number or 0)) * AMOUNT_UNITS[unit]
            chunk = Decimal(0)
            number = None
    return int(total + chunk + (number or 0))


def krw_amounts(text: str) -> list[tuple[str, int]]:
    """문장에서 원화 금액 표현을 찾아 (표현, 원 단위 정수) 목록으로 돌려준다."""
    return [
        (match.group(0).strip(), _amount_value(match.group(1)))
        for match in AMOUNT_PATTERN.finditer(text)
    ]


def tool_amounts(tool_results: list) -> set[int]:
    """도구 결과의 정수 값(절댓값)과 문자열(`…Text`·summary 등) 속 원화 금액을 모은다.

    저장 항목은 공급자마다 모양이 달라(Ollama `content`, Gemini `result[].text`) 모든 문자열을
    JSON으로 풀어 본 뒤 훑는다.
    """
    known: set[int] = set()

    def walk(value) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int):
            known.add(abs(value))
        elif isinstance(value, float):
            known.add(abs(round(value)))
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = value
            if isinstance(decoded, dict | list):
                walk(decoded)
            else:
                known.update(amount for _, amount in krw_amounts(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(tool_results)
    return known


def amount_mismatches(text: str, tool_results: list) -> list[str]:
    """응답의 원화 금액(1만원 이상) 중 도구 결과에 없는 것의 표현 목록"""
    known = tool_amounts(tool_results)
    return [
        expression
        for expression, amount in krw_amounts(text)
        if amount >= MIN_AMOUNT and amount not in known
    ]


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if len(lines) != 2:
            continue
        name = lines[0].removeprefix("event: ")
        events.append((name, json.loads(lines[1].removeprefix("data: "))))
    return events


# ── 시드 데이터(공개 API) ───────────────────────────────────────────


def _ok(response, *codes: int) -> dict:
    if response.status_code not in (codes or (200, 201)):
        raise RuntimeError(f"시드 요청 실패: {response.request.url.path} {response.status_code}")
    return response.json() if response.content else {}


def seed_user(client: TestClient, today: date) -> None:
    password = uuid4().hex
    _ok(
        client.post(
            "/api/auth/signup",
            json={
                "email": f"eval-{uuid4().hex[:12]}@example.com",
                "password": password,
                "passwordConfirm": password,
            },
        ),
        201,
    )
    _ok(client.put("/api/profile", json={"birthYear": 1988, "nickname": "평가"}))
    answers = {
        "horizon": 3,
        "experience": 4,
        "knowledge": 3,
        "income": 3,
        "investmentRatio": 3,
        "lossTolerance": 3,
    }
    _ok(
        client.post(
            "/api/allocation/risk-assessments", json={"surveyVersion": 1, "answers": answers}
        )
    )
    for asset_class, amount, include in (
        ("stock", 30_000_000, True),
        ("bond", 15_000_000, True),
        ("gold", 3_000_000, True),
        ("cash", 10_000_000, True),
        ("other", 400_000_000, False),
    ):
        _ok(
            client.post(
                "/api/allocation/holdings",
                json={
                    "name": f"평가 {asset_class}",
                    "assetClass": asset_class,
                    "amount": amount,
                    "includeInAllocation": include,
                    "memo": None,
                },
            )
        )
    _ok(
        client.put(
            "/api/allocation/situation",
            json={"monthlyExpense": 2_500_000, "dependents": 2, "consumerDebt": 0},
        )
    )
    categories = {
        (item["kind"], item["name"]): item["id"]
        for item in _ok(client.get("/api/ledger/categories"))["items"]
    }
    first_day = today.replace(day=1)
    for kind, name, amount in (
        ("income", "급여", 4_000_000),
        ("expense", "식비", 450_000),
        ("expense", "쇼핑", 300_000),
        ("expense", "교통", 120_000),
        ("expense", "카페·간식", 80_000),
    ):
        _ok(
            client.post(
                "/api/ledger/entries",
                json={
                    "kind": kind,
                    "occurredOn": first_day.isoformat(),
                    "amount": amount,
                    "categoryId": categories[(kind, name)],
                },
            )
        )
    for side, quantity, price, traded_on in (
        ("buy", "10", "70000", today.replace(month=1, day=2)),
        ("sell", "5", "80000", today - timedelta(days=1)),
    ):
        _ok(
            client.post(
                "/api/journal/trades",
                json={
                    "tradedOn": traded_on.isoformat(),
                    "market": "KRX",
                    "symbolCode": "005930",
                    "symbolName": "삼성전자",
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "currency": "KRW",
                    "fee": "0",
                    "tax": "0",
                },
            )
        )
    first = _ok(
        client.post(
            "/api/estate/members",
            json={"relation": "child", "displayName": "첫째", "birthDate": "2015-04-01"},
        )
    )
    client.post(
        "/api/estate/members",
        json={"relation": "child", "displayName": "둘째", "birthDate": "2019-09-09"},
    )
    client.post("/api/estate/members", json={"relation": "spouse", "displayName": "배우자"})
    client.post(
        "/api/estate/gifts",
        json={
            "recipientId": first["id"],
            "giftedOn": "2025-01-10",
            "donorGroup": "parents",
            "amount": 20_000_000,
        },
    )
    # 종목 마스터에 없으면 404다(관심종목 목록이 비어도 평가는 이어 간다).
    client.post("/api/watchlist", json={"code": "000660"})


# ── 실행 ────────────────────────────────────────────────────────────


def ask(client: TestClient, db: Session, question: Question) -> Outcome:
    outcome = Outcome(question)
    conversation = client.post("/api/chat/conversations", json={"title": "도구 평가"})
    if conversation.status_code != 201:
        outcome.error = f"대화 생성 실패 {conversation.status_code}"
        return outcome
    conversation_id = conversation.json()["id"]
    response = client.post(
        f"/api/chat/conversations/{conversation_id}/messages",
        json={"content": question.question},
    )
    if response.status_code != 200:
        outcome.error = f"전송 실패 {response.status_code}"
        return outcome
    for name, data in parse_sse(response.text):
        if name == "tool_start":
            outcome.tools.append(data["name"])
        elif name == "text_delta":
            outcome.text += data["text"]
        elif name == "error":
            outcome.error = data["message"]
    # 모델에 보낸 도구 결과는 SSE에 없어 저장 행에서 읽는다(금액 불일치 대조용).
    outcome.tool_results = [
        row.content
        for row in repository.list_messages(db, UUID(conversation_id))
        if row.kind == "tool_results"
    ]
    return outcome


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def report(outcomes: list[Outcome], provider: str, model: str) -> str:
    lines = [
        f"공급자 {provider}, 모델 {model}, 질문 {len(outcomes)}개",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    for index, outcome in enumerate(outcomes, 1):
        preview = outcome.error or outcome.text[:PREVIEW]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _cell(outcome.question.question),
                    outcome.question.expected_tool or "(없음)",
                    outcome.first_tool or "(없음)",
                    "O" if outcome.first_match else "X",
                    "O" if outcome.called_expected else "X",
                    ", ".join(outcome.forbidden) or "-",
                    _cell(", ".join(outcome.amount_mismatches)) or "-",
                    _cell(preview),
                ]
            )
            + " |"
        )
    total = len(outcomes) or 1
    first = sum(outcome.first_match for outcome in outcomes)
    called = sum(outcome.called_expected for outcome in outcomes)
    forbidden = sum(len(outcome.forbidden) for outcome in outcomes)
    mismatches = sum(len(outcome.amount_mismatches) for outcome in outcomes)
    errors = sum(outcome.error is not None for outcome in outcomes)
    lines += [
        "",
        f"- 처음 부른 도구 일치율: {first}/{len(outcomes)} ({first / total * 100:.1f}%)",
        f"- 기대 도구 호출률: {called}/{len(outcomes)} ({called / total * 100:.1f}%)",
        f"- 금지 문구: {forbidden}건",
        f"- 금액 불일치: {mismatches}건",
        f"- 오류 응답: {errors}건",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="챗봇 도구 선택 평가(실제 personal 공급자)")
    parser.add_argument("--limit", type=int, default=None, help="앞에서부터 몇 개만 돌린다")
    args = parser.parse_args(argv)

    personal = get_ai_status().personal
    if not personal.enabled:
        print(
            f"personal 공급자가 꺼져 있어 평가를 건너뜁니다(reason: {personal.reason}).",
            file=sys.stderr,
        )
        return 1
    questions = load_questions()[: args.limit]

    from app.main import create_app

    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                app = create_app()

                def override_db():
                    yield db

                app.dependency_overrides[get_db] = override_db
                with TestClient(app) as client:
                    seed_user(client, today_kst())
                    outcomes = [ask(client, db, question) for question in questions]
        finally:
            transaction.rollback()  # 시드·대화 기록을 남기지 않는다
    print(report(outcomes, personal.provider or "-", personal.model or "-"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
