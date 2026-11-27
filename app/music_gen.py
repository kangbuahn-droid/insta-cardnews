"""릴스 배경음악을 프로그램이 직접 작곡·합성한다(저작권 걱정 없는 자체 제작 음원).

music/<분위기>/ 폴더에 곡이 없을 때 쓰인다. 같은 분위기라도 매번 코드 진행·템포·악기 배치가 조금씩 달라진다.
- upbeat : 밝은 팝 (메이저, 플럭 아르페지오 + 킥·스네어·하이햇)
- calm   : 로파이 (메이저7 코드, 부드러운 일렉피아노 + 느린 비트)
- serious: 시네마틱 (마이너, 패드 + 낮은 펄스, 드럼 거의 없음)
- tense  : 긴장감 (마이너, 8분음 베이스 펄스 + 하이햇)
"""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# 코드: (근음, 구성음 반음 간격)
MAJ, MIN, MAJ7, MIN7, DOM7, SUS2 = (0, 4, 7), (0, 3, 7), (0, 4, 7, 11), (0, 3, 7, 10), (0, 4, 7, 10), (0, 2, 7)

STYLES = {
    "upbeat": {"bpm": (108, 122), "keys": ["C", "D", "F", "G"], "progs": [
        [(0, MAJ), (7, MAJ), (9, MIN), (5, MAJ)], [(0, MAJ), (5, MAJ), (9, MIN), (7, MAJ)],
        [(5, MAJ), (7, MAJ), (4, MIN), (9, MIN)]]},
    "calm": {"bpm": (76, 88), "keys": ["F", "D", "E", "A#"], "progs": [
        [(0, MAJ7), (9, MIN7), (5, MAJ7), (7, DOM7)], [(5, MAJ7), (4, MIN7), (2, MIN7), (0, MAJ7)],
        [(0, MAJ7), (2, MIN7), (4, MIN7), (5, MAJ7)]]},
    "serious": {"bpm": (68, 80), "keys": ["A", "D", "E", "C"], "progs": [
        [(0, MIN), (8, MAJ), (3, MAJ), (10, MAJ)], [(0, MIN), (5, MIN), (8, MAJ), (7, MAJ)],
        [(0, MIN), (10, MAJ), (8, MAJ), (10, SUS2)]]},
    "tense": {"bpm": (92, 104), "keys": ["D", "E", "C", "F"], "progs": [
        [(0, MIN), (0, MIN), (8, MAJ), (7, MAJ)], [(0, MIN), (1, MAJ), (0, MIN), (10, MAJ)],
        [(0, MIN), (3, MAJ), (8, MAJ), (7, MAJ)]]},
}


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _env(n: int, attack: float, release: float, decay: float | None = None) -> np.ndarray:
    t = np.arange(n) / SR
    e = np.minimum(1.0, t / max(attack, 1e-4))
    if decay:
        e *= np.exp(-t / decay)
    rel = int(release * SR)
    if 0 < rel < n:
        e[-rel:] *= np.linspace(1, 0, rel)
    return e


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """FFT 로 부드럽게 고음을 깎는다."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec, len(x))


def _tone(freq: float, n: int, harmonics: list[float], detune: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k, amp in enumerate(harmonics, start=1):
        out += amp * np.sin(2 * np.pi * freq * k * t + k)
        if detune:
            out += amp * 0.7 * np.sin(2 * np.pi * freq * k * (1 + detune) * t)
    return out


def _add(buf: np.ndarray, start: int, sig: np.ndarray, gain: float) -> None:
    end = min(len(buf), start + len(sig))
    if start < end:
        buf[start:end] += sig[: end - start] * gain


def _kick(rng) -> np.ndarray:
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    f = 45 + 90 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _snare(rng) -> np.ndarray:
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise = noise - _lowpass(noise, 1500)  # 고음 위주
    return (0.7 * noise + 0.5 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 20)


def _hat(rng, length: float = 0.05) -> np.ndarray:
    n = int(length * SR)
    noise = rng.standard_normal(n)
    noise = noise - _lowpass(noise, 7000)
    return noise * np.exp(-np.arange(n) / SR * 60)


def _reverb(x: np.ndarray, rng, seconds: float = 1.6, mix: float = 0.25) -> np.ndarray:
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * (5 / seconds))
    ir = _lowpass(ir, 5000)
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    wet *= np.max(np.abs(x)) / (np.max(np.abs(wet)) + 1e-9)
    return x * (1 - mix) + wet * mix


def compose(mood: str, seconds: float = 40, seed: int | None = None) -> np.ndarray:
    """스테레오 float 배열(n, 2)을 돌려준다."""
    rng = np.random.default_rng(seed)
    pick = random.Random(seed)
    style = STYLES.get(mood, STYLES["calm"])
    bpm = pick.randint(*style["bpm"])
    root = 48 + NOTE[pick.choice(style["keys"])]  # C3 부근
    prog = pick.choice(style["progs"])
    beat = 60 / bpm
    bar = 4 * beat
    n = int(seconds * SR)
    L, R = np.zeros(n), np.zeros(n)
    bars = int(seconds / bar) + 1

    for b in range(bars):
        start = int(b * bar * SR)
        deg, chord = prog[b % len(prog)]
        notes = [root + deg + iv for iv in chord]
        span = int(bar * SR)
        intro = b < 2  # 처음 두 마디는 드럼 없이 조용히 시작

        # 패드 / 코드
        if mood in ("serious", "tense", "calm"):
            for i, m in enumerate(notes):
                sig = _tone(_freq(m + 12), span + int(0.5 * SR), [1, 0.35, 0.15, 0.06], detune=0.004)
                sig *= _env(len(sig), 0.4 if mood != "calm" else 0.02, 0.6, decay=None if mood != "calm" else 1.6)
                pan = 0.35 + 0.3 * (i / max(1, len(notes) - 1))
                g = 0.10 if mood != "calm" else 0.13
                _add(L, start, sig, g * (1 - pan) * 2)
                _add(R, start, sig, g * pan * 2)
            if mood == "calm":  # 일렉피아노 느낌으로 반 마디마다 다시 치기
                for m in notes:
                    sig = _tone(_freq(m + 12), int(bar / 2 * SR), [1, 0.4, 0.1]) * _env(int(bar / 2 * SR), 0.01, 0.2, decay=0.9)
                    _add(L, start + int(bar / 2 * SR) + int(0.012 * SR), sig, 0.08)
                    _add(R, start + int(bar / 2 * SR), sig, 0.08)
        else:  # upbeat: 부드러운 패드 + 플럭 아르페지오
            for m in notes:
                sig = _tone(_freq(m + 12), span, [1, 0.3, 0.1], detune=0.003) * _env(span, 0.15, 0.3)
                _add(L, start, sig, 0.06)
                _add(R, start, sig, 0.06)
            pattern = [0, 1, 2, 1, 2, 0, 1, 2]
            for s in range(8):
                m = notes[pattern[s] % len(notes)] + 24
                ln = int(beat / 2 * SR)
                sig = _tone(_freq(m), ln * 2, [1, 0.5, 0.25, 0.1]) * _env(ln * 2, 0.003, 0.1, decay=0.18)
                pan = 0.3 if s % 2 else 0.7
                _add(L, start + s * ln, sig, 0.11 * (1 - pan) * 2)
                _add(R, start + s * ln, sig, 0.11 * pan * 2)

        # 베이스
        bass_root = root - 12 + deg
        if mood == "tense":
            hits = [(i * beat / 2, beat / 2 * 0.9) for i in range(8)]
        elif mood == "serious":
            hits = [(0, bar * 0.95)]
        elif mood == "calm":
            hits = [(0, beat * 1.5), (beat * 2.5, beat * 1.4)]
        else:
            hits = [(0, beat * 0.9), (beat * 1.5, beat * 0.4), (beat * 2, beat * 0.9), (beat * 3, beat * 0.9)]
        for t0, d in hits:
            ln = int(d * SR)
            sig = _tone(_freq(bass_root), ln, [1, 0.25, 0.08]) * _env(ln, 0.01, 0.05)
            _add(L, start + int(t0 * SR), sig, 0.22)
            _add(R, start + int(t0 * SR), sig, 0.22)

        # 드럼
        if intro and mood != "tense":
            continue
        k, s_, h = _kick(rng), _snare(rng), _hat(rng)
        if mood == "upbeat":
            for i in range(4):
                _add(L, start + int(i * beat * SR), k, 0.45); _add(R, start + int(i * beat * SR), k, 0.45)
            for i in (1, 3):
                _add(L, start + int(i * beat * SR), s_, 0.18); _add(R, start + int(i * beat * SR), s_, 0.18)
            for i in range(8):
                _add(R, start + int(i * beat / 2 * SR), h, 0.06 if i % 2 else 0.035)
        elif mood == "calm":
            swing = beat / 2 * 1.16
            for t0 in (0, beat * 2.5):
                _add(L, start + int(t0 * SR), k, 0.32); _add(R, start + int(t0 * SR), k, 0.32)
            for i in (1, 3):
                _add(L, start + int(i * beat * SR), s_, 0.10); _add(R, start + int(i * beat * SR), s_, 0.10)
            for i in range(4):
                _add(L, start + int((i * beat + swing) * SR), h, 0.03)
                _add(L, start + int(i * beat * SR), h, 0.04)
        elif mood == "tense":
            if not intro:
                for i in (0, 2):
                    _add(L, start + int(i * beat * SR), k, 0.38); _add(R, start + int(i * beat * SR), k, 0.38)
            for i in range(16):
                _add(R if i % 2 else L, start + int(i * beat / 4 * SR), _hat(rng, 0.03), 0.04)
        else:  # serious: 마디 첫 박에 낮은 북만
            _add(L, start, k, 0.3); _add(R, start, k, 0.3)

    mix = np.stack([L, R], axis=1)
    mix[:, 0] = _reverb(mix[:, 0], rng)
    mix[:, 1] = _reverb(mix[:, 1], rng)
    fade = int(1.5 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)[:, None]
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = np.max(np.abs(mix)) + 1e-9
    mix = np.tanh(mix / peak * 1.2) / np.tanh(1.2) * 0.85  # 부드러운 리미터
    return mix


def generate(mood: str, out: Path, seconds: float = 40, seed: int | None = None) -> Path:
    data = compose(mood, seconds, seed)
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out
