"""Where margin-ta writes its caches, snapshots and charts.

Priority:
  1. MARGIN_TA_DATA_DIR / MARGIN_TA_CHARTS_DIR (explicit override)
  2. `<repo>/data` and `<repo>/charts` when running from a source checkout and
     that location is writable (keeps the familiar layout for clone-&-run users)
  3. ~/.cache/margin-ta/{data,charts} — used for `pip install`ed copies, where
     the package lives inside site-packages and must not be written to.
"""
from __future__ import annotations

import os
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_SCRIPT_DIR)


def _usable(candidate: str) -> str | None:
    """Return `candidate` if it can be created and written to, else None."""
    try:
        os.makedirs(candidate, exist_ok=True)
    except OSError:
        return None
    return candidate if os.access(candidate, os.W_OK) else None


def _resolve(env_var: str, name: str) -> str:
    """First writable candidate wins; never raises.

    Callers import this at module load, and the process may run under a
    sandbox that mounts $HOME (and the checkout) read-only — e.g. a systemd
    unit with ProtectHome=read-only. Falling through to a temp dir keeps the
    pipeline usable instead of failing at import time; only cached/derived
    data lives here, so a throwaway location is acceptable.
    """
    override = os.environ.get(env_var, "").strip()
    if override:
        resolved = _usable(os.path.expanduser(override))
        if resolved:
            return resolved

    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    for candidate in (
        os.path.join(_REPO_DIR, name),
        os.path.join(os.path.expanduser(xdg_cache), "margin-ta", name) if xdg_cache else None,
        os.path.join(os.path.expanduser("~"), ".cache", "margin-ta", name),
        os.path.join(tempfile.gettempdir(), "margin-ta", name),
    ):
        if candidate:
            resolved = _usable(candidate)
            if resolved:
                return resolved

    # Last resort: a private temp dir, guaranteed writable for this process.
    return tempfile.mkdtemp(prefix=f"margin-ta-{name}-")


def data_dir() -> str:
    """Directory for caches and saved analysis snapshots."""
    return _resolve("MARGIN_TA_DATA_DIR", "data")


def charts_dir() -> str:
    """Directory for generated chart PNGs."""
    return _resolve("MARGIN_TA_CHARTS_DIR", "charts")
