"""KIS session credentials + Google Drive upload must be optional and env-driven.

Hermetic: never touches real KIS dashboard / google token paths.
KIS creds resolve via direct env (KIS_APP_KEY/...) first, then KIS_ENV_FILE
(prefixed or unprefixed keys); absent -> None + graceful skip (no RuntimeError).
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import layer1_session  # noqa: E402
import scan_nightly  # noqa: E402

KIS_ENV_KEYS = (
    "KIS_ENV_FILE",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_CANO",
    "KIS_ACNT_PRDT_CD",
    "KIS_URL_BASE",
    "MARGIN_TA_KIS_TOKEN_CACHE",
)


class EnvIsolatedTestCase(unittest.TestCase):
    """Snapshot/restore the env vars under test so tests are hermetic."""

    ENV_KEYS: tuple = KIS_ENV_KEYS

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        tmp = tempfile.mkdtemp(prefix="margin_ta_optional_")
        self.tmp = pathlib.Path(tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class LoadKisCredentialsTests(EnvIsolatedTestCase):
    def test_env_file_unprefixed_keys(self):
        env = self.tmp / "kis.env"
        env.write_text(
            "# comment\nAPP_KEY=filekey\nAPP_SECRET=filesecret\n"
            "CANO=12345678\nACNT_PRDT_CD=01\n",
            encoding="utf-8",
        )
        os.environ["KIS_ENV_FILE"] = str(env)

        creds = layer1_session.load_kis_credentials()

        self.assertIsNotNone(creds)
        self.assertEqual(creds["app_key"], "filekey")
        self.assertEqual(creds["app_secret"], "filesecret")
        self.assertEqual(creds["cano"], "12345678")
        self.assertEqual(creds["acnt_prdt_cd"], "01")
        self.assertTrue(creds["url_base"].startswith("https://"))

    def test_env_file_prefixed_keys(self):
        env = self.tmp / "kis.env"
        env.write_text(
            'KIS_APP_KEY="pkey"\nKIS_APP_SECRET="psecret"\n'
            "KIS_CANO=87654321\nKIS_ACNT_PRDT_CD=02\n"
            "KIS_URL_BASE=https://example.test:9443/\n",
            encoding="utf-8",
        )
        os.environ["KIS_ENV_FILE"] = str(env)

        creds = layer1_session.load_kis_credentials()

        self.assertEqual(creds["app_key"], "pkey")
        self.assertEqual(creds["app_secret"], "psecret")
        self.assertEqual(creds["cano"], "87654321")
        self.assertEqual(creds["acnt_prdt_cd"], "02")
        self.assertEqual(creds["url_base"], "https://example.test:9443")

    def test_direct_env_wins_over_file(self):
        env = self.tmp / "kis.env"
        env.write_text("APP_KEY=filekey\nAPP_SECRET=filesecret\n", encoding="utf-8")
        os.environ["KIS_ENV_FILE"] = str(env)
        os.environ["KIS_APP_KEY"] = "directkey"
        os.environ["KIS_APP_SECRET"] = "directsecret"

        creds = layer1_session.load_kis_credentials()

        self.assertEqual(creds["app_key"], "directkey")
        self.assertEqual(creds["app_secret"], "directsecret")

    def test_direct_env_alone(self):
        os.environ["KIS_APP_KEY"] = "dk"
        os.environ["KIS_APP_SECRET"] = "ds"

        creds = layer1_session.load_kis_credentials()

        self.assertIsNotNone(creds)
        self.assertEqual(creds["app_key"], "dk")
        self.assertEqual(creds["app_secret"], "ds")

    def test_unset_returns_none(self):
        self.assertIsNone(layer1_session.load_kis_credentials())

    def test_missing_env_file_returns_none(self):
        os.environ["KIS_ENV_FILE"] = str(self.tmp / "does_not_exist.env")
        self.assertIsNone(layer1_session.load_kis_credentials())

    def test_incomplete_creds_returns_none(self):
        env = self.tmp / "kis.env"
        env.write_text("APP_KEY=only_key\nCANO=12345678\n", encoding="utf-8")
        os.environ["KIS_ENV_FILE"] = str(env)
        self.assertIsNone(layer1_session.load_kis_credentials())


class KisTokenCachePathTests(EnvIsolatedTestCase):
    def test_default_path(self):
        path = layer1_session._kis_token_cache_path()
        self.assertEqual(
            path, pathlib.Path.home() / ".cache" / "margin-ta" / "kis_token.json"
        )

    def test_env_override(self):
        target = self.tmp / "custom" / "token.json"
        os.environ["MARGIN_TA_KIS_TOKEN_CACHE"] = str(target)
        self.assertEqual(layer1_session._kis_token_cache_path(), target)

    def test_token_cache_roundtrip_uses_env_path(self):
        cache = self.tmp / "cache" / "kis_token.json"
        os.environ["MARGIN_TA_KIS_TOKEN_CACHE"] = str(cache)
        import time as _time

        now = _time.time()
        layer1_session._save_cached_token("scope1", "tok123", now, now + 3600)
        self.assertTrue(cache.exists())
        self.assertEqual(layer1_session._load_cached_token("scope1"), "tok123")
        self.assertIsNone(layer1_session._load_cached_token("other_scope"))


class KisUnconfiguredGracefulTests(EnvIsolatedTestCase):
    """No RuntimeError may escape when KIS is unconfigured."""

    def test_fetch_overseas_price_graceful(self):
        result = layer1_session.fetch_kis_overseas_price("AAPL", "NASDAQ")
        self.assertFalse(result["ok"])
        self.assertTrue(result["warnings"])

    def test_fetch_domestic_price_graceful(self):
        result = layer1_session.fetch_kis_domestic_price("005930")
        self.assertFalse(result["ok"])
        self.assertTrue(result["warnings"])

    def test_load_kis_config_returns_none(self):
        self.assertIsNone(layer1_session._load_kis_config())


class GdriveUploadOptionalTests(EnvIsolatedTestCase):
    ENV_KEYS = ("MARGIN_TA_GOOGLE_TOKEN",)

    def test_default_token_path(self):
        expected = os.path.expanduser("~/.config/margin-ta/google_token.json")
        self.assertEqual(scan_nightly.google_token_path(), expected)

    def test_token_path_env_override(self):
        custom = str(self.tmp / "tok.json")
        os.environ["MARGIN_TA_GOOGLE_TOKEN"] = custom
        self.assertEqual(scan_nightly.google_token_path(), custom)

    def test_upload_skips_when_token_missing(self):
        missing = str(self.tmp / "no_such_token.json")
        os.environ["MARGIN_TA_GOOGLE_TOKEN"] = missing
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            link = scan_nightly.upload_to_gdrive(str(self.tmp / "results.json"))
        self.assertIsNone(link)
        self.assertIn("MARGIN_TA_GOOGLE_TOKEN", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
