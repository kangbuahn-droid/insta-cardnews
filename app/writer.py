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
                    # 따라 입력해볼 프롬프트 예시(없으면 빈 문자열)
                    "prompt": {"type": "string"},
                    # 슬라이드에 넣을 그림: none / illustration(분위기 그림) / example(prompt 를 실제로 넣은 결과)
                    "image_mode": {"type": "string", "enum": ["none", "illustration", "example"]},
                    # 이미지 생성용 영어 설명(image_mode 가 none 이면 빈 문자열)
                    "image": {"type": "string"},
                },
                "required": ["type", "title", "body", "prompt", "image_mode", "image"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
        "mood": {"type": "string", "enum": ["upbeat", "calm", "serious", "tense"]},
        "keyword": {"type": "string"},
        "emoji": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["slides", "caption", "mood", "keyword", "emoji", "hashtags"],
    "additionalProperties": False,
}

# 채널 yaml 에 write_rules 가 없을 때 쓰는 기본(사연형) 규칙
DEFAULT_WRITE_RULES = """
1. 원문을 그대로 옮기지 말고, 내용을 이해한 뒤 너의 문장으로 다시 써라. 원문 문장을 그대로 쓰는 것은 15자 이하의 짧은 인용만 허용한다.
2. 회사명, 실명, 부서·직급으로 특정 가능한 정보, 연락처 등 개인을 알아볼 수 있는 정보는 모두 지우거나 일반화한다(예: "A사", "한 대기업").
3. 욕설·비방·혐오 표현은 순화한다. 사실처럼 단정하지 말고 "~라는 사연", "~라고 해요"처럼 사연임을 드러낸다.
4. 댓글이 있으면 type=reaction 한 장: title 은 "사람들 반응" 같은 제목, body 는 댓글 의견 2~3개를 요약·재작성해 줄바꿈(\\n)으로 구분.
5. caption 마지막 줄에 "출처: 직장인 커뮤니티 사연 재구성"을 쓴다.
"""

LAYOUT_RULES = """
[슬라이드 구성]
- 첫 장은 type=cover. title 은 스크롤을 멈추게 하는 후킹 문구(25자 이내, 가장 눈에 띄어야 할 단어 하나를 *별표*로 감싸 강조), body 는 한 줄 부제(30자 이내).
- 중간은 type=content. title 은 소제목(18자 이내), body 는 2~4문장(110자 이내).
- type=reaction 은 채널 규칙에서 쓰라고 할 때만 쓴다.
- 마지막은 type=outro: title 은 독자에게 던지는 질문, body 는 행동 유도 문구.
- caption 은 인스타 본문용 3~5문장. 해시태그는 caption 에 넣지 말고 hashtags 에 5~10개.
- keyword 는 표지 배경에 크게 깔릴 영어 대문자 단어 하나(2~8자, 예: OPENAI, CHIP, ROBOT, LAW). 기사 핵심 대상.
- emoji 는 기사 주제를 나타내는 이모지 딱 1개(예: 🤖 💸 ⚖️ 🔥 📱).
- 각 슬라이드의 prompt, image_mode, image 는 채널 규칙에서 쓰라고 할 때만 채운다. 아니면 prompt="", image_mode="none", image="".
- mood 는 배경 색감과 릴스 배경음악 분위기: upbeat(신기하고 반가운 소식) · calm(정보·팁) · serious(규제·사회적 영향·논란) · tense(사고·위험·경쟁 격화).
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))


def write_script(channel: Channel, source: dict, feedback: str | None = None) -> dict:
    comments = "\n".join(f"- {c}" for c in source.get("comments", [])[:15]) or "(없음)"
    user = (
        f"슬라이드 수: {channel.min_slides}~{channel.max_slides}장\n"
        f"마무리 행동 유도 문구 참고: {channel.cta}\n\n"
        + (f"[출처] {source.get('source_name', '')} {source.get('url', '')}\n\n" if source.get("url") else "")
        + f"[원본 제목]\n{source.get('title', '')}\n\n"
        + f"[원본 본문]\n{source['body']}\n\n"
        + f"[댓글]\n{comments}\n"
    )
    if feedback:
        user += f"\n[이전 결과에 대한 수정 요청]\n{feedback}\n"

    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=channel.persona + "\n[작성 규칙]\n" + (channel.write_rules or DEFAULT_WRITE_RULES) + LAYOUT_RULES,
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
    script["keyword"] = "".join(ch for ch in script.get("keyword", "").upper() if ch.isalnum())[:8]
    script["emoji"] = script.get("emoji", "")[:4]
    if source.get("source_name") and "출처" not in script["caption"]:
        script["caption"] = script["caption"].rstrip() + f"\n\n출처: {source['source_name']}"
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
