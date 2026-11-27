"""단계를 엮는다: 접수 → 작성 → 렌더 → 검수 → 예약 게시."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import collector, db, ingest, instagram, renderer, storage, telegram, writer
from .config import TZ, Channel, load_channels

CHANNELS: dict[str, Channel] = load_channels()


def _notify(text: str) -> None:
    try:
        telegram.send_text(text)
    except Exception as e:
        print(f"[notify] {e}\n{text}")


# ---------- 작성 + 렌더 + 검수 요청 ----------

def prepare(post_id: int, feedback: str | None = None) -> None:
    row = db.get(post_id)
    channel = CHANNELS[row["channel"]]
    try:
        script = writer.write_script(channel, json.loads(row["source"]), feedback)
        db.update(post_id, script=script, status="written", error=None)
        images = renderer.render(channel, script, post_id)
        db.update(post_id, images=[str(p) for p in images], status="rendered")
        if channel.review:
            msg_id = telegram.send_review(post_id, channel.display_name, images, writer.full_caption(script))
            db.update(post_id, status="pending_review", review_message_id=msg_id)
        else:
            db.update(post_id, status="approved")
        print(f"[prepare] #{post_id} 완료")
    except Exception as e:
        traceback.print_exc()
        db.update(post_id, status="failed", error=str(e))
        _notify(f"#{post_id} 카드 생성 실패: {e}")


def run_collect(channel: Channel, k: int | None = None) -> None:
    try:
        ids, warnings = collector.collect(channel, k)
    except Exception as e:
        traceback.print_exc()
        _notify(f"[{channel.display_name}] 자동 수집 실패: {e}")
        return
    msg = f"[{channel.display_name}] 기사 {len(ids)}개 수집, 카드 만드는 중"
    if warnings:
        msg += "\n" + "\n".join(f"- {w}" for w in warnings[:5])
    _notify(msg)


def collect_due(now: datetime | None = None) -> None:
    now = now or datetime.now(ZoneInfo(TZ))
    for channel in CHANNELS.values():
        if channel.sources and collector.due(channel, now):
            run_collect(channel)


def prepare_new() -> None:
    for name in CHANNELS:
        ingest.scan(name)
    for row in db.by_status("new"):
        prepare(row["id"])


# ---------- 게시 ----------

def publish(post_id: int) -> None:
    row = db.get(post_id)
    channel = CHANNELS[row["channel"]]
    try:
        script = json.loads(row["script"])
        urls = json.loads(row["image_urls"]) if row["image_urls"] else None
        if not urls:
            urls = storage.upload([Path(p) for p in json.loads(row["images"])], f"{channel.name}/{post_id}")
            db.update(post_id, image_urls=urls)
        media_id = instagram.publish_carousel(channel, urls, writer.full_caption(script))
        db.update(post_id, status="published", ig_media_id=media_id, published_at=db.now())
        _notify(f"#{post_id} 게시 완료 ({channel.display_name})")
    except Exception as e:
        traceback.print_exc()
        db.update(post_id, status="failed", error=str(e))
        _notify(f"#{post_id} 게시 실패: {e}")


def publish_due(now: datetime | None = None) -> None:
    """각 채널의 게시 시간이 되면 승인된 글 중 가장 오래된 것을 하나 올린다."""
    now = now or datetime.now(ZoneInfo(TZ))
    for name, channel in CHANNELS.items():
        for slot in channel.schedule:
            hh, mm = map(int, slot.split(":"))
            slot_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # 게시 시간이 지났고 15분 이내이며, 오늘 이 슬롯을 아직 안 썼을 때
            if not (slot_time <= now < slot_time + timedelta(minutes=15)):
                continue
            key = f"slot:{name}:{slot_time.date()}:{slot}"
            if db.kv_get(key):
                continue
            db.kv_set(key, "1")
            queue = db.by_status("approved", name)
            if queue:
                publish(queue[0]["id"])
            else:
                _notify(f"[{channel.display_name}] {slot} 게시 시간인데 승인된 글이 없어요. 글을 보내주세요.")


# ---------- 텔레그램 이벤트 ----------

def handle_updates() -> None:
    for u in telegram.get_updates():
        if "callback_query" in u:
            _handle_button(u["callback_query"])
        elif "message" in u:
            _handle_message(u["message"])


def _handle_button(cb: dict) -> None:
    action, _, raw_id = cb.get("data", "").partition(":")
    if action == "noop" or not raw_id.isdigit():
        telegram.answer_callback(cb["id"], "")
        return
    post_id = int(raw_id)
    row = db.get(post_id)
    msg_id = cb["message"]["message_id"]
    if not row:
        telegram.answer_callback(cb["id"], "없는 글이에요")
        return
    if action == "approve":
        db.update(post_id, status="approved")
        telegram.answer_callback(cb["id"], "승인했어요. 다음 게시 시간에 올라갑니다.")
        telegram.clear_buttons(msg_id, "✅ 승인됨(예약)")
    elif action == "now":
        telegram.answer_callback(cb["id"], "게시를 시작합니다")
        telegram.clear_buttons(msg_id, "🚀 게시 중")
        db.update(post_id, status="approved")
        publish(post_id)
    elif action == "discard":
        db.update(post_id, status="discarded")
        telegram.answer_callback(cb["id"], "폐기했어요")
        telegram.clear_buttons(msg_id, "🗑 폐기됨")
    elif action == "rewrite":
        telegram.answer_callback(cb["id"], "수정 요청을 답장으로 보내주세요")
        ask = telegram.send_text(
            f"#{post_id} 어떻게 고칠까요? 이 메시지에 답장으로 보내주세요.\n(그냥 다시 쓰려면 '다시'라고 답장)",
            reply_markup={"force_reply": True},
        )
        db.kv_set(f"feedback_msg:{ask['message_id']}", str(post_id))
        telegram.clear_buttons(msg_id, "✏️ 재작성 요청됨")


def _handle_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    reply_to = msg.get("reply_to_message", {}).get("message_id")
    if reply_to and (pid := db.kv_get(f"feedback_msg:{reply_to}")):
        feedback = None if text in ("다시", "ㄱ") else text
        telegram.send_text(f"#{pid} 다시 만드는 중…")
        prepare(int(pid), feedback)
        return
    if text.startswith("/start") or text.startswith("/help"):
        telegram.send_text(
            "블라인드 글을 그대로 붙여넣어 보내주세요.\n첫 줄 = 제목, 본문, 그리고 '---' 아래에 댓글을 한 줄씩.\n"
            f"채널 지정: 첫 줄에 #채널이름 (가능: {', '.join(CHANNELS)})\n/collect 뉴스 바로 수집\n/status 대기열 확인"
        )
        return
    if text.startswith("/collect"):
        parts = text.split()
        names = [n for n in CHANNELS if CHANNELS[n].sources]
        name = parts[1] if len(parts) > 1 and parts[1] in names else (names[0] if names else None)
        if not name:
            telegram.send_text("자동 수집 소스가 있는 채널이 없어요.")
            return
        telegram.send_text(f"[{CHANNELS[name].display_name}] 기사 수집 시작")
        run_collect(CHANNELS[name])
        prepare_new()
        return
    if text.startswith("/status"):
        lines = []
        for name in CHANNELS:
            counts = {s: len(db.by_status(s, name)) for s in ("pending_review", "approved", "failed")}
            lines.append(f"{name}: 검수대기 {counts['pending_review']} · 예약 {counts['approved']} · 실패 {counts['failed']}")
        telegram.send_text("\n".join(lines))
        return
    if len(text) < 80:
        telegram.send_text("글이 너무 짧아요. 제목과 본문을 함께 보내주세요. (/help)")
        return
    channel = next((n for n, c in CHANNELS.items() if not c.sources), next(iter(CHANNELS)))
    first, _, rest = text.partition("\n")
    if first.startswith("#") and first[1:].strip() in CHANNELS:
        channel, text = first[1:].strip(), rest
    head, _, comments = text.partition("\n---")
    lines = head.strip().splitlines()
    source = {
        "title": lines[0].strip(),
        "body": "\n".join(lines[1:]).strip() or lines[0],
        "comments": [c.strip() for c in comments.strip("-\n ").splitlines() if c.strip()],
    }
    post_id = db.insert_post(channel, ingest.source_key(source), source)
    if not post_id:
        telegram.send_text("이미 받은 글이에요.")
        return
    telegram.send_text(f"#{post_id} 접수했어요. 카드 만드는 중…")
    prepare(post_id)
