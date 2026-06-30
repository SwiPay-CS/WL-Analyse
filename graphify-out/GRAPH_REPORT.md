# Graph Report - .  (2026-06-30)

## Corpus Check
- Corpus is ~13,465 words - fits in a single context window. You may not need a graph.

## Summary
- 296 nodes · 560 edges · 17 communities (10 shown, 7 thin omitted)
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 95 edges (avg confidence: 0.74)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Engine Core & Projection|Engine Core & Projection]]
- [[_COMMUNITY_Settings & Brand Master|Settings & Brand Master]]
- [[_COMMUNITY_Ingest & Worldline Loader|Ingest & Worldline Loader]]
- [[_COMMUNITY_UI Design System & Charts|UI Design System & Charts]]
- [[_COMMUNITY_Streamlit App & Partner Groups|Streamlit App & Partner Groups]]
- [[_COMMUNITY_Report Generation (PDFCSV)|Report Generation (PDF/CSV)]]
- [[_COMMUNITY_Locked Fee-Model Decisions|Locked Fee-Model Decisions]]
- [[_COMMUNITY_Davos Validation Anchor|Davos Validation Anchor]]
- [[_COMMUNITY_SwiPay Logo Brand Mark|SwiPay Logo Brand Mark]]
- [[_COMMUNITY_Logo Asset & Fallback|Logo Asset & Fallback]]
- [[_COMMUNITY_Locked Build Order|Locked Build Order]]
- [[_COMMUNITY_Non-Offerable Brands Rule|Non-Offerable Brands Rule]]
- [[_COMMUNITY_Partner-ID Grouping Rule|Partner-ID Grouping Rule]]
- [[_COMMUNITY_Projection Anchor Rule|Projection Anchor Rule]]
- [[_COMMUNITY_Security No PANPII|Security: No PAN/PII]]
- [[_COMMUNITY_Versioning Scheme|Versioning Scheme]]
- [[_COMMUNITY_Project Root|Project Root]]

## God Nodes (most connected - your core abstractions)
1. `BrandMaster` - 21 edges
2. `ingest_files()` - 18 edges
3. `Offer` - 17 edges
4. `ParamTable` - 16 edges
5. `BrandRecord` - 16 edges
6. `page_praesentation()` - 15 edges
7. `run_comparison()` - 13 edges
8. `_SwiPayPDF` - 13 edges
9. `TypeRate` - 13 edges
10. `RateProfile` - 12 edges

## Surprising Connections (you probably didn't know these)
- `validate.py` --semantically_similar_to--> `validate.py Sanity Check`  [INFERRED] [semantically similar]
  README.md → CLAUDE.md
- `page_praesentation()` --calls--> `run_comparison()`  [INFERRED]
  app.py → src/pipeline.py
- `page_praesentation()` --calls--> `project_tier_b()`  [INFERRED]
  app.py → src/projection.py
- `page_praesentation()` --calls--> `build_csv()`  [INFERRED]
  app.py → src/reporter.py
- `page_praesentation()` --calls--> `build_pdf()`  [INFERRED]
  app.py → src/reporter.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Engine Core Modules (calculation, loader, pipeline)** — readme_engine_py, readme_loader_py, readme_pipeline_py [EXTRACTED 1.00]
- **SwiPay Fee Model (composition, ASF lever, floor)** — claude_fee_composition, claude_asf_variable_lever, claude_floor_logic, claude_dcc_logic [EXTRACTED 1.00]

## Communities (17 total, 7 thin omitted)

### Community 0 - "Engine Core & Projection"
Cohesion: 0.05
Nodes (61): Enum, BrandParams, FeeResult, Offer, ParamTable, SwiPay Worldline-Konditionenvergleich - Engine-Kern (framework-agnostisch).  Rei, Worldline-Vergleichsbasis aus den Ist-Daten. (fee_total, cashback, net)., SwiPay-Konditionen je Brand (Brand-Typ-Modell).      asf_pct  ASF-Anteil vom Bru (+53 more)

### Community 1 - "Settings & Brand Master"
Cohesion: 0.09
Nodes (37): page_einstellungen(), Path, BrandMaster, BrandRecord, build_param_table(), conservative_collapse(), default_rate_profile(), list_profiles() (+29 more)

### Community 2 - "Ingest & Worldline Loader"
Cohesion: 0.07
Nodes (40): Connection, _audit(), _detect_fanout(), _file_hash(), ingest_files(), IngestReport, init_db(), _known_keys() (+32 more)

### Community 3 - "UI Design System & Charts"
Cohesion: 0.09
Nodes (34): Axis, Chart, _base(), chart_dcc_compare(), chart_dcc_potential(), chart_fees_compare(), chart_hist(), chart_monthly() (+26 more)

### Community 4 - "Streamlit App & Partner Groups"
Cohesion: 0.14
Nodes (27): _apply_period(), _derive(), _ensure_month(), _hist(), _monthly(), _months(), page_partner(), page_praesentation() (+19 more)

### Community 5 - "Report Generation (PDF/CSV)"
Cohesion: 0.14
Nodes (21): FPDF, _big_kpi(), build_csv(), build_pdf(), _chf(), _divider(), _flag(), _kv() (+13 more)

### Community 6 - "Locked Fee-Model Decisions"
Cohesion: 0.19
Nodes (13): ASF Default Placeholders, ASF as Single Variable Lever, DCC Logic (global SwiPay rate, real payback column), Fee Composition (ASF + ICF + CSF), Floor on ASF+ICF+CSF per Brand/Category, Multi-File Ingest Three-Stage Dedup, WL Compare Tool, dcc_cashback_pct global parameter (+5 more)

### Community 7 - "Davos Validation Anchor"
Cohesion: 0.40
Nodes (6): Davos Dataset Validation Anchor, Per-Transaction Calculation Principle, validate.py Sanity Check, Worldline Fee Basis (raw signed Gebuehren column), data/ directory (gitignored input), validate.py

### Community 8 - "SwiPay Logo Brand Mark"
Cohesion: 0.50
Nodes (4): SwiPay Logo (SVG Brand Mark), SwiPay Claim (Tagline Text), SwiPay Signet (Geometric Mark), SwiPay Wordmark

### Community 9 - "Logo Asset & Fallback"
Cohesion: 1.00
Nodes (3): Hexagon Wordmark Fallback, SwiPay Logo Asset, src/ui.py sidebar_brand()

## Knowledge Gaps
- **7 isolated node(s):** `swipay-wl-compare`, `WL Compare Tool`, `Versioning Scheme (Major.Minor.Patch.Hotfix)`, `data/ directory (gitignored input)`, `SwiPay Signet (Geometric Mark)` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `page_einstellungen()` connect `Settings & Brand Master` to `Ingest & Worldline Loader`, `Streamlit App & Partner Groups`?**
  _High betweenness centrality (0.098) - this node is a cross-community bridge._
- **Why does `BrandMaster` connect `Settings & Brand Master` to `Engine Core & Projection`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Why does `ingest_files()` connect `Ingest & Worldline Loader` to `Settings & Brand Master`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `BrandMaster` (e.g. with `page_einstellungen()` and `BrandParams`) actually correct?**
  _`BrandMaster` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `ingest_files()` (e.g. with `page_einstellungen()` and `Path`) actually correct?**
  _`ingest_files()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `Offer` (e.g. with `CoverageLabel` and `CoverageTier`) actually correct?**
  _`Offer` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `ParamTable` (e.g. with `CoverageLabel` and `CoverageTier`) actually correct?**
  _`ParamTable` has 8 INFERRED edges - model-reasoned connections that need verification._