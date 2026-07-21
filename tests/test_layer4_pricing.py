import os
import sys
import unittest

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer4_pricing import build_entry_plans  # noqa: E402


def _support_bounce_df() -> pd.DataFrame:
    """30-bar flat OHLCV series with a bullish bounce candle on the last bar.

    Designed so that ``_eval_support_bounce`` fires with high quality (dist<2%,
    bullish close, close-in-upper-half-of-range, no RSI column so
    not_oversold_extreme defaults True) while ``_eval_trend_confirm`` (needs
    EMA_20/SMA_20 columns, absent here) and ``_eval_breakout`` (needs a
    resistance within 4%, ours is 10% away) both stay silent — so
    `build_entry_plans` has exactly one candidate plan and its `recommended`
    is deterministic.
    """
    n = 30
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    open_ = np.full(n, 100.0)
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    close = np.full(n, 100.0)
    volume = np.full(n, 1_000_000.0)

    # Last bar: opens low, closes bullish in the upper half of its range.
    open_[-1] = 98.5
    high[-1] = 101.0
    low[-1] = 97.8
    close[-1] = 100.0

    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


CURRENT_PRICE = 100.0
ENTRY_SCORE = 70
SUPPORTS = [{"price": 99.0, "source": "Test Support"}]
RESISTANCES = [{"price": 110.0, "source": "Test Resistance"}]


def _build(df, consensus=None, major_supports=None):
    return build_entry_plans(
        CURRENT_PRICE,
        ENTRY_SCORE,
        SUPPORTS,
        RESISTANCES,
        df,
        market_regime=None,
        ownership=None,
        options_data=None,
        currency="USD",
        consensus=consensus,
        major_supports=major_supports,
    )


class FinalizePlanConsensusDowngradeTests(unittest.TestCase):
    """Exercises layer4_pricing._finalize_plan's consensus-driven confidence
    demotion and major_support_nearby annotation via the real
    build_entry_plans -> _eval_support_bounce -> _finalize_plan code path.

    Baseline (no consensus / agreement=None) recommended plan has
    quality=90, first_target_rr~3.04 -> confidence="high" with no
    consensus_warning key. This is verified by test_no_consensus_baseline
    and relied on by the agreement=30 case to assert a one-step demotion.
    """

    def test_baseline_no_consensus_is_high_confidence_with_no_warning(self):
        df = _support_bounce_df()
        result = _build(df, consensus=None)
        rec = result["recommended"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["confidence"], "high")
        self.assertNotIn("consensus_warning", rec)

    def test_agreement_none_no_demotion_no_warning(self):
        df = _support_bounce_df()
        result = _build(df, consensus={"agreement": None})
        rec = result["recommended"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["confidence"], "high")
        self.assertIsNone(rec.get("consensus_warning"))

    def test_low_agreement_demotes_confidence_one_step_and_warns(self):
        df = _support_bounce_df()
        baseline = _build(df, consensus=None)["recommended"]
        result = _build(df, consensus={"agreement": 30})
        rec = result["recommended"]
        self.assertIsNotNone(rec)

        order = ["low", "medium", "high"]
        expected = order[max(0, order.index(baseline["confidence"]) - 1)]
        self.assertEqual(rec["confidence"], expected)
        self.assertEqual(rec["confidence"], "medium")  # baseline is "high" here
        self.assertIsNotNone(rec.get("consensus_warning"))
        self.assertIn("30", rec["consensus_warning"])

    def test_major_support_between_stop_and_entry_is_annotated(self):
        df = _support_bounce_df()
        result = _build(df, consensus=None, major_supports=[{"price": 97.5, "source": "Weekly Pivot"}])
        rec = result["recommended"]
        self.assertIsNotNone(rec)
        self.assertLess(rec["stop"], 97.5)
        self.assertLess(97.5, rec["entry"])
        self.assertEqual(rec["major_support_nearby"], 97.5)

    def test_major_support_outside_stop_entry_band_is_absent(self):
        df = _support_bounce_df()
        result = _build(df, consensus=None, major_supports=[{"price": 90.0, "source": "Weekly Pivot"}])
        rec = result["recommended"]
        self.assertIsNotNone(rec)
        self.assertNotIn("major_support_nearby", rec)

        result_none = _build(df, consensus=None, major_supports=None)
        rec_none = result_none["recommended"]
        self.assertNotIn("major_support_nearby", rec_none)


if __name__ == "__main__":
    unittest.main()
