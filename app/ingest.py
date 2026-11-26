"""inbox/<채널>/ 폴더에 넣은 원본 글을 읽어 DB 에 넣는다.

지원 형식
- .json : {"title": "...", "body": "...", "comments": ["...", ...], "likes": 123, "url": "..."}
- .txt  : 첫 줄 = 제목, 그 다음 = 본문, '---' 한 줄 아래 = 댓글(한 줄에 하나)
나중에 자동 수집기를 붙일 때도 이 형식으로 inbox 에 떨어뜨리거나 insert_post 를 직접 호출하면 된다.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import db
from .config import INBOX_DIR


def parse_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        head, _, comments = text.partition("\n---")
        lines = head.splitlines()
        data = {
            "title": lines[0].strip() if lines else "",
            "body": "\n".join(lines[1:]).strip(),
            "comments": [c.strip() for c in comments.strip("-\n ").splitlines() if c.strip()],
        }
    data.setdefault("comments", [])
    if not data.get("body"):
        raise ValueError(f"{path.name}: 본문이 비어 있습니다")
    return data


def source_key(data: dict) -> str:
    basis = data.get("url") or (data.get("title", "") + data["body"][:200])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def scan(channel: str) -> list[int]:
    """채널 inbox 를 훑어 새 글을 DB 에 넣고, 처리한 파일은 done/ 으로 옮긴다."""
    folder = INBOX_DIR / channel
    done = folder / "done"
    folder.mkdir(parents=True, exist_ok=True)
    new_ids: list[int] = []
    for path in sorted(folder.iterdir()):
        if path.suffix not in (".json", ".txt") or not path.is_file():
            continue
        try:
            data = parse_file(path)
        except Exception as e:  # 잘못된 파일은 건너뛰고 표시만
            print(f"[ingest] {path.name} 읽기 실패: {e}")
            path.rename(path.with_suffix(path.suffix + ".error"))
            continue
        post_id = db.insert_post(channel, source_key(data), data)
        if post_id:
            new_ids.append(post_id)
            print(f"[ingest] #{post_id} {data.get('title', '')[:30]}")
        done.mkdir(exist_ok=True)
        shutil.move(str(path), done / path.name)
    return new_ids
