"""카드 이미지 + 분위기에 맞는 배경음악 → 릴스용 세로 영상(1080×1920 MP4).

음악은 music/<분위기>/ 폴더의 mp3·m4a·wav 중 하나를 무작위로 고른다.
분위기: upbeat(밝음) · calm(차분) · serious(진지) · tense(긴장)
해당 분위기 폴더가 비어 있으면 music/ 아래 아무 곡이나, 그것도 없으면 무음으로 만든다.
저작권 문제가 없는 곡(YouTube 오디오 보관함, Pixabay Music 등)만 넣을 것.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from .config import ROOT, Channel

MUSIC_DIR = ROOT / "music"
MOODS = ["upbeat", "calm", "serious", "tense"]
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg"}

W, H, FPS = 1080, 1920, 30
COVER_SEC, SLIDE_SEC, FADE = 3.5, 3.0, 0.4


def pick_music(mood: str) -> Path | None:
    def tracks(folder: Path) -> list[Path]:
        return [p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXT] if folder.exists() else []

    return random.choice(tracks(MUSIC_DIR / mood) or tracks(MUSIC_DIR) or [None])


def make_video(channel: Channel, images: list[Path], mood: str, out: Path, bg: str | None = None) -> tuple[Path, Path | None]:
    """(영상 경로, 사용한 음악 경로) 반환."""
    bg = bg or channel.extra.get("reels_bg", "0x0E1426")
    durations = [COVER_SEC] + [SLIDE_SEC] * (len(images) - 1)
    total = sum(durations) - FADE * (len(images) - 1)
    music = pick_music(mood)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for img, d in zip(images, durations):
        cmd += ["-loop", "1", "-t", f"{d}", "-framerate", str(FPS), "-i", str(img)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    # 4:5 카드를 9:16 화면 가운데에 놓기
    parts = [
        f"[{i}:v]scale={W}:-2,pad={W}:{H}:0:(oh-ih)/2:color={bg},setsar=1,format=yuv420p[v{i}]"
        for i in range(len(images))
    ]
    last, offset = "v0", 0.0
    for i in range(1, len(images)):
        offset += durations[i - 1] - FADE
        parts.append(f"[{last}][v{i}]xfade=transition=slideleft:duration={FADE}:offset={offset:.2f}[x{i}]")
        last = f"x{i}"
    if music:
        a = len(images)
        parts.append(f"[{a}:a]atrim=0:{total:.2f},afade=t=in:d=1,afade=t=out:st={total - 1.5:.2f}:d=1.5,volume=0.8[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]"]
    if music:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-r", str(FPS),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)]
    subprocess.run(cmd, check=True)
    return out, music
