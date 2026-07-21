import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from market_risk import build_market_risk  # noqa: E402


def _s(val, n=1300):
    return pd.Series([val] * n, index=pd.bdate_range("2019-01-01", periods=n))


class MarketRiskTests(unittest.TestCase):
    def test_build_market_risk_shape(self):
        fake = {"tickers": {"vxn": _s(30.0), "vix": _s(18.0), "sp500": _s(100.0)},
                "sectors": {"XLK": _s(100.0)}, "fred": {}, "unavailable": ["fred:buffett_gdp"],
                "as_of": "2026-07-20T00:00:00"}
        with patch("market_risk.fetch_risk_data", return_value=fake):
            out = build_market_risk(cache_path=None)
        for key in ("score", "regime", "indicators", "sector_risk", "alerts", "unavailable"):
            self.assertIn(key, out)
        self.assertIn(out["regime"], ("calm", "caution", "stress", "crisis"))
        self.assertEqual(out["indicators"]["vxn_minus_vix"]["signal"], "alert")

    def test_cache_only_returns_none_without_live_fetch_on_cold_cache(self):
        # analyze 서브프로세스(120s 캡) 안에서 cache_only=True인데 캐시가 없거나 만료면
        # 절대 라이브 수집(fetch_risk_data)을 하지 않고 조용히 None을 반환해야 한다.
        with patch("market_risk.fetch_risk_data") as mock_fetch:
            mock_fetch.side_effect = AssertionError("fetch_risk_data must not be called when cache_only=True and cache is cold")
            out = build_market_risk(
                cache_path="/tmp/does-not-exist-market-risk-cache.json",
                cache_only=True,
            )
        self.assertIsNone(out)
        mock_fetch.assert_not_called()

    def test_cache_only_false_default_still_collects(self):
        fake = {"tickers": {"vxn": _s(30.0), "vix": _s(18.0), "sp500": _s(100.0)},
                "sectors": {"XLK": _s(100.0)}, "fred": {}, "unavailable": [],
                "as_of": "2026-07-20T00:00:00"}
        with patch("market_risk.fetch_risk_data", return_value=fake) as mock_fetch:
            out = build_market_risk(cache_path=None, cache_only=False)
        mock_fetch.assert_called_once()
        self.assertIsNotNone(out)


if __name__ == "__main__":
    unittest.main()
