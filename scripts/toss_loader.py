"""Optional Toss Securities client loader.

토스 데이터 소스는 옵셔널이다. 환경변수 MARGIN_TA_TOSS_IMPORT에 토스 클라이언트
모듈의 import 경로(예: "toss_client")를 지정하면 그 모듈에서 TossClient를 로드한다.
모듈이 표준 경로에 없으면 MARGIN_TA_TOSS_PATH(디렉터리, os.pathsep으로 여러 개)를
sys.path에 추가해 찾는다. 미설정 시 토스 소스는 비활성이고, 파이프라인은
pykrx(KR)→yfinance로 폴백한다.
"""
from __future__ import annotations

import importlib
import os
import sys

_client_module = None
_last_error = None


def _ensure_toss_search_paths() -> None:
    """MARGIN_TA_TOSS_PATH의 디렉터리들을 sys.path에 추가(이미 있으면 스킵)."""
    raw = os.environ.get("MARGIN_TA_TOSS_PATH", "").strip()
    if not raw:
        return
    for p in raw.split(os.pathsep):
        p = p.strip()
        if p and p not in sys.path:
            sys.path.insert(0, p)


def load_toss_module():
    """MARGIN_TA_TOSS_IMPORT env의 모듈을 로드·캐시. 미설정/실패 → None."""
    global _client_module, _last_error
    if _client_module is not None:
        return _client_module if _client_module is not False else None
    path = os.environ.get("MARGIN_TA_TOSS_IMPORT", "").strip()
    if not path:
        _client_module = False  # 미설정 마커
        _last_error = None
        return None
    try:
        _client_module = importlib.import_module(path)
        _last_error = None  # success, clear any previous error
    except Exception:
        # 표준 경로에서 못 찾으면 MARGIN_TA_TOSS_PATH를 sys.path에 추가해 재시도
        _ensure_toss_search_paths()
        try:
            _client_module = importlib.import_module(path)
            _last_error = None
            return _client_module
        except Exception as e:
            _client_module = False
            _last_error = str(e)
            return None
    return _client_module


def is_toss_configured() -> bool:
    return load_toss_module() is not None


def get_toss_client():
    """토스 클라이언트 인스턴스. _load_env_file이 있으면 먼저 호출. 실패 → None."""
    module = load_toss_module()
    if module is None:
        return None
    try:
        load_env = getattr(module, "_load_env_file", None)
        if callable(load_env):
            load_env()
        client_cls = getattr(module, "TossClient", None)
        if client_cls is None:
            return None
        return client_cls()
    except Exception:
        return None
