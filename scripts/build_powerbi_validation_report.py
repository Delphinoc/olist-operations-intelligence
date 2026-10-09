"""Generate reports/powerbi_preparation_validation.md for the Stage A Power BI package.

Usage (after `python scripts/build_model.py` and `python scripts/export_powerbi.py`):
    python scripts/build_powerbi_validation_report.py

All tables are read from the exported files in powerbi/data/, recomputed through the DAX-equivalent evaluators and
compared with independent SQL on the DuckDB model, or taken from the pytest runs executed here. Narrative sections
(review notes, unresolved decisions) are static text written after inspecting those results.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_powerbi as ex  # noqa: E402
import powerbi_dax_equivalents as dax  # noqa: E402

DATA = ROOT / "powerbi" / "data"
OUT = ROOT / "reports" / "powerbi_preparation_validation.md"


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def run_pytest(args: list[str]) -> str:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *args], cwd=ROOT, capture_output=True, text=True)
    last = [ln for ln in r.stdout.strip().splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    return last[-1].strip() if last else f"no summary (exit {r.returncode})"


def fmt(v) -> str:
    if v is None:
        return "blank"
    return f"{v:,.4f}" if isinstance(v, float) and abs(v) < 1 else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}")


def main() -> int:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    con = duckdb.connect(str(ex.DEFAULT_DB), read_only=True)
    model = dax.PowerBIModel(DATA)

    # 1. schemas and row counts
    rows = []
    for name, m in manifest["tables"].items():
        rows.append({"Table": f"`{name}`", "Kind": m["kind"], "Rows": f"{m['rows']:,}", "Columns": len(m["columns"]), "Size (MB)": f"{m['bytes'] / 1e6:.2f}"})
    t_tables = md(pd.DataFrame(rows))
    t_cols = []
    for name, spec in ex.DICT.items():
        t_cols.append(f"**`{name}`**: " + ", ".join(f"`{c}` ({ty})" for c, ty, *_ in spec))
    schemas = "\n\n".join(t_cols)

    # 2. relationships
    rel = pd.DataFrame([{"ID": r["id"], "One side (unique)": f"`{r['one']}`", "Many side": f"`{r['many']}`", "Cardinality": r["cardinality"], "Cross-filter": r["cross_filter"], "Active": "yes" if r["active"] else "no"} for r in manifest["relationships"]])
    ints = {}
    for rid, one_t, one_c, many_t, many_c in ex.RELATIONSHIPS:
        one = pd.read_csv(DATA / f"{one_t}.csv")[one_c].astype(str)
        many = pd.read_csv(DATA / f"{many_t}.csv")[many_c].astype(str)
        ints[rid] = (bool(one.is_unique), int((~many.isin(set(one))).sum()), int(many.isna().sum()))
    integrity = pd.DataFrame([{"ID": k, "One side unique": "yes" if v[0] else "NO", "Orphan foreign keys": v[1], "Blank foreign keys": v[2]} for k, v in ints.items()])

    # 3. anchors through DAX equivalents
    m = dax.measures_orders(model.orders())
    s = dax.measures_seller(model.seller_orders())
    anchors = [
        ("All orders in file", 99_441, m["All orders in file"]), ("Orders in window", 99_092, m["Orders in window"]), ("Delivered orders", 96_203, m["Delivered orders"]),
        ("Late orders", 6_531, m["Late orders"]), ("Severe-late orders", 2_860, m["Severe-late orders"]), ("Seller: eligible orders", 94_931, s["Seller: eligible orders"]),
        ("Reviewed orders (P0)", 95_037, m["Reviewed orders (P0)"]), ("Reviewed orders (P1)", 90_103, m["Reviewed orders (P1)"]),
        ("Late-delivery rate (%)", 6.79, round(m["Late-delivery rate"] * 100, 2)),
    ]
    t_anchor = md(pd.DataFrame([{"Measure": a, "Expected": f"{e:,}", "DAX-equivalent on exported CSVs": f"{g:,}", "Match": "yes" if e == g else "NO"} for a, e, g in anchors]))

    # 4. filter-context scenarios
    scen_rows, mism, compared = [], 0, 0
    top = model.fact_seller_orders.seller_id.value_counts().index[0]
    scenarios = dax.SCENARIOS + [("Top seller by volume", {"seller_id": [top]})]
    for name, ctx in scenarios:
        a, ref = dax.measures_orders(model.orders(**ctx)), dax.sql_reference_orders(con, **ctx)
        sa, sref = dax.measures_seller(model.seller_orders(**ctx)), dax.sql_reference_seller(con, **ctx)
        bad = 0
        for x, y in [(a, ref), (sa, sref)]:
            for k, v in y.items():
                compared += 1
                ok = (x[k] is None and v is None) or (x[k] is not None and v is not None and math.isclose(x[k], v, rel_tol=1e-9, abs_tol=1e-9))
                bad += 0 if ok else 1
        mism += bad
        scen_rows.append({"Filter context": name, "Delivered": fmt(a["Delivered orders"]), "Late-delivery rate (DAX-eq.)": fmt(a["Late-delivery rate"]),
                          "Late-delivery rate (SQL)": fmt(ref["Late-delivery rate"]), "Seller: eligible": fmt(sa["Seller: eligible orders"]),
                          "Seller: late rate (DAX-eq.)": fmt(sa["Seller: late-delivery rate"]), "Seller: late rate (SQL)": fmt(sref["Seller: late-delivery rate"]), "Mismatches": bad})
    t_scen = md(pd.DataFrame(scen_rows))

    # 5. Wilson
    w = []
    for k, n in [(6531, 96203), (1495, 12310), (5, 40), (0, 30), (30, 30)]:
        lo, hi = dax.wilson(k, n)
        ci = binomtest(k, n).proportion_ci(method="wilson")
        w.append({"k": k, "n": n, "DAX formula lower": f"{lo:.6f}", "SciPy lower": f"{ci.low:.6f}", "DAX formula upper": f"{hi:.6f}", "SciPy upper": f"{ci.high:.6f}", "Max abs diff": f"{max(abs(lo - ci.low), abs(hi - ci.high)):.1e}"})
    t_wilson = md(pd.DataFrame(w))

    # 6. snapshots
    snap = pd.read_csv(DATA / "snap_priority_candidates.csv")
    tiers = snap.groupby(["snapshot_level", "tier"]).size().unstack(fill_value=0).reset_index().rename(columns={"snapshot_level": "Level"})
    tiers = tiers[["Level"] + [c for c in ["Investigate", "Watch", "No Signal", "Insufficient Data"] if c in tiers.columns]]
    t_snap = md(tiers)
    lane = snap[snap.snapshot_level == "lane"]
    ne = int(lane.northeast_bound.sum())

    # 7. tests
    t_new = run_pytest(["tests/test_powerbi_export.py"])
    t_all = run_pytest([])

    text = f"""# Power BI Stage A: preparation and validation report

Stage A prepares a reproducible import package and implementation guide for the Power BI dashboard. **No `.pbix` file has been built, no
Power BI Desktop action was performed, and nothing was pushed to GitHub.** The source of truth is the validated DuckDB model and the
existing analysis outputs; no business definition was changed. DAX was not executed in Power BI: measures were validated through
Python equivalents evaluated on the exported files with the relationship semantics applied, and compared with independent SQL (section 4).
That is strong evidence the formulas express the intended logic, but it is not a substitute for checking the same values in Power BI
Desktop after building the model (checklist in `powerbi/README.md`, section 9).

Generated by `scripts/build_powerbi_validation_report.py`.

## 1. Exported package

Format: {manifest['format']}. Parquet was not used because compatibility with the target Power BI version could not be confirmed. Raw Olist
files, customer identifiers, review text and raw timestamps are not exported (guarded by tests).

{t_tables}

Column names, types and descriptions: `powerbi/data/data_dictionary.csv`; typed Power Query script: `powerbi/power_query.m`.

Column lists (type as set in Power Query):

{schemas}

## 2. Relationship design

{md(rel)}

Design rules (each enforced by `tests/test_powerbi_export.py`):

- Six single-direction, one-to-many relationships; every one-side key is unique and non-blank; no many-to-many relationships.
- `dim_state` is shared by both facts because it filters the same attribute (customer destination state). Seller origin is a different role,
  so it has its own table, `dim_origin_state`; a single shared state table would be ambiguous.
- No relationship between `fact_orders` and `fact_seller_orders`, and none between dimensions or involving snapshot tables. The seller fact
  is reconciled to the order fact by `order_id` in tests only.
- Only purchase date is related to the date table. Estimated and actual delivery dates are not exported and cannot become filters.
- The number of directed filter paths between any two tables is at most one (tested), so no path is ambiguous.

Referential integrity of the exported keys:

{md(integrity)}

Slicer behaviour (how a selection reaches each fact) is tabulated in `powerbi/README.md`, section 4. Verified consequences: a seller or
origin-state selection does not change any order-level measure; a customer-state selection filters both facts, and the seller fact is smaller
by the multi-seller orders (RJ: 12,310 delivered vs 12,154 single-seller orders); a date range before 2017-01 returns orders but no
delivery-KPI population.

## 3. Anchor reconciliation (no filter)

{t_anchor}

The unrounded late-delivery rate is 6,531 / 96,203 = {6531 / 96203:.6f}.

## 4. Filter-context validation: DAX-equivalent vs independent SQL

Each scenario evaluates every order and seller measure twice: once on the exported CSVs with relationship semantics (the DAX-equivalent), once
with separate SQL on the DuckDB model. {compared:,} values compared; mismatches (tolerance 1e-9): **{mism}**.

{t_scen}

### Wilson interval formula vs SciPy

{t_wilson}

## 5. Snapshot tables

`snap_priority_candidates`, `snap_tier_counts`, `snap_overlap` and `snap_metadata` hold the validated state, lane and seller priority results
as **fixed-period snapshots** (analysis window 2017-01 to 2018-08, single-seller delivered orders for lanes and sellers). They are disconnected
from the model: slicers do not recalculate them, and the pages state this next to each snapshot visual. Tiers use the 20-order excess threshold,
which is an operational screening policy, not statistical significance (10 and 30 shown as sensitivity). Seller results are secondary
evidence: only 2 of the 8 Investigate sellers persist without the three high-delay months. Excess is never added across state, lane and seller
levels (the overlap table shows the double counting).

{t_snap}

Lane destination check: {ne} lanes are flagged Northeast-bound, defined by destination state only (MA>SP is not).

## 6. Review of the package (bi-dashboard-reviewer and sql-analytics-reviewer checks)

Static review plus the executed checks above. Findings, none of which changed a business definition:

- **Model.** Star schema with conformed date and customer-state dimensions and a role-specific origin dimension; no ambiguity found. The one
  deliberate asymmetry is that seller measures live on the single-seller fact only, so state-level seller counts are lower than delivered orders.
  This is documented, with the RJ example, in the DAX dictionary and README. No critical or major model issue found.
- **SQL (`sql/powerbi/*.sql`).** Row counts equal the model (99,441 and 94,931); keys are unique and joins do not multiply rows (tested by merge row
  counts); flags are coalesced so NULL never drops an order; no `SELECT *` in the export queries. No distinct-count workaround hides a join problem.
- **Measures.** Ratios use `DIVIDE`; seller measures reference only `fact_seller_orders`, order measures never reference it, and snapshot measures
  reference neither fact (all lint-tested). Delivery dates are never used as filters.
- **Presentation.** Pages separate delivered-only KPIs from all-order fulfilment classes. P0 (one review row, {int(m['Reviewed orders (P0)']):,}) is
  the headline review population and P1 (P0 minus reviews created before delivery, {int(m['Reviewed orders (P1)']):,}) the sensitivity view. Review
  timing limits any link between late delivery and score; all findings are associations, not causal effects.
- **Medium/low items.** The distance band cut points are fixed quartiles of the full eligible set and do not move with slicers; the P0 measures
  exclude multi-review orders (reported, not imputed); month labels are text sorted by `month_sort`.

## 7. Tests

- `tests/test_powerbi_export.py`: {t_new}
- Full suite: {t_all}

Coverage: file set and row counts with hashes; no raw tables or personal identifiers; schema versus data dictionary; 0/1 flags; key uniqueness;
relationship cardinality, orphan keys and directed-path uniqueness; date table; flag consistency between the two facts; blank semantics;
snapshot tables against `operational_prioritization.compute_all`; DAX-equivalent versus SQL scenarios; slicer semantics; blank behaviour on empty
selections; Wilson versus SciPy; documentation lint (every measure defined, every column referenced exists, scoping rules, spec consistency);
Power Query script; export determinism.

## 8. Unresolved decisions and limitations

1. **Git tracking of the fact CSVs** (about 36 MB together). Currently untracked. Options: commit them, ignore them and rely on the rebuild
   command, or publish them as a release asset. Not decided; nothing has been committed.
2. **Parquet** variant only after compatibility with the target Power BI version is confirmed.
3. **Refresh.** The export is a one-time snapshot of a static dataset; no refresh schedule is needed unless the data changes.
4. **Scatter-plot error bars** (Page 4 primary visual) depend on the Power BI version; the page spec falls back to showing the intervals in the tooltip and table.
5. **DAX is not executed in Power BI here.** Values must be reconciled in Desktop using the checklist before the pages are treated as verified.
6. Distance is a straight-line ZIP-prefix approximation; the dataset is a 2016 to 2018 sample from one marketplace and cannot support causal claims.
"""
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}  (mismatches={mism}, compared={compared})")
    return 0 if mism == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
