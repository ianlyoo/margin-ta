import os
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd


SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer1_data import (  # noqa: E402
    _get_exchange,
    fetch_ohlcv_data,
    fetch_pykrx_data,
    fetch_toss_data,
    resolve_yfinance_candidates,
)


class Layer1DataMarketResolutionTests(unittest.TestCase):
    def test_numeric_symbol_resolves_to_korean_candidates(self):
        resolved = resolve_yfinance_candidates("005930", market="auto")

        self.assertEqual(resolved["market"], "KR")
        self.assertEqual(resolved["symbol"], "005930")
        self.assertEqual(resolved["candidates"], ["005930.KS", "005930.KQ"])
        self.assertEqual(resolved["exchange"], "KRX")
        self.assertEqual(resolved["currency"], "KRW")
        self.assertEqual(resolved["tv_screener"], "korea")

    def test_korean_suffix_keeps_requested_candidate_first(self):
        resolved = resolve_yfinance_candidates("035720.KQ", market="auto")

        self.assertEqual(resolved["market"], "KR")
        self.assertEqual(resolved["symbol"], "035720")
        self.assertEqual(resolved["candidates"][0], "035720.KQ")
        self.assertEqual(resolved["candidates"][1], "035720.KS")

    def test_us_symbol_resolves_to_single_us_candidate(self):
        resolved = resolve_yfinance_candidates("aapl", market="auto")

        self.assertEqual(resolved["market"], "US")
        self.assertEqual(resolved["symbol"], "AAPL")
        self.assertEqual(resolved["candidates"], ["AAPL"])
        self.assertEqual(resolved["currency"], "USD")
        self.assertEqual(resolved["tv_screener"], "america")

    def test_exchange_mapping_respects_market(self):
        self.assertEqual(_get_exchange({"exchange": "NYQ"}, provider_symbol="NOW", market="US"), "NYSE")
        self.assertEqual(_get_exchange({"exchange": "NMS"}, provider_symbol="AAPL", market="US"), "NASDAQ")
        self.assertEqual(_get_exchange({}, provider_symbol="005930.KS", market="KR"), "KRX")

    def test_fetch_pykrx_data_normalizes_korean_ohlcv(self):
        class FakeStock:
            def get_market_ohlcv(self, fromdate, todate, ticker, adjusted=True):
                self.last_ohlcv_args = (fromdate, todate, ticker, adjusted)
                return pd.DataFrame(
                    {
                        "시가": [70000, 71000],
                        "고가": [72000, 73000],
                        "저가": [69000, 70500],
                        "종가": [71500, 72800],
                        "거래량": [1000000, 1100000],
                        "거래대금": [71500000000, 80080000000],
                        "등락률": [1.2, 1.8],
                    },
                    index=pd.to_datetime(["2026-05-26", "2026-05-27"]),
                )

            def get_market_ticker_name(self, ticker):
                return "삼성전자"

            def get_market_cap_by_date(self, fromdate, todate, ticker):
                return pd.DataFrame(
                    {"시가총액": [430000000000000, 435000000000000], "상장주식수": [5969782550, 5969782550]},
                    index=pd.to_datetime(["2026-05-26", "2026-05-27"]),
                )

        fake_stock = FakeStock()
        fake_pykrx = types.SimpleNamespace(stock=fake_stock)
        with patch.dict(sys.modules, {"pykrx": fake_pykrx}):
            result = fetch_pykrx_data("005930")

        self.assertEqual(fake_stock.last_ohlcv_args[2], "005930")
        self.assertFalse(any("pykrx" in warning and "실패" in warning for warning in result["warnings"]))
        self.assertEqual(result["info_summary"]["data_source"], "pykrx")
        self.assertEqual(result["info_summary"]["name"], "삼성전자")
        self.assertEqual(result["info_summary"]["market_cap"], 435000000000000)
        self.assertEqual(result["info_summary"]["currency"], "KRW")
        self.assertEqual(list(result["df"].columns), ["Open", "High", "Low", "Close", "Volume", "Amount", "ChangePct"])
        self.assertEqual(float(result["df"]["Close"].iloc[-1]), 72800)

    def test_auto_ohlcv_provider_falls_back_to_yfinance_when_pykrx_unavailable(self):
        fallback_df = pd.DataFrame(
            {
                "Open": [1.0],
                "High": [1.0],
                "Low": [1.0],
                "Close": [1.0],
                "Volume": [1],
            },
            index=pd.to_datetime(["2026-05-27"]),
        )
        pykrx_result = {"df": None, "info_summary": {}, "warnings": ["pykrx import 실패: missing"]}
        toss_result = {"df": None, "info_summary": {}, "warnings": ["토스 실패"]}
        yfinance_result = {
            "df": fallback_df,
            "info_summary": {"symbol": "005930", "market": "KR", "data_source": "yfinance"},
            "warnings": [],
        }

        with patch("layer1_data.fetch_pykrx_data", return_value=pykrx_result), \
             patch("layer1_data.fetch_toss_data", return_value=toss_result), \
             patch("layer1_data.fetch_yfinance_data", return_value=yfinance_result):
            result = fetch_ohlcv_data("005930", market="auto", provider="auto")

        self.assertIs(result["df"], fallback_df)
        self.assertIn("상위 provider 실패로 yfinance fallback 사용", result["warnings"])


class ProviderOrderTests(unittest.TestCase):
    def _result(self, source):
        df = pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-01-05"]))
        return {"df": df, "info_summary": {"data_source": source}, "warnings": []}

    def test_kr_auto_prefers_pykrx(self):
        with patch("layer1_data.fetch_pykrx_data", return_value=self._result("pykrx")) as mock_pykrx, \
             patch("layer1_data.fetch_toss_data", return_value=self._result("toss")) as mock_toss:
            out = fetch_ohlcv_data("005930", market="auto")
        self.assertEqual(out["info_summary"]["data_source"], "pykrx")
        mock_pykrx.assert_called_once()
        mock_toss.assert_not_called()

    def test_kr_auto_falls_back_to_toss_when_pykrx_empty(self):
        empty = {"df": None, "info_summary": {}, "warnings": ["pykrx 실패"]}
        with patch("layer1_data.fetch_pykrx_data", return_value=empty), \
             patch("layer1_data.fetch_toss_data", return_value=self._result("toss")):
            out = fetch_ohlcv_data("005930", market="auto")
        self.assertEqual(out["info_summary"]["data_source"], "toss")
        self.assertIn("pykrx 실패", out["warnings"])


if __name__ == "__main__":
    unittest.main()
