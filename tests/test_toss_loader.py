import os
import sys
import types
import unittest

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import toss_loader  # noqa: E402


class TossLoaderTests(unittest.TestCase):
    def setUp(self):
        toss_loader._client_module = None  # reset cache
        toss_loader._last_error = None

    def test_unset_env_returns_none(self):
        old = os.environ.pop("MARGIN_TA_TOSS_IMPORT", None)
        try:
            self.assertIsNone(toss_loader.load_toss_module())
            self.assertFalse(toss_loader.is_toss_configured())
        finally:
            if old is not None:
                os.environ["MARGIN_TA_TOSS_IMPORT"] = old

    def test_bad_import_returns_none(self):
        os.environ["MARGIN_TA_TOSS_IMPORT"] = "no_such_module_xyz"
        try:
            self.assertIsNone(toss_loader.load_toss_module())
            self.assertIsNone(toss_loader.get_toss_client())
            self.assertFalse(toss_loader.is_toss_configured())
        finally:
            del os.environ["MARGIN_TA_TOSS_IMPORT"]
            toss_loader._client_module = None
            toss_loader._last_error = None

    def test_configured_module_yields_client(self):
        fake = types.ModuleType("fake_toss")
        calls = []
        class FakeClient:
            def __init__(self):
                calls.append("init")
        fake.TossClient = FakeClient
        fake._load_env_file = lambda: calls.append("env")
        sys.modules["fake_toss"] = fake
        os.environ["MARGIN_TA_TOSS_IMPORT"] = "fake_toss"
        try:
            client = toss_loader.get_toss_client()
            self.assertIsInstance(client, FakeClient)
            self.assertIn("env", calls)   # _load_env_file called first
            self.assertTrue(toss_loader.is_toss_configured())
        finally:
            del sys.modules["fake_toss"]
            del os.environ["MARGIN_TA_TOSS_IMPORT"]
            toss_loader._client_module = None
            toss_loader._last_error = None
