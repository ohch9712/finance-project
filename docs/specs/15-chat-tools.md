# 15. 챗봇 도구 통합과 결과 카드

> 웨이브 4 · 브랜치 `feature/15-chat-tools` · 구현 claude · 검증 codex `gpt-6.1-sol` / `xhigh`
> 선행: 09(챗봇 코어)와 02·03·05·06·07·12·13이 develop에 병합된 상태.
> LLM SDK를 직접 부르지 않는다. 09의 공급자 어댑터와 루프를 그대로 쓴다.
> **착수 직전 갱신**: 코디네이터는 각 모듈 `service.py`의 실제 시그니처를 architecture 8절·각 스펙과 대조하고, 다르면 이 문서의 도구 표를 고친 뒤 디스패치한다.

## 1. 목표

- 챗봇이 사용자 데이터(자산배분과 추천 이유·관리 방향, 가계부, 매매일지, 관심종목, 가족·증여)와 서비스 기능(조건검색, 세금 계산, 상속·증여 방향 비교, 학습 콘텐츠)을 도구로 쓰게 한다.
- 도구 결과를 화면 카드로 보여 주고, 카드에서 해당 화면으로 이동할 수 있게 한다.
- 실제 키가 있을 때 돌려 볼 수 있는 작은 평가 세트를 만든다.

## 2. 범위

### 포함

- 도구 16개(4절), 카드 타입 8개(5절)와 프론트 렌더러
- 시스템 프롬프트 보강(도구 사용 안내 몇 줄, 가변 값 금지 규칙 유지)
- 수동 평가 스크립트와 질문 세트

### 제외

- 쓰기 도구는 `add_to_watchlist` 하나뿐이다. 가계부·매매 기록 쓰기, 주문은 만들지 않는다.

## 3. 소유 파일

- `backend/app/modules/chat/**`(09 코드 수정, `chat/tools/*.py` 추가, `chat/evals/**`)
- `backend/tests/chat/**`
- `frontend/src/features/chat/**`(카드 컴포넌트 추가)
- `frontend/tests/chat/**`
- 이 문서의 `## 구현 결과`

다른 모듈은 공개 서비스 함수만 import한다(architecture 8절). 다른 모듈 파일을 고치지 않는다.

## 4. 도구 목록

모든 입력 모델은 `extra="forbid"`. 결과 `content`는 숫자를 형식화하지 않은 JSON(모델이 정확히 인용하도록)과 핵심 요약 문장 하나를 함께 담는다. 금액 단위는 원.

| name | 입력 | 호출하는 서비스 함수 | 카드 |
| --- | --- | --- | --- |
| `get_allocation_report` | `{}` | `allocation.get_allocation_report` | `allocation` |
| `get_recommendation` | `{}` | `allocation.get_recommendation` | `directions` |
| `get_risk_profile` | `{}` | `allocation.get_latest_risk_profile`, `allocation.get_target_allocation`(409는 상태로 바꿔 전달) | `key_value` |
| `get_spending_summary` | `{"month": "YYYY-MM" \| null}`(null이면 이번 달) | `ledger.get_monthly_summary` | `spending` |
| `get_trade_stats` | `{"from": date \| null, "to": date \| null}` | `journal.get_trade_stats` | `table` |
| `get_positions` | `{}` | `journal.get_positions(with_quotes=True)` | `table` |
| `search_stocks` | `{"query": str}` | `market.search_stocks(limit=10)` | `stock_list` |
| `get_stock_detail` | `{"code": str}` | `market.get_stock_detail` | `stock` |
| `screen_stocks` | `{"filter": ScreenFilter}` | `screener.run_screen` | `stock_list`(링크 `/stocks/screener?f=<encode_filter>`) |
| `get_watchlist` | `{}` | `watchlist.list_items` | `stock_list` |
| `add_to_watchlist` | `{"code": str}` | `watchlist.add_item` | `link` |
| `get_gift_deductions` | `{"memberName": str \| null}` | `estate.list_members`, `estate.get_deduction_status` | `table` |
| `estimate_gift_tax` | `{"memberName": str \| null, "relation": … \| null, "birthDate": date \| null, "donorGroup": …, "amount": int, "giftedOn": date \| null}` | `estate.resolve_gift_input`(가족이면 `memberId`, 아니면 `recipient`로 `GiftTaxRequest`를 만든다), `estate.calc_gift_tax` | `gift_tax` |
| `compare_estate_plans` | `{"horizonYears": int \| null, "annualGrowthRate": str \| null, "lumpSumPerChild": int \| null}`(null이면 06 기본값 20년·"3"·1억, 재산은 사전 채움) | `estate.build_compare_input`, `estate.compare_estate_plans` | `comparison` |
| `plan_gifts` | `{"memberName": str \| null, "birthDate": date \| null, "targetAge": int \| null, "annualReturnRate": str \| null}` | `estate.member_recipient`, `estate.prior_gifts`, `estate.simulate_gift_plan`(06 라우터 `POST /api/estate/simulate/gift-plan`과 같은 순서로 `GiftPlanInput`을 만든다) | `table`(`href: "/estate?tab=plan"`) |
| `search_learning` | `{"query": str}` | `learn.search_posts(limit=5)` | `link_list` |

도구 설명(`description`) 작성 규칙

- 언제 쓰는지, 무엇을 돌려주는지, 쓰지 말아야 할 때를 한국어로 적는다.
- `screen_stocks` 설명에는 `screener.get_filter_guide()`의 내용을 그대로 붙인다(필드·단위·용어 기준). 모델이 필터를 직접 만든다.
- `add_to_watchlist`는 "사용자가 담아 달라고 분명히 말했을 때만" 쓴다고 적는다.
- `estimate_gift_tax`·`plan_gifts`·`compare_estate_plans`는 결과가 참고용 추정이라고 적는다. `compare_estate_plans`는 사전 채움 재산이 없으면 오류 대신 "자산배분에 자산을 입력하거나 상속·증여 화면에서 재산을 입력해 주세요" 상태를 돌려준다.
- `get_recommendation`은 "왜 이렇게 추천했어", "뭐부터 해야 해" 같은 질문에 쓴다고 적는다. 결과 `content`에는 방향·이유 문장을 그대로 담는다(모델이 새 수치를 만들지 않게).
- `memberName`은 가족 별칭과 공백 무시 일치로 찾는다. 없거나 여럿이면 도구가 후보 목록을 결과로 돌려준다(오류가 아니다).
- 상속·증여 도구 셋의 쓰임을 서로 구분해 적는다(웨이브 4 평가 F4: 13·15번 질문에서 계산 도구 대신 공제 현황 도구를 불렀다).
  - `get_gift_deductions`: 남은 공제 한도·사용액·다시 생기는 날만 궁금할 때 쓴다. 금액이 주어진 세금 질문("1억 주면 세금 얼마")에는 쓰지 않고 `estimate_gift_tax`를, 언제·얼마씩 나눠 줄지 묻는 일정 질문에는 쓰지 않고 `plan_gifts`를 쓴다고 적는다.
  - `estimate_gift_tax`·`plan_gifts`: 공제 한도와 10년 합산 증여 기록을 도구가 직접 반영하므로 `get_gift_deductions`를 먼저 부를 필요가 없다고 적는다.
- `get_spending_summary`는 이번 달이면 `get_today` 없이 바로 `month: null`로 부른다고 적는다. 지난달처럼 상대적인 달만 날짜를 먼저 확인한다.
- 6절 시스템 프롬프트에도 "세금 계산·증여 일정 질문은 공제 현황 도구가 아니라 계산 도구를 쓴다"는 뜻의 고정 문장 하나를 더한다(가변 값 없음).

### 4.1 착수 전 대조 결과 (코디네이터, 2026-10-17)

develop `5d82533`의 실제 코드와 대조해 정한 내용이다. 표와 어긋나면 이 절을 따른다.

- **예상 오류는 도구가 상태로 바꾼다.** 09의 `execute_tool`은 핸들러가 던진 예외를 모두 "도구 실행 실패"(`is_error`)로 바꾸고 예외 로그를 남긴다. 그래서 아래 `AppError`는 핸들러가 잡아 `{"status": "<상태>", "message": "<서버 메시지>"}`를 담은 정상 결과로 돌려준다. 그 밖의 예외는 잡지 않는다.
  - `allocation.get_target_allocation`: 409 `PROFILE_INCOMPLETE` → `profile_incomplete`, 409 `RISK_PROFILE_REQUIRED` → `risk_profile_required`
  - `watchlist.add_item`: 404 `NOT_FOUND` → `not_found`, 409 `CONFLICT`(이미 담김, 100개 상한) → `conflict`
  - `estate.build_compare_input`: 사전 채움 재산이 없으면 422 `VALIDATION_ERROR`(`assets`) → `assets_required`(4절 안내 문장). 실제 06 코드는 자산이 없어도 0원 사전 채움을 돌려주므로, 만든 입력의 `assets` 합계가 0이어도 `assets_required`다.
  - estate 공개 함수 전부(`resolve_gift_input`·`member_recipient`·`prior_gifts`·`get_deduction_status`·`calc_gift_tax`·`simulate_gift_plan`·`compare_estate_plans`)와 `plan_gifts`의 자체 입력 검증(06 라우터와 같은 `field_error`): 404 `NOT_FOUND`·422 `VALIDATION_ERROR` → `invalid_input`(메시지와 `details`의 메시지를 그대로)
  - `allocation.get_allocation_report`·`get_recommendation`은 오류 대신 `status` 필드(`needs_profile`·`needs_assessment`·`needs_holdings`, 추천은 앞의 둘)로 데이터 없음을 알린다. 그 값을 그대로 전달한다.
- **`add_to_watchlist`의 커밋**: `watchlist.add_item`은 함수 안에서 `commit`(실패 경로는 `commit` 또는 `rollback`)하고 커밋 뒤에도 쿼리한다. SQLAlchemy 2.0.54에서 `with db.begin_nested():` 블록 안에서 커밋한 뒤 쿼리하면 `InvalidRequestError`(closed transaction inside context manager)가 난다. 그래서 09의 `execute_tool`은 컨텍스트 매니저 대신 `savepoint = db.begin_nested()`를 쓰고, 성공하면 `savepoint.is_active`일 때만 `savepoint.commit()`, 예외면 활성일 때 `savepoint.rollback()`, 도구가 이미 커밋해 savepoint가 닫혔으면 `db.rollback()`으로 세션만 정리한다. 09의 `TurnWriter`가 메시지 행을 쓸 때마다 커밋하므로 도구 실행 시점에 미커밋 변경이 없다. 이 순서를 바꾸지 않는다.
- **카드에 넣을 값의 출처**
  - `allocation`: `AllocationReport.classes`의 `asset_class`·`actual_weight`·`target_weight`, 이름은 `app.core.domain.ASSET_CLASS_LABEL`. `classes`가 null이면 빈 목록. 리포트에는 Mock 잔고 여부가 없어 이 카드에는 샘플 배지를 달지 않는다(링크한 `/allocation` 화면이 고지를 보여 준다).
  - `directions`: `Recommendation.directions`를 `priority` 순으로 앞 3개, `amount`는 형식화한 원 또는 null.
  - `spending`: `MonthlySummary`의 `income_total`·`expense_total`·`net`, `topCategories`는 `by_category` 중 `kind == "expense"` 앞 5개. `href`는 `/ledger?month=YYYY-MM`.
  - `stock`: `StockDetail.quote`(가격·등락률), `snapshot`(PER·PBR·ROE·시가총액 등 있는 값만), `source`. `get_stock_detail`이 None이면 카드 없이 `not_found` 상태.
  - `stock_list`: `search_stocks`는 시세가 없는 종목 마스터 검색이라 `price`·`changeRate`는 null, `source`는 `"master"`(배지 없음), `extra`는 시장·ETF 표시. `screen_stocks`는 `ScreenResult.as_of`·`source`·`items`, `get_watchlist`는 항목의 `quote`와 첫 항목의 `source`.
  - `link_list`: `search_posts` 결과의 `title`·`summary`, `href`는 `/learn/{slug}`.
  - `gift_tax`: `GiftTaxResult.steps`와 `payable_tax`, `href`는 `/estate?tab=gift-tax`. `plan_gifts`의 `link`는 `/estate?tab=plan`.
  - `comparison`: `EstateCompareResult.scenarios`의 `key`·`label`·`total_tax`·`family_receives`, `recommended`는 `key == recommended_key`, `horizonDate`는 `horizon_date`.
- `search_learning`은 사용자 없이 `learn.search_posts(db, query, limit=5)`를 부르므로 북마크·읽음 표시는 넣지 않는다.

### 4.2 금액 인용 (웨이브 4 후속, 2026-10-18)

로컬 Ollama `gemma4:12b` 평가에서 모델이 도구 결과의 9,700,000원을 응답 본문에 "970,000원"으로 옮겼다(summary 문장에는 정확한 금액이 있었다). 숫자 원값만 주면 자릿수를 다시 세다 틀리기 쉬우므로 아래를 지킨다.

- 도구 결과 `content`의 원화 금액 필드마다 같은 이름에 `Text`를 붙인 형식화 문자열을 함께 둔다. 예: `"payableTax": 9700000, "payableTaxText": "9,700,000원"`. 목록 안의 금액(`steps[].amount` 등)도 같다. 값은 `app.core.format.format_krw`(전체 자릿수)다. 원값 필드는 지우지 않는다. 퍼센트·비율·주식 수량·외화 금액은 대상이 아니다(외화는 이미 `money()` 문자열이 있으면 그대로 둔다).
- `summary` 문장의 원화 금액은 `format_krw` 전체 자릿수로 쓰고 만원·억원 축약을 쓰지 않는다(카드 표시 값은 지금처럼 축약해도 된다).
- 6절 시스템 프롬프트에 고정 문장을 더한다: 금액은 도구 결과의 `…Text` 값이나 `summary`에 적힌 그대로 옮기고, 만원·억원으로 바꾸거나 자릿수를 다시 세지 않는다.
- `chat/evals/run_eval.py`에 지표 "금액 불일치"를 더한다. 응답 본문에서 원화 금액 표현(`9,700,000원`, `970만원`, `1.2억원`, `12,756억원` 등)을 찾아 원 단위 정수로 바꾸고, 그 질문에서 부른 도구 결과 `content` 안의 정수 값(원값과 `…Text`를 원으로 바꾼 값)에 없으면 불일치 1건으로 센다. 표에 열을 더하고 요약에 합계를 낸다. 1만원 미만 금액과 퍼센트는 세지 않는다.
- 도구 이름·입력 모델·카드 형식·도구 순서와 시스템 프롬프트 고정 규칙(가변 값 없음)은 바꾸지 않는다.

## 5. 카드 타입 (09의 카드 형식에 추가)

```ts
| { type: "allocation"; status: string; score: number | null; grade: string | null;
    classes: { assetClass: string; label: string; actualWeight: number | null; targetWeight: number | null }[]; href: "/allocation" }
| { type: "spending"; month: string; incomeTotal: string; expenseTotal: string; net: string;
    topCategories: { name: string; amount: string; ratio: string }[]; href: string }
| { type: "stock"; code: string; name: string; price: string | null; changeRate: string | null;
    metrics: { label: string; value: string }[]; source: string; href: string }
| { type: "stock_list"; title: string; asOf: string | null; source: string;
    items: { code: string; name: string; price: string | null; changeRate: string | null; extra: string | null }[]; href: string | null }
| { type: "gift_tax"; title: string; steps: { label: string; amount: string; note: string | null }[]; payableTax: string; href: string }
| { type: "link_list"; title: string; links: { title: string; description: string | null; href: string }[] }
| { type: "directions"; title: string; items: { title: string; body: string; amount: string | null }[];
    href: "/allocation/recommendation/detail" }
| { type: "comparison"; title: string; horizonDate: string;
    rows: { key: string; label: string; totalTax: string; familyReceives: string; recommended: boolean }[];
    href: "/estate?tab=compare" }
```

- 09의 `table` 카드에 선택 필드 `href?: string`(앱 내부 경로, "/"로 시작)을 더한다. 있으면 표 아래에 "자세히 보기" 링크를 그린다. 도구 하나는 카드 하나만 낸다(09의 `ToolOutput.card`·SSE `tool_end.card` 형식을 바꾸지 않는다). `plan_gifts`는 `/estate?tab=plan`, `get_trade_stats`·`get_positions`는 `/journal`, `get_gift_deductions`는 `/estate?tab=deductions`를 쓴다.
- 카드의 표시 값은 서버에서 형식화한 문자열이다(`"1,234,000원"`, `"-1.11%"`). `allocation` 카드는 도넛을 그리므로 비중은 숫자다.
- `stock`·`stock_list` 카드는 `source=mock`이면 "샘플 데이터" 배지, `gift_tax`·`comparison` 카드는 `tax` 고지 한 줄, `directions` 카드는 `investment` 고지 한 줄을 붙인다. `directions`는 방향 앞 3개, `comparison`은 06 8.1의 방향 순서다.
- 프론트: `features/chat/cards/`에 타입별 컴포넌트를 추가하고 레지스트리에 등록한다. 01b의 `DonutChart`와 `--asset-*` 색을 쓴다.

## 6. 시스템 프롬프트 보강

09의 고정 프롬프트 끝에 아래 의미를 더한다(가변 값 없음).

- 개인 데이터 질문에는 먼저 해당 도구를 쓴다. 도구가 "데이터 없음" 상태를 돌려주면 그 화면에서 입력하는 방법을 안내한다.
- 조건검색 요청은 `screen_stocks`로 필터를 만들어 실행하고, 결과 개수와 상위 종목 몇 개만 말한 뒤 카드 링크로 전체를 보게 한다.
- 용어 설명은 `search_learning` 결과를 근거로 하고, 링크를 함께 준다.
- 세금 질문은 계산 결과와 함께 가정(신고기한 내 신고 등)을 짧게 밝힌다.
- 추천 이유나 먼저 할 일을 물으면 `get_recommendation`의 방향·이유 문장을 근거로 설명한다.
- 상속·증여 방향을 물으면 `compare_estate_plans`로 비교하고, 같은 가정에서의 참고용 비교이며 실제 결정은 세무 전문가와 상의하라고 말한다.

## 7. 평가 세트 (`chat/evals/`)

- `questions.yaml`: 17개 질문과 기대 도구(예: "왜 이렇게 배분하라고 추천했어?" → `get_recommendation`, "자녀에게 미리 증여하는 게 나아, 그냥 상속하는 게 나아?" → `compare_estate_plans`, "내 자산배분 점수 알려줘" → `get_allocation_report`, "코스피 대형주 중 PER 10 이하 찾아줘" → `screen_stocks`, "PER이 뭐야?" → `search_learning`, "삼성전자 관심종목에 넣어줘" → `add_to_watchlist`, "첫째에게 1억 주면 세금 얼마야?" → `estimate_gift_tax`, "이번 달 어디에 제일 많이 썼어?" → `get_spending_summary`, "주문 넣어줘" → 도구 없음·거절 안내).
- `run_eval.py`: 실제 공급자(`LLM_PERSONAL_PROVIDER`가 켜진 환경: 로컬 Ollama 또는 결제가 연결된 Gemini)와 시드 사용자 데이터로 질문을 돌려 처음 부른 도구가 기대와 같은지, 금지 문구("사세요", "매수하세요" 등 권유 표현)가 없는지 집계해 표로 출력한다. CI에서 돌리지 않는다(비용). 결과를 `## 구현 결과`에 붙인다.

## 8. 테스트

### 백엔드

- 도구마다: 입력 검증(extra 금지), 서비스 함수 호출 인자, 결과 `content` 구조, 카드 형식, 데이터 없음 상태(진단 전, 자산 없음, 스냅샷 없음), 다른 사용자 데이터가 섞이지 않음
- `memberName` 일치·후보 반환
- `get_registered_tools()`가 이름순 18개(09 기본 2개 + 16개)이고, 두 공급자 어댑터의 도구 선언이 모두 `portable_json_schema` 결과다
- 가짜 클라이언트 시나리오: "점수 알려줘" → `get_allocation_report` 호출 → 카드가 마지막 응답 행 `cards`에 저장 → 상세 API의 `DisplayMessage.cards`
- 시스템 프롬프트에 가변 값이 없다(두 번 생성해 바이트 비교)

### 프론트

- 카드 8종 렌더링(샘플 배지, 고지, 링크, `comparison`의 추천 표시), 모르는 타입 무시

## 9. 완료 기준

- [ ] 공통 완료 기준(AGENTS.md) 전 항목
- [ ] 실제 personal 공급자(로컬 Ollama 또는 결제 Gemini)가 있으면 평가 세트 결과(도구 일치율, 금지 문구 0건, 공급자·모델 이름)를 `## 구현 결과`에 남긴다. 없으면 그렇다고 적는다

## 10. 검증 체크리스트

1. 4절 도구 이름·입력·호출 함수가 같고, 다른 모듈의 비공개 코드를 import하지 않는다.
2. 쓰기 도구가 `add_to_watchlist` 하나뿐이다.
3. 카드 형식이 5절과 같고 표시 값이 서버에서 형식화된다.
4. `screen_stocks` 설명에 `get_filter_guide()` 내용이 들어 있다.
5. 도구 결과에 이메일·사용자 ID·계좌번호가 없다.
6. 09의 append-only·고정 프롬프트 규칙이 유지된다(도구 목록 이름순, 시스템 프롬프트 고정).
7. `get_recommendation`·`compare_estate_plans` 도구가 02·06의 공개 함수만 부르고, 결과 문장을 바꾸지 않고 전달한다.

## 구현 결과

### 구현한 것

- 백엔드 도구 16개(`backend/app/modules/chat/tools/`): `allocation.py`(get_allocation_report·get_recommendation·get_risk_profile), `ledger.py`(get_spending_summary), `journal.py`(get_trade_stats·get_positions), `market.py`(search_stocks·get_stock_detail), `screener.py`(screen_stocks), `watchlist.py`(get_watchlist·add_to_watchlist), `estate.py`(get_gift_deductions·estimate_gift_tax·compare_estate_plans·plan_gifts), `learn.py`(search_learning), 공통 도우미 `common.py`. `tools/__init__.py`에서 등록해 `get_registered_tools()`가 이름순 18개다. 다른 모듈은 4절 표의 공개 서비스 함수와 그 함수가 쓰는 타입(`schemas`)만 import한다.
- 결과 `content`는 숫자를 형식화하지 않은 JSON(원 정수, Decimal은 JSON 숫자, 날짜는 ISO)과 요약 문장 `summary`다. 카드 값은 서버에서 형식화한 문자열이고(`allocation`의 비중만 숫자) 5절 카드 8종과 09의 `table`·`link`·`key_value`를 쓴다.
- 4.1의 상태 변환: 409 `PROFILE_INCOMPLETE`·`RISK_PROFILE_REQUIRED`, 관심종목 404·409, estate 공개 함수와 `plan_gifts` 자체 검증의 404·422(`details` 포함), 사전 채움 재산 없음(합계 0 포함) `assets_required`를 `{"status", "message"}` 결과로 돌려준다. 그 밖의 예외는 잡지 않는다.
- 09 수정: `agent.execute_tool`의 savepoint를 명시적 방식(`begin_nested()` → 활성이면 commit/rollback, 도구가 이미 커밋했으면 `db.rollback()`)으로 바꿨고(4.1 정정 반영), `_safe_card`가 모든 카드의 `href`와 `link_list`의 링크를 앱 내부 경로로 제한한다. 시스템 프롬프트 끝에 6절의 도구 사용 안내를 고정 문자열로 더했다.
- 평가 세트 `chat/evals/questions.yaml`(17개: 데이터 도구 16개 각 1개 + 주문 거절 1개)과 `run_eval.py`(`uv run python -m app.modules.chat.evals.run_eval`). 시드 사용자 데이터는 공개 API로 만들고 끝나면 트랜잭션을 롤백한다. 처음 부른 도구 일치율, 기대 도구 호출률, 금지 문구 건수, 공급자·모델을 Markdown 표로 출력한다.
- 프론트 `features/chat/cards/`: `AllocationCards.tsx`(allocation: `DonutChart`+`--asset-*` 색, directions: investment 고지), `SpendingCard.tsx`, `StockCards.tsx`(stock·stock_list: mock이면 `SampleDataBadge`+sample 고지), `EstateCards.tsx`(gift_tax·comparison: tax 고지, 추천 방향 표시), `LinkListCard.tsx`, 공통 `CardParts.tsx`. `registry.tsx`에 등록했고 `types.ts`에 카드 타입을 더했다. `features/chat/index.ts`의 `openChatWidget({ draft })`는 바꾸지 않았다.

### 스펙에 없어 정한 것

- 도구 입력 모델은 camelCase 키만 받는다(`alias_generator=to_camel`, `populate_by_name=False`, `extra="forbid"`). `get_trade_stats`는 `from`·`to` 별칭, `from > to`면 입력 오류다. `get_spending_summary`의 `month`는 `YYYY-MM` 검증, null이면 `ToolContext.today`(KST)의 달이다. 금리·수익률 문자열(`annualGrowthRate` 0~10, `annualReturnRate` 0~20)은 숫자 문자열만 받는다.
- 데이터 없음 상태 이름: `no_entries`(가계부 그 달 없음), `no_trades`, `no_positions`, `no_snapshot`(조건검색 스냅샷 없음), `empty`(관심종목), `no_results`(학습 글), `not_found`(종목), `no_members`(가족 없음), `member_not_found`·`member_ambiguous`(후보 `candidates`: 별칭·관계·나이, ID 없음). `search_stocks` 결과 없음은 빈 `items`와 카드 없음이다.
- 카드를 내지 않는 경우: 상태 결과, 추천이 ready가 아닐 때, 검색 결과 없음, 분산 증여 일정이 비었을 때. `allocation` 카드는 데이터 없음 상태에도 상태 문구와 링크를 보여 준다.
- `plan_gifts`의 증여자: 사용자가 증여자이므로 손자녀 가족이면 `grandparents`, 그 밖은 `parents`(06 라우터 기본값). 입력 순서는 06 라우터와 같다.
- `compare_estate_plans`의 기본값(20년·"3"·1억)과 `plan_gifts`의 기본값(30세·"4")은 06 스키마 기본값과 같은지 테스트로 고정했다.
- `screen_stocks`는 모델·카드에 상위 10개만 보내고 링크(`/stocks/screener?f=<encode_filter>`)로 전체를 보게 한다. `get_watchlist` 카드는 앞 20개, `extra`는 그룹 이름이다.
- 360px 채팅 폭(카드 안 표 270px)에 맞추려고 표 카드 열을 줄였다: 보유 종목은 "종목(수량)" 한 열, 공제 현황은 `가족·공제 | 남은 공제/한도(짧은 금액) | 회복일`, 분산 증여는 `증여일(나이)`. 정확한 값은 모델용 `content`에 있다.
- 카드 금액 표기: `app.core.format.format_krw_compact`는 억 단위 정수부에 쉼표가 없어(`12756억원`) 프론트 `formatKRWCompact`(`12,756억원`)와 다르다. 공용 파일(01a)은 고치지 않고 `chat/tools/common.py`의 `krw_compact`에서 쉼표를 넣어 화면 규칙과 맞췄다(코디네이터 참고). 비율은 사사오입(ROUND_HALF_UP)으로 `+1.11%`·`48.4%`처럼 쓴다.
- 09의 기본 도구 테스트 중 "도구가 기본 2개뿐"을 전제한 단언 4곳을 18개 목록(`ALL_TOOL_NAMES`)으로 바꿨다.

### 검사 결과

- 백엔드(develop `87fbb76` 병합 후): `uv run ruff check .`·`uv run ruff format --check .` 통과, `uv run pytest` 1355개 통과(챗봇 249개: 도구별 입력 검증·호출 인자·content·카드·데이터 없음 상태·다른 사용자 격리, memberName 일치·후보, 이름순 18개와 두 공급자 도구 선언, 설명 규칙, 공개 함수만 호출(AST 검사), 개인정보 없음, savepoint 회귀, "점수 알려줘" 시나리오 → 마지막 응답 행 `cards` → 상세 `DisplayMessage.cards`, 시스템 프롬프트 고정).
- 마이그레이션: 15는 revision을 추가하지 않았다. 확인으로 빈 DB `finance_15_cycle`에서 `upgrade heads` → `downgrade base` → `upgrade heads` 성공(head 11개) 후 삭제했다.
- 프론트: `npm run lint`·`npx tsc --noEmit` 통과, `npm run test` 466개 통과(카드 테스트 13개 추가), `npm run build` 성공.
- 화면: `scripts/start.sh`(8015·3015)에 Mock 종목·학습 글을 시드하고, 화면 확인용 사용자를 공개 API로 만든 뒤 실제 도구 16개가 만든 카드를 담은 대화를 Orca 독립 프로필 브라우저로 열었다. 360px에서 `scrollWidth` 360, 넘치는 요소 0개, 표 카드 4개 모두 내부 폭 270/270, 카드 15종 표시(관심종목 담기는 두 번째 실행에서 `conflict` 상태라 카드 없음을 함께 확인), 고지 investment·sample·tax·ai 표시, 콘솔 오류 0건. 1280px도 넘침 0개. 확인 후 서버를 `scripts/stop.sh`로 멈췄다.
- 평가 세트: 이 환경에서는 personal 공급자로 실행하지 않음(코디네이터 지시, 실제 Gemini·Ollama 호출 금지). 도구 일치율·금지 문구 결과는 없다. 테스트 환경(personal 꺼짐)에서 `run_eval.main()`이 공급자·DB를 부르지 않고 끝나는 것과 질문 세트 구조·집계 함수만 테스트로 확인했다.

### 남은 일

- 실제 personal 공급자(로컬 Ollama `gemma4:12b` 또는 결제 Gemini)로 `run_eval.py`를 돌려 도구 일치율·금지 문구 0건·공급자·모델을 이 절에 남긴다.
- 01a의 `format_krw_compact` 억 단위 쉼표를 프론트와 맞추면 `chat/tools/common.py`의 보정을 지울 수 있다.

### 평가 세트 실행 결과 (코디네이터, 2026-10-18)

- 환경: develop `e0d69ca`, personal 공급자 로컬 Ollama 0.40.5, 모델 `gemma4:12b`. `uv run python -m app.modules.chat.evals.run_eval`(시드 사용자는 실행 뒤 롤백). 문항당 약 1분.
- 처음 부른 도구 일치율 13/17(76.5%), 기대 도구 호출률 15/17(88.2%), 금지 문구 0건, 오류 응답 0건. 주문 요청(17번)은 도구 없이 거절 안내했다.

| # | 기대 도구 | 처음 부른 도구 | 기대 도구 호출 |
| --- | --- | --- | --- |
| 4 | `get_spending_summary` | `get_today` | O(두 번째) |
| 5 | `get_trade_stats` | `get_today` | O(두 번째) |
| 13 | `estimate_gift_tax` | `get_gift_deductions` | X |
| 15 | `plan_gifts` | `get_gift_deductions` | X |

- 나머지 13문항은 처음 부른 도구가 기대와 같았다.
- 4·5번은 날짜를 먼저 확인한 뒤 맞는 도구를 불렀다. `month`·`from`·`to`를 비우면 이번 달·전체라는 점을 도구 설명에 더 분명히 적으면 줄일 수 있다.
- 13·15번은 공제 현황만 보고 세금 계산·분산 증여 일정을 직접 설명했다. 금액이 주어진 세금 질문은 `estimate_gift_tax`, 시기·나눠 주기 질문은 `plan_gifts`를 쓰라는 구분을 두 도구 설명과 `get_gift_deductions` 설명("세금 계산·일정에는 쓰지 않는다")에 더하는 것이 후속 과제다.

### 웨이브 4 평가 수정(F4)

- 대상: 위 평가에서 13번(`estimate_gift_tax`)·15번(`plan_gifts`)이 `get_gift_deductions`만 부르고, 4번(`get_spending_summary`)이 `get_today`를 먼저 부른 것. 브랜치 `feature/15-chat-tools-fix-1`(develop `aa88ccd`에서 시작).
- 고친 것(4절 설명 규칙대로, 도구 이름·입력 모델·결과 형식·도구 순서는 그대로)
  - `get_gift_deductions`: 남은 공제 한도·사용액·다시 생기는 날만 궁금할 때 쓰고, 금액이 주어진 세금 질문에는 `estimate_gift_tax`, 언제·얼마씩 나눠 줄지 묻는 일정 질문에는 `plan_gifts`를 쓴다고 적었다.
  - `estimate_gift_tax`·`plan_gifts`: 공제 한도와 10년 합산 증여 기록을 도구가 직접 반영하므로 `get_gift_deductions`를 먼저 부를 필요가 없다고 적었다.
  - `get_spending_summary`: 이번 달이면 `get_today` 없이 바로 `month: null`로 부르고(도구가 오늘 날짜로 이번 달을 정한다), 지난달처럼 상대적인 달만 날짜를 먼저 확인한다고 적었다.
  - 시스템 프롬프트 "도구 사용" 절의 세금 질문 안내 바로 뒤에 고정 문장 하나를 더했다: "증여세 계산·증여 일정 질문은 공제 현황 도구(get_gift_deductions)가 아니라 계산 도구(세금은 estimate_gift_tax, 나눠 주는 일정은 plan_gifts)를 바로 쓴다." 가변 값은 없다.
- 스펙에 없어 정한 것: 첫 수정 뒤 실제 서버 확인에서 13번이 도구 없이 "생년월일을 알려 주세요"라고 되묻고, 15번이 "목표 나이·수익률을 알려 주시면"이라며 공제 현황 도구를 불렀다. 그래서 두 계산 도구 설명에 "사용자가 '첫째'·'둘째'처럼 가족을 말하면 memberName에 그 별칭만 넣고 바로 부른다. 관계·생년월일·증여 기록은 도구가 가족 정보에서 채우므로 사용자에게 묻지 않는다"를, `plan_gifts`에는 "targetAge·annualReturnRate는 사용자가 말하지 않았으면 묻지 말고 null로 두면 기본값으로 계산한다"를 더했다. 원래 `plan_gifts` 설명의 "memberName, 등록하지" 사이 공백이 빠졌던 것도 함께 고쳤다.
- 회귀 테스트 `backend/tests/chat/test_tool_descriptions.py`(11개): 세 상속·증여 도구 설명의 쓰임 구분 문구, 별칭만으로 바로 부르기·기본값을 묻지 않기 문구, `get_spending_summary`의 `get_today` 없이 `month: null` 문구, 시스템 프롬프트 새 문장의 위치(도구 사용 절, 세금 안내 바로 뒤)와 한 번만 있는지, 서로 다른 시각에 두 번 생성한 시스템 프롬프트가 바이트 단위로 같고 네 자리 숫자가 없는지.

#### 검사 결과

- 백엔드: `uv run ruff check .`·`uv run ruff format --check .` 통과, `uv run pytest` 1366개 통과. 프론트와 마이그레이션은 바꾸지 않았다.
- 실제 서버 확인(`LLM_PERSONAL_PROVIDER=ollama scripts/start.sh backend`, 8015, 로컬 Ollama `gemma4:12b`): `run_eval.seed_user`와 같은 시드를 공개 API로 만들고 질문을 보냈다.
  - 첫 수정 뒤: 4번 `get_spending_summary` 처음 호출(해결), 13번 도구 없음(생년월일을 되물음), 15번 `get_gift_deductions`.
  - 설명 보강 뒤: 13번 `estimate_gift_tax`, 15번 `plan_gifts`를 처음 불렀다. 확인 뒤 `scripts/stop.sh backend`로 멈췄다.
- 평가 세트(설명 보강 뒤 한 번, `cd backend && LLM_PERSONAL_PROVIDER=ollama uv run python -m app.modules.chat.evals.run_eval`, 로컬 Ollama `gemma4:12b`, 약 17분, 시드는 롤백): 처음 부른 도구 일치율 16/17(94.1%), 기대 도구 호출률 17/17(100%), 금지 문구 0건, 오류 응답 0건.

| 지표 | 이전(develop `e0d69ca`) | 이번 |
| --- | --- | --- |
| 처음 부른 도구 일치 | 13/17 | 16/17 |
| 기대 도구 호출 | 15/17 | 17/17 |
| 금지 문구 | 0건 | 0건 |
| 오류 응답 | 0건 | 0건 |

| # | 질문 | 기대 도구 | 이전 처음 부른 도구 | 이번 처음 부른 도구 |
| --- | --- | --- | --- | --- |
| 4 | 이번 달 어디에 제일 많이 썼어? | `get_spending_summary` | `get_today` | `get_spending_summary` |
| 5 | 올해 내 매매 성적 어때? | `get_trade_stats` | `get_today` | `get_today`(두 번째에 기대 도구) |
| 12 | 첫째에게 세금 없이 얼마까지 줄 수 있어? | `get_gift_deductions` | `get_gift_deductions` | `get_gift_deductions` |
| 13 | 첫째에게 1억 주면 세금 얼마야? | `estimate_gift_tax` | `get_gift_deductions`(기대 도구 호출 X) | `estimate_gift_tax` |
| 15 | 둘째에게 언제 얼마씩 나눠서 증여하면 돼? | `plan_gifts` | `get_gift_deductions`(기대 도구 호출 X) | `plan_gifts` |

- 나머지 12문항(1~3, 6~11, 14, 16, 17)은 이전과 같이 처음 부른 도구가 기대와 같았다. 17번 주문 요청은 도구 없이 거절 안내했다. 12번(공제 현황 질문)이 계산 도구로 넘어가지 않은 것도 확인했다.

#### 남은 일

- 5번("올해 매매 성적")은 연도를 알아야 해서 `get_today`를 먼저 부른다. `get_trade_stats` 설명(`journal.py`)은 이번 소유 범위가 아니라 고치지 않았다.
- 13번 응답 첫 문장이 세액을 "970,000원"이라고 잘못 옮겼다. 같은 시드로 `estate.calc_gift_tax`를 직접 부르면 `payable_tax`는 9,700,000원이고, 도구 결과(content·카드)는 맞다. 모델의 숫자 인용 오류라 도구 선택(F4) 범위 밖이다. 실제 서버 확인과 평가 두 번 모두 `gemma4:12b`가 같은 자릿수 오류를 냈다.

### 금액 인용 개선

- 대상: 위 F4 남은 일의 13번 "첫째에게 1억 주면 세금 얼마야?" 응답이 도구 결과 `payableTax` 9700000(summary에는 9,700,000원)을 "970,000원"으로 옮긴 것. 브랜치 `feature/15-chat-tools-fix-2`(develop `25cfdea`에서 시작), 4.2대로 고쳤다. 도구 이름·입력 모델·카드 형식·도구 순서·시스템 프롬프트 고정 규칙은 바꾸지 않았다.
- 고친 것
  - 도구 결과 `Text` 필드: `chat/tools/common.py`에 원화 금액 필드 이름 목록 `KRW_FIELDS`와 공용 도우미 `with_krw_text`·`tool_output`을 두고, 모듈 도구 16개가 결과를 `tool_output`(상태 결과는 `status_output`·`error_status`)으로만 만든다. content를 끝까지 훑어 목록 안의 금액(`steps[].amount`, `schedule[].tax` 등)까지 `"payableTax": 9700000, "payableTaxText": "9,700,000원"`처럼 옆에 `format_krw` 문자열을 더하고 원값은 그대로 둔다. 카드는 바꾸지 않았다. 01a의 `app/core/format.py`는 고치지 않았다.
  - summary: 금액이 들어가는 문장(가계부·증여세·분산 증여)을 `krw()`(=`format_krw`)로 쓰게 바꿨다. 결과 문자열은 전과 같은 전체 자릿수이고, 만원·억원 축약은 summary에 없다(테스트로 고정).
  - 시스템 프롬프트: "도구 사용" 절 첫 줄 바로 뒤에 고정 문장 "- 금액은 도구 결과에서 이름이 Text로 끝나는 값(예: payableTaxText)이나 summary에 적힌 그대로 옮긴다. 만원·억원으로 바꾸거나 자릿수를 다시 세지 않는다."를 더했다. 가변 값은 없다.
  - 평가 지표 "금액 불일치": `run_eval.py`가 응답의 원화 금액 표현을 원 단위 정수로 바꿔 그 질문의 도구 결과에 없으면 1건으로 센다. 표에 "금액 불일치" 열(불일치 표현 목록)을, 요약에 합계를 더했다.
- 스펙에 없어 정한 것
  - `Text` 대상 필드는 도구 전체가 함께 쓰는 이름 목록 하나다. 자산배분(`totalAmount`·`excludedAmount`·`actualAmount`·`targetAmount`·`diffAmount`·`targetAmounts`·`amount`), 가계부(`incomeTotal`·`expenseTotal`·`net`·`amount`), 매매일지(`realizedPnl`·`avgWin`·`avgLoss`·`avgCost`·`costBasis`·`currentPrice`·`evalAmount`·`unrealizedPnl`), 시세·지표(`price`·`change`·`marketCap`·`tradingValue`·`eps`·`bps`·`high52w`·`low52w`), 상속·증여(공제 `limit`·`usedWithin10y`·`used`·`available`, 세액 계산 단계 값, 재산·방향별 금액, 증여 일정 `amount`·`tax`, `futureValue` 등)다. 원값이 null이면 `Text`도 null, 자산군별 금액 맵(`targetAmounts`)은 같은 키의 문자열 맵(`targetAmountsText`), 정수가 아닌 원화(평균단가 등)는 카드의 `money()`와 같게 반올림한다.
  - 외화 판단: 같은 객체의 `currency`가 있으면 그것(`KRW`만 원화), 없으면 `market`(KOSPI·KOSDAQ·KONEX·매매일지의 KRX 또는 null이면 원화), 둘 다 없으면 바깥 객체를 따른다. 달러 거래·해외 종목 값에는 `Text`를 넣지 않는다(summary의 `$` 문자열은 그대로).
  - 지표의 도구 결과는 SSE에 없어 대화 저장 행(`tool_results`)에서 읽는다(Ollama `content`·Gemini `result[].text` 모두 문자열을 JSON으로 풀어 훑는다). 비교 대상은 정수 값(음수는 절댓값, 정수가 아닌 숫자는 반올림)과, 문자열(`…Text`·summary·02의 방향·이유 문장 등) 속 원화 금액을 같은 파서로 뽑은 값이다. 4.2는 "원값과 `…Text`"라고 적었지만, 도구가 준 문장 속 금액을 그대로 인용한 것은 불일치로 세지 않으려고 문자열 전체를 넣었다. 1만원 미만은 세지 않고, 반올림한 축약(예: "8억 2,753만 원")은 원 단위로 정확히 같지 않으면 불일치다.
  - 금액 표현 파서: 쉼표·소수, 조·억·만 단위와 그 안의 천·백(`2천5백만원`), 단위 사이 공백(`1억 2,000만 원`)을 읽는다. 숫자 뒤 "원칙·원리·원래"는 금액이 아니다.
- 회귀 테스트
  - `backend/tests/chat/test_tool_amount_text.py`(9개): 도우미의 금액·null·맵·반올림·원값 유지, 외화 건너뛰기, 기존 `Text` 유지, 모듈 도구가 `ToolOutput`을 직접 만들지 않음, 시드 사용자로 도구 16개를 실제로 불러 모든 숫자 필드가 원화 금액(`Text` 있음·값 일치) 또는 비금액 목록 중 하나로 분류되는지(빠진 필드를 잡는지 `futureValue`를 잠시 빼서 확인함)·summary 축약 없음·카드에 `Text` 없음, 증여세 `payableTaxText`와 summary, 원화만 `realizedPnlText`, 분산 증여·가계부 `Text`, 시스템 프롬프트 새 문장이 도구 사용 절에 한 번.
  - `backend/tests/chat/test_evals.py`(7개 추가): 만원·억원·쉼표·조·천·백 표현 파싱, 날짜·나이·퍼센트·"원칙" 제외, 도구 결과 정수·`Text`·summary·음수·bool 처리, Gemini 저장 항목, 불일치 판정(자릿수 오류만, 같은 값의 만원 표기와 1만원 미만 제외, 도구 없는 질문), 표 열과 합계, 가짜 공급자로 `ask`가 저장 행에서 도구 결과를 읽는지.
  - 기존 테스트 기대값: `test_tools_ledger_journal.py`의 `bestTrade` exact 비교와 `byCategory` 항목에 `…Text`를 더했다.

#### 검사 결과

- 백엔드: `uv run ruff check .`·`uv run ruff format --check .` 통과, `uv run pytest` 1384개 통과. 프론트·마이그레이션은 바꾸지 않았다.
- 평가 세트(로컬 Ollama `gemma4:12b`만, `cd backend && LLM_PERSONAL_PROVIDER=ollama uv run python -m app.modules.chat.evals.run_eval`, 각 약 17분, 시드는 롤백, 시작 전 메모리 압력 1 확인, 평가 중 pytest·build 동시 실행 없음). 변경 전은 지표만 넣은 커밋 `6ad025b`, 변경 후는 `50bfe23`이다. 실행 로그의 외부 요청은 `127.0.0.1:11434`(Ollama)뿐이었다.

| 지표 | 변경 전(`6ad025b`) | 변경 후(`50bfe23`) |
| --- | --- | --- |
| 처음 부른 도구 일치 | 16/17 | 16/17 |
| 기대 도구 호출 | 17/17 | 17/17 |
| 금지 문구 | 0건 | 0건 |
| 금액 불일치 | 5건 | 0건 |
| 오류 응답 | 0건 | 0건 |

| # | 질문 | 변경 전 금액 불일치 | 변경 후 |
| --- | --- | --- | --- |
| 13 | 첫째에게 1억 주면 세금 얼마야? | 970,000원(도구 결과 9,700,000원) | 없음(응답이 "첫째에게 100,000,000원을 증여할 때 예상되는 세금은"으로 시작하고 응답의 금액이 모두 도구 결과에 있음) |
| 14 | 자녀에게 미리 증여하는 게 나아, 그냥 상속하는 게 나아? | 8억 2,753만 원, 8억 2,190만 원, 8억 2,190만 원, 562만 원 | 없음 |

- 변경 전 14번은 방향별 원 단위 금액을 만원 단위로 반올림해 옮겼고(원 단위로 같지 않음), "562만 원"은 두 방향의 차이로 보이는 값을 모델이 직접 계산했다(도구 결과에 없음). 변경 후에는 두 질문 모두 도구 결과의 금액을 그대로 옮겼다. 나머지 15문항은 두 번 모두 불일치가 없었고, 처음 부른 도구는 두 번 모두 5번만 `get_today`였다(F4 남은 일과 같음).
- 실제 서버 확인(`LLM_PERSONAL_PROVIDER=ollama scripts/start.sh backend`, 8015, 로컬 Ollama `gemma4:12b`): `run_eval.seed_user`와 같은 시드를 공개 API로 만든 사용자로 13번 질문을 보냈다. `estimate_gift_tax`를 처음 불렀고, 응답 첫 문장이 "첫째에게 100,000,000원을 증여할 경우, 예상 납부 세액은 9,700,000원입니다"로 카드 세액(9,700,000원)과 같았다. 응답의 다른 금액(증여재산공제 20,000,000원, 과세표준 100,000,000원, 산출세액 10,000,000원, 신고세액공제 300,000원)도 모두 도구 결과에 있는 값이다. 확인 뒤 `scripts/stop.sh backend`로 멈췄다. 확인용 시드 사용자는 워크트리 DB(`finance_15`)에만 남아 있다.

#### 남은 일

- 금액 불일치 0건은 평가 한 번의 결과다. 모델 출력은 실행마다 달라지므로 다음 평가에서도 지표를 함께 본다.
- 5번("올해 매매 성적")이 `get_today`를 먼저 부르는 것은 F4 남은 일 그대로다(`journal.py` 설명은 이번 범위에서 바꾸지 않았다).
