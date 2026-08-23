"""Case store: one customer situation on disk, saveable and exchangeable.

A "Fall" is a customer (the ordering concept) holding named variants
("konservativ", "aggressiv"). The variant file IS the exchange format --
saving writes the JSON, exporting copies it out, importing drops it in. No
SQLite table on purpose: one schema, one code path, nothing that can drift.

Layout (data/cases/, gitignored -- holds customer data):

    <kunde-slug>/
        kunde.json                   name, notiz, angelegt_am
        export/<file>.xlsb           ONCE per customer, not per variant
        varianten/<variante>.json    the payload described below
        berichte/<ts>.pdf            only on explicit request

Deliberately split from config/profiles/ (Vorlagen: git-versioned, shared,
no customer reference) -- see settings.py.

The payload pins its OWN copy of everything a number depends on, including
the brand master and the Hochrechnung values that otherwise live durably in
swipay.db. Those drift: whoever adjusts a Jahresumsatz in six months would
otherwise retroactively change a case that was already presented.

Transaction rows are never written into the payload -- the file is meant to
be handed on, and cardholder data must not travel in a config file. The
payload pins file_name + sha256 instead, so a load can say whether the
export in front of you is the one the case was built on.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import db_groups
import hochrechnung_store as hoch_store
from settings import BrandMaster, BrandRecord, RateProfile, TypeRate
from version import TOOL_VERSION

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = _ROOT / "data" / "cases"
DATA_DIR = _ROOT / "data"

SCHEMA_VERSION = 1

_KUNDE_FILE = "kunde.json"
_VARIANTS = "varianten"
_EXPORT = "export"
_REPORTS = "berichte"

# Reserved customer holding the automatic snapshots taken before a Reset or a
# case switch. Not slugified from user input, so it can never be collided with.
AUTOSAVE_SLUG = "_autosave"
AUTOSAVE_NAME = "Autosave"

CONFIDENTIAL = "SwiPay AG · Vertraulich - nur für autorisierte Empfänger"

EXPORT_SUFFIX = ".swipaycase.json"


class CaseError(Exception):
    """Anything the user needs to see as a plain message."""


class CaseVersionError(CaseError):
    """Payload written by a newer tool than this one."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    """Compact UTC stamp for generated names (Autosave, report files)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


_UMLAUT = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}


def slugify(name: str) -> str:
    """Filesystem-safe slug. Rejects nothing silently: an empty result raises,
    so a mistyped name can never land in an unrelated directory."""
    s = str(name).strip()
    for k, v in _UMLAUT.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    out = []
    for ch in s.lower():
        out.append(ch if ch.isalnum() else "-")
    slug = "-".join(p for p in "".join(out).split("-") if p)
    if not slug:
        raise CaseError(f"Aus «{name}» lässt sich kein Dateiname bilden.")
    return slug[:80]


# ---------------------------------------------------------------------------
# Info records (what the UI lists)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VariantInfo:
    kunde_slug: str
    kunde_name: str
    slug: str
    name: str
    notiz: str
    gespeichert_am: str          # ISO UTC, as written into the payload
    path: Path


@dataclass(frozen=True)
class CustomerInfo:
    slug: str
    name: str
    notiz: str
    angelegt_am: str
    path: Path
    variants: tuple[VariantInfo, ...] = ()
    bytes_used: int = 0
    export_files: tuple[str, ...] = ()

    @property
    def is_autosave(self) -> bool:
        return self.slug == AUTOSAVE_SLUG


# ---------------------------------------------------------------------------
# Diff records (what the user confirms before an import writes anything)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrandDiff:
    """Brand master comparison. config/brands.json is shared and git-versioned,
    so the master wins by default: a stale case must not silently rewrite the
    basis for every future customer. Conflicts are shown, new brands offered."""
    conflicts: list[dict] = field(default_factory=list)   # Brand/Feld/Stammliste/Fall
    new_brands: list[dict] = field(default_factory=list)  # records only the case knows

    @property
    def identical(self) -> bool:
        return not self.conflicts and not self.new_brands


@dataclass(frozen=True)
class DbDiff:
    """What applying the case would change in swipay.db (Gruppen + Hochrechnung)."""
    rows: list[dict] = field(default_factory=list)   # Bereich/Eintrag/Aktuell/Fall

    @property
    def empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class DataMatch:
    """Does the loaded export belong to this case?"""
    state: str                  # "match" | "none" | "mismatch"
    expected: list[dict]
    found: list[dict]

    @property
    def ok(self) -> bool:
        return self.state == "match"


# ---------------------------------------------------------------------------
# Data-file fingerprint
# ---------------------------------------------------------------------------

def resolve_data_files(db_path: str, df: pd.DataFrame | None) -> list[dict]:
    """Which export files the loaded rows come from, via row_keys.

    Works after a restart because the mapping lives in the DB, not in memory.
    """
    if df is None or df.empty or "idempotency_key" not in df.columns:
        return []
    keys = df["idempotency_key"].dropna().astype(str).tolist()
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as con:
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = (
                "SELECT file_hash, COUNT(*) FROM row_keys WHERE idempotency_key IN (%s) "
                "GROUP BY file_hash" % ",".join("?" * len(chunk))
            )
            for h, n in con.execute(q, chunk):
                counts[h] = counts.get(h, 0) + int(n)
        names = dict(
            con.execute("SELECT file_hash, file_name FROM processed_files").fetchall()
        )
    return [
        {"file_name": names.get(h, "unbekannt"), "sha256": h, "rows": n}
        for h, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def match_data(expected: list[dict], found: list[dict]) -> DataMatch:
    """Compare the case's pinned file hashes against what is loaded now."""
    exp = {d.get("sha256") for d in expected if d.get("sha256")}
    fnd = {d.get("sha256") for d in found if d.get("sha256")}
    if not fnd:
        state = "none"
    elif exp and exp == fnd:
        state = "match"
    elif not exp:
        state = "none"
    else:
        state = "mismatch"
    return DataMatch(state=state, expected=list(expected), found=list(found))


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def build_payload(
    *,
    variant_name: str,
    variant_note: str = "",
    profile: RateProfile,
    master: BrandMaster,
    groups: dict[str, list[str]],
    partner_vols: dict[str, float],
    group_vols: dict[str, float],
    auswahl: dict | None = None,
    daten: list[dict] | None = None,
    vorlage_name: str = "",
    vorlage_art: str = "",
) -> dict:
    """Everything a number in this case depends on, as one JSON-ready dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "vertraulich": CONFIDENTIAL,
        "gespeichert_am": _utc(),
        "correlation_id": str(uuid.uuid4()),
        "variante": {"name": str(variant_name), "notiz": str(variant_note)},
        "basis": {"vorlage_name": vorlage_name, "vorlage_art": vorlage_art},
        "konditionen": profile.to_json(),
        "brands": master.to_json(),
        "merchants": {
            "groups": {k: list(v) for k, v in (groups or {}).items()},
            "hochrechnung": {
                "partner": {k: float(v) for k, v in (partner_vols or {}).items()},
                "group": {k: float(v) for k, v in (group_vols or {}).items()},
            },
        },
        "auswahl": dict(auswahl or {}),
        "daten": list(daten or []),
    }


def validate_payload(data: dict) -> dict:
    """Gate every read (own file or imported). Refuses a newer schema instead
    of guessing what an unknown field means."""
    if not isinstance(data, dict):
        raise CaseError("Datei enthält kein Fall-Objekt.")
    v = data.get("schema_version")
    if not isinstance(v, int):
        raise CaseError("Datei hat keine gültige schema_version — kein Fall-Export?")
    if v > SCHEMA_VERSION:
        raise CaseVersionError(
            f"Fall wurde mit einer neueren Version geschrieben "
            f"(schema_version {v}, dieses Tool kennt {SCHEMA_VERSION}). "
            "Bitte das Tool aktualisieren."
        )
    for key in ("konditionen", "brands", "merchants"):
        if key not in data:
            raise CaseError(f"Fall-Datei unvollständig: «{key}» fehlt.")
    return data


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------

def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def list_customers(cases_dir: str | Path = CASES_DIR) -> list[CustomerInfo]:
    """All customers, newest activity first. Autosave sorts last."""
    d = Path(cases_dir)
    if not d.exists():
        return []
    out: list[CustomerInfo] = []
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        kf = sub / _KUNDE_FILE
        meta = _read_json(kf) if kf.exists() else {}
        variants = list_variants(sub)
        out.append(
            CustomerInfo(
                slug=sub.name,
                name=meta.get("name", sub.name),
                notiz=meta.get("notiz", ""),
                angelegt_am=meta.get("angelegt_am", ""),
                path=sub,
                variants=tuple(variants),
                bytes_used=_dir_bytes(sub),
                export_files=tuple(
                    sorted(f.name for f in (sub / _EXPORT).glob("*") if f.is_file())
                ),
            )
        )
    return sorted(out, key=lambda c: (c.is_autosave, c.name.lower()))


def list_variants(kunde_path: str | Path) -> list[VariantInfo]:
    """Variants of one customer, most recently saved first."""
    kp = Path(kunde_path)
    kf = kp / _KUNDE_FILE
    kunde_name = _read_json(kf).get("name", kp.name) if kf.exists() else kp.name
    out: list[VariantInfo] = []
    for f in (kp / _VARIANTS).glob("*.json"):
        try:
            data = _read_json(f)
        except Exception:
            continue
        var = data.get("variante", {})
        out.append(
            VariantInfo(
                kunde_slug=kp.name,
                kunde_name=kunde_name,
                slug=f.stem,
                name=var.get("name", f.stem),
                notiz=var.get("notiz", ""),
                gespeichert_am=data.get("gespeichert_am", ""),
                path=f,
            )
        )
    return sorted(out, key=lambda v: v.gespeichert_am, reverse=True)


def load_variant(
    kunde_slug: str, variant_slug: str, cases_dir: str | Path = CASES_DIR
) -> dict:
    p = Path(cases_dir) / kunde_slug / _VARIANTS / f"{variant_slug}.json"
    if not p.exists():
        raise CaseError(f"Variante «{variant_slug}» nicht gefunden.")
    return validate_payload(_read_json(p))


# ---------------------------------------------------------------------------
# Write side
# ---------------------------------------------------------------------------

def save_variant(
    kunde_name: str,
    payload: dict,
    *,
    cases_dir: str | Path = CASES_DIR,
    kunde_slug: str | None = None,
    kunde_notiz: str | None = None,
    export_src: str | Path | None = None,
) -> VariantInfo:
    """Write one variant, creating the customer directory on first use.

    export_src is copied ONCE per customer (three variants of one customer
    share one 11.9 MB XLSB, not three). Pass None to skip the copy -- that is
    what the Autosave path does: the original still sits in data/, and copying
    12 MB on every Reset would be waste.
    """
    validate_payload(payload)
    d = Path(cases_dir)
    kslug = kunde_slug or slugify(kunde_name)
    kpath = d / kslug
    vslug = slugify(payload.get("variante", {}).get("name", "") or "variante")

    kf = kpath / _KUNDE_FILE
    if kf.exists():
        meta = _read_json(kf)
        if kunde_notiz is not None:
            meta["notiz"] = kunde_notiz
        meta["name"] = kunde_name or meta.get("name", kslug)
    else:
        meta = {
            "name": kunde_name or kslug,
            "notiz": kunde_notiz or "",
            "angelegt_am": _utc(),
        }
    _write_json(kf, meta)

    if export_src:
        src = Path(export_src)
        if src.exists():
            dst = kpath / _EXPORT / src.name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    vpath = kpath / _VARIANTS / f"{vslug}.json"
    _write_json(vpath, payload)
    return VariantInfo(
        kunde_slug=kslug,
        kunde_name=meta["name"],
        slug=vslug,
        name=payload["variante"]["name"],
        notiz=payload["variante"].get("notiz", ""),
        gespeichert_am=payload["gespeichert_am"],
        path=vpath,
    )


def rename_variant(
    kunde_slug: str, variant_slug: str, new_name: str,
    cases_dir: str | Path = CASES_DIR,
) -> VariantInfo:
    p = Path(cases_dir) / kunde_slug / _VARIANTS / f"{variant_slug}.json"
    if not p.exists():
        raise CaseError(f"Variante «{variant_slug}» nicht gefunden.")
    data = validate_payload(_read_json(p))
    new_slug = slugify(new_name)
    target = p.with_name(f"{new_slug}.json")
    if target.exists() and target != p:
        raise CaseError(f"Es gibt bereits eine Variante «{new_name}».")
    data.setdefault("variante", {})["name"] = new_name
    _write_json(target, data)
    if target != p:
        p.unlink()
    kf = Path(cases_dir) / kunde_slug / _KUNDE_FILE
    kname = _read_json(kf).get("name", kunde_slug) if kf.exists() else kunde_slug
    return VariantInfo(kunde_slug, kname, new_slug, new_name,
                       data["variante"].get("notiz", ""),
                       data.get("gespeichert_am", ""), target)


def delete_variant(
    kunde_slug: str, variant_slug: str, cases_dir: str | Path = CASES_DIR
) -> int:
    """Delete one variant. Returns how many variants the customer has left --
    deleting the last one must NOT silently take the export copy and the
    reports with it, so the caller asks separately (delete_customer)."""
    p = Path(cases_dir) / kunde_slug / _VARIANTS / f"{variant_slug}.json"
    p.unlink(missing_ok=True)
    return len(list_variants(Path(cases_dir) / kunde_slug))


def delete_customer(kunde_slug: str, cases_dir: str | Path = CASES_DIR) -> None:
    """Remove a customer directory including export copy and reports."""
    kpath = Path(cases_dir) / kunde_slug
    if not kpath.exists():
        return
    if kpath.resolve().parent != Path(cases_dir).resolve():
        raise CaseError("Unerwarteter Pfad — Löschen abgebrochen.")
    shutil.rmtree(kpath)


def save_report(
    kunde_slug: str, pdf_bytes: bytes, *, cases_dir: str | Path = CASES_DIR,
    label: str = "",
) -> Path:
    """Park a delivered customer PDF with the case (explicit request only)."""
    d = Path(cases_dir) / kunde_slug / _REPORTS
    d.mkdir(parents=True, exist_ok=True)
    name = f"{stamp()}{'-' + slugify(label) if label else ''}.pdf"
    p = d / name
    p.write_bytes(pdf_bytes)
    return p


def find_export_source(daten: list[dict], data_dir: str | Path = DATA_DIR) -> Path | None:
    """Locate the original export for a case, so it can be copied in.

    Uploads land in a temp dir that is gone by then; a file loaded from data/
    is still there. Returns None when nothing is found -- the case is then
    saved without its own copy and says so.
    """
    for entry in daten or []:
        name = entry.get("file_name")
        if not name:
            continue
        p = Path(data_dir) / name
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Apply: diff first, then write
# ---------------------------------------------------------------------------

def profile_from_payload(payload: dict) -> RateProfile:
    d = payload["konditionen"]
    return RateProfile(
        dcc_pct=float(d["dcc_pct"]),
        type_rates={k: TypeRate(**v) for k, v in d.get("type_rates", {}).items()},
        brand_overrides={k: TypeRate(**v) for k, v in d.get("brand_overrides", {}).items()},
        mode=d.get("mode", "schnell"),
    )


def master_from_payload(payload: dict) -> BrandMaster:
    return BrandMaster([
        BrandRecord(
            display_name=b["display_name"],
            type=b["type"],
            offerable=bool(b.get("offerable", True)),
            order=int(b.get("order", 999)),
            search_codes=list(b.get("search_codes", [])),
        )
        for b in payload["brands"].get("brands", [])
    ])


def diff_brands(master: BrandMaster, payload: dict) -> BrandDiff:
    """Compare the shared master against the case's pinned copy."""
    case = master_from_payload(payload)
    cur = {r.display_name: r for r in master.brands}
    conflicts: list[dict] = []
    new_brands: list[dict] = []
    for rec in case.sorted_brands():
        mine = cur.get(rec.display_name)
        if mine is None:
            new_brands.append({
                "Brand": rec.display_name, "Typ": rec.type,
                "Anbietbar": rec.offerable, "Reihenfolge": rec.order,
                "Such-Codes": ", ".join(rec.search_codes),
            })
            continue
        if mine.type != rec.type:
            conflicts.append({"Brand": rec.display_name, "Feld": "Typ",
                              "Stammliste": mine.type, "Fall": rec.type})
        if bool(mine.offerable) != bool(rec.offerable):
            conflicts.append({"Brand": rec.display_name, "Feld": "Anbietbar",
                              "Stammliste": mine.offerable, "Fall": rec.offerable})
        if set(map(str.lower, mine.search_codes)) != set(map(str.lower, rec.search_codes)):
            conflicts.append({"Brand": rec.display_name, "Feld": "Such-Codes",
                              "Stammliste": ", ".join(mine.search_codes),
                              "Fall": ", ".join(rec.search_codes)})
    return BrandDiff(conflicts=conflicts, new_brands=new_brands)


def merge_new_brands(master: BrandMaster, payload: dict, names: list[str]) -> BrandMaster:
    """Additive merge: take only the named brands the master does not know.
    Existing records are never touched -- the master wins on conflict."""
    case = {r.display_name: r for r in master_from_payload(payload).brands}
    have = {r.display_name for r in master.brands}
    add = [case[n] for n in names if n in case and n not in have]
    return BrandMaster(list(master.brands) + add)


def _fmt_chf(v: float) -> str:
    return f"{v:,.2f}".replace(",", "'")


def diff_db(db_path: str, payload: dict) -> DbDiff:
    """What applying this case would change in swipay.db.

    Only groups NAMED IN THE PAYLOAD are reconciled -- other groups belong to
    other customers and are none of this case's business.
    """
    rows: list[dict] = []
    m = payload.get("merchants", {})
    pay_groups = {k: list(v) for k, v in (m.get("groups") or {}).items()}
    pay_p = {k: float(v) for k, v in ((m.get("hochrechnung") or {}).get("partner") or {}).items()}
    pay_g = {k: float(v) for k, v in ((m.get("hochrechnung") or {}).get("group") or {}).items()}

    cur_groups = db_groups.get_groups(db_path)
    for gname, pids in pay_groups.items():
        now = sorted(cur_groups.get(gname, []))
        want = sorted(pids)
        if now != want:
            rows.append({
                "Bereich": "Gruppe", "Eintrag": gname,
                "Aktuell": f"{len(now)} Partner" if now else "nicht vorhanden",
                "Fall": f"{len(want)} Partner",
            })

    cur_p = hoch_store.get_partner_volumes(db_path)
    cur_g = hoch_store.get_group_volumes(db_path)
    for label, cur, pay in (("Hochrechnung Partner", cur_p, pay_p),
                            ("Hochrechnung Gruppe", cur_g, pay_g)):
        for key, val in pay.items():
            old = cur.get(key)
            if old is None or abs(old - val) > 0.005:
                rows.append({
                    "Bereich": label, "Eintrag": key,
                    "Aktuell": _fmt_chf(old) if old is not None else "nichts hinterlegt",
                    "Fall": _fmt_chf(val),
                })
    return DbDiff(rows=rows)


def apply_merchants(db_path: str, payload: dict) -> None:
    """Write the case's groups and Hochrechnung values into swipay.db.

    Reconciles only the groups the payload names: a partner that the case has
    dropped from one of ITS groups gets unassigned, everything else is left
    alone. Every write goes through db_groups / hochrechnung_store, so the
    audit_log entries look the same as a manual edit.
    """
    m = payload.get("merchants", {})
    pay_groups = {k: list(v) for k, v in (m.get("groups") or {}).items()}
    cur_groups = db_groups.get_groups(db_path)
    for gname, pids in pay_groups.items():
        stale = set(cur_groups.get(gname, [])) - set(pids)
        if stale:
            db_groups.unassign_group(db_path, sorted(stale))
        if pids:
            db_groups.assign_group(db_path, gname, list(pids))

    h = m.get("hochrechnung") or {}
    hoch_store.save_volumes(
        db_path,
        {k: float(v) for k, v in (h.get("partner") or {}).items()},
        {k: float(v) for k, v in (h.get("group") or {}).items()},
    )


def _norm_pid(v) -> str:
    """Mirror of ui.pid(): pandas float-casts whole-number Partner-IDs, so the
    export column reads "82046.0" while swipay.db stores "82046". Without this
    every ID would look missing. Kept local rather than importing the
    Streamlit-bound ui module into a pure one.
    """
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def missing_partners(payload: dict, df: pd.DataFrame | None, pid_col: str = "partner_id") -> list[str]:
    """Partner-IDs the case carries that the loaded data does not contain.

    Surfaced, never swallowed: otherwise you compute with a Hochrechnung whose
    merchant is absent from the export (lieber eine Lücke als eine Lüge).
    """
    m = payload.get("merchants", {})
    want: set[str] = set(((m.get("hochrechnung") or {}).get("partner") or {}).keys())
    for pids in (m.get("groups") or {}).values():
        want.update(str(p) for p in pids)
    if not want:
        return []
    if df is None or df.empty or pid_col not in df.columns:
        return sorted(want)
    have = {_norm_pid(x) for x in df[pid_col].dropna()}
    have |= {h.lstrip("0") for h in have}
    return sorted(w for w in want
                  if _norm_pid(w) not in have
                  and _norm_pid(w).lstrip("0") not in have)


# ---------------------------------------------------------------------------
# Autosave
# ---------------------------------------------------------------------------

def autosave(
    reason: str,
    *,
    profile: RateProfile,
    master: BrandMaster,
    db_path: str,
    df: pd.DataFrame | None = None,
    auswahl: dict | None = None,
    cases_dir: str | Path = CASES_DIR,
) -> VariantInfo:
    """Snapshot the current working state before it is thrown away.

    Runs BEFORE a Reset and BEFORE an incoming case overwrites swipay.db --
    so the snapshot holds the OLD Hochrechnung and group values and an
    accidental import stays reversible. No export copy (see save_variant).
    """
    payload = build_payload(
        variant_name=f"{reason} · {stamp()}",
        variant_note="Automatisch gesichert.",
        profile=profile,
        master=master,
        groups=db_groups.get_groups(db_path),
        partner_vols=hoch_store.get_partner_volumes(db_path),
        group_vols=hoch_store.get_group_volumes(db_path),
        auswahl=auswahl,
        daten=resolve_data_files(db_path, df),
    )
    return save_variant(AUTOSAVE_NAME, payload, cases_dir=cases_dir,
                        kunde_slug=AUTOSAVE_SLUG,
                        kunde_notiz="Automatische Sicherungen vor Reset und Fallwechsel.")


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

def export_bytes(payload: dict) -> bytes:
    validate_payload(payload)
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def export_filename(kunde_name: str, payload: dict) -> str:
    return (f"{slugify(kunde_name)}--"
            f"{slugify(payload.get('variante', {}).get('name', 'variante'))}"
            f"{EXPORT_SUFFIX}")


def parse_import(raw: bytes | str) -> dict:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaseError(f"Datei ist kein gültiges JSON: {exc}") from exc
    return validate_payload(data)
