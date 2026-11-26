"""실행 진입점.

python main.py run                         # 서버에서 계속 실행(텔레그램 + inbox + 예약 게시)
python main.py preview inbox/sample.txt    # AI 작성 + 이미지 렌더만 로컬에서 확인
python main.py preview --script examples/sample_script.json   # AI 없이 템플릿만 확인
python main.py quota                       # 인스타 24시간 게시 사용량
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path


def cmd_run() -> None:
    from app import instagram, pipeline

    print(f"[run] 채널: {', '.join(pipeline.CHANNELS)}")
    last_tick = 0.0
    while True:
        try:
            pipeline.handle_updates()  # 롱폴링(최대 25초 대기)
        except Exception:
            traceback.print_exc()
            time.sleep(5)
        if time.time() - last_tick >= 60:
            last_tick = time.time()
            try:
                pipeline.prepare_new()
                pipeline.publish_due()
                for ch in pipeline.CHANNELS.values():
                    instagram.refresh_token_if_needed(ch)
            except Exception:
                traceback.print_exc()


def cmd_preview(source: str | None, script_path: str | None, channel_name: str | None) -> None:
    from app import ingest, renderer, writer
    from app.config import load_channels

    channels = load_channels()
    channel = channels[channel_name or next(iter(channels))]
    if script_path:
        script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    else:
        script = writer.write_script(channel, ingest.parse_file(Path(source)))
        print(json.dumps(script, ensure_ascii=False, indent=2))
    paths = renderer.render(channel, script, "preview")
    print("\n".join(str(p) for p in paths))
    print("\n--- 캡션 ---\n" + writer.full_caption(script))


def cmd_quota() -> None:
    from app import instagram
    from app.config import load_channels

    for ch in load_channels().values():
        print(ch.name, instagram.publishing_quota(ch))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    p = sub.add_parser("preview")
    p.add_argument("source", nargs="?")
    p.add_argument("--script")
    p.add_argument("--channel")
    sub.add_parser("quota")
    args = parser.parse_args()
    if args.cmd == "run":
        cmd_run()
    elif args.cmd == "preview":
        if not args.source and not args.script:
            parser.error("원본 파일 경로나 --script 를 지정하세요")
        cmd_preview(args.source, args.script, args.channel)
    elif args.cmd == "quota":
        cmd_quota()
