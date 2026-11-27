"""슬라이드 JSON → 1080×1350 JPEG 이미지."""
from __future__ import annotations

import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from playwright.sync_api import sync_playwright

from .config import DATA_DIR, TEMPLATES_DIR, Channel

WIDTH, HEIGHT = 1080, 1350


def _highlight(text: str) -> Markup:
    """*강조* 로 감싼 부분을 포인트 색으로 표시."""
    safe = str(escape(text))
    return Markup(re.sub(r"\*(.+?)\*", r"<em>\1</em>", safe))


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html"]))
    env.filters["highlight"] = _highlight
    return env


def render_html(channel: Channel, script: dict) -> list[str]:
    template = _env().get_template(f"{channel.template}/card.html")
    slides = script["slides"]
    pages, content_no = [], 0
    for i, slide in enumerate(slides, start=1):
        if slide["type"] == "content":
            content_no += 1
        pages.append(
            template.render(
                slide=slide,
                index=i,
                total=len(slides),
                content_no=content_no,
                channel_name=channel.display_name,
                handle=channel.handle,
                cover_label=channel.cover_label,
                mood=script.get("mood", "calm"),
                keyword=script.get("keyword", ""),
                emoji=script.get("emoji", ""),
            )
        )
    return pages


def render(channel: Channel, script: dict, post_id: int | str) -> list[Path]:
    out_dir = DATA_DIR / "images" / f"{channel.name}-{post_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with sync_playwright() as p:
        # CHROMIUM_PATH 가 있으면 그 브라우저 사용(Docker 이미지에서는 비워둠)
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        for i, html in enumerate(render_html(channel, script), start=1):
            page.set_content(html, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            path = out_dir / f"{i:02d}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=92, full_page=False)
            paths.append(path)
        browser.close()
    return paths
