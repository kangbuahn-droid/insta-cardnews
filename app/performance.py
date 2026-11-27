"""게시물 성과 수집 → 주간 리포트 → 다음 기사 선별에 반영.

- 게시 후 7일 동안 6시간마다 인스타 Insights(도달·저장·공유·팔로우 등)를 모은다.
- 매주 월요일 09:00(채널 시간대)에 텔레그램으로 주간 리포트를 보낸다. /report 로 바로 볼 수도 있다.
- 최근 30일 성과 상·하위 주제를 collector.pick 에 알려줘서 반응 좋은 주제를 더 고르게 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, instagram
from .config import TZ, Channel

REFRESH_HOURS = 6
TRACK_DAYS = 7


def score(ins: dict) -> float:
    """팔로워를 늘리는 행동일수록 가중치를 높게 준다(팔로우 > 공유 > 저장 > 댓글 > 좋아요)."""
    raw = (ins.get("follows", 0) * 10 + ins.get("shares", 0) * 5 + ins.get("saved", 0) * 4
           + ins.get("comments", 0) * 2 + ins.get("likes", 0))
    reach = ins.get("reach", 0) + 300  # 도달이 아주 적은 글이 우연히 1등 하지 않도록 보정
    return round(raw / reach * 1000, 1)  # 도달 1000명당 점수


def _merge(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def _title(row) -> str:
    try:
        cover = json.loads(row["script"])["slides"][0]["title"]
        return cover.replace("*", "")
    except Exception:
        return f"#{row['id']}"


def collect(channels: dict[str, Channel], force: bool = False) -> list[str]:
    """최근 게시물의 성과를 갱신한다. 실패 메시지 목록 반환."""
    since = (datetime.now() - timedelta(days=TRACK_DAYS)).isoformat(timespec="seconds")
    stale = (datetime.now() - timedelta(hours=REFRESH_HOURS)).isoformat(timespec="seconds")
    errors = []
    for row in db.published_since(since):
        channel = channels.get(row["channel"])
        if not channel or (not force and row["insights_at"] and row["insights_at"] > stale):
            continue
        total, parts = {}, {}
        for kind, media_id in (("carousel", row["ig_media_id"]), ("reels", row["reels_media_id"])):
            if not media_id:
                continue
            try:
                ins = instagram.media_insights(channel, media_id)
            except Exception as e:
                errors.append(f"#{row['id']} {kind}: {e}")
                continue
            parts[kind] = ins
            total = _merge(total, ins)
        if parts:
            db.update(row["id"], insights={"total": total, **parts, "score": score(total)}, insights_at=db.now())
    return errors


def snapshot_followers(channel: Channel, today: str) -> None:
    key = f"followers:{channel.name}:{today}"
    if db.kv_get(key) is None and (n := instagram.followers_count(channel)) is not None:
        db.kv_set(key, str(n))


def _rows(channel: Channel, days: int) -> list:
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    return [r for r in db.published_since(since, channel.name) if r["insights"]]


def report(channel: Channel, days: int = 7) -> str:
    rows = _rows(channel, days)
    lines = [f"📊 [{channel.display_name}] 최근 {days}일 성과"]
    today = datetime.now(ZoneInfo(TZ)).date()
    now_f = instagram.followers_count(channel)
    before = db.kv_get(f"followers:{channel.name}:{today - timedelta(days=days)}")
    if now_f is not None:
        diff = f" ({now_f - int(before):+d})" if before else ""
        lines.append(f"팔로워 {now_f:,}명{diff}")
    if not rows:
        lines.append("아직 성과가 모인 게시물이 없어요. 게시 후 몇 시간 뒤부터 집계돼요.")
        return "\n".join(lines)

    total = {}
    for r in rows:
        total = _merge(total, json.loads(r["insights"])["total"])
    lines.append(f"게시 {len(rows)}개 · 도달 {total.get('reach', 0):,} · 저장 {total.get('saved', 0):,} · "
                 f"공유 {total.get('shares', 0):,} · 팔로우 {total.get('follows', 0):,}")

    ranked = sorted(rows, key=lambda r: json.loads(r["insights"])["score"], reverse=True)
    lines.append("\n🏆 반응 좋았던 글")
    for r in ranked[:3]:
        ins = json.loads(r["insights"])
        t = ins["total"]
        lines.append(f"#{r['id']} {_title(r)}\n   점수 {ins['score']} · 도달 {t.get('reach', 0):,} · "
                     f"저장 {t.get('saved', 0)} · 공유 {t.get('shares', 0)}")
    if len(ranked) > 3:
        r = ranked[-1]
        lines.append(f"\n😶 반응 낮았던 글\n#{r['id']} {_title(r)} (점수 {json.loads(r['insights'])['score']})")

    # 형식별 비교 (캐러셀 vs 릴스)
    fmt = {}
    for r in rows:
        ins = json.loads(r["insights"])
        for kind in ("carousel", "reels"):
            if kind in ins:
                fmt.setdefault(kind, []).append(ins[kind].get("reach", 0))
    if len(fmt) == 2:
        avg = {k: sum(v) // len(v) for k, v in fmt.items()}
        lines.append(f"\n평균 도달: 캐러셀 {avg['carousel']:,} · 릴스 {avg['reels']:,}")

    # 게시 시간대별 평균 점수
    by_slot = {}
    for r in rows:
        hour = r["published_at"][11:13]
        by_slot.setdefault(hour, []).append(json.loads(r["insights"])["score"])
    if len(by_slot) > 1:
        best = max(by_slot, key=lambda h: sum(by_slot[h]) / len(by_slot[h]))
        lines.append(f"가장 반응 좋은 게시 시간대: {best}시")
    lines.append("\n다음 기사 선별에 이 결과를 반영해요.")
    return "\n".join(lines)


def learning_hint(channel: Channel, days: int = 30) -> str:
    """기사 선별 AI에게 줄 과거 성과 요약. 데이터가 적으면 빈 문자열."""
    rows = _rows(channel, days)
    if len(rows) < 4:
        return ""
    ranked = sorted(rows, key=lambda r: json.loads(r["insights"])["score"], reverse=True)
    n = max(2, len(ranked) // 4)
    good = "\n".join(f"- {_title(r)}" for r in ranked[:n])
    bad = "\n".join(f"- {_title(r)}" for r in ranked[-n:])
    return ("\n[우리 계정의 지난 성과]\n저장·공유·팔로우가 많았던 주제:\n" + good +
            "\n반응이 낮았던 주제:\n" + bad +
            "\n반응 좋았던 주제와 비슷한 성격의 기사를 우선하되, 같은 주제만 반복하지는 마라.")


def weekly_due(channels: dict[str, Channel], send) -> None:
    """월요일 09:00 이후 첫 실행 때 주간 리포트. 매일 팔로워 수 기록."""
    now = datetime.now(ZoneInfo(TZ))
    for ch in channels.values():
        snapshot_followers(ch, str(now.date()))
    errors = collect(channels)
    if errors and not db.kv_get(f"insights_warned:{now.date()}"):
        db.kv_set(f"insights_warned:{now.date()}", "1")
        hint = ""
        if any("permission" in e.lower() or "(#10)" in e or " 10 " in e for e in errors):
            hint = "\n토큰에 인사이트 권한(instagram_business_manage_insights)이 없을 수 있어요. Meta에서 그 권한을 넣고 토큰을 다시 만들어 주세요."
        send("성과 수집 중 오류가 있었어요:\n" + "\n".join(errors[:3]) + hint)
    week = now.strftime("%G-W%V")
    if now.weekday() == 0 and now.hour >= 9 and not db.kv_get(f"weekly_report:{week}"):
        db.kv_set(f"weekly_report:{week}", "1")
        for ch in channels.values():
            send(report(ch))
