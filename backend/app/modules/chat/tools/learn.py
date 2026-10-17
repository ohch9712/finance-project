"""학습 콘텐츠 도구: search_learning(스펙 15 4절).

사용자 없이 learn.search_posts를 부르므로 북마크·읽음 표시는 넣지 않는다(4.1).
"""

from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, StringConstraints

from app.modules.chat.tools.common import ToolInput, tool_output
from app.modules.chat.tools.registry import ToolContext, ToolOutput, ToolSpec, register_tool
from app.modules.learn import service as learn_service

SEARCH_LIMIT = 5
KIND_LABEL = {"article": "글", "video": "영상", "glossary": "용어"}

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class LearnInput(ToolInput):
    query: Query


def post_href(slug: str) -> str:
    return f"/learn/{quote(slug, safe='')}"


def search_learning(ctx: ToolContext, params: BaseModel) -> ToolOutput:
    assert isinstance(params, LearnInput)
    posts = learn_service.search_posts(ctx.db, params.query, limit=SEARCH_LIMIT)
    if not posts:
        return tool_output(
            {
                "status": "no_results",
                "message": (
                    f"'{params.query}' 관련 학습 글이 없습니다. 일반적인 설명이라고 밝히고 "
                    "답하세요."
                ),
            }
        )
    content = {
        "summary": (
            f"'{params.query}' 관련 학습 자료 {len(posts)}개입니다. 링크(href)를 함께 안내하세요."
        ),
        "posts": [
            {
                "title": post.title,
                "summary": post.summary,
                "kind": post.kind,
                "kindLabel": KIND_LABEL.get(post.kind, post.kind),
                "term": post.term,
                "level": post.level,
                "tags": post.tags,
                "href": post_href(post.slug),
            }
            for post in posts
        ],
    }
    card = {
        "type": "link_list",
        "title": f"'{params.query}' 학습 자료",
        "links": [
            {
                "title": post.title,
                "description": post.summary or None,
                "href": post_href(post.slug),
            }
            for post in posts
        ],
    }
    return tool_output(content, card)


register_tool(
    ToolSpec(
        name="search_learning",
        description=(
            "주식·투자 용어와 학습 글·영상을 이 서비스의 학습 게시판에서 찾아 "
            "제목·요약·링크(href)를 최대 5개 돌려준다. 'PER이 뭐야?', 'ETF가 뭐야?' 같은 "
            "용어·개념 질문에 먼저 쓴다. 결과를 근거로 설명하고 링크를 함께 준다. query는 "
            "용어나 짧은 주제어(예: PER)다. status가 no_results면 게시판에 자료가 없다는 뜻이다. "
            "사용자 개인 데이터는 다루지 않는다."
        ),
        input_model=LearnInput,
        label="학습 자료 찾는 중",
        handler=search_learning,
    )
)
