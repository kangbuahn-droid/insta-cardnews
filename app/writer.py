"""Claude 로 원본 글을 카드뉴스 슬라이드 JSON 으로 재구성한다."""
from __future__ import annotations

import json

import anthropic

from .config import Channel, env

MODEL = env("CLAUDE_MODEL", "claude-opus-5-5")

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["cover", "content", "reaction", "outro"]},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["type", "title", "body"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["slides", "caption", "hashtags"],
    "additionalProperties": False,
}

RULES = """
[작성 규칙]
1. 원문을 그대로 옮기지 말고, 내용을 이해한 뒤 너의 문장으로 다시 써라. 원문 문장을 그대로 쓰는 것은 15자 이하의 짧은 인용만 허용한다.
2. 회사명, 실명, 부서·직급으로 특정 가능한 정보, 연락처 등 개인을 알아볼 수 있는 정보는 모두 지우거나 일반화한다(예: "A사", "한 대기업").
3. 욕설·비방·혐오 표현은 순화한다. 사실처럼 단정하지 말고 "~라는 사연", "~라고 해요"처럼 사연임을 드러낸다.
4. 슬라이드 구성:
   - 첫 장은 type=cover. title 은 스크롤을 멈추게 하는 후킹 문구(25자 이내, 가장 자극적인 단어 하나를 *별표*로 감싸 강조), body 는 한 줄 부제(30자 이내).
   - 중간은 type=content 로 사연을 순서대로. title 은 소제목(18자 이내), body 는 2~4문장(110자 이내).
   - 댓글이 있으면 type=reaction 한 장: title 은 "사람들 반응" 같은 제목, body 는 댓글 의견 2~3개를 요약·재작성해 줄바꿈(\\n)으로 구분.
   - 마지막은 type=outro: title 은 독자에게 던지는 질문, body 는 행동 유도 문구.
5. caption 은 인스타 본문용으로 3~5문장 + 마지막 줄에 출처 표기("출처: 직장인 커뮤니티 사연 재구성"). 해시태그는 caption 에 넣지 말고 hashtags 에 5~10개.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def write_script(channel: Channel, source: dict, feedback: str | None = None) -> dict:
    comments = "\n".join(f"- {c}" for c in source.get("comments", [])[:15]) or "(없음)"
    user = (
        f"슬라이드 수: {channel.min_slides}~{channel.max_slides}장\n"
        f"마무리 행동 유도 문구 참고: {channel.cta}\n\n"
        f"[원본 제목]\n{source.get('title', '')}\n\n"
        f"[원본 본문]\n{source['body']}\n\n"
        f"[댓글]\n{comments}\n"
    )
    if feedback:
        user += f"\n[이전 결과에 대한 수정 요청]\n{feedback}\n"

    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=channel.persona + "\n" + RULES,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCRIPT_SCHEMA}},
        # 안전 분류기가 거절하면 서버가 다른 모델로 자동 재시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 이 글의 작성을 거절했습니다(민감한 내용일 수 있음).")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 길이 제한에 걸려 잘렸습니다.")
    text = "".join(b.text for b in response.content if b.type == "text")
    script = json.loads(text)
    _normalize(script, channel)
    return script


def _normalize(script: dict, channel: Channel) -> None:
    slides = script["slides"]
    if not slides or slides[0]["type"] != "cover":
        raise ValueError("첫 슬라이드가 표지가 아닙니다")
    # 인스타 캐러셀은 최대 10장
    script["slides"] = slides[:10]
    tags = []
    for t in script.get("hashtags", []) + channel.hashtags:
        t = "#" + t.lstrip("#").replace(" ", "")
        if t not in tags:
            tags.append(t)
    script["hashtags"] = tags[:20]


def full_caption(script: dict) -> str:
    return script["caption"].strip() + "\n\n" + " ".join(script["hashtags"])
