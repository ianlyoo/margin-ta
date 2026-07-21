import os
import sys
import unittest
from unittest.mock import patch

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from fred_client import fetch_fred_series  # noqa: E402


class FredClientTests(unittest.TestCase):
    def test_parses_csv_last_value(self):
        csv = "observation_date,GDP\n2025-07-01,29000.0\n2025-10-01,29500.5\n"
        with patch("fred_client._http_get", return_value=csv):
            out = fetch_fred_series("GDP")
        self.assertEqual(out["last"], 29500.5)
        self.assertEqual(out["values"][-1], ("2025-10-01", 29500.5))
        self.assertIsNone(out["error"])

    def test_skips_missing_dot_values(self):
        csv = "observation_date,T10Y2Y\n2025-10-01,0.55\n2025-10-02,.\n2025-10-03,0.60\n"
        with patch("fred_client._http_get", return_value=csv):
            out = fetch_fred_series("T10Y2Y")
        self.assertEqual(out["last"], 0.60)  # "." 행 스킵, 마지막 유효값

    def test_timeout_returns_error_not_raise(self):
        with patch("fred_client._http_get", side_effect=TimeoutError("timed out")):
            out = fetch_fred_series("GDP", timeout=1.0)
        self.assertIsNone(out["last"])
        self.assertEqual(out["values"], [])
        self.assertIn("timed out", out["error"])
