"""FRED public CSV fetcher (no API key). Best-effort: failures never raise."""
from __future__ import annotations

import urllib.request

_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "margin-ta/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def fetch_fred_series(series_id: str, timeout: float = 8.0) -> dict:
    """FRED CSV → {"values": [(date, float)], "last": float|None, "error": str|None}.

    FRED은 egress 환경에 따라 타임아웃될 수 있다 — 실패는 예외 없이 error로 반환한다.
    """
    try:
        raw = _http_get(f"{_BASE}{series_id}", timeout)
    except Exception as e:  # noqa: BLE001 — best-effort, 모든 실패를 error로
        return {"values": [], "last": None, "error": f"{type(e).__name__}: {e}"}

    values: list[tuple[str, float]] = []
    for line in raw.strip().splitlines()[1:]:  # 헤더 스킵
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date_str, val = parts[0].strip(), parts[1].strip()
        if not val or val == ".":  # 결측치
            continue
        try:
            values.append((date_str, float(val)))
        except ValueError:
            continue
    return {"values": values, "last": values[-1][1] if values else None, "error": None}
