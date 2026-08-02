"""Tests for session_store.py: the working-session snapshot (data + Konditionen)
that survives an app restart until the user resets it."""

import pandas as pd

import session_store
from settings import RateProfile, TypeRate


def _profile() -> RateProfile:
    return RateProfile(
        dcc_pct=0.014,
        type_rates={
            "debit":   TypeRate(0.0030, 0.01, 0.15),
            "credit":  TypeRate(0.0035, 0.01, 0.20),
            "credit2": TypeRate(0.0050, 0.01, 0.25),
        },
    )


def test_load_df_returns_none_when_nothing_saved(tmp_path):
    assert session_store.load_df(tmp_path) is None


def test_save_and_load_df_round_trips(tmp_path):
    df = pd.DataFrame({"brand": ["Visa"], "brutto": [12.5]})
    session_store.save_df(df, tmp_path)
    loaded = session_store.load_df(tmp_path)
    pd.testing.assert_frame_equal(loaded, df)


def test_load_profile_returns_none_when_nothing_saved(tmp_path):
    assert session_store.load_profile(tmp_path) is None


def test_save_and_load_profile_round_trips(tmp_path):
    p = _profile()
    p.brand_overrides["Visa Debit"] = TypeRate(0.0099, 0.02, 0.50)
    p.mode = "experte"
    session_store.save_profile(p, tmp_path)
    loaded = session_store.load_profile(tmp_path)
    assert loaded.dcc_pct == p.dcc_pct
    assert loaded.mode == "experte"
    assert loaded.type_rates["debit"].asf_pct == p.type_rates["debit"].asf_pct
    assert loaded.brand_overrides["Visa Debit"].asf_pct == 0.0099


def test_reset_removes_both_files(tmp_path):
    session_store.save_df(pd.DataFrame({"a": [1]}), tmp_path)
    session_store.save_profile(_profile(), tmp_path)
    session_store.reset(tmp_path)
    assert session_store.load_df(tmp_path) is None
    assert session_store.load_profile(tmp_path) is None


def test_reset_on_empty_dir_does_not_raise(tmp_path):
    session_store.reset(tmp_path)  # nothing to delete — must be a no-op, not an error
