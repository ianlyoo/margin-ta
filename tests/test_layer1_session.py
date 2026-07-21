import os
import sys
import unittest
from datetime import datetime


SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

from layer1_session import (  # noqa: E402
    KST,
    _extract_domestic_quote_prices,
    _normalize_domestic_symbol,
    get_korea_session,
)


class Layer1SessionKoreaTests(unittest.TestCase):
    def test_normalize_domestic_symbol(self):
        self.assertEqual(_normalize_domestic_symbol("5930"), "005930")
        self.assertEqual(_normalize_domestic_symbol("005930.KS"), "005930")

    def test_extract_domestic_regular_and_after_hours_prices(self):
        regular, after = _extract_domestic_quote_prices({
            "stck_prpr": "78000",
            "ovtm_untp_prpr": "78100",
        })

        self.assertEqual(regular, 78000)
        self.assertEqual(after, 78100)

    def test_korea_session_regular_hours(self):
        session = get_korea_session(datetime(2026, 5, 27, 10, 0, tzinfo=KST))

        self.assertEqual(session["session"], "regular")
        self.assertTrue(session["is_open"])


if __name__ == "__main__":
    unittest.main()
