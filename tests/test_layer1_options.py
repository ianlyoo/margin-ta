import os
import sys
import unittest
from datetime import datetime, timedelta, timezone


SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer1_options import (  # noqa: E402
    build_chain_feature,
    compute_options_score_overlay,
    filter_reliable_contracts,
    max_pain_from_oi,
    rank_expiries,
    select_primary_expiry,
)


def future_expiry(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def contract(
    side: str,
    strike: float,
    oi: int,
    volume: int = 100,
    bid: float = 1.0,
    ask: float = 1.2,
    iv: float = 0.45,
    gamma: float = 0.002,
) -> dict:
    return {
        "expiry": future_expiry(30),
        "type": side,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last_price": (bid + ask) / 2,
        "volume": volume,
        "open_interest": oi,
        "implied_volatility": iv,
        "gamma": gamma,
    }


class Layer1OptionsTests(unittest.TestCase):
    def test_max_pain_from_open_interest(self):
        calls = {95.0: 10, 100.0: 50, 105.0: 20}
        puts = {95.0: 20, 100.0: 40, 105.0: 10}

        self.assertEqual(max_pain_from_oi(calls, puts), 100.0)

    def test_filter_rejects_low_quality_contracts(self):
        contracts = [
            contract("call", 100, 100, bid=1.0, ask=1.2),
            contract("call", 101, 100, bid=0.0, ask=1.2),
            contract("put", 99, 0, bid=1.0, ask=1.2),
            contract("put", 98, 100, bid=1.0, ask=5.0),
            contract("call", 97, 100, bid=1.0, ask=1.2, iv=9.0),
            contract("put", 130, 100, bid=1.0, ask=1.2),
        ]

        filtered, warnings = filter_reliable_contracts(contracts, 100)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["strike"], 100)
        self.assertEqual(warnings, [])

    def test_expiry_selection_prefers_nearest_large_expiry(self):
        exp7 = future_expiry(7)
        exp30 = future_expiry(30)
        exp45 = future_expiry(45)
        features = [
            {
                "expiry": exp7,
                "days_to_expiry": 7,
                "total_oi": 650,
                "near_spot_oi": 0,
                "total_volume": 0,
                "is_monthly_opex": False,
                "quality_status": "usable",
                "quality_score": 55,
            },
            {
                "expiry": exp30,
                "days_to_expiry": 30,
                "total_oi": 1000,
                "near_spot_oi": 0,
                "total_volume": 0,
                "is_monthly_opex": False,
                "quality_status": "good",
                "quality_score": 80,
            },
            {
                "expiry": exp45,
                "days_to_expiry": 45,
                "total_oi": 400,
                "near_spot_oi": 0,
                "total_volume": 0,
                "is_monthly_opex": False,
                "quality_status": "good",
                "quality_score": 75,
            },
        ]

        rankings = rank_expiries(features)
        selected = select_primary_expiry(rankings)

        self.assertEqual(selected["expiry"], exp7)
        self.assertGreaterEqual(selected["size_score"], rankings[0]["size_score"] * 0.60)

    def test_chain_feature_computes_walls_expected_move_and_confluence(self):
        expiry = future_expiry(30)
        strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]
        contracts = []
        for strike in strikes:
            call_oi = 4000 if strike == 105 else 1000
            put_oi = 3500 if strike == 95 else 900
            contracts.append({**contract("call", strike, call_oi), "expiry": expiry})
            contracts.append({**contract("put", strike, put_oi), "expiry": expiry})

        feature = build_chain_feature(
            "TEST",
            expiry,
            100,
            contracts,
            "unit",
            technical_levels={
                "supports": [{"price": 95.2, "source": "Support"}],
                "resistances": [{"price": 105.2, "source": "Resistance"}],
            },
            realized_vol=0.30,
        )

        self.assertIn(feature["quality_status"], {"good", "usable"})
        self.assertEqual(feature["call_wall"], 105)
        self.assertEqual(feature["put_wall"], 95)
        self.assertEqual(feature["volatility"]["status"], "available")
        self.assertEqual(feature["greeks_exposure"]["status"], "estimated")
        self.assertTrue(any(row["sr_confluence"] for row in feature["options_map"]))

    def test_options_overlay_is_bounded_and_quality_gated(self):
        weak = {"chain_quality": {"quality_status": "weak", "quality_score": 20}}
        disabled = compute_options_score_overlay(weak, 100)
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["clamped_score"], 0)

        good = {
            "chain_quality": {"quality_status": "good", "quality_score": 80},
            "selected_expiry": {"expiry": future_expiry(7), "days_to_expiry": 7},
            "max_pain": {"price": 90, "distance_pct": 11.1},
            "put_call_ratios": {"oi": 0.4, "volume": 0.6},
            "walls": {
                "call_wall": 101,
                "dist_to_call_wall_pct": 1.0,
                "put_wall": 99,
                "dist_to_put_wall_pct": 1.0,
                "top_strike_oi_concentration": 0.4,
            },
            "greeks_exposure": {"status": "estimated", "gamma_regime": "negative"},
            "volatility": {"iv_rv_premium": 0.30},
        }

        overlay = compute_options_score_overlay(good, 100, close_up=False, close_down=True)

        self.assertTrue(overlay["enabled"])
        self.assertLessEqual(abs(overlay["clamped_score"]), 8)
        self.assertTrue(overlay["details"])


if __name__ == "__main__":
    unittest.main()
