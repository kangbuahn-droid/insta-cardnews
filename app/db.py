"""SQLite 로 게시물 상태를 관리한다.

상태 흐름: new → written → rendered → pending_review → approved → published
            (실패 시 failed, 폐기 시 discarded)
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from .config import DATA_DIR

DB_PATH = DATA_DIR / "posts.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source TEXT NOT NULL,          -- 원본 입력(JSON)
    script TEXT,                   -- LLM 이 만든 슬라이드 JSON
    images TEXT,                   -- 렌더된 이미지 경로 목록(JSON)
    image_urls TEXT,               -- 업로드된 공개 URL 목록(JSON)
    status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    ig_media_id TEXT,
    review_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(channel, source_key)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def insert_post(channel: str, source_key: str, source: dict) -> int | None:
    with connect() as c:
        try:
            cur = c.execute(
                "INSERT INTO posts(channel, source_key, source, created_at, updated_at) VALUES (?,?,?,?,?)",
                (channel, source_key, json.dumps(source, ensure_ascii=False), now(), now()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # 이미 들어온 글


def update(post_id: int, **fields: Any) -> None:
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v, ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE posts SET {cols} WHERE id=?", (*fields.values(), post_id))


def get(post_id: int) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def by_status(status: str, channel: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM posts WHERE status=?"
    args: list[Any] = [status]
    if channel:
        q += " AND channel=?"
        args.append(channel)
    q += " ORDER BY id"
    with connect() as c:
        return list(c.execute(q, args).fetchall())


def kv_get(k: str) -> str | None:
    with connect() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None


def kv_set(k: str, v: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
