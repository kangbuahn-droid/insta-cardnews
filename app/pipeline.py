"""단계를 엮는다: 접수 → 작성 → 렌더 → 검수 → 예약 게시."""
from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import collector, db, images as imagegen, ingest, instagram, performance, planner, reels, renderer, storage, telegram, writer
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
        art_warnings = imagegen.for_script(channel, script, renderer.out_dir(channel, post_id) / "art")
        if art_warnings:
            _notify(f"#{post_id} 그림 일부를 못 만들어서 디자인만으로 채웠어요:\n" + "\n".join(art_warnings[:3]))
        db.update(post_id, script=script)
        images = renderer.render(channel, script, post_id)
        db.update(post_id, images=[str(p) for p in images], status="rendered")
        video, video_note = None, ""
        if "reels" in channel.formats:
            try:
                mood = script.get("mood", "calm")
                video, music = reels.make_video(channel, images, mood, images[0].parent / "reels.mp4")
                db.update(post_id, video=str(video), video_url=None)
                video_note = f"(분위기: {mood}, 음악: {music.name if music else '없음 - music 폴더에 곡을 넣어주세요'})"
            except Exception as e:
                traceback.print_exc()
                _notify(f"#{post_id} 릴스 영상 생성 실패(캐러셀만 진행): {e}")
        if channel.review:
            msg_id = telegram.send_review(post_id, channel.display_name, images, writer.full_caption(script),
                                          video, video_note)
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
        if channel.sources:
            ids, warnings = collector.collect(channel, k)
        else:
            ids, warnings = planner.plan(channel, k)
    except Exception as e:
        traceback.print_exc()
        _notify(f"[{channel.display_name}] 자동 수집 실패: {e}")
        return
    what = "기사" if channel.sources else "주제"
    msg = f"[{channel.display_name}] {what} {len(ids)}개 준비, 카드 만드는 중"
    if warnings:
        msg += "\n" + "\n".join(f"- {w}" for w in warnings[:5])
    _notify(msg)


def collect_due(now: datetime | None = None) -> None:
    now = now or datetime.now(ZoneInfo(TZ))
    for channel in CHANNELS.values():
        if (channel.sources or channel.topics) and collector.due(channel, now):
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
        caption = writer.full_caption(script)
        done = []
        if "carousel" in channel.formats and not row["ig_media_id"]:
            media_id = instagram.publish_carousel(channel, urls, caption)
            db.update(post_id, ig_media_id=media_id)
            done.append("캐러셀")
        if "reels" in channel.formats and row["video"] and not row["reels_media_id"]:
            video_url = row["video_url"] or storage.upload([Path(row["video"])], f"{channel.name}/{post_id}")[0]
            db.update(post_id, video_url=video_url)
            reels_id = instagram.publish_reels(channel, video_url, caption)
            db.update(post_id, reels_media_id=reels_id)
            done.append("릴스")
        db.update(post_id, status="published", published_at=db.now())
        _notify(f"#{post_id} 게시 완료 ({channel.display_name}: {', '.join(done) or '이미 게시됨'})")
    except Exception as e:
        traceback.print_exc()
        db.update(post_id, status="failed", error=str(e))
        _notify(f"#{post_id} 게시 실패: {e}\n다시 시도하려면 /retry {post_id}")


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


def performance_due() -> None:
    performance.weekly_due(CHANNELS, _notify)


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
        db.update(post_id, status="discarded", source="{}", script=None)  # 원문은 지움
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


SECRET_PATTERNS = [
    re.compile(r"^\s*[A-Z][A-Z0-9_]{2,}\s*=", re.M),          # .env 형식 줄
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),            # 텔레그램 봇 토큰
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),                  # Claude 키
    re.compile(r"\bIG[A-Za-z0-9]{100,}"),                     # 인스타 토큰
]


def _looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


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
            f"채널 지정: 첫 줄에 #채널이름 (가능: {', '.join(CHANNELS)})\n/collect 새 콘텐츠 바로 만들기\n/tip 주제 원하는 주제로 하나 만들기\n/status 대기열 확인\n/report 최근 7일 성과(/report 30 처럼 기간 지정)"
        )
        return
    if text.startswith("/collect"):
        parts = text.split()
        names = [n for n in CHANNELS if CHANNELS[n].sources or CHANNELS[n].topics]
        name = parts[1] if len(parts) > 1 and parts[1] in names else (names[0] if names else None)
        if not name:
            telegram.send_text("자동 수집 소스가 있는 채널이 없어요.")
            return
        telegram.send_text(f"[{CHANNELS[name].display_name}] 새 콘텐츠 준비 시작")
        run_collect(CHANNELS[name])
        prepare_new()
        return
    if text.startswith("/tip"):
        idea = text[4:].strip()
        name = next((n for n, c in CHANNELS.items() if c.topics), None)
        if not name or len(idea) < 4:
            telegram.send_text("사용법: /tip 만들고 싶은 주제 (예: /tip 미드저니로 제품 사진 찍는 프롬프트)")
            return
        ids, _ = planner.plan(CHANNELS[name], 1, idea=idea)
        if ids:
            telegram.send_text(f"#{ids[0]} 주제 접수했어요. 카드 만드는 중…")
            prepare(ids[0])
        else:
            telegram.send_text("이미 같은 제목의 글이 있어서 만들지 않았어요. 조금 다르게 적어 주세요.")
        return
    if text.startswith("/retry"):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit() and db.get(int(parts[1])):
            telegram.send_text(f"#{parts[1]} 다시 게시합니다")
            publish(int(parts[1]))
        else:
            telegram.send_text("사용법: /retry 글번호")
        return
    if text.startswith("/report"):
        parts = text.split()
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 7
        errors = performance.collect(CHANNELS, force=True)
        for ch in CHANNELS.values():
            telegram.send_text(performance.report(ch, days))
        if errors:
            telegram.send_text("일부 성과를 못 가져왔어요:\n" + "\n".join(errors[:3]))
        return
    if text.startswith("/status"):
        lines = []
        for name in CHANNELS:
            counts = {s: len(db.by_status(s, name)) for s in ("pending_review", "approved", "failed")}
            lines.append(f"{name}: 검수대기 {counts['pending_review']} · 예약 {counts['approved']} · 실패 {counts['failed']}")
        telegram.send_text("\n".join(lines))
        return
    if _looks_like_secret(text):
        telegram.send_text(
            "설정값(토큰·키)처럼 보이는 메시지라 기사로 처리하지 않았어요.\n"
            "이 대화에 키를 보내면 안 돼요. 방금 메시지는 길게 눌러 삭제하고, 노출된 키는 재발급하는 걸 권해요."
        )
        return
    if len(text) < 80:
        telegram.send_text("글이 너무 짧아요. 제목과 본문을 함께 보내주세요. (/help)")
        return
    channel = next((n for n, c in CHANNELS.items() if not c.sources and not c.topics), next(iter(CHANNELS)))
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
