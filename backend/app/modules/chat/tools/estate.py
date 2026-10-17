"""상속·증여 도구: get_gift_deductions, estimate_gift_tax, compare_estate_plans, plan_gifts
(스펙 15 4절).

06의 공개 함수만 부른다. 결과는 참고용 추정이고, 06의 문장(단계 이름·가정·안내)을
바꾸지 않고 전달한다.
estate 공개 함수와 plan_gifts 자체 검증의 404·422는 invalid_input 상태로 바꾼다(4.1).
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.core.errors import AppError, field_error
from app.modules.chat.tools.common import (
    ToolInput,
    day_text,
    error_status,
    krw,
    krw_compact,
    number,
    plain,
    status_output,
    tool_output,
)
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.estate import service as estate_service
from app.modules.estate.schemas import (
    DonorGroup,
    GiftPlanInput,
    GiftTaxRequest,
    MemberInfo,
    Recipient,
    Relation,
)

ESTATE_STATUSES = {"NOT_FOUND": "invalid_input", "VALIDATION_ERROR": "invalid_input"}

# 06 기본값(CompareRequest·GiftPlanRequest와 같다).
DEFAULT_HORIZON_YEARS = 20
DEFAULT_GROWTH_RATE = Decimal("3")
DEFAULT_LUMP_SUM_PER_CHILD = 100_000_000
DEFAULT_TARGET_AGE = 30
DEFAULT_RETURN_RATE = Decimal("4")

ASSETS_REQUIRED_MESSAGE = "자산배분에 자산을 입력하거나 상속·증여 화면에서 재산을 입력해 주세요"
NO_MEMBERS_MESSAGE = (
    "등록된 가족이 없습니다. 상속·증여 화면의 가족 탭에서 가족을 먼저 등록해 주세요."
)
REFERENCE_NOTE = "참고용 추정"
GIFT_LIST_LIMIT = 12

# 06 화면(features/estate/utils.ts)과 같은 표시 이름
RELATION_LABEL = {
    "spouse": "배우자",
    "child": "자녀",
    "grandchild": "손자녀",
    "parent": "부모",
    "other_relative": "기타친족",
}
DONOR_LABEL = {
    "parents": "부모",
    "grandparents": "조부모",
    "spouse": "배우자",
    "children": "자녀",
    "relative": "기타친족",
    "non_relative": "친족 아님",
}
CATEGORY_LABEL = {
    "lineal_ascendant": "직계존속",
    "lineal_descendant": "직계비속",
    "spouse": "배우자",
    "other_relative": "기타친족",
    "none": "공제 없음",
}
DEDUCTION_TYPE_LABEL = {"basic": "기본", "marriage": "혼인·출산"}

MemberName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Money = Annotated[int, Field(ge=1, le=1_000_000_000_000)]


def _rate_text(value: str | None, *, high: int) -> str | None:
    """'3', '3.5' 같은 숫자 문자열(%)을 검증한다."""
    if value is None:
        return None
    try:
        decimal = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError('숫자 문자열(예: "3")이어야 합니다.') from None
    if not decimal.is_finite() or not 0 <= decimal <= high:
        raise ValueError(f"0 이상 {high} 이하여야 합니다.")
    return format(decimal.normalize(), "f")


# ── 가족 별칭 찾기 ─────────────────────────────────────────────────────


def _key(name: str) -> str:
    return "".join(name.split()).casefold()


def _candidate(member: MemberInfo) -> dict:
    return {
        "name": member.display_name,
        "relation": member.relation,
        "relationLabel": RELATION_LABEL[member.relation],
        "age": member.age,
    }


def find_member(members: list[MemberInfo], name: str) -> MemberInfo | ToolOutput:
    """별칭을 공백 무시로 찾는다. 없거나 여럿이면 후보 목록 결과(오류 아님)를 돌려준다."""
    if not members:
        return status_output("no_members", NO_MEMBERS_MESSAGE)
    matched = [member for member in members if _key(member.display_name) == _key(name)]
    if len(matched) == 1:
        return matched[0]
    if matched:
        return status_output(
            "member_ambiguous",
            f"'{name}' 별칭의 가족이 여러 명입니다. 후보 중 누구인지 사용자에게 물어봐 주세요.",
            candidates=[_candidate(member) for member in matched],
        )
    return status_output(
        "member_not_found",
        f"'{name}' 별칭의 가족이 없습니다. 후보 중 누구인지 사용자에게 물어봐 주세요.",
        candidates=[_candidate(member) for member in members],
    )


# ── get_gift_deductions ────────────────────────────────────────────────


class DeductionInput(ToolInput):
    member_name: MemberName | None = None


def _deduction_entry(member: MemberInfo, status) -> dict:
    return {
        "name": member.display_name,
        "relation": member.relation,
        "relationLabel": RELATION_LABEL[member.relation],
        "age": status.age,
        "isMinor": status.is_minor,
        "adultOn": plain(status.adult_on),
        "pools": [
            {**plain(pool), "categoryLabel": CATEGORY_LABEL[pool.category]} for pool in status.pools
        ],
        "marriageBirth": plain(status.marriage_birth),
    }


def get_gift_deductions(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, DeductionInput)
    members = estate_service.list_members(ctx.db, ctx.user.id)
    if not members:
        return status_output("no_members", NO_MEMBERS_MESSAGE)
    if params.member_name is not None:
        found = find_member(members, params.member_name)
        if isinstance(found, ToolOutput):
            return found
        targets = [found]
    else:
        targets = members
    entries: list[dict] = []
    rows: list[dict] = []
    rule_version = None
    for member in targets:
        try:
            status = estate_service.get_deduction_status(ctx.db, ctx.user.id, member.id, ctx.today)
        except AppError as exc:
            output = error_status(exc, ESTATE_STATUSES)
            if len(targets) == 1:
                return output
            entries.append({"name": member.display_name, **output.content})
            continue
        rule_version = status.rule_version
        entries.append(_deduction_entry(member, status))
        rows.extend(
            {
                # 360px 채팅 폭에 맞게 열을 줄이고 금액은 짧게 쓴다(정확한 값은 content에 있다).
                "member": f"{member.display_name} · {CATEGORY_LABEL[pool.category]}",
                "available": f"{krw_compact(pool.available)} / {krw_compact(pool.limit)}",
                "resetOn": day_text(pool.next_reset_on) or "-",
            }
            for pool in status.pools
        )
    content = {
        "summary": (
            f"{day_text(ctx.today)} 기준 가족 {len(targets)}명의 10년 합산 증여재산공제 현황입니다"
            f"({REFERENCE_NOTE})."
        ),
        "asOf": ctx.today.isoformat(),
        "members": entries,
        "ruleVersion": rule_version,
    }
    if not rows:
        return tool_output(content)
    card = {
        "type": "table",
        "title": f"증여재산공제 현황 ({day_text(ctx.today)} 기준)",
        "columns": [
            {"key": "member", "label": "가족·공제"},
            {"key": "available", "label": "남은 공제/한도", "align": "right"},
            {"key": "resetOn", "label": "회복일", "align": "right"},
        ],
        "rows": rows,
        "footnote": (
            "남은 공제는 10년 합산 기준이고, 회복일은 다음 공제 회복일입니다. 혼인·출산 공제는 "
            "따로 봅니다. 참고용 추정입니다."
        ),
        "href": "/estate?tab=deductions",
    }
    return tool_output(content, card)


# ── estimate_gift_tax ──────────────────────────────────────────────────


class GiftTaxToolInput(ToolInput):
    member_name: MemberName | None = None
    relation: Relation | None = None
    birth_date: date | None = None
    donor_group: DonorGroup = "parents"
    amount: Money
    gifted_on: date | None = None


def estimate_gift_tax(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, GiftTaxToolInput)
    gifted_on = params.gifted_on or ctx.today
    member: MemberInfo | None = None
    common = {"donor_group": params.donor_group, "gifted_on": gifted_on, "amount": params.amount}
    if params.member_name is not None:
        found = find_member(estate_service.list_members(ctx.db, ctx.user.id), params.member_name)
        if isinstance(found, ToolOutput):
            return found
        member = found
        # 가족이면 memberId로 증여 기록(10년 합산)까지 06이 채운다.
        request = GiftTaxRequest(member_id=member.id, **common)
    else:
        request = GiftTaxRequest(
            recipient=Recipient(relation=params.relation or "child", birth_date=params.birth_date),
            **common,
        )
    try:
        tax_input = estate_service.resolve_gift_input(ctx.db, ctx.user.id, request)
        result = estate_service.calc_gift_tax(tax_input)
    except AppError as exc:
        return error_status(exc, ESTATE_STATUSES)
    relation = tax_input.recipient.relation
    who = member.display_name if member else RELATION_LABEL[relation]
    content = {
        "summary": (
            f"{who}에게 {krw(params.amount)}을 증여하면(증여자 {DONOR_LABEL[params.donor_group]}) "
            f"예상 납부 세액은 {krw(result.payable_tax)}입니다({REFERENCE_NOTE})."
        ),
        "recipient": {
            "name": member.display_name if member else None,
            "relation": relation,
            "relationLabel": RELATION_LABEL[relation],
            "birthDate": plain(tax_input.recipient.birth_date),
        },
        "donorGroup": params.donor_group,
        "donorGroupLabel": DONOR_LABEL[params.donor_group],
        "giftedOn": gifted_on.isoformat(),
        "priorGiftCount": len(tax_input.prior_gifts),
        **plain(result.model_dump(by_alias=True, exclude={"steps"})),
        "steps": [
            {"label": step.label, "amount": step.amount, "note": step.note} for step in result.steps
        ],
    }
    card = {
        "type": "gift_tax",
        "title": f"{who} 증여세 계산",
        "steps": [
            {"label": step.label, "amount": krw(step.amount), "note": step.note}
            for step in result.steps
        ],
        "payableTax": krw(result.payable_tax),
        "href": "/estate?tab=gift-tax",
    }
    return tool_output(content, card)


# ── compare_estate_plans ───────────────────────────────────────────────


class CompareToolInput(ToolInput):
    horizon_years: int | None = Field(default=None, ge=1, le=40)
    annual_growth_rate: str | None = None
    lump_sum_per_child: Money | None = None

    @field_validator("annual_growth_rate")
    @classmethod
    def valid_growth(cls, value: str | None) -> str | None:
        return _rate_text(value, high=10)


def _assets_required() -> ToolOutput:
    return status_output("assets_required", ASSETS_REQUIRED_MESSAGE)


def compare_estate_plans(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, CompareToolInput)
    horizon_years = params.horizon_years or DEFAULT_HORIZON_YEARS
    growth_rate = (
        Decimal(params.annual_growth_rate)
        if params.annual_growth_rate is not None
        else DEFAULT_GROWTH_RATE
    )
    try:
        compare_input = estate_service.build_compare_input(
            ctx.db,
            ctx.user.id,
            base_date=ctx.today,
            horizon_years=horizon_years,
            growth_rate=growth_rate,
            lump_sum_per_child=params.lump_sum_per_child or DEFAULT_LUMP_SUM_PER_CHILD,
        )
    except AppError as exc:
        if exc.code == "VALIDATION_ERROR" and any(d.field == "assets" for d in exc.details):
            return _assets_required()
        return error_status(exc, ESTATE_STATUSES)
    assets = compare_input.assets
    # 06은 자산이 없어도 0원 사전 채움을 돌려준다. 합계가 0이면 재산 입력을 안내한다(4.1).
    if assets.financial_assets + assets.real_estate + assets.other_assets == 0:
        return _assets_required()
    try:
        result = estate_service.compare_estate_plans(compare_input)
    except AppError as exc:
        return error_status(exc, ESTATE_STATUSES)
    recommended = next(item for item in result.scenarios if item.key == result.recommended_key)
    members = compare_input.members
    content = {
        "summary": (
            f"{day_text(result.horizon_date)}에 상속한다고 가정하면 같은 가정에서 가족에게 가장 "
            f"많이 남는 방향은 '{recommended.label}'입니다({REFERENCE_NOTE}, 실제 결정은 세무 "
            "전문가와 상의)."
        ),
        "baseDate": result.base_date.isoformat(),
        "horizonDate": result.horizon_date.isoformat(),
        "horizonYears": horizon_years,
        "annualGrowthRate": number(result.annual_growth_rate),
        "assets": {
            **plain(assets),
            "source": (
                "자산배분 보유 자산으로 채운 값(금융재산=주식·채권·금·현금성 자산, "
                "기타재산=기타, 부동산=배분에서 제외한 자산)"
            ),
        },
        "spouseAlive": compare_input.spouse_alive,
        "childCount": sum(1 for m in members if m.relation == "child"),
        "grandchildCount": sum(1 for m in members if m.relation == "grandchild"),
        "scenarios": [
            {
                "key": item.key,
                "label": item.label,
                "totalGifted": item.total_gifted,
                "giftTaxTotal": item.gift_tax_total,
                "giftsValueAtHorizon": item.gifts_value_at_horizon,
                "estateAtHorizon": item.estate_at_horizon,
                "inheritanceTax": item.inheritance_tax,
                "totalTax": item.total_tax,
                "familyReceives": item.family_receives,
                "giftCount": len(item.gifts),
                "gifts": [
                    {
                        "name": gift.display_name,
                        "giftOn": gift.gift_on.isoformat(),
                        "amount": gift.amount,
                        "giftTax": gift.gift_tax,
                    }
                    for gift in item.gifts[:GIFT_LIST_LIMIT]
                ],
            }
            for item in result.scenarios
        ],
        "recommendedKey": result.recommended_key,
        "recommendedMeaning": "같은 가정에서 가족에게 가장 많이 남는 방향(세무 판단이 아님)",
        "notes": result.notes,
        "assumptions": result.assumptions,
        "ruleVersion": result.rule_version,
    }
    card = {
        "type": "comparison",
        "title": "상속·증여 방향 비교",
        "horizonDate": day_text(result.horizon_date),
        "rows": [
            {
                "key": item.key,
                "label": item.label,
                "totalTax": krw(item.total_tax),
                "familyReceives": krw(item.family_receives),
                "recommended": item.key == result.recommended_key,
            }
            for item in result.scenarios
        ],
        "href": "/estate?tab=compare",
    }
    return tool_output(content, card)


# ── plan_gifts ─────────────────────────────────────────────────────────


class PlanToolInput(ToolInput):
    member_name: MemberName | None = None
    birth_date: date | None = None
    target_age: int | None = Field(default=None, ge=19, le=40)
    annual_return_rate: str | None = None

    @field_validator("annual_return_rate")
    @classmethod
    def valid_return(cls, value: str | None) -> str | None:
        return _rate_text(value, high=20)


def plan_gifts(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, PlanToolInput)
    member: MemberInfo | None = None
    if params.member_name is not None:
        found = find_member(estate_service.list_members(ctx.db, ctx.user.id), params.member_name)
        if isinstance(found, ToolOutput):
            return found
        member = found
    member_id = member.id if member else None
    target_age = params.target_age or DEFAULT_TARGET_AGE
    return_rate = (
        Decimal(params.annual_return_rate)
        if params.annual_return_rate is not None
        else DEFAULT_RETURN_RATE
    )
    try:
        # 06 라우터(POST /api/estate/simulate/gift-plan)와 같은 순서로 입력을 만든다.
        if (member_id is None) == (params.birth_date is None):
            raise field_error("memberId", "가족 또는 생년월일 중 하나를 입력해 주세요.")
        recipient = (
            estate_service.member_recipient(ctx.db, ctx.user.id, member_id) if member_id else None
        )
        birth = recipient.birth_date if recipient else params.birth_date
        if birth is None:
            raise field_error("birthDate", "생년월일이 필요합니다.")
        # 사용자가 증여자다. 손자녀에게는 조부모 증여(세대생략 할증 포함)로 본다.
        donor_group = (
            "grandparents"
            if recipient is not None and recipient.relation == "grandchild"
            else "parents"
        )
        plan = estate_service.simulate_gift_plan(
            GiftPlanInput(
                birth_date=birth,
                start_date=ctx.today,
                recipient=recipient,
                prior_gifts=(
                    estate_service.prior_gifts(ctx.db, ctx.user.id, member_id) if member_id else []
                ),
                target_age=target_age,
                annual_return_rate=return_rate,
                donor_group=donor_group,
                expected_marriage_date=None,
            )
        )
    except AppError as exc:
        return error_status(exc, ESTATE_STATUSES)
    who = member.display_name if member else "수증자"
    content = {
        "summary": (
            f"{who}의 {target_age}세({day_text(plan.end_date)})까지 10년마다 공제 한도만큼 나눠 "
            f"증여하면 총 {krw(plan.total_gifted)}, 예상 세금 "
            f"{krw(plan.total_tax)}입니다({REFERENCE_NOTE})."
        ),
        "recipient": {
            "name": member.display_name if member else None,
            "relation": recipient.relation if recipient else None,
            "relationLabel": RELATION_LABEL[recipient.relation] if recipient else None,
            "birthDate": birth.isoformat(),
        },
        "donorGroup": donor_group,
        "donorGroupLabel": DONOR_LABEL[donor_group],
        "targetAge": target_age,
        "annualReturnRate": number(return_rate),
        "startDate": ctx.today.isoformat(),
        **plain(plan),
    }
    if not plan.schedule:
        return tool_output(content)
    card = {
        "type": "table",
        "title": f"{who} 분산 증여 플랜",
        "columns": [
            {"key": "giftOn", "label": "증여일(나이)"},
            {"key": "amount", "label": "증여액", "align": "right"},
            {"key": "deduction", "label": "공제"},
            {"key": "tax", "label": "세금", "align": "right"},
        ],
        "rows": [
            {
                "giftOn": f"{day_text(item.gift_on)} ({item.age}세)",
                "amount": krw(item.amount),
                "deduction": DEDUCTION_TYPE_LABEL[item.deduction_type],
                "tax": krw(item.tax),
            }
            for item in plan.schedule
        ],
        "footnote": (
            f"총 {krw(plan.total_gifted)} 증여 · 세금 {krw(plan.total_tax)} · "
            f"{day_text(plan.end_date)} 평가액 {krw(plan.future_value)}(연 {number(return_rate)}% "
            f"가정). 같은 금액을 지금 한 번에 주면 세금 {krw(plan.lump_sum.payable_tax)}. 참고용 "
            "추정입니다."
        ),
        "href": "/estate?tab=plan",
    }
    return tool_output(content, card)


MEMBER_NAME_RULE = (
    "memberName은 상속·증여 화면에 등록한 가족 별칭(예: 첫째)이고 공백을 무시하고 찾는다. "
    "없거나 여럿이면 status가 member_not_found·member_ambiguous이고 candidates에 후보가 있으니 "
    "사용자에게 누구인지 물어본다. status가 invalid_input이면 message·details의 안내대로 입력을 "
    "고친다."
)

register_tool(
    ToolSpec(
        name="get_gift_deductions",
        description=(
            "등록한 가족별 증여재산공제(10년 합산) 한도·사용액·남은 공제·다시 생기는 날과 "
            "혼인·출산 공제 현황을 확인한다. '첫째에게 세금 없이 얼마까지 줄 수 있어?'처럼 남은 "
            "공제 한도·사용액·다시 생기는 날만 궁금할 때 쓴다. 금액이 주어진 세금 질문('1억 주면 "
            "세금 얼마야?')에는 이 도구를 쓰지 않고 estimate_gift_tax를 쓴다. 언제·얼마씩 나눠 "
            "줄지 묻는 일정 질문에는 이 도구를 쓰지 않고 plan_gifts를 쓴다. 두 계산 도구가 공제 "
            "한도를 직접 반영한다. memberName이 null이면 가족 전원을 본다. "
            f"{MEMBER_NAME_RULE} 결과는 참고용 추정이다."
        ),
        input_model=DeductionInput,
        label="증여 공제 확인 중",
        handler=get_gift_deductions,
    )
)
register_tool(
    ToolSpec(
        name="estimate_gift_tax",
        description=(
            "증여세를 계산한다. '첫째에게 1억 주면 세금 얼마야?'처럼 증여 금액이 주어진 세금 "
            "질문에 쓴다. 공제 한도와 10년 합산 증여 기록을 이 도구가 직접 반영하므로 "
            "get_gift_deductions를 먼저 부를 필요가 없다. 사용자가 '첫째'·'둘째'처럼 가족을 "
            "말하면 memberName에 그 별칭만 넣고 바로 부른다. 그 가족의 관계·생년월일·증여 "
            "기록은 도구가 가족 정보에서 채우므로 사용자에게 묻지 않는다. 등록하지 않은 사람이면 "
            "memberName을 null로 두고 relation(spouse·child·grandchild·parent·other_relative)과 "
            "birthDate를 넣는다. donorGroup은 증여하는 사람(parents·grandparents·spouse·children·"
            "relative·non_relative, 보통 parents), amount는 원 단위 정수(1억=100000000), "
            f"giftedOn이 null이면 오늘이다. {MEMBER_NAME_RULE} 결과는 신고기한 안에 신고한다고 "
            "가정한 참고용 추정이다. 답할 때 가정을 짧게 밝히고 실제 신고 전 세무 전문가 상담을 "
            "권한다."
        ),
        input_model=GiftTaxToolInput,
        label="증여세 계산 중",
        handler=estimate_gift_tax,
    )
)
register_tool(
    ToolSpec(
        name="compare_estate_plans",
        description=(
            "증여 없이 상속, 자녀에게 10년마다 분산 증여, 자녀에게 지금 일시 증여, 손자녀에게 분산 "
            "증여 같은 상속·증여 방향을 같은 가정에서 비교해 방향별 총 세금과 가족에게 남는 금액을 "
            "돌려준다. '자녀에게 미리 증여하는 게 나아, 그냥 상속하는 게 나아?' 같은 질문에 쓴다. "
            "재산은 자산배분 보유 자산으로 채운다. 인자는 모두 null이면 06 기본값이다: "
            "horizonYears(상속 가정까지 몇 년, "
            '1~40, 기본 20), annualGrowthRate(연 재산 증가율 % 숫자 문자열, 0~10, 기본 "3"), '
            "lumpSumPerChild(일시 증여 시 자녀 1명당 원, 기본 100000000). status가 "
            "assets_required면 재산이 없으니 message대로 입력을 안내하고, invalid_input이면 "
            "message를 전한다(예: 자녀나 배우자 미등록). recommendedKey는 같은 가정에서 가족에게 "
            "가장 많이 남는 방향일 뿐이다. 결과는 참고용 비교이며 실제 결정은 세무 전문가와 "
            "상의하라고 말한다."
        ),
        input_model=CompareToolInput,
        label="상속·증여 방향 비교 중",
        handler=compare_estate_plans,
    )
)
register_tool(
    ToolSpec(
        name="plan_gifts",
        description=(
            "자녀·손자녀에게 목표 나이까지 10년마다 공제 한도만큼 나눠 증여하는 "
            "일정(증여일·나이·금액·세금)과 총 증여액·세금·목표 시점 평가액, 같은 금액을 지금 한 "
            "번에 줄 때의 세금을 계산한다. '둘째에게 언제 얼마씩 증여하면 돼?'처럼 언제·얼마씩 "
            "나눠 줄지 묻는 일정 질문에 쓴다. 공제 한도와 10년 합산 증여 기록을 이 도구가 직접 "
            "반영하므로 get_gift_deductions를 먼저 부를 필요가 없다. 사용자가 '첫째'·'둘째'처럼 "
            "가족을 말하면 memberName에 그 별칭만 넣고 바로 부른다. 그 가족의 생년월일·증여 "
            "기록은 도구가 가족 정보에서 채우므로 사용자에게 묻지 않는다. 등록하지 않은 사람이면 "
            "memberName을 null로 두고 birthDate를 넣는다(둘 중 하나만). targetAge는 19~40(기본 "
            '30), annualReturnRate는 연 수익률 % 숫자 문자열(0~20, 기본 "4")이다. 사용자가 '
            "말하지 않았으면 묻지 말고 null로 두면 기본값으로 계산한다. "
            f"{MEMBER_NAME_RULE} 결과는 참고용 추정이다."
        ),
        input_model=PlanToolInput,
        label="분산 증여 플랜 계산 중",
        handler=plan_gifts,
    )
)
