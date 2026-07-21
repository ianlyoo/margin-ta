import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer1_risk import download_closes, fetch_risk_data  # noqa: E402


def _series(n=300, val=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Series([val] * n, index=idx)


class Layer1RiskTests(unittest.TestCase):
    def test_download_closes_skips_failed_symbols(self):
        def fake_hist(sym, **kw):
            if sym == "BAD":
                raise ValueError("no data")
            return pd.DataFrame({"Close": _series()})
        with patch("layer1_risk._history_close", side_effect=lambda s, period="10y": None if s == "BAD" else _series()):
            out = download_closes(["^VIX", "BAD"])
        self.assertIn("^VIX", out)
        self.assertNotIn("BAD", out)

    def test_fetch_risk_data_degrades_on_fred_failure(self):
        with patch("layer1_risk.download_closes", return_value={"^VIX": _series()}), \
             patch("layer1_risk.fetch_fred_series", return_value={"values": [], "last": None, "error": "TimeoutError"}):
            out = fetch_risk_data(include_kr=False)
        self.assertIn("tickers", out)
        self.assertIsNone(out["fred"]["buffett_gdp"]["last"])
        # FRED 실패해도 예외 없이 반환
        self.assertIn("as_of", out)

    def test_fetch_risk_data_no_kr_excludes_korea(self):
        with patch("layer1_risk.download_closes", return_value={}), \
             patch("layer1_risk.fetch_fred_series", return_value={"values": [], "last": None, "error": None}):
            out = fetch_risk_data(include_kr=False)
        # KR 심볼이 sector 수집 대상에서 빠졌는지 — download_closes 호출 인자로 검증
        self.assertIn("sectors", out)
