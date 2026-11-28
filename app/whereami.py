"""봇이 어디서 돌고 있는지 알려준다(/whereami). 서버를 못 찾을 때 확인용."""
from __future__ import annotations

import json
import socket

import httpx

IMDS = "http://169.254.169.254"


def _aws() -> dict:
    headers = {}
    try:  # IMDSv2 토큰(도커 안에서는 막힐 수 있어 실패하면 v1 시도)
        r = httpx.put(f"{IMDS}/latest/api/token", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}, timeout=2)
        if r.status_code == 200:
            headers["X-aws-ec2-metadata-token"] = r.text
    except Exception:
        pass
    r = httpx.get(f"{IMDS}/latest/dynamic/instance-identity/document", headers=headers, timeout=2)
    r.raise_for_status()
    return json.loads(r.text)


def report() -> str:
    lines = ["📍 봇이 돌고 있는 곳"]
    try:
        ip = httpx.get("https://checkip.amazonaws.com", timeout=10).text.strip()
        lines.append(f"공개 IP: {ip}")
    except Exception:
        lines.append("공개 IP: 확인 실패")
    lines.append(f"컴퓨터 이름: {socket.gethostname()} (도커 안이면 컨테이너 이름)")
    try:
        doc = _aws()
        lines += [
            "클라우드: AWS (Lightsail 또는 EC2)",
            f"리전: {doc.get('region')} / 영역: {doc.get('availabilityZone')}",
            f"AWS 계정 번호: {doc.get('accountId')}",
            f"인스턴스 ID: {doc.get('instanceId')} ({doc.get('instanceType')})",
        ]
    except Exception:
        lines.append("클라우드 정보: AWS 정보를 못 읽었어요(AWS가 아니거나 막혀 있음)")
    return "\n".join(lines)
