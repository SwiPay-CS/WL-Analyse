"""
Settings layer: brand master list + rate profiles.

Two persistence levels, deliberately kept separate (see CLAUDE.md):

  * Brand master (search codes, display name, type, offerable, order)
    -> config/brands.json, git-versioned, general-purpose.
  * Rate profile (ASF %, Trx-Fee, Mindestgebuehr per type/brand + DCC rate)
    -> per analysis, optionally saved as a named profile under config/profiles/.
    NEVER mixed into the brand master, so customer-specific prices can't
    overwrite the shared master.

The brand master drives two things the engine needs:
  * which raw brand codes are offerable (Offer)
  * which BrandParams apply to each raw code (ParamTable, keyed by brand)

Brand-type model (four price-relevant types + special case):
  debit   -> ASF Debit
  credit  -> ASF Credit
  credit2 -> ASF Credit 2 (Diners/Discover, JCB, Union Pay)
  spezial -> not offerable; SwiPay mirrors Worldline 1:1 (TWINT, WeChat Pay)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from engine import BrandParams, ParamTable, Offer


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CONFIG_DIR    = Path(__file__).resolve().parent.parent / "config"
BRANDS_PATH    = _CONFIG_DIR / "brands.json"
PROFILES_DIR   = _CONFIG_DIR / "profiles"

# Price-relevant brand types. "spezial" is never offerable.
OFFERABLE_TYPES = ("debit", "credit", "credit2")
ALL_TYPES       = OFFERABLE_TYPES + ("spezial",)

# Fallback key for ParamTable rows; unmapped/non-offerable brands resolve here
# but the Offer gate makes the value irrelevant (they mirror Worldline).
_FALLBACK_KEY = "__fallback__"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _norm(code: str) -> str:
    """Case/whitespace-insensitive key for alias matching."""
    return " ".join(str(code).strip().split()).lower()


# ---------------------------------------------------------------------------
# Brand master
# ---------------------------------------------------------------------------

@dataclass
class BrandRecord:
    display_name: str
    type: str                 # debit | credit | credit2 | spezial
    offerable: bool
    order: int
    search_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type not in ALL_TYPES:
            raise ValueError(
                f"Unbekannter Brand-Typ '{self.type}' fuer {self.display_name!r}; "
                f"erlaubt: {ALL_TYPES}"
            )
        # A spezial brand is never offerable by definition.
        if self.type == "spezial":
            self.offerable = False


@dataclass
class BrandMaster:
    brands: list[BrandRecord]

    # -- lookup -------------------------------------------------------------

    def _alias_index(self) -> dict[str, BrandRecord]:
        idx: dict[str, BrandRecord] = {}
        for rec in self.brands:
            for code in rec.search_codes:
                idx[_norm(code)] = rec
        return idx

    def match(self, raw_code: str) -> BrandRecord | None:
        """Resolve a raw brand string from the export to its master record."""
        return self._alias_index().get(_norm(raw_code))

    def type_of(self, raw_code: str) -> str | None:
        rec = self.match(raw_code)
        return rec.type if rec else None

    def offerable_codes(self) -> set[str]:
        """All raw search codes that belong to an offerable brand."""
        out: set[str] = set()
        for rec in self.brands:
            if rec.offerable:
                out.update(rec.search_codes)
        return out

    def sorted_brands(self) -> list[BrandRecord]:
        return sorted(self.brands, key=lambda r: (r.order, r.display_name))

    def offerable_brands(self) -> list[BrandRecord]:
        return [r for r in self.sorted_brands() if r.offerable]

    def display_order(self) -> dict[str, int]:
        """Map every raw search code to its brand's display order."""
        return {c: r.order for r in self.brands for c in r.search_codes}

    def display_name_for(self, raw_code: str) -> str:
        rec = self.match(raw_code)
        return rec.display_name if rec else str(raw_code)

    # -- reconcile ----------------------------------------------------------

    def reconcile(self, data_codes) -> tuple[dict[str, BrandRecord], list[str]]:
        """Split observed brand codes into mapped and unmapped.

        Empty / NaN-like codes are ignored (e.g. trailing blank rows), not
        reported as unmapped. No silent pass-through: unmapped brands must be
        surfaced to the user (Schutzregel 3).
        """
        idx = self._alias_index()
        mapped: dict[str, BrandRecord] = {}
        unmapped: list[str] = []
        seen: set[str] = set()
        for raw in data_codes:
            s = str(raw).strip()
            if not s or s.lower() in ("nan", "none"):
                continue
            key = _norm(s)
            if key in seen:
                continue
            seen.add(key)
            rec = idx.get(key)
            if rec:
                mapped[s] = rec
            else:
                unmapped.append(s)
        return mapped, sorted(unmapped)

    # -- persistence --------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": 1,
            "brands": [
                {
                    "display_name": r.display_name,
                    "type": r.type,
                    "offerable": r.offerable,
                    "order": r.order,
                    "search_codes": list(r.search_codes),
                }
                for r in self.sorted_brands()
            ],
        }

    def save(self, path: str | Path = BRANDS_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def load_brand_master(path: str | Path = BRANDS_PATH) -> BrandMaster:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    brands = [
        BrandRecord(
            display_name=b["display_name"],
            type=b["type"],
            offerable=bool(b.get("offerable", True)),
            order=int(b.get("order", 999)),
            search_codes=list(b.get("search_codes", [])),
        )
        for b in data.get("brands", [])
    ]
    return BrandMaster(brands)


# ---------------------------------------------------------------------------
# Rate profile
# ---------------------------------------------------------------------------

@dataclass
class TypeRate:
    """Three levers per brand type. asf_pct is a fraction (0.0030 = 0.30 %)."""
    asf_pct: float
    trx_fee: float   # CHF per transaction (default 0.01 = 1.0 Rappen)
    min_fee: float   # CHF, floor against ASF + Trx-Fee + ICF + CSF


@dataclass
class RateProfile:
    dcc_pct: float                                  # fraction, e.g. 0.014
    type_rates: dict[str, TypeRate]                 # debit/credit/credit2
    brand_overrides: dict[str, TypeRate] = field(default_factory=dict)  # by display_name
    mode: str = "schnell"                           # schnell | experte

    def to_json(self) -> dict:
        return {
            "dcc_pct": self.dcc_pct,
            "mode": self.mode,
            "type_rates": {k: asdict(v) for k, v in self.type_rates.items()},
            "brand_overrides": {k: asdict(v) for k, v in self.brand_overrides.items()},
        }

    def save(self, name: str, profiles_dir: str | Path = PROFILES_DIR) -> Path:
        d = Path(profiles_dir)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.json"
        p.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return p


def default_rate_profile() -> RateProfile:
    """Placeholder rates. NOT the real SwiPay price list.

    Per CLAUDE.md the real list must be entered before any customer run.
    Trx-Fee default 0.01 CHF (1.0 Rappen). credit2/min values are placeholders.
    """
    return RateProfile(
        dcc_pct=0.014,
        type_rates={
            "debit":   TypeRate(asf_pct=0.0030, trx_fee=0.01, min_fee=0.15),
            "credit":  TypeRate(asf_pct=0.0035, trx_fee=0.01, min_fee=0.20),
            "credit2": TypeRate(asf_pct=0.0050, trx_fee=0.01, min_fee=0.25),
        },
        brand_overrides={},
        mode="schnell",
    )


def load_rate_profile(name: str, profiles_dir: str | Path = PROFILES_DIR) -> RateProfile:
    data = json.loads((Path(profiles_dir) / f"{name}.json").read_text(encoding="utf-8"))
    return RateProfile(
        dcc_pct=float(data["dcc_pct"]),
        type_rates={k: TypeRate(**v) for k, v in data.get("type_rates", {}).items()},
        brand_overrides={k: TypeRate(**v) for k, v in data.get("brand_overrides", {}).items()},
        mode=data.get("mode", "schnell"),
    )


def list_profiles(profiles_dir: str | Path = PROFILES_DIR) -> list[str]:
    d = Path(profiles_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


# ---------------------------------------------------------------------------
# Mode transitions
# ---------------------------------------------------------------------------

def prefill_brand_overrides(
    master: BrandMaster, profile: RateProfile
) -> dict[str, TypeRate]:
    """Schnell -> Experte: seed every offerable brand with its type's values.

    Existing per-brand overrides win over the type default.
    """
    out: dict[str, TypeRate] = {}
    for rec in master.offerable_brands():
        if rec.display_name in profile.brand_overrides:
            tr = profile.brand_overrides[rec.display_name]
        else:
            tr = profile.type_rates.get(rec.type)
        if tr is not None:
            out[rec.display_name] = TypeRate(tr.asf_pct, tr.trx_fee, tr.min_fee)
    return out


def conservative_collapse(master: BrandMaster, profile: RateProfile) -> RateProfile:
    """Experte -> Schnell, conservative (variant a).

    Per type and per variable independently, the HIGHEST value found across that
    type's brands becomes the new type default (deliberately overestimating,
    analogous to the DCC default philosophy). Individual brand overrides are
    KEPT (only hidden), never discarded.
    """
    new_type_rates: dict[str, TypeRate] = {
        k: TypeRate(v.asf_pct, v.trx_fee, v.min_fee) for k, v in profile.type_rates.items()
    }
    for t in OFFERABLE_TYPES:
        names = [r.display_name for r in master.offerable_brands() if r.type == t]
        vals = [profile.brand_overrides[n] for n in names if n in profile.brand_overrides]
        if not vals:
            continue
        base = profile.type_rates.get(t)
        asf = [v.asf_pct for v in vals] + ([base.asf_pct] if base else [])
        trx = [v.trx_fee for v in vals] + ([base.trx_fee] if base else [])
        mnf = [v.min_fee for v in vals] + ([base.min_fee] if base else [])
        new_type_rates[t] = TypeRate(max(asf), max(trx), max(mnf))
    return RateProfile(
        dcc_pct=profile.dcc_pct,
        type_rates=new_type_rates,
        brand_overrides=dict(profile.brand_overrides),  # kept, not discarded
        mode="schnell",
    )


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------

def build_param_table(
    master: BrandMaster, profile: RateProfile, mode: str | None = None
) -> tuple[ParamTable, Offer]:
    """Compose a brand-keyed ParamTable + Offer for the engine.

    mode 'schnell' uses type defaults; 'experte' uses per-brand overrides
    (falling back to the type default for brands without an override).
    Non-offerable / spezial brands are left out of both -> the engine mirrors
    Worldline for them (delta 0).
    """
    mode = mode or profile.mode
    rows: dict[str, BrandParams] = {_FALLBACK_KEY: BrandParams(0.0, 0.0, 0.0)}
    offerable: set[str] = set()

    for rec in master.offerable_brands():
        if mode == "experte" and rec.display_name in profile.brand_overrides:
            tr = profile.brand_overrides[rec.display_name]
        else:
            tr = profile.type_rates.get(rec.type)
        if tr is None:
            continue
        bp = BrandParams(asf_pct=tr.asf_pct, asf_fix=tr.trx_fee, min_fee=tr.min_fee)
        for code in rec.search_codes:
            rows[code] = bp
            offerable.add(code)

    return ParamTable(rows, fallback_key=_FALLBACK_KEY), Offer(frozenset(offerable))
