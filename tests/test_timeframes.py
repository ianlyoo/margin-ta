import os
import sys
import unittest

import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from timeframes import last_bar_incomplete, resample_ohlcv  # noqa: E402


def _daily(days: int, start: str = "2026-01-05") -> pd.DataFrame:
    """월요일 시작 영업일 인덱스의 합성 일봉."""
    idx = pd.bdate_range(start, periods=days)
    return pd.DataFrame(
        {
            "Open": [float(i) for i in range(days)],
            "High": [float(i + 1) for i in range(days)],
            "Low": [float(max(0, i - 1)) for i in range(days)],
            "Close": [float(i) + 0.5 for i in range(days)],
            "Volume": [100] * days,
        },
        index=idx,
    )


class ResampleTests(unittest.TestCase):
    def test_weekly_ohlcv_aggregation(self):
        df = _daily(10)  # 2026-01-05(월) ~ 01-16(금) = 정확히 2주
        w = resample_ohlcv(df, "W-FRI")
        self.assertEqual(len(w), 2)
        self.assertEqual(w.Open.iloc[0], 0.0)      # 첫날 시가
        self.assertEqual(w.High.iloc[0], 5.0)      # 첫 주 최고 (i=4 → 5)
        self.assertEqual(w.Low.iloc[0], 0.0)
        self.assertEqual(w.Close.iloc[0], 4.5)     # 금요일 종가 (i=4)
        self.assertEqual(w.Volume.iloc[0], 500)

    def test_monthly_aggregation_row_count(self):
        df = _daily(45)  # 1월 초 ~ 3월 초 → 3개 월봉
        m = resample_ohlcv(df, "ME")
        self.assertEqual(len(m), 3)
        self.assertEqual(m.Volume.sum(), 4500)

    def test_incomplete_last_bar(self):
        self.assertFalse(last_bar_incomplete(_daily(10), "W-FRI"))   # 금요일 마감
        self.assertTrue(last_bar_incomplete(_daily(11), "W-FRI"))    # 월요일에 끝남
        self.assertTrue(last_bar_incomplete(_daily(10), "ME"))       # 1월 중순에 끝남

    def test_incomplete_last_bar_tz_aware(self):
        df = _daily(11)
        df.index = df.index.tz_localize("America/New_York")
        self.assertTrue(last_bar_incomplete(df, "W-FRI"))
        df2 = _daily(10)
        df2.index = df2.index.tz_localize("America/New_York")
        self.assertFalse(last_bar_incomplete(df2, "W-FRI"))

    def test_resample_tz_aware_weekly(self):
        df = _daily(10)
        df.index = df.index.tz_localize("America/New_York")
        w = resample_ohlcv(df, "W-FRI")
        self.assertEqual(len(w), 2)
        self.assertEqual(w.Volume.iloc[0], 500)


if __name__ == "__main__":
    unittest.main()
