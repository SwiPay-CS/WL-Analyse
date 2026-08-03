"""Tests for hochrechnung_store.py: durable, Partner-ID/Gruppenname-keyed
persistence of Jahresumsatz-Hochrechnung inputs (replaces the old, session-
scoped data/session/hochrechnung.json)."""

from pathlib import Path

from ingest import init_db
import hochrechnung_store as hs


def _db(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    init_db(path)               # audit_log table
    hs.init_hochrechnung_db(path)
    return path


def test_get_volumes_empty_when_nothing_stored(tmp_path):
    db = _db(tmp_path)
    assert hs.get_partner_volumes(db) == {}
    assert hs.get_group_volumes(db) == {}


def test_set_and_get_partner_volume_round_trips(tmp_path):
    db = _db(tmp_path)
    hs.set_volume(db, "partner", "82046", 1_839_242.5)
    assert hs.get_partner_volumes(db) == {"82046": 1_839_242.5}
    assert hs.get_group_volumes(db) == {}


def test_set_and_get_group_volume_round_trips(tmp_path):
    db = _db(tmp_path)
    hs.set_volume(db, "group", "Davos Destinations Org.", 5_339_562.5)
    assert hs.get_group_volumes(db) == {"Davos Destinations Org.": 5_339_562.5}


def test_set_volume_upserts_existing_key(tmp_path):
    db = _db(tmp_path)
    hs.set_volume(db, "partner", "82046", 100.0)
    hs.set_volume(db, "partner", "82046", 200.0)
    assert hs.get_partner_volumes(db) == {"82046": 200.0}


def test_set_volume_zero_or_negative_deletes_record(tmp_path):
    db = _db(tmp_path)
    hs.set_volume(db, "partner", "82046", 100.0)
    hs.set_volume(db, "partner", "82046", 0.0)
    assert hs.get_partner_volumes(db) == {}


def test_set_volume_rejects_unknown_scope(tmp_path):
    db = _db(tmp_path)
    try:
        hs.set_volume(db, "bogus", "x", 1.0)
        assert False, "should have raised"
    except ValueError:
        pass


def test_save_volumes_bulk_upserts_both_maps(tmp_path):
    db = _db(tmp_path)
    hs.save_volumes(
        db,
        partner_vols={"82046": 1_839_242.5, "174723": 368_338.5},
        group_vols={"Davos Destinations Org.": 5_339_562.5},
    )
    assert hs.get_partner_volumes(db) == {"82046": 1_839_242.5, "174723": 368_338.5}
    assert hs.get_group_volumes(db) == {"Davos Destinations Org.": 5_339_562.5}


def test_persists_across_reconnects(tmp_path):
    """Durability is the whole point: a value written in one 'session' must
    still be there in a fresh connection, unlike the old session-JSON that
    was wiped on Reset."""
    db = _db(tmp_path)
    hs.set_volume(db, "partner", "82046", 1_839_242.5)
    # Simulate a completely new process/connection reading the same file.
    assert hs.get_partner_volumes(str(Path(db))) == {"82046": 1_839_242.5}


def test_migrate_from_session_json_maps_widget_keys(tmp_path):
    db = _db(tmp_path)
    legacy = {
        "hoch_vol_s_82046": 1_839_242.5,
        "hoch_vol_s_174723": 368_338.5,
        "hoch_vol_s_nan": 0.0,                              # zero -> skipped
        "hoch_vol_g_Davos Destinations Org.": 5_339_562.5,
        "hoch_vol_g_Hotel Ochsen": 0.0,                     # zero -> skipped
        "hoch_open_g_Hotel Ochsen": True,                   # not a volume key
    }
    n = hs.migrate_from_session_json(db, legacy)
    assert n == 3
    assert hs.get_partner_volumes(db) == {
        "82046": 1_839_242.5, "174723": 368_338.5,
    }
    assert hs.get_group_volumes(db) == {
        "Davos Destinations Org.": 5_339_562.5,
    }


def test_migrate_is_idempotent(tmp_path):
    db = _db(tmp_path)
    legacy = {"hoch_vol_s_82046": 1_839_242.5}
    hs.migrate_from_session_json(db, legacy)
    hs.migrate_from_session_json(db, legacy)
    assert hs.get_partner_volumes(db) == {"82046": 1_839_242.5}
