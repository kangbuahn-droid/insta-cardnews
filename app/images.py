"""슬라이드에 넣을 그림을 AI 이미지 생성으로 만든다.

IMAGE_PROVIDER = openai | gemini | none (비우면 키가 있는 쪽을 자동 선택)
- openai: OPENAI_API_KEY, 모델 OPENAI_IMAGE_MODEL (비우면 키로 쓸 수 있는 모델을 자동 선택)
- gemini: GEMINI_API_KEY, 모델 GEMINI_IMAGE_MODEL (기본 gemini-2.5-flash-image)
키가 없거나 실패하면 그림 없이 디자인만으로 카드를 만든다.
"""
from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .config import Channel, env

NO_TEXT = "No text, no letters, no words, no logos, no watermark."


def provider() -> str:
    p = (env("IMAGE_PROVIDER") or "").strip().lower()
    if p:
        return p
    if env("OPENAI_API_KEY"):
        return "openai"
    if env("GEMINI_API_KEY"):
        return "gemini"
    return "none"


# 선호 순서. 키가 쓸 수 있는 모델을 /v1/models 로 확인해 첫 번째 것을 쓴다.
OPENAI_PREFERRED = ["gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini", "dall-e-3"]
_openai_model: str | None = None


def _openai_models() -> list[str]:
    """쓸 모델 후보 목록. .env 의 OPENAI_IMAGE_MODEL 이 있으면 그것부터."""
    global _openai_model
    if _openai_model:
        return [_openai_model]
    first = (env("OPENAI_IMAGE_MODEL") or "").strip()
    available: list[str] = []
    try:
        r = httpx.get("https://api.openai.com/v1/models",
                      headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"}, timeout=30)
        ids = {m["id"] for m in r.json().get("data", [])}
        available = [m for m in OPENAI_PREFERRED if m in ids]
        available += sorted(i for i in ids if ("image" in i or "dall-e" in i) and i not in available)
    except Exception:
        pass
    order = ([first] if first else []) + [m for m in (available or OPENAI_PREFERRED) if m != first]
    return order


def _openai_once(model: str, prompt: str, portrait: bool) -> bytes:
    body = {"model": model, "prompt": prompt, "n": 1}
    if model.startswith("dall-e"):
        body.update(size="1024x1792" if portrait else "1024x1024", response_format="b64_json", quality="standard")
    else:
        body.update(size="1024x1536" if portrait else "1024x1024", quality=env("OPENAI_IMAGE_QUALITY", "medium"))
    r = httpx.post("https://api.openai.com/v1/images/generations",
                   headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"}, json=body, timeout=180)
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(data.get("error", {}).get("message", r.text)[:200])
    return base64.b64decode(data["data"][0]["b64_json"])


def _openai(prompt: str, portrait: bool) -> bytes:
    """모델이 없거나 권한이 없으면 다음 모델로 넘어간다. 성공한 모델은 기억한다."""
    global _openai_model
    errors = []
    for model in _openai_models():
        try:
            raw = _openai_once(model, prompt, portrait)
            if _openai_model != model:
                print(f"[images] OpenAI 이미지 모델: {model}", flush=True)
            _openai_model = model
            return raw
        except Exception as e:
            msg = str(e)
            errors.append(f"{model}: {msg[:120]}")
            low = msg.lower()
            # 모델 문제(없음/권한/인증 필요)일 때만 다음 모델 시도. 내용 거절 등은 바로 실패.
            if not any(w in low for w in ("does not exist", "not found", "verif", "access", "permission", "invalid model", "not supported")):
                break
    hint = ""
    if any("verif" in e.lower() for e in errors):
        hint = " (OpenAI 조직 인증 필요: platform.openai.com > Settings > Organization > Verify)"
    raise RuntimeError("OpenAI 이미지 오류: " + " / ".join(errors[:3]) + hint)


def _gemini(prompt: str, portrait: bool) -> bytes:
    model = env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": env("GEMINI_API_KEY") or ""},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseModalities": ["IMAGE"],
                                   "imageConfig": {"aspectRatio": "4:5" if portrait else "1:1"}}},
        timeout=180,
    )
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini 이미지 오류: {data.get('error', {}).get('message', r.text)[:200]}")
    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("Gemini 가 이미지를 돌려주지 않았어요")


def generate(prompt: str, out: Path, portrait: bool = False) -> Path:
    p = provider()
    if p == "openai":
        raw = _openai(prompt, portrait)
    elif p == "gemini":
        raw = _gemini(prompt, portrait)
    else:
        raise RuntimeError("이미지 생성 키가 없어요")
    out.write_bytes(raw)
    return out


def for_script(channel: Channel, script: dict, out_dir: Path) -> list[str]:
    """슬라이드마다 필요한 그림을 만들어 slide['image_file'] 에 경로를 넣는다. 경고 목록 반환."""
    if provider() == "none":
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = int(env("IMAGE_MAX_PER_POST", "4"))
    warnings, made = [], 0
    for i, slide in enumerate(script["slides"], start=1):
        slide.pop("image_file", None)
        mode, desc = slide.get("image_mode", "none"), (slide.get("image") or "").strip()
        if mode == "none" or not desc or made >= limit:
            continue
        if mode == "example":
            prompt = f"{desc}\n{NO_TEXT}"  # 슬라이드에 적힌 프롬프트의 실제 결과물
        else:
            prompt = f"{desc}. {channel.image_style} {NO_TEXT}"
        try:
            path = generate(prompt, out_dir / f"img{i:02d}.png", portrait=slide["type"] == "cover")
            slide["image_file"] = str(path)
            made += 1
        except Exception as e:
            warnings.append(f"{i}번 장 그림 실패: {e}")
    return warnings
