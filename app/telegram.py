"""텔레그램 봇: 미리보기 전송, 승인 버튼 처리, 글 직접 접수.

봇 사용법
- 버튼: [승인] 다음 게시 시간에 올림 / [지금 게시] / [재작성] / [폐기]
- [재작성]을 누르면 봇 메시지에 '답장'으로 수정 요청을 보내면 그 내용을 반영해 다시 만든다.
- 블라인드 글을 그대로 붙여넣어 보내면 새 글로 접수된다(첫 줄 = 제목, '---' 아래 = 댓글).
  다른 채널에 넣으려면 첫 줄에 '#채널이름' 을 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from . import db
from .config import env

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    return env("TELEGRAM_BOT_TOKEN", required=True)  # type: ignore[return-value]


def chat_id() -> str:
    return env("TELEGRAM_CHAT_ID", required=True)  # type: ignore[return-value]


def call(method: str, files: dict | None = None, http_timeout: float = 60, **params) -> dict:
    url = API.format(token=_token(), method=method)
    with httpx.Client(timeout=http_timeout) as client:
        if files:
            r = client.post(url, data=params, files=files)
        else:
            r = client.post(url, json=params)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method} 실패: {data}")
    return data["result"]


def send_text(text: str, **kwargs) -> dict:
    return call("sendMessage", chat_id=chat_id(), text=text[:4000], **kwargs)


def send_review(post_id: int, channel_name: str, images: list[Path], caption: str) -> int:
    media, files = [], {}
    for i, path in enumerate(images[:10]):
        key = f"img{i}"
        files[key] = (path.name, path.read_bytes(), "image/jpeg")
        media.append({"type": "photo", "media": f"attach://{key}"})
    call("sendMediaGroup", files=files, http_timeout=120, chat_id=chat_id(), media=json.dumps(media))

    keyboard = {"inline_keyboard": [
        [{"text": "✅ 승인(예약)", "callback_data": f"approve:{post_id}"},
         {"text": "🚀 지금 게시", "callback_data": f"now:{post_id}"}],
        [{"text": "✏️ 재작성", "callback_data": f"rewrite:{post_id}"},
         {"text": "🗑 폐기", "callback_data": f"discard:{post_id}"}],
    ]}
    msg = send_text(f"[{channel_name}] #{post_id} 검수 요청\n\n{caption}", reply_markup=keyboard)
    return msg["message_id"]


def get_updates() -> list[dict]:
    offset = int(db.kv_get("tg_offset") or 0)
    # timeout = 텔레그램 롱폴링 대기 시간(초)
    updates = call("getUpdates", http_timeout=40, offset=offset, timeout=25,
                   allowed_updates=["message", "callback_query"])
    if updates:
        db.kv_set("tg_offset", str(updates[-1]["update_id"] + 1))
    allowed = str(chat_id())
    result = []
    for u in updates:
        chat = (u.get("message") or u.get("callback_query", {}).get("message") or {}).get("chat", {})
        if str(chat.get("id")) == allowed:  # 내 채팅방에서 온 것만 처리
            result.append(u)
    return result


def answer_callback(callback_id: str, text: str) -> None:
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except Exception:
        pass


def clear_buttons(message_id: int, note: str) -> None:
    try:
        call("editMessageReplyMarkup", chat_id=chat_id(), message_id=message_id,
             reply_markup={"inline_keyboard": [[{"text": note, "callback_data": "noop"}]]})
    except Exception:
        pass
