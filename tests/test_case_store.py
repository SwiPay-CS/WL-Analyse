"""Tests for case_store.py: a customer situation saved, exchanged, re-applied.

The interesting cases are the ones that protect existing work: the master must
survive a stale case, an accidental import must stay reversible, and a case
must never quietly claim it belongs to the export that happens to be loaded.
"""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import case_store as cs
import db_groups
import hochrechnung_store as hs
from ingest import init_db
from settings import BrandMaster, BrandRecord, RateProfile, TypeRate


# ── fixtures ────────────────────────────────────────────────────────────────

def _db(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    init_db(path)
    db_groups.init_groups_db(path)
    hs.init_hochrechnung_db(path)
    return path


def _profile(asf: float = 0.0008) -> RateProfile:
    return RateProfile(
        dcc_pct=0.0185,
        type_rates={
            "debit": TypeRate(asf, 0.01, 0.0),
            "credit": TypeRate(asf * 2, 0.01, 0.0),
            "credit2": TypeRate(asf * 2, 0.01, 0.0),
        },
        brand_overrides={},
        mode="schnell",
    )


def _master() -> BrandMaster:
    return BrandMaster([
        BrandRecord("Visa", "credit", True, 1, ["Visa", "VisaDebit"]),
        BrandRecord("Mastercard", "credit", True, 2, ["Mastercard"]),
        BrandRecord("TWINT", "spezial", False, 9, ["TWINT"]),
    ])


def _payload(**kw) -> dict:
    base = dict(
        variant_name="konservativ",
        profile=_profile(),
        master=_master(),
        groups={"Davos Bergbahnen": ["82046", "82047"]},
        partner_vols={"82046": 3_800_000.0},
        group_vols={},
    )
    base.update(kw)
    return cs.build_payload(**base)


# ── slugs ───────────────────────────────────────────────────────────────────

def test_slugify_handles_umlauts_and_punctuation():
    assert cs.slugify("Davos Klosters Bergbahnen AG") == "davos-klosters-bergbahnen-ag"
    assert cs.slugify("Übergrösse & Co.") == "uebergroesse-co"


def test_slugify_refuses_a_name_that_yields_nothing():
    # An empty slug would land the file in the parent directory.
    with pytest.raises(cs.CaseError):
        cs.slugify("///")


# ── payload / validation ────────────────────────────────────────────────────

def test_payload_carries_version_confidentiality_and_no_transactions():
    p = _payload()
    assert p["schema_version"] == cs.SCHEMA_VERSION
    assert p["tool_version"]
    assert "Vertraulich" in p["vertraulich"]
    # Nothing transaction-shaped may ever be in a file that gets handed on.
    assert "rows" not in p and "transactions" not in p
    assert p["daten"] == []


def test_validate_refuses_a_newer_schema_instead_of_guessing():
    p = _payload()
    p["schema_version"] = cs.SCHEMA_VERSION + 1
    with pytest.raises(cs.CaseVersionError):
        cs.validate_payload(p)


def test_validate_refuses_an_incomplete_file():
    p = _payload()
    del p["konditionen"]
    with pytest.raises(cs.CaseError):
        cs.validate_payload(p)


def test_parse_import_rejects_non_json_and_foreign_json(tmp_path):
    with pytest.raises(cs.CaseError):
        cs.parse_import(b"not json at all")
    with pytest.raises(cs.CaseError):
        cs.parse_import(json.dumps({"hello": "world"}).encode())


# ── save / load / list ──────────────────────────────────────────────────────

def test_roundtrip_preserves_conditions_and_hochrechnung(tmp_path):
    cs.save_variant("Davos Klosters", _payload(), cases_dir=tmp_path)
    back = cs.load_variant("davos-klosters", "konservativ", cases_dir=tmp_path)
    prof = cs.profile_from_payload(back)
    assert prof.type_rates["debit"].asf_pct == pytest.approx(0.0008)
    assert prof.dcc_pct == pytest.approx(0.0185)
    assert back["merchants"]["hochrechnung"]["partner"] == {"82046": 3_800_000.0}
    assert back["merchants"]["groups"] == {"Davos Bergbahnen": ["82046", "82047"]}


def test_variants_share_one_export_copy_per_customer(tmp_path):
    export = tmp_path / "Analyse_Davos.xlsb"
    export.write_bytes(b"x" * 2048)
    cs.save_variant("Davos", _payload(variant_name="a"), cases_dir=tmp_path,
                    export_src=export)
    cs.save_variant("Davos", _payload(variant_name="b"), cases_dir=tmp_path,
                    export_src=export)
    copies = list((tmp_path / "davos" / "export").glob("*"))
    assert len(copies) == 1                       # not one per variant
    cust = [c for c in cs.list_customers(tmp_path) if c.slug == "davos"][0]
    assert len(cust.variants) == 2


def test_list_variants_orders_newest_first_and_reports_save_time(tmp_path):
    cs.save_variant("K", _payload(variant_name="alt"), cases_dir=tmp_path)
    p2 = _payload(variant_name="neu")
    p2["gespeichert_am"] = "2099-01-01T00:00:00+00:00"
    cs.save_variant("K", p2, cases_dir=tmp_path)
    got = cs.list_variants(tmp_path / "k")
    assert [v.name for v in got] == ["neu", "alt"]
    assert got[0].gespeichert_am == "2099-01-01T00:00:00+00:00"


def test_rename_variant_moves_the_file_and_refuses_a_collision(tmp_path):
    cs.save_variant("K", _payload(variant_name="alt"), cases_dir=tmp_path)
    cs.save_variant("K", _payload(variant_name="belegt"), cases_dir=tmp_path)
    cs.rename_variant("k", "alt", "neu", cases_dir=tmp_path)
    assert not (tmp_path / "k" / "varianten" / "alt.json").exists()
    assert cs.load_variant("k", "neu", cases_dir=tmp_path)["variante"]["name"] == "neu"
    with pytest.raises(cs.CaseError):
        cs.rename_variant("k", "neu", "belegt", cases_dir=tmp_path)


def test_deleting_the_last_variant_keeps_export_and_reports(tmp_path):
    export = tmp_path / "Analyse_K.xlsb"
    export.write_bytes(b"y" * 512)
    cs.save_variant("K", _payload(variant_name="nur-eine"), cases_dir=tmp_path,
                    export_src=export)
    cs.save_report("k", b"%PDF-1.4 fake", cases_dir=tmp_path)
    left = cs.delete_variant("k", "nur-eine", cases_dir=tmp_path)
    assert left == 0
    # 11.9 MB of export and the delivered report must not vanish silently.
    assert list((tmp_path / "k" / "export").glob("*"))
    assert list((tmp_path / "k" / cs._REPORTS).glob("*.pdf"))
    cs.delete_customer("k", cases_dir=tmp_path)
    assert not (tmp_path / "k").exists()


# ── brand master: the shared file wins ──────────────────────────────────────

def test_diff_brands_reports_conflicts_and_new_brands():
    payload = _payload(master=BrandMaster([
        BrandRecord("Visa", "debit", True, 1, ["Visa", "VisaDebit"]),   # type differs
        BrandRecord("Mastercard", "credit", True, 2, ["Mastercard"]),   # identical
        BrandRecord("Alipay", "spezial", False, 8, ["Alipay"]),         # only in case
    ]))
    d = cs.diff_brands(_master(), payload)
    assert not d.identical
    assert [c["Feld"] for c in d.conflicts if c["Brand"] == "Visa"] == ["Typ"]
    assert [b["Brand"] for b in d.new_brands] == ["Alipay"]


def test_diff_brands_identical_when_master_matches():
    assert cs.diff_brands(_master(), _payload()).identical


def test_merge_new_brands_is_additive_and_never_overwrites():
    payload = _payload(master=BrandMaster([
        BrandRecord("Visa", "debit", True, 1, ["Visa"]),        # conflict, must be kept
        BrandRecord("Alipay", "spezial", False, 8, ["Alipay"]),
    ]))
    merged = cs.merge_new_brands(_master(), payload, ["Alipay"])
    names = {r.display_name: r for r in merged.brands}
    assert "Alipay" in names
    assert names["Visa"].type == "credit"     # master's value survives


# ── swipay.db: diff before write ────────────────────────────────────────────

def test_diff_db_lists_group_and_hochrechnung_changes(tmp_path):
    db = _db(tmp_path)
    db_groups.assign_group(db, "Davos Bergbahnen", ["82046"])
    hs.set_volume(db, "partner", "82046", 3_500_000.0)
    d = cs.diff_db(db, _payload())
    kinds = {(r["Bereich"], r["Eintrag"]) for r in d.rows}
    assert ("Gruppe", "Davos Bergbahnen") in kinds
    assert ("Hochrechnung Partner", "82046") in kinds
    assert any("3'500'000.00" in str(r["Aktuell"]) for r in d.rows)


def test_diff_db_empty_when_nothing_changes(tmp_path):
    db = _db(tmp_path)
    cs.apply_merchants(db, _payload())
    assert cs.diff_db(db, _payload()).empty


def test_apply_merchants_writes_and_reconciles_only_its_own_groups(tmp_path):
    db = _db(tmp_path)
    db_groups.assign_group(db, "Davos Bergbahnen", ["82046", "99999"])
    db_groups.assign_group(db, "Fremder Kunde", ["70001"])
    hs.set_volume(db, "partner", "70001", 1_000_000.0)

    cs.apply_merchants(db, _payload())

    groups = db_groups.get_groups(db)
    assert sorted(groups["Davos Bergbahnen"]) == ["82046", "82047"]   # 99999 dropped
    assert groups["Fremder Kunde"] == ["70001"]                       # untouched
    vols = hs.get_partner_volumes(db)
    assert vols["82046"] == 3_800_000.0
    assert vols["70001"] == 1_000_000.0                               # untouched


# ── data fingerprint ────────────────────────────────────────────────────────

def _seed_rows(db: str, keys: list[str], file_hash: str, name: str) -> None:
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO row_keys (idempotency_key, file_hash, correlation_id, "
            "ingested_at) VALUES (?,?,?,?)",
            [(k, file_hash, "cid", "2026-01-01T00:00:00+00:00") for k in keys])
        con.execute(
            "INSERT INTO processed_files (file_name, file_hash, rows_added, "
            "correlation_id, processed_at) VALUES (?,?,?,?,?)",
            (name, file_hash, len(keys), "cid", "2026-01-01T00:00:00+00:00"))


def test_resolve_data_files_maps_loaded_rows_back_to_the_export(tmp_path):
    db = _db(tmp_path)
    keys = [f"k{i}" for i in range(1500)]           # crosses the query chunk size
    _seed_rows(db, keys, "hash-a", "Analyse_Davos.xlsb")
    df = pd.DataFrame({"idempotency_key": keys})
    got = cs.resolve_data_files(db, df)
    assert got == [{"file_name": "Analyse_Davos.xlsb", "sha256": "hash-a",
                    "rows": 1500}]


def test_resolve_data_files_empty_without_data(tmp_path):
    db = _db(tmp_path)
    assert cs.resolve_data_files(db, pd.DataFrame()) == []
    assert cs.resolve_data_files(db, None) == []


def test_match_data_distinguishes_match_none_and_mismatch():
    exp = [{"file_name": "a.xlsb", "sha256": "h1", "rows": 10}]
    assert cs.match_data(exp, exp).state == "match"
    assert cs.match_data(exp, []).state == "none"
    assert cs.match_data(
        exp, [{"file_name": "b.xlsb", "sha256": "h2", "rows": 9}]).state == "mismatch"


def test_missing_partners_surfaces_ids_absent_from_the_data():
    payload = _payload(partner_vols={"82046": 1.0, "70999": 2.0},
                       groups={"G": ["82046", "60123"]})
    df = pd.DataFrame({"partner_id": ["82046", "82047"]})
    assert cs.missing_partners(payload, df) == ["60123", "70999"]


def test_missing_partners_tolerates_leading_zeros():
    payload = _payload(partner_vols={"082046": 1.0}, groups={})
    df = pd.DataFrame({"partner_id": ["82046"]})
    assert cs.missing_partners(payload, df) == []


# ── autosave: an accidental import stays reversible ─────────────────────────

def test_autosave_captures_the_db_state_before_an_import_overwrites_it(tmp_path):
    db = _db(tmp_path)
    db_groups.assign_group(db, "Davos Bergbahnen", ["82046"])
    hs.set_volume(db, "partner", "82046", 3_500_000.0)

    snap = cs.autosave("Vor Fallwechsel", profile=_profile(0.003),
                       master=_master(), db_path=db, cases_dir=tmp_path)
    cs.apply_merchants(db, _payload())          # the import writes 3.8 Mio

    assert hs.get_partner_volumes(db)["82046"] == 3_800_000.0
    saved = cs.load_variant(cs.AUTOSAVE_SLUG, snap.slug, cases_dir=tmp_path)
    # The snapshot must hold the OLD value, else the import is irreversible.
    assert saved["merchants"]["hochrechnung"]["partner"] == {"82046": 3_500_000.0}
    assert cs.profile_from_payload(saved).type_rates["debit"].asf_pct == 0.003


def test_autosave_customer_sorts_last_and_is_flagged(tmp_path):
    db = _db(tmp_path)
    cs.save_variant("Aaa Kunde", _payload(), cases_dir=tmp_path)
    cs.autosave("Vor Reset", profile=_profile(), master=_master(),
                db_path=db, cases_dir=tmp_path)
    listed = cs.list_customers(tmp_path)
    assert listed[-1].is_autosave
    assert not listed[0].is_autosave


# ── export / import ─────────────────────────────────────────────────────────

def test_export_import_roundtrip_through_bytes(tmp_path):
    p = _payload()
    raw = cs.export_bytes(p)
    assert cs.parse_import(raw)["merchants"] == p["merchants"]
    assert cs.export_filename("Davos Klosters", p) == (
        "davos-klosters--konservativ" + cs.EXPORT_SUFFIX)


def test_find_export_source_locates_the_original_and_tolerates_absence(tmp_path):
    (tmp_path / "Analyse_Davos.xlsb").write_bytes(b"z")
    daten = [{"file_name": "Analyse_Davos.xlsb", "sha256": "h", "rows": 1}]
    assert cs.find_export_source(daten, data_dir=tmp_path).name == "Analyse_Davos.xlsb"
    assert cs.find_export_source(
        [{"file_name": "weg.xlsb"}], data_dir=tmp_path) is None
    assert cs.find_export_source([], data_dir=tmp_path) is None


def test_missing_partners_handles_float_cast_ids_from_the_export():
    # The Worldline export delivers partner_id as float64, so the column reads
    # "174723.0" while swipay.db stores "174723". Comparing raw would report
    # every single stored partner as missing.
    payload = _payload(partner_vols={"174723": 1.0, "82046": 2.0},
                       groups={"G": ["297048"]})
    df = pd.DataFrame({"partner_id": [174723.0, 82046.0, 297048.0]})
    assert cs.missing_partners(payload, df) == []
    df2 = pd.DataFrame({"partner_id": [174723.0]})
    assert cs.missing_partners(payload, df2) == ["297048", "82046"]
