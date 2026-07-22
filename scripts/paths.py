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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_SCRIPT_DIR)


def _writable_repo_subdir(name: str) -> str | None:
    """Return `<repo>/<name>` if we can create/write it, else None."""
    candidate = os.path.join(_REPO_DIR, name)
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate if os.access(candidate, os.W_OK) else None
    except OSError:
        return None


def _resolve(env_var: str, name: str) -> str:
    override = os.environ.get(env_var, "").strip()
    target = os.path.expanduser(override) if override else (
        _writable_repo_subdir(name)
        or os.path.join(os.path.expanduser("~"), ".cache", "margin-ta", name)
    )
    os.makedirs(target, exist_ok=True)
    return target


def data_dir() -> str:
    """Directory for caches and saved analysis snapshots."""
    return _resolve("MARGIN_TA_DATA_DIR", "data")


def charts_dir() -> str:
    """Directory for generated chart PNGs."""
    return _resolve("MARGIN_TA_CHARTS_DIR", "charts")
