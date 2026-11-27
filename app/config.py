"""환경변수와 채널 설정(YAML)을 불러온다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
INBOX_DIR = Path(os.getenv("INBOX_DIR", ROOT / "inbox"))
TEMPLATES_DIR = ROOT / "templates"
CHANNELS_DIR = ROOT / "channels"
TZ = os.getenv("TZ_NAME", "Asia/Seoul")


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"환경변수 {name} 가 비어 있습니다. .env 를 확인하세요.")
    return value


@dataclass
class Channel:
    name: str
    display_name: str
    template: str
    persona: str
    schedule: list[str]  # "HH:MM" (TZ 기준)
    review: bool
    hashtags: list[str]
    cta: str
    handle: str
    # 이 채널이 쓰는 인스타 계정의 환경변수 이름
    ig_user_id_env: str
    ig_token_env: str
    min_slides: int = 5
    max_slides: int = 8
    write_rules: str = ""          # 채널별 작성 규칙(비면 기본 규칙)
    pick_rules: str = ""           # 자동 수집 시 기사 선별 기준
    cover_label: str = ""          # 표지 왼쪽 아래 문구
    sources: list = field(default_factory=list)      # RSS 등 자동 수집 소스
    collect_at: list = field(default_factory=list)   # 자동 수집 시간 "HH:MM"
    per_collect: int = 3           # 한 번 수집할 때 만들 카드뉴스 수
    extra: dict = field(default_factory=dict)

    @property
    def ig_user_id(self) -> str:
        return env(self.ig_user_id_env, required=True)  # type: ignore[return-value]

    @property
    def ig_token(self) -> str:
        return env(self.ig_token_env, required=True)  # type: ignore[return-value]


def load_channels() -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    for path in sorted(CHANNELS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw.get("enabled", True):
            continue
        name = raw.get("name", path.stem)
        ig = raw.get("instagram", {})
        channels[name] = Channel(
            name=name,
            display_name=raw.get("display_name", name),
            template=raw.get("template", "default"),
            persona=raw["persona"].strip(),
            schedule=[str(s) for s in raw.get("schedule", ["08:00", "19:00"])],
            review=bool(raw.get("review", True)),
            hashtags=raw.get("hashtags", []),
            cta=raw.get("cta", ""),
            handle=raw.get("handle", ""),
            ig_user_id_env=ig.get("user_id_env", "IG_USER_ID"),
            ig_token_env=ig.get("token_env", "IG_ACCESS_TOKEN"),
            min_slides=int(raw.get("min_slides", 5)),
            max_slides=int(raw.get("max_slides", 8)),
            write_rules=(raw.get("write_rules") or "").strip(),
            pick_rules=(raw.get("pick_rules") or "").strip(),
            cover_label=raw.get("cover_label", ""),
            sources=raw.get("sources", []),
            collect_at=[str(t) for t in raw.get("collect_at", [])],
            per_collect=int(raw.get("per_collect", 3)),
            extra=raw.get("extra", {}),
        )
    return channels
