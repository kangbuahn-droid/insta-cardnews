"""RSS 자동 수집: 피드에서 최근 기사를 모으고, AI가 카드뉴스감을 골라 DB 에 넣는다.

채널 yaml 의 sources 에 RSS 주소를 넣으면 동작한다.
수집 시간(collect_at)마다 실행되며, 텔레그램 /collect 로 바로 실행할 수도 있다.
"""
from __future__ import annotations

import calendar
import json
import time
from datetime import datetime, timedelta

import feedparser
import httpx
import trafilatura

from . import db, ingest
from .config import Channel
from .writer import MODEL, _client

UA = "Mozilla/5.0 (compatible; insta-cardnews/1.0; +rss reader)"


def fetch_candidates(channel: Channel, max_age_hours: int = 30) -> tuple[list[dict], list[str]]:
    """모든 피드에서 최근 기사 목록. (후보, 실패한 피드) 반환."""
    cutoff = time.time() - max_age_hours * 3600
    items, failed, seen = [], [], set()
    for src in channel.sources:
        try:
            r = httpx.get(src["url"], headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            failed.append(f"{src.get('name', src['url'])}: {str(e).splitlines()[0][:120]}")
            continue
        for e in feed.entries[:40]:
            link = e.get("link")
            if not link or link in seen:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            if ts and calendar.timegm(ts) < cutoff:
                continue
            seen.add(link)
            summary = trafilatura.extract(e.get("summary", "") or "<p></p>") or e.get("summary", "")
            items.append({
                "title": e.get("title", "").strip(),
                "url": link,
                "summary": (summary or "")[:400],
                "source_name": src.get("name", feed.feed.get("title", "")),
            })
    # 이미 처리한 기사 제외
    items = [it for it in items if not db.exists(channel.name, ingest.source_key({"url": it["url"]}))]
    return items, failed


PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["index", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def pick(channel: Channel, items: list[dict], k: int) -> list[dict]:
    """후보 중 카드뉴스로 반응이 좋을 기사 k개를 AI가 고른다. 같은 사건을 다룬 기사는 하나만."""
    if len(items) <= k:
        return items
    listing = "\n".join(f"[{i}] ({it['source_name']}) {it['title']} — {it['summary'][:150]}" for i, it in enumerate(items))
    response = _client().beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=channel.persona + "\n" + channel.pick_rules,
        messages=[{"role": "user", "content": f"아래 기사 중 {k}개를 골라라. 같은 사건은 하나만.\n\n{listing}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        return items[:k]
    picks = json.loads("".join(b.text for b in response.content if b.type == "text"))["picks"]
    chosen = []
    for p in picks:
        if 0 <= p["index"] < len(items) and items[p["index"]] not in chosen:
            chosen.append(items[p["index"]])
    return chosen[:k] or items[:k]


def article_text(url: str) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=25, follow_redirects=True)
        r.raise_for_status()
        return trafilatura.extract(r.text, include_comments=False, include_tables=False)
    except Exception:
        return None


def collect(channel: Channel, k: int | None = None) -> tuple[list[int], list[str]]:
    """후보 수집 → 선별 → 본문 추출 → DB 등록. (새 글 id, 경고 메시지) 반환."""
    k = k or channel.per_collect
    items, failed = fetch_candidates(channel)
    warnings = [f"피드 실패 {f}" for f in failed]
    if not items:
        warnings.append("새 기사가 없어요")
        return [], warnings
    new_ids = []
    for it in pick(channel, items, k):
        body = article_text(it["url"]) or it["summary"]
        if not body or len(body) < 200:
            warnings.append(f"본문을 못 가져와서 건너뜀: {it['title'][:30]}")
            continue
        source = {
            "title": it["title"],
            "body": body[:6000],
            "comments": [],
            "url": it["url"],
            "source_name": it["source_name"],
        }
        post_id = db.insert_post(channel.name, ingest.source_key(source), source)
        if post_id:
            new_ids.append(post_id)
    return new_ids, warnings


def due(channel: Channel, now: datetime) -> bool:
    """collect_at 시간이 됐고 오늘 아직 안 돌았으면 True."""
    for slot in channel.collect_at:
        hh, mm = map(int, slot.split(":"))
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now < t + timedelta(minutes=30):
            key = f"collect:{channel.name}:{t.date()}:{slot}"
            if not db.kv_get(key):
                db.kv_set(key, "1")
                return True
    return False
