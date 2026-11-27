"""소스 없이 AI가 팁 콘텐츠 주제를 기획한다(AI 팁 채널용).

채널 yaml 의 topics(분야 목록) 안에서, 지난 주제와 겹치지 않게 k개를 고른다.
지난 성과(performance.learning_hint)도 함께 넘겨 반응 좋은 성격의 주제를 늘린다.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import db, ingest, performance
from .config import Channel
from .writer import MODEL, _client

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "title": {"type": "string"},
                    "outline": {"type": "string"},
                },
                "required": ["category", "title", "outline"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ideas"],
    "additionalProperties": False,
}


def _past_titles(channel: Channel, limit: int = 80) -> list[str]:
    with db.connect() as c:
        rows = c.execute("SELECT source FROM posts WHERE channel=? ORDER BY id DESC LIMIT ?",
                         (channel.name, limit)).fetchall()
    titles = []
    for r in rows:
        try:
            titles.append(json.loads(r["source"])["title"])
        except Exception:
            pass
    return titles


def plan(channel: Channel, k: int | None = None, idea: str | None = None) -> tuple[list[int], list[str]]:
    """주제 k개를 기획해 DB 에 넣는다. idea 를 주면 그 주제 하나를 구체화한다."""
    k = 1 if idea else (k or channel.per_collect)
    past = "\n".join(f"- {t}" for t in _past_titles(channel)) or "(없음)"
    if idea:
        ask = f"사용자가 요청한 주제로 콘텐츠 1개를 기획해라: {idea}"
    else:
        ask = (f"아래 분야에서 서로 다른 분야로 콘텐츠 {k}개를 기획해라.\n분야: {', '.join(channel.topics)}\n"
               f"오늘 날짜: {datetime.now():%Y-%m-%d}")
    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=channel.persona + "\n" + channel.pick_rules + performance.learning_hint(channel),
        messages=[{"role": "user", "content": (
            f"{ask}\n\n이미 다룬 주제(겹치지 말 것):\n{past}\n\n"
            "title 은 저장하고 싶어지는 구체적인 제목. outline 은 기획 기준에 맞춰 단계·설정·예시·주의점을 10~15줄로 자세히.")}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return [], [f"주제 기획 실패({response.stop_reason})"]
    ideas = json.loads("".join(b.text for b in response.content if b.type == "text"))["ideas"][:k]
    ids, warnings = [], []
    for it in ideas:
        source = {"title": it["title"], "body": f"[분야] {it['category']}\n{it['outline']}", "comments": [],
                  "category": it["category"]}
        post_id = db.insert_post(channel.name, ingest.source_key(source), source)
        if post_id:
            ids.append(post_id)
        else:
            warnings.append(f"이미 있는 주제라 건너뜀: {it['title'][:30]}")
    return ids, warnings
