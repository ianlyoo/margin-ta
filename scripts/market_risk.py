"""시장/섹터 위험 대시보드 — 개별 종목과 독립된 엔트리포인트. Spec #2 §3-4."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layer1_market import _load_cache, _save_cache
from layer1_risk import fetch_risk_data
from layer3_risk import (
    compute_market_indicators,
    compute_market_risk_score,
    compute_sector_risk,
)


def build_market_risk(include_kr: bool = True, cache_path: str | None = None,
                      max_age_hours: int = 12, cache_only: bool = False,
                      breadth_cache_path: str | None = None) -> dict | None:
    cached = _load_cache(cache_path, max_age_hours) if cache_path else None
    if cached:
        return cached
    if cache_only:
        # 캐시 미스/만료 — analyze 서브프로세스(120s 캡) 안에서는 라이브 수집(30s~3min)을
        # 절대 하지 않는다. SIGKILL은 내부 try/except로 못 잡으므로 조용히 None 반환.
        return None

    risk_data = fetch_risk_data(include_kr=include_kr, breadth_cache_path=breadth_cache_path)
    indicators = compute_market_indicators(risk_data)
    composite = compute_market_risk_score(indicators)
    sector_risk = compute_sector_risk(risk_data)

    result = {
        "score": composite["score"],
        "regime": composite["regime"],
        "group_scores": composite["group_scores"],
        "alerts": composite["alerts"],
        "indicators": indicators,
        "sector_risk": sector_risk,
        "unavailable": risk_data.get("unavailable", []),
        "as_of": risk_data.get("as_of"),
    }
    if cache_path:
        _save_cache(cache_path, result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Market/sector risk dashboard")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sectors", action="store_true", help="섹터 위험 상세")
    ap.add_argument("--no-kr", action="store_true")
    args = ap.parse_args()

    from paths import data_dir as _data_dir
    data_dir = _data_dir()
    cache_path = os.path.join(data_dir, "market_risk_cache.json")
    breadth_cache_path = os.path.join(data_dir, "market_breadth_cache.json")
    result = build_market_risk(include_kr=not args.no_kr, cache_path=cache_path,
                               breadth_cache_path=breadth_cache_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"시장 위험: {result['score']}/100 [{result['regime']}]")
    if result["alerts"]:
        print(f"  ⚠️ ALERT: {', '.join(result['alerts'])}")
    if args.sectors:
        for sym, sr in sorted(result["sector_risk"].items(), key=lambda x: -x[1]["score"]):
            print(f"  {sym}: {sr['score']} [{sr['level']}]")
    if result["unavailable"]:
        print(f"  (미수집: {len(result['unavailable'])}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
