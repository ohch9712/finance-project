"""자산배분 도구: get_allocation_report, get_recommendation, get_risk_profile(스펙 15 4절).

02의 공개 함수만 부르고, 방향·이유 문장은 바꾸지 않고 그대로 전달한다.
"""

from pydantic import BaseModel

from app.core.domain import AGE_BAND_LABEL, ASSET_CLASS_LABEL, RISK_PROFILE_LABEL, AssetClass
from app.core.errors import AppError
from app.core.time import KST
from app.modules.allocation import service as allocation_service
from app.modules.chat.tools.common import (
    NO_ARGUMENTS,
    NoInput,
    day_text,
    error_status,
    krw,
    number,
    plain,
    tool_output,
)
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool

DIRECTION_LIMIT = 3  # 화면의 "먼저 할 일"과 같은 개수(스펙 15 5절)

STATUS_SUMMARY = {
    "needs_profile": (
        "출생연도가 없어 계산할 수 없습니다. 설정의 프로필 화면에서 출생연도를 입력해 주세요."
    ),
    "needs_assessment": (
        "투자성향 진단 전이라 계산할 수 없습니다. 자산배분 화면에서 투자성향 진단을 해 주세요."
    ),
    "needs_holdings": (
        "배분 대상 자산이 없어 점수를 계산할 수 없습니다. 자산배분 화면에서 보유 자산을 입력해 "
        "주세요."
    ),
}
TARGET_STATUSES = {
    "PROFILE_INCOMPLETE": "profile_incomplete",
    "RISK_PROFILE_REQUIRED": "risk_profile_required",
}


def _label(value, labels) -> str | None:
    return labels[value] if value is not None else None


def get_allocation_report(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    # ready면 02가 오늘 점수 스냅샷을 남긴다. 커밋은 다음 대화 행을 쓸 때 함께 된다.
    report = allocation_service.get_allocation_report(ctx.db, ctx.user.id)
    classes = report.classes or []
    if report.status == "ready":
        summary = f"배분 일치 점수는 {report.score}점({report.grade}등급)입니다."
    else:
        summary = STATUS_SUMMARY[report.status]
    content = {
        "summary": summary,
        **plain(report),
        "ageBandLabel": _label(report.age_band, AGE_BAND_LABEL),
        "riskProfileLabel": _label(report.risk_profile, RISK_PROFILE_LABEL),
        "classes": [
            {**plain(item), "label": ASSET_CLASS_LABEL[item.asset_class]} for item in classes
        ],
    }
    card = {
        "type": "allocation",
        "status": report.status,
        "score": report.score,
        "grade": report.grade,
        "classes": [
            {
                "assetClass": item.asset_class.value,
                "label": ASSET_CLASS_LABEL[item.asset_class],
                "actualWeight": item.actual_weight,
                "targetWeight": item.target_weight,
            }
            for item in classes
        ],
        "href": "/allocation",
    }
    return tool_output(content, card)


def get_recommendation(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    recommendation = allocation_service.get_recommendation(ctx.db, ctx.user.id)
    if recommendation.status != "ready":
        return tool_output(
            {
                "status": recommendation.status,
                "summary": STATUS_SUMMARY[recommendation.status],
            }
        )
    directions = sorted(recommendation.directions, key=lambda item: item.priority)
    content = {
        "status": recommendation.status,
        "summary": (
            f"관리 방향 {len(directions)}개와 추천 이유 {len(recommendation.reasons)}개입니다."
            + (f" 가장 먼저 할 일은 '{directions[0].title}'입니다." if directions else "")
        ),
        "ageBand": plain(recommendation.age_band),
        "ageBandLabel": _label(recommendation.age_band, AGE_BAND_LABEL),
        "riskProfile": plain(recommendation.risk_profile),
        "riskProfileLabel": _label(recommendation.risk_profile, RISK_PROFILE_LABEL),
        "riskProfileSummary": recommendation.risk_profile_summary,
        "layering": recommendation.layering,
        "totalAmount": recommendation.total_amount,
        "baseWeights": plain(recommendation.base_weights),
        "targetWeights": plain(recommendation.target_weights),
        "targetAmounts": plain(recommendation.target_amounts),
        "directions": [
            {
                "priority": item.priority,
                "kind": item.kind,
                "title": item.title,
                "body": item.body,
                "amount": item.amount,
                "assetClass": plain(item.asset_class),
            }
            for item in directions
        ],
        "reasons": [{"title": item.title, "body": item.body} for item in recommendation.reasons],
        "assetClassNotes": [
            {
                "assetClass": item.asset_class.value,
                "label": ASSET_CLASS_LABEL[item.asset_class],
                "weight": item.weight,
                "note": item.note,
            }
            for item in recommendation.asset_class_notes
        ],
    }
    card = None
    if directions:
        card = {
            "type": "directions",
            "title": "먼저 할 일",
            "items": [
                {"title": item.title, "body": item.body, "amount": krw(item.amount)}
                for item in directions[:DIRECTION_LIMIT]
            ],
            "href": "/allocation/recommendation/detail",
        }
    return tool_output(content, card)


def get_risk_profile(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    info = allocation_service.get_latest_risk_profile(ctx.db, ctx.user.id)
    profile = (
        {
            "riskProfile": info.risk_profile.value,
            "riskProfileLabel": RISK_PROFILE_LABEL[info.risk_profile],
            "score": number(info.score),
            "assessedAt": plain(info.assessed_at),
        }
        if info is not None
        else {}
    )
    try:
        target = allocation_service.get_target_allocation(ctx.db, ctx.user.id)
    except AppError as exc:
        # 409는 진단 전·출생연도 없음 상태로 바꿔 전달한다(4.1). 진단 결과가 있으면 함께 준다.
        output = error_status(exc, TARGET_STATUSES)
        output.content.update(profile)
        if info is not None:
            output.card = _risk_card(info, None)
        return output
    weights = {asset_class.value: target.weights[asset_class] for asset_class in AssetClass}
    summary = (
        f"투자성향은 {RISK_PROFILE_LABEL[target.risk_profile]}이고 "
        f"{AGE_BAND_LABEL[target.age_band]} 기본 배분은 "
        + ", ".join(
            f"{ASSET_CLASS_LABEL[asset_class]} {target.weights[asset_class]}%"
            for asset_class in AssetClass
        )
        + "입니다."
    )
    content = {
        "summary": summary,
        **profile,
        "ageBand": target.age_band.value,
        "ageBandLabel": AGE_BAND_LABEL[target.age_band],
        "baseWeights": weights,
    }
    return tool_output(content, _risk_card(info, target))


def _risk_card(info, target) -> dict:
    items = [
        {"label": "투자성향", "value": RISK_PROFILE_LABEL[info.risk_profile]},
        {"label": "진단 점수", "value": f"{number(info.score)}점"},
        {"label": "진단일", "value": day_text(info.assessed_at.astimezone(KST).date())},
    ]
    if target is not None:
        items.append({"label": "나이대", "value": AGE_BAND_LABEL[target.age_band]})
        items.extend(
            {
                "label": f"{ASSET_CLASS_LABEL[asset_class]} 기본 비중",
                "value": f"{target.weights[asset_class]}%",
            }
            for asset_class in AssetClass
        )
    else:
        items.append({"label": "기본 배분", "value": "출생연도 입력 후 확인"})
    return {"type": "key_value", "title": "내 투자성향", "items": items}


register_tool(
    ToolSpec(
        name="get_allocation_report",
        description=(
            "사용자의 자산배분 일치 점수·등급과 자산군별(주식·채권·금·현금성 자산·기타) 실제 "
            "비중과 권장 비중, 리밸런싱 금액을 확인한다. '내 자산배분 점수 알려줘', '지금 배분이 "
            f"맞아?' 같은 질문에 쓴다. {NO_ARGUMENTS} 결과의 status가 ready가 아니면(needs_profile "
            "출생연도 없음, needs_assessment 진단 전, needs_holdings 보유 자산 없음) 점수가 "
            "없으니 summary의 안내대로 입력 방법을 알려 준다. 추천 이유나 먼저 할 일을 물으면 이 "
            "도구 대신 get_recommendation을 쓴다."
        ),
        input_model=NoInput,
        label="자산배분 확인 중",
        handler=get_allocation_report,
    )
)
register_tool(
    ToolSpec(
        name="get_recommendation",
        description=(
            "사용자에게 맞는 권장 자산배분의 관리 방향(먼저 할 일)과 추천 이유를 확인한다. "
            "'왜 이렇게 추천했어?', '뭐부터 해야 해?', '비상금은 얼마나 둬야 해?' 같은 질문에 "
            f"쓴다. {NO_ARGUMENTS} 결과의 directions(우선순위 순)와 reasons 문장을 바꾸지 말고 "
            "근거로 설명하며 새 수치를 만들지 않는다. status가 needs_profile이나 "
            "needs_assessment면 추천이 없으니 summary의 안내대로 입력 방법을 알려 준다. 점수만 "
            "물으면 get_allocation_report를 쓴다."
        ),
        input_model=NoInput,
        label="추천 이유 확인 중",
        handler=get_recommendation,
    )
)
register_tool(
    ToolSpec(
        name="get_risk_profile",
        description=(
            "사용자의 최근 투자성향 진단 결과(성향·점수·진단일)와 나이대·성향 표의 기본 자산군 "
            f"비중(%)을 확인한다. '내 투자성향이 뭐야?' 같은 질문에 쓴다. {NO_ARGUMENTS} "
            "status가 risk_profile_required면 진단 전이고, profile_incomplete면 출생연도가 없다. "
            "목표·비상금을 반영한 권장 배분은 get_recommendation을 쓴다."
        ),
        input_model=NoInput,
        label="투자성향 확인 중",
        handler=get_risk_profile,
    )
)
