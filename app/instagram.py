"""Instagram Graph API 로 캐러셀을 게시한다.

기본은 'Instagram 로그인 방식'(graph.instagram.com). 페이스북 페이지 연동 방식을 쓰면
.env 에 IG_GRAPH_HOST=graph.facebook.com 으로 바꾸면 된다.
순서: 이미지별 자식 컨테이너 생성 → 처리 완료 대기 → 캐러셀 컨테이너 → publish
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import httpx

from . import db
from .config import Channel, env

HOST = env("IG_GRAPH_HOST", "graph.instagram.com")
VERSION = env("IG_API_VERSION", "v23.0")
BASE = f"https://{HOST}/{VERSION}"


class InstagramError(RuntimeError):
    pass


def _token(channel: Channel) -> str:
    # 자동 갱신된 토큰이 있으면 그것을 우선 사용
    return db.kv_get(f"token:{channel.ig_token_env}") or channel.ig_token


def _call(method: str, path: str, token: str, **params) -> dict:
    params["access_token"] = token
    with httpx.Client(timeout=60) as client:
        r = client.request(method, f"{BASE}/{path}", params=params if method == "GET" else None,
                           data=params if method == "POST" else None)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise InstagramError(f"{err.get('code')} {err.get('message', r.text)}")
    return data


def _wait_ready(container_id: str, token: str, timeout: int = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _call("GET", container_id, token, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"컨테이너 {container_id} 처리 실패: {status}")
        time.sleep(3)
    raise InstagramError(f"컨테이너 {container_id} 처리 시간 초과")


def publish_carousel(channel: Channel, image_urls: list[str], caption: str) -> str:
    if not 2 <= len(image_urls) <= 10:
        raise InstagramError(f"캐러셀은 2~10장이어야 합니다(현재 {len(image_urls)}장)")
    token, user_id = _token(channel), channel.ig_user_id

    children = []
    for url in image_urls:
        child = _call("POST", f"{user_id}/media", token, image_url=url, is_carousel_item="true")
        children.append(child["id"])
    for cid in children:
        _wait_ready(cid, token)

    carousel = _call("POST", f"{user_id}/media", token,
                     media_type="CAROUSEL", children=",".join(children), caption=caption)
    _wait_ready(carousel["id"], token)
    published = _call("POST", f"{user_id}/media_publish", token, creation_id=carousel["id"])
    return published["id"]


def publishing_quota(channel: Channel) -> dict:
    """최근 24시간 게시 사용량."""
    return _call("GET", f"{channel.ig_user_id}/content_publishing_limit", _token(channel),
                 fields="quota_usage,config")


def refresh_token_if_needed(channel: Channel) -> None:
    """장기 토큰(60일)을 50일마다 갱신. Instagram 로그인 방식에서만 동작."""
    if HOST != "graph.instagram.com":
        return
    key = f"token_refreshed_at:{channel.ig_token_env}"
    last = db.kv_get(key)
    if last and datetime.fromisoformat(last) > datetime.now() - timedelta(days=50):
        return
    with httpx.Client(timeout=30) as client:
        r = client.get("https://graph.instagram.com/refresh_access_token",
                       params={"grant_type": "ig_refresh_token", "access_token": _token(channel)})
    data = r.json()
    if "access_token" in data:
        db.kv_set(f"token:{channel.ig_token_env}", data["access_token"])
        db.kv_set(key, datetime.now().isoformat(timespec="seconds"))
        print(f"[instagram] {channel.name} 토큰 갱신 완료")
    else:
        print(f"[instagram] 토큰 갱신 실패: {data}")
