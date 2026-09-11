"""Tests for the settings layer (settings.py): brand master, alias matching,
reconcile, engine wiring, and the schnell/experte mode transitions."""

import pytest

import settings as s
from settings import (
    BrandMaster,
    BrandRecord,
    RateProfile,
    TypeRate,
    build_param_table,
    conservative_collapse,
    default_rate_profile,
    load_brand_master,
    prefill_brand_overrides,
)


def _master() -> BrandMaster:
    return BrandMaster([
        BrandRecord("Visa Debit", "debit", True, 10, ["VisaDebit", "DebitVisa"]),
        BrandRecord("Mastercard", "credit", True, 20, ["Mastercard", "MasterCard"]),
        BrandRecord("Union Pay", "credit2", True, 30, ["Union Pay", "China Union Pay"]),
        BrandRecord("TWINT", "spezial", False, 99, ["TWINT"]),
    ])


def _profile() -> RateProfile:
    return RateProfile(
        dcc_pct=0.014,
        type_rates={
            "debit":   TypeRate(0.0030, 0.01, 0.15),
            "credit":  TypeRate(0.0035, 0.01, 0.20),
            "credit2": TypeRate(0.0050, 0.01, 0.25),
        },
    )


# --- alias matching ---------------------------------------------------------

def test_alias_matching_is_case_and_space_insensitive():
    m = _master()
    assert m.match("VisaDebit").display_name == "Visa Debit"
    assert m.match("debitvisa").display_name == "Visa Debit"   # case
    assert m.match("  MasterCard ").display_name == "Mastercard"  # space + alias
    assert m.match("China Union Pay").type == "credit2"
    assert m.match("Amex") is None


def test_spezial_brand_forced_non_offerable():
    rec = BrandRecord("TWINT", "spezial", True, 1, ["TWINT"])  # offerable=True ignored
    assert rec.offerable is False


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        BrandRecord("Foo", "premium", True, 1, ["Foo"])


# --- reconcile --------------------------------------------------------------

def test_reconcile_splits_mapped_and_unmapped():
    m = _master()
    mapped, unmapped = m.reconcile(["VisaDebit", "Mastercard", "Amex", "", "nan", None])
    assert set(mapped) == {"VisaDebit", "Mastercard"}
    assert unmapped == ["Amex"]   # blanks/nan ignored, not reported


# --- engine wiring ----------------------------------------------------------

def test_build_param_table_offerable_and_special():
    m, p = _master(), _profile()
    table, offer = build_param_table(m, p, mode="schnell")
    # offerable codes include all aliases of offerable brands, exclude spezial
    assert offer.is_offerable("VisaDebit")
    assert offer.is_offerable("China Union Pay")
    assert not offer.is_offerable("TWINT")
    # debit brand resolves to the debit type rate (asf_fix == trx_fee)
    bp = table.resolve("VisaDebit")
    assert bp.asf_pct == pytest.approx(0.0030)
    assert bp.asf_fix == pytest.approx(0.01)
    assert bp.min_fee == pytest.approx(0.15)
    # credit2 brand resolves to the credit2 rate
    assert table.resolve("Union Pay").asf_pct == pytest.approx(0.0050)


def test_expert_override_applies_only_in_expert_mode():
    m, p = _master(), _profile()
    p.brand_overrides["Visa Debit"] = TypeRate(0.0099, 0.02, 0.50)
    schnell, _ = build_param_table(m, p, mode="schnell")
    experte, _ = build_param_table(m, p, mode="experte")
    assert schnell.resolve("VisaDebit").asf_pct == pytest.approx(0.0030)   # type default
    assert experte.resolve("VisaDebit").asf_pct == pytest.approx(0.0099)   # override


# --- mode transitions -------------------------------------------------------

def test_prefill_seeds_every_offerable_brand_from_type():
    m, p = _master(), _profile()
    seeded = prefill_brand_overrides(m, p)
    assert set(seeded) == {"Visa Debit", "Mastercard", "Union Pay"}  # no TWINT
    assert seeded["Visa Debit"].asf_pct == pytest.approx(0.0030)


def test_conservative_collapse_takes_max_per_variable():
    m, p = _master(), _profile()
    # Two debit-ish? Only one debit brand here; add a second for the max test.
    m.brands.append(BrandRecord("Maestro", "debit", True, 15, ["Maestro"]))
    p.brand_overrides = {
        "Visa Debit": TypeRate(0.0040, 0.01, 0.10),
        "Maestro":    TypeRate(0.0020, 0.03, 0.30),
    }
    collapsed = conservative_collapse(m, p)
    # per-variable max across the debit brands (and the existing type default)
    debit = collapsed.type_rates["debit"]
    assert debit.asf_pct == pytest.approx(0.0040)   # max(0.0040, 0.0020, 0.0030)
    assert debit.trx_fee == pytest.approx(0.03)     # max(0.01, 0.03, 0.01)
    assert debit.min_fee == pytest.approx(0.30)     # max(0.10, 0.30, 0.15)
    assert collapsed.mode == "schnell"
    # overrides are kept, not discarded
    assert "Visa Debit" in collapsed.brand_overrides


# --- shipped master ---------------------------------------------------------

def test_shipped_brand_master_loads_and_covers_known_brands():
    m = load_brand_master()
    for code in ["VisaDebit", "Debit Mastercard", "Mastercard", "Visa",
                 "Maestro", "Maestro-CH", "V PAY", "Diners/Discover", "Union Pay"]:
        assert m.match(code) is not None, f"{code} fehlt in der Stammliste"
    assert m.match("TWINT").offerable is False


# ── Vorlagen (templates) ────────────────────────────────────────────────────
# A Vorlage is a reusable price sheet with no customer reference: the SwiPay
# standard list, an association rate, a framework contract. Separate from a
# Fall (case_store.py) on purpose.

def _tmpl_profile() -> s.RateProfile:
    return s.RateProfile(
        dcc_pct=0.0185,
        type_rates={
            "debit": s.TypeRate(0.0008, 0.01, 0.0),
            "credit": s.TypeRate(0.0016, 0.01, 0.0),
            "credit2": s.TypeRate(0.0016, 0.01, 0.0),
        },
    )


def test_template_roundtrip_keeps_rates_and_metadata(tmp_path):
    s.save_template(_tmpl_profile(), "SwiPay Standard 2026", "standard",
                    "Echtes Preisblatt", profiles_dir=tmp_path)
    prof, meta = s.load_template("SwiPay Standard 2026", profiles_dir=tmp_path)
    assert prof.type_rates["debit"].asf_pct == 0.0008
    assert prof.dcc_pct == 0.0185
    assert meta.art == "standard"
    assert meta.art_label == "Standardkonditionen"
    assert meta.notiz == "Echtes Preisblatt"
    assert meta.updated_at            # stamped on save


def test_template_rejects_unknown_art_and_empty_name(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        s.save_template(_tmpl_profile(), "X", "hausintern", profiles_dir=tmp_path)
    with pytest.raises(ValueError):
        s.save_template(_tmpl_profile(), "   ", "standard", profiles_dir=tmp_path)


def test_list_templates_groups_by_art_then_name(tmp_path):
    s.save_template(_tmpl_profile(), "Zebra Rahmen", "rahmenvertrag",
                    profiles_dir=tmp_path)
    s.save_template(_tmpl_profile(), "Hotellerie", "verband", profiles_dir=tmp_path)
    s.save_template(_tmpl_profile(), "Standard 2026", "standard", profiles_dir=tmp_path)
    assert [m.name for m in s.list_templates(tmp_path)] == [
        "Standard 2026", "Hotellerie", "Zebra Rahmen"]


def test_a_plain_rate_profile_file_still_loads_as_a_template(tmp_path):
    # Files written before the metadata fields existed must not break the list.
    _tmpl_profile().save("alt", profiles_dir=tmp_path)
    metas = s.list_templates(tmp_path)
    assert [m.name for m in metas] == ["alt"]
    assert metas[0].art == "standard"
    prof, meta = s.load_template("alt", profiles_dir=tmp_path)
    assert prof.dcc_pct == 0.0185
    assert meta.updated_at == ""


def test_delete_template_is_idempotent(tmp_path):
    s.save_template(_tmpl_profile(), "weg", "standard", profiles_dir=tmp_path)
    s.delete_template("weg", profiles_dir=tmp_path)
    s.delete_template("weg", profiles_dir=tmp_path)
    assert s.list_templates(tmp_path) == []


def test_update_template_meta_keeps_the_rates(tmp_path):
    # The rates are the expensive part -- typed by hand from a price sheet.
    s.save_template(_tmpl_profile(), "Pool Alpin", "verband", "2025",
                    profiles_dir=tmp_path)
    s.update_template_meta("Pool Alpin", art="rahmenvertrag", notiz="2026",
                           profiles_dir=tmp_path)
    prof, meta = s.load_template("Pool Alpin", profiles_dir=tmp_path)
    assert prof.type_rates["debit"].asf_pct == 0.0008
    assert prof.type_rates["credit"].asf_pct == 0.0016
    assert prof.dcc_pct == 0.0185
    assert meta.art == "rahmenvertrag"
    assert meta.notiz == "2026"


def test_update_template_meta_renames_the_file(tmp_path):
    s.save_template(_tmpl_profile(), "Alt", "standard", profiles_dir=tmp_path)
    s.update_template_meta("Alt", new_name="Neu", profiles_dir=tmp_path)
    assert not (tmp_path / "Alt.json").exists()
    assert [m.name for m in s.list_templates(tmp_path)] == ["Neu"]
    prof, _ = s.load_template("Neu", profiles_dir=tmp_path)
    assert prof.type_rates["debit"].asf_pct == 0.0008


def test_rename_refuses_to_swallow_an_existing_template(tmp_path):
    import pytest
    s.save_template(_tmpl_profile(), "Eins", "standard", profiles_dir=tmp_path)
    s.save_template(_tmpl_profile(), "Zwei", "verband", profiles_dir=tmp_path)
    with pytest.raises(ValueError):
        s.update_template_meta("Eins", new_name="Zwei", profiles_dir=tmp_path)
    # Both survive untouched.
    assert sorted(m.name for m in s.list_templates(tmp_path)) == ["Eins", "Zwei"]
    assert s.load_template("Zwei", profiles_dir=tmp_path)[1].art == "verband"


def test_update_template_meta_touches_updated_at_and_validates(tmp_path):
    import pytest
    p = s.save_template(_tmpl_profile(), "X", "standard", profiles_dir=tmp_path)
    before = s.load_template("X", profiles_dir=tmp_path)[1].updated_at
    s.update_template_meta("X", notiz="neu", profiles_dir=tmp_path)
    assert s.load_template("X", profiles_dir=tmp_path)[1].updated_at > before
    with pytest.raises(ValueError):
        s.update_template_meta("X", art="hausintern", profiles_dir=tmp_path)
    with pytest.raises(ValueError):
        s.update_template_meta("gibt-es-nicht", notiz="x", profiles_dir=tmp_path)
    assert p.exists()


def test_overwriting_a_template_replaces_only_the_rates(tmp_path):
    # The "save the current conditions into this template" path.
    s.save_template(_tmpl_profile(), "Pool Alpin", "verband", "2025",
                    profiles_dir=tmp_path)
    teurer = s.RateProfile(
        dcc_pct=0.014,
        type_rates={
            "debit": s.TypeRate(0.0030, 0.01, 0.15),
            "credit": s.TypeRate(0.0035, 0.01, 0.20),
            "credit2": s.TypeRate(0.0050, 0.01, 0.25),
        },
    )
    s.save_template(teurer, "Pool Alpin", "verband", "2025", profiles_dir=tmp_path)
    prof, meta = s.load_template("Pool Alpin", profiles_dir=tmp_path)
    assert prof.type_rates["debit"].asf_pct == 0.0030
    assert (meta.art, meta.notiz) == ("verband", "2025")
    assert len(s.list_templates(tmp_path)) == 1      # no second file


def test_template_name_cannot_escape_the_directory(tmp_path):
    import pytest
    for bad in ("../boese", "a/b", ".versteckt"):
        with pytest.raises(ValueError):
            s.save_template(_tmpl_profile(), bad, "standard", profiles_dir=tmp_path)
    s.save_template(_tmpl_profile(), "Gut", "standard", profiles_dir=tmp_path)
    with pytest.raises(ValueError):
        s.update_template_meta("Gut", new_name="../weg", profiles_dir=tmp_path)
    assert [m.name for m in s.list_templates(tmp_path)] == ["Gut"]
