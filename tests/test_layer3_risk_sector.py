import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer3_risk import compute_sector_risk  # noqa: E402


def _series(closes):
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    return pd.Series(closes, index=idx)


class SectorRiskTests(unittest.TestCase):
    def test_overheated_sector_scores_higher_than_calm(self):
        n = 400
        # 과열 섹터: 최근 급등 후 200일선 크게 이격
        hot = _series(list(np.linspace(50, 100, 300)) + list(np.linspace(100, 160, 100)))
        calm = _series(list(np.linspace(95, 100, 400)))
        bench = _series(list(np.linspace(90, 100, 400)))
        rd = {"sectors": {"XLK": hot, "XLP": calm}, "tickers": {"spy": bench}}
        out = compute_sector_risk(rd)
        self.assertIn("XLK", out)
        self.assertIn(out["XLK"]["level"], ("low", "elevated", "high", "critical"))
        self.assertGreater(out["XLK"]["score"], out["XLP"]["score"])

    def test_missing_benchmark_still_scores(self):
        rd = {"sectors": {"XLK": _series(list(np.linspace(50, 100, 300)))}, "tickers": {}}
        out = compute_sector_risk(rd)  # spy 없음 → 모멘텀 컴포넌트만 스킵, 나머지로 스코어
        self.assertIn("XLK", out)
        self.assertIsInstance(out["XLK"]["score"], int)

    def test_components_json_serializable_no_numpy_leak(self):
        import json
        # 최근 10봉 하락일 수 <9 → volume_anomaly*12 < 100 → 예전엔 numpy.int64가
        # 새어 캐시 bare json.dump가 TypeError로 깨졌다. 40봉(>30 스킵 기준) 상승 추세.
        close = _series([100 + i for i in range(38)] + [138, 137])  # 마지막 1봉만 하락
        rd = {"sectors": {"XLK": close}, "tickers": {}}
        out = compute_sector_risk(rd)
        json.dumps(out)  # numpy 누출 시 TypeError
        for comp in out["XLK"]["components"].values():
            self.assertIsInstance(comp, float)


if __name__ == "__main__":
    unittest.main()

