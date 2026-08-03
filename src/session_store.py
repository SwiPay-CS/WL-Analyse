"""Working-session persistence: keep the last analysis on disk.

Separate from settings.py's named profiles (config/profiles/, git-versioned,
meant to be reusable across analyses). This is a single unversioned,
gitignored snapshot under data/session/ of "what I was working on" — the
loaded transaction data and the current Konditionen (RateProfile) — so the
last analysis survives an app restart or a closed browser tab. Cleared only
by an explicit reset (Einstellungen -> Reset).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from settings import RateProfile, load_rate_profile

SESSION_DIR = Path(__file__).resolve().parent.parent / "data" / "session"
_DF_NAME = "data.pkl"
_PROFILE_NAME = "_current"
_HOCH_NAME = "hochrechnung.json"


def save_df(df: pd.DataFrame, session_dir: str | Path = SESSION_DIR) -> None:
    d = Path(session_dir)
    d.mkdir(parents=True, exist_ok=True)
    df.to_pickle(d / _DF_NAME)


def load_df(session_dir: str | Path = SESSION_DIR) -> pd.DataFrame | None:
    p = Path(session_dir) / _DF_NAME
    return pd.read_pickle(p) if p.exists() else None


def save_profile(profile: RateProfile, session_dir: str | Path = SESSION_DIR) -> None:
    profile.save(_PROFILE_NAME, profiles_dir=session_dir)


def load_profile(session_dir: str | Path = SESSION_DIR) -> RateProfile | None:
    if not (Path(session_dir) / f"{_PROFILE_NAME}.json").exists():
        return None
    return load_rate_profile(_PROFILE_NAME, profiles_dir=session_dir)


def save_hochrechnung(values: dict[str, float], session_dir: str | Path = SESSION_DIR) -> None:
    """Persist the Hochrechnung Jahresumsatz inputs (Einstellungen -> Merchants
    -> Hochrechnung), keyed by their Streamlit widget key."""
    d = Path(session_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / _HOCH_NAME).write_text(
        json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_hochrechnung(session_dir: str | Path = SESSION_DIR) -> dict[str, float]:
    p = Path(session_dir) / _HOCH_NAME
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def reset(session_dir: str | Path = SESSION_DIR) -> None:
    """Delete the persisted working state (Einstellungen -> Reset)."""
    d = Path(session_dir)
    (d / _DF_NAME).unlink(missing_ok=True)
    (d / f"{_PROFILE_NAME}.json").unlink(missing_ok=True)
    (d / _HOCH_NAME).unlink(missing_ok=True)
