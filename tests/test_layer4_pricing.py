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


class RoundedTargetTests(unittest.TestCase):
    """Public plan outputs must use distinct prices and matching returns."""

    def _plans(self, *, currency='USD', resistances=(), supports=(), trend=False, high=None):
        df = _support_bounce_df()
        price = 20_000.0 if currency == 'KRW' else 100.0
        scale = price / 100.0
        for column in ('Open', 'High', 'Low', 'Close'):
            df[column] *= scale
        if trend:
            df['EMA_20'] = price * 0.995
        if high is not None:
            df.loc[df.index[0], 'High'] = high
        result = build_entry_plans(
            price, 80,
            [{'price': p, 'source': 'support'} for p in supports],
            [{'price': p, 'source': f'resistance {i}'} for i, p in enumerate(resistances)],
            df, currency=currency,
        )
        return {p['type']: p for p in result['all_plans']}

    def test_trend_finds_two_distinct_targets_after_currency_rounding(self):
        for currency, levels, expected in [
            ('USD', [105.004, 105.003, 110.0], [105.0, 110.0]),
            ('KRW', [21123.0, 21145.0, 22000.0], [21100.0, 22000.0]),
        ]:
            with self.subTest(currency=currency):
                plan = self._plans(currency=currency, resistances=levels, trend=True)['trend_confirm']
                self.assertEqual([t['price'] for t in plan['targets']], expected)
                self.assertEqual([t['level'] for t in plan['targets']], [1, 2])

    def test_trend_skips_resistance_that_rounds_to_entry(self):
        plan = self._plans(resistances=[100.201, 105.0, 110.0], trend=True)['trend_confirm']
        self.assertEqual(plan['entry'], 100.2)
        self.assertEqual([t['price'] for t in plan['targets']], [105.0, 110.0])

    def test_trend_without_resistances_returns_extension(self):
        plan = self._plans(trend=True)['trend_confirm']
        self.assertEqual(plan['targets'], [
            {'level': 1, 'price': 105.0, 'gain_pct': 4.79, 'source': '5% ext'},
        ])

    def test_support_extension_gain_uses_displayed_krw_price(self):
        plan = self._plans(currency='KRW', supports=[19800.0])['support_bounce']
        target = plan['targets'][0]
        self.assertEqual(plan['entry'], 19900.0)
        self.assertEqual(target['price'], 20850.0)
        self.assertEqual(target['gain_pct'], 4.77)
        self.assertEqual(target['source'], '5% ext')

    def test_breakout_high_gain_uses_displayed_krw_price(self):
        plan = self._plans(currency='KRW', resistances=[20201.0], high=23049.0)['breakout']
        target = plan['targets'][0]
        self.assertEqual(plan['entry'], 20300.0)
        self.assertEqual(target['price'], 23000.0)
        self.assertEqual(target['gain_pct'], 13.3)
        self.assertEqual(target['source'], '52-week high')

    def test_existing_usd_plan_targets_keep_sources_and_order(self):
        plans = self._plans(supports=[99.0], resistances=[103.0, 110.0, 115.0], trend=True)
        expected = {
            'support_bounce': [(103.0, 'resistance 0'), (110.0, 'resistance 1')],
            'trend_confirm': [(103.0, 'resistance 0'), (110.0, 'resistance 1')],
            'breakout': [(110.0, 'resistance 1')],
        }
        self.assertEqual(set(plans), set(expected))
        for name, targets in expected.items():
            self.assertEqual([(t['price'], t['source']) for t in plans[name]['targets']], targets)


if __name__ == "__main__":
    unittest.main()
