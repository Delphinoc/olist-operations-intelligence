"""Export the Power BI import package (CSV) from the validated DuckDB model.

Usage (project root, after `python scripts/build_model.py`):
    python scripts/export_powerbi.py [--db data/processed/olist_model.duckdb] [--out powerbi/data]

Writes ten CSV files, `manifest.json` (row counts, sizes, SHA-256, column types) and `data_dictionary.csv`:
  facts      fact_orders (order grain, 99,441 rows), fact_seller_orders (eligible single-seller orders, 94,931)
  dimensions dim_date, dim_state, dim_origin_state, dim_seller
  snapshots  snap_priority_candidates, snap_tier_counts, snap_overlap, snap_metadata  (FIXED-PERIOD outputs of the
             prioritization workstream; disconnected from every slicer; never recalculated in Power BI)
CSV (UTF-8, comma-separated, ISO dates, '.' decimals, booleans as 0/1, empty = missing) is used rather than Parquet
because Parquet support in Power BI Desktop could not be confirmed in this environment. Raw source CSVs are not exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import operational_prioritization as op  # noqa: E402

SQL_DIR = ROOT / "sql" / "powerbi"
DEFAULT_DB = ROOT / "data" / "processed" / "olist_model.duckdb"
DEFAULT_OUT = ROOT / "powerbi" / "data"
DISTANCE_SQL = ROOT / "sql" / "analysis" / "geography" / "distance_quartiles.sql"
MODEL_TABLES = ["fact_orders", "fact_seller_orders", "dim_date", "dim_state", "dim_origin_state", "dim_seller"]
SNAPSHOT_TABLES = ["snap_priority_candidates", "snap_tier_counts", "snap_overlap", "snap_metadata"]
TIER_SORT = {"Investigate": 1, "Watch": 2, "No Signal": 3, "Insufficient Data": 4}

# Relationships of the semantic model: (id, one-side table, one-side column, many-side table, many-side column).
# All are one-to-many, single direction (dimension filters fact), active. There are NO relationships between the two fact
# tables, none between dimensions, none involving snapshot tables, and no bidirectional filtering.
RELATIONSHIPS = [
    ("R1", "dim_date", "date_key", "fact_orders", "purchase_date"),
    ("R2", "dim_date", "date_key", "fact_seller_orders", "purchase_date"),
    ("R3", "dim_state", "state_code", "fact_orders", "customer_state"),
    ("R4", "dim_state", "state_code", "fact_seller_orders", "customer_state"),
    ("R5", "dim_origin_state", "origin_state_code", "fact_seller_orders", "seller_state"),
    ("R6", "dim_seller", "seller_id", "fact_seller_orders", "seller_id"),
]
M_TYPE = {"Text": "type text", "Date": "type date", "Whole number": "Int64.Type", "Decimal number": "type number"}

# --------------------------------------------------------------------------------------------- data dictionary
# (column, Power BI type, role, description)
DICT: dict[str, list[tuple[str, str, str, str]]] = {
    "fact_orders": [
        ("order_id", "Text", "key", "Unique order id (grain key)."),
        ("purchase_date", "Date", "foreign key -> dim_date[date_key]", "Purchase date; the only date related to dim_date."),
        ("customer_state", "Text", "foreign key -> dim_state[state_code]", "Customer (destination) state."),
        ("order_status", "Text", "attribute", "Original order status."),
        ("fulfilment_class", "Text", "attribute", "Six mutually exclusive fulfilment classes (machine value)."),
        ("fulfilment_label", "Text", "attribute", "Display label of the class, numbered for natural sort order."),
        ("in_window", "Whole number", "flag", "1 = purchased 2017-01..2018-08."),
        ("is_delivery_kpi_eligible", "Whole number", "flag", "1 = delivered with a delivery date AND purchased in window (96,203 orders)."),
        ("is_late", "Whole number", "flag", "1 = delivered after the estimated calendar date; blank unless delivered with a date."),
        ("is_severe_late", "Whole number", "flag", "1 = more than 7 calendar days after the estimated date; blank unless delivered with a date."),
        ("delivery_outcome", "Text", "attribute", "'On time' / 'Late' text legend for charts; blank unless delivered with a date."),
        ("lead_time_days", "Decimal number", "measure input", "Purchase to delivery in fractional days; blank unless delivered with a date."),
        ("promised_lead_days", "Whole number", "measure input", "Estimated date minus purchase date, days; blank unless delivered with a date."),
        ("promise_error_days", "Whole number", "measure input", "Delivery date minus estimated date, signed days (negative = early)."),
        ("days_late_band", "Text", "attribute", "0. On time / 1. 1-3 / 2. 4-7 / 3. 8+ days late; blank unless delivered with a date."),
        ("promised_group", "Text", "attribute", "Promised lead-time group (<=14, 15-21, 22-28, 29-35, 36+ days)."),
        ("review_row_count", "Whole number", "attribute", "Number of review rows for the order (0, 1 or more)."),
        ("review_score", "Whole number", "measure input", "1-5 stars; blank unless exactly one review row (the latest review is never assumed authoritative)."),
        ("review_before_delivery_flag", "Whole number", "flag", "1 = the single review was created strictly before the recorded delivery date; blank unless one review on a delivered order."),
        ("is_review_p0", "Whole number", "flag", "1 = primary review population P0 (delivered in window, exactly one review row; 95,037 orders)."),
        ("is_review_p1", "Whole number", "flag", "1 = sensitivity population P1 (P0 minus reviews created before delivery; 90,103 orders)."),
        ("is_single_seller", "Whole number", "flag", "1 = exactly one seller on the order."),
        ("ts_sequence_violation", "Whole number", "flag", "1 = at least one timestamp-sequence anomaly (kept in KPIs; available for sensitivity)."),
    ],
    "fact_seller_orders": [
        ("order_id", "Text", "key", "Unique order id; repeats fact_orders[order_id] for reconciliation only (NO relationship)."),
        ("seller_id", "Text", "foreign key -> dim_seller[seller_id]", "The single seller of the order."),
        ("seller_state", "Text", "foreign key -> dim_origin_state[origin_state_code]", "Seller (origin) state."),
        ("customer_state", "Text", "foreign key -> dim_state[state_code]", "Customer (destination) state."),
        ("lane", "Text", "attribute", "seller state > customer state, e.g. SP>RJ."),
        ("purchase_date", "Date", "foreign key -> dim_date[date_key]", "Purchase date."),
        ("is_cross_state", "Whole number", "flag", "1 = seller state differs from customer state."),
        ("distance_km", "Decimal number", "measure input", "Straight-line ZIP-prefix centroid distance in km (an approximation, not road distance); blank if a centroid is missing."),
        ("distance_band", "Text", "attribute", "Quartile band of distance_km (Q0 = unknown)."),
        ("promised_group", "Text", "attribute", "Promised lead-time group."),
        ("is_late", "Whole number", "flag", "1 = delivered after the estimated calendar date."),
        ("is_severe_late", "Whole number", "flag", "1 = more than 7 calendar days late."),
        ("delivery_outcome", "Text", "attribute", "'On time' / 'Late' text legend for charts."),
        ("lead_time_days", "Decimal number", "measure input", "Purchase to delivery, fractional days."),
        ("promised_lead_days", "Whole number", "measure input", "Estimated date minus purchase date, days."),
        ("promise_error_days", "Whole number", "measure input", "Signed days vs the estimated date."),
        ("days_late_band", "Text", "attribute", "0. On time / 1. 1-3 / 2. 4-7 / 3. 8+ days late."),
        ("review_row_count", "Whole number", "attribute", "Number of review rows."),
        ("review_score", "Whole number", "measure input", "1-5 stars; blank unless exactly one review row."),
        ("review_before_delivery_flag", "Whole number", "flag", "1 = review created before the recorded delivery date."),
        ("is_review_p0", "Whole number", "flag", "1 = exactly one review row."),
        ("is_review_p1", "Whole number", "flag", "1 = exactly one review row, created on or after the delivery date."),
    ],
    "dim_date": [
        ("date_key", "Date", "key", "Calendar day (purchase-date dimension)."), ("year", "Whole number", "attribute", "Year."),
        ("month_number", "Whole number", "attribute", "Month 1-12."), ("year_month", "Text", "attribute", "YYYY-MM."),
        ("month_start", "Date", "attribute", "First day of the month."), ("month_label", "Text", "attribute", "e.g. Nov 2017; sort by month_sort."),
        ("month_sort", "Whole number", "sort key", "YYYYMM, used as 'sort by column' for month_label."),
        ("is_in_window", "Whole number", "flag", "1 = day belongs to the analysis window (purchases 2017-01..2018-08)."),
    ],
    "dim_state": [
        ("state_code", "Text", "key", "Two-letter customer state (27 rows)."), ("state_name", "Text", "attribute", "State name (ASCII)."),
        ("macro_region", "Text", "attribute", "IBGE macro-region (external display grouping)."), ("macro_region_order", "Whole number", "sort key", "1 North .. 5 South."),
    ],
    "dim_origin_state": [
        ("origin_state_code", "Text", "key", "Seller state code (role-specific copy of dim_state)."), ("origin_state_name", "Text", "attribute", "State name."),
        ("origin_macro_region", "Text", "attribute", "IBGE macro-region of the origin state."), ("origin_macro_region_order", "Whole number", "sort key", "1 North .. 5 South."),
    ],
    "dim_seller": [
        ("seller_id", "Text", "key", "Seller id (one row per seller with at least one eligible single-seller order)."),
        ("seller_label", "Text", "attribute", "First 8 characters of the id, for display."),
    ],
    "snap_priority_candidates": [
        ("snapshot_level", "Text", "attribute", "state, lane or seller (long table; one row per candidate segment)."),
        ("segment", "Text", "key (with level)", "State code, lane label (seller state > customer state) or full seller id."),
        ("segment_label", "Text", "attribute", "Display label (sellers shortened to 8 characters)."),
        ("origin_state", "Text", "attribute", "Lane origin (seller) state; blank otherwise."), ("destination_state", "Text", "attribute", "Lane destination (customer) state; blank otherwise."),
        ("display_region", "Text", "attribute", "State: its macro-region; lane: DESTINATION macro-region; seller: blank."),
        ("northeast_bound", "Whole number", "flag", "Lanes only: 1 = destination state is in the Northeast (destination-based; origin is irrelevant)."),
        ("tier", "Text", "attribute", "Provisional evidence tier (Investigate / Watch / No Signal / Insufficient Data) at the 20-order screening policy."),
        ("tier_sort", "Whole number", "sort key", "1 Investigate .. 4 Insufficient Data."),
        ("tier_x10", "Text", "attribute", "Tier with a 10-order screening threshold."), ("tier_x30", "Text", "attribute", "Tier with a 30-order screening threshold."),
        ("tier_excl_episodes", "Text", "attribute", "Tier recomputed without purchases in 2017-11, 2018-02, 2018-03 (reference rate recomputed too)."),
        ("consistency", "Text", "attribute", "consistent / inconsistent / unverified (a half-window with < 30 orders is unverified, not negative)."),
        ("eligible", "Whole number", "flag", "1 = meets the minimum-N policy (state/lane >= 100, seller >= 50 orders)."),
        ("low_confidence", "Whole number", "flag", "1 = seller with 30-49 orders."), ("signal", "Whole number", "flag", "1 = Wilson interval of the late rate entirely above the reference rate."),
        ("n_delivered", "Whole number", "snapshot value", "Delivered orders in the level's population, full window."), ("n_late", "Whole number", "snapshot value", "Late orders."),
        ("late_rate", "Decimal number", "snapshot value", "n_late / n_delivered."), ("late_rate_lo", "Decimal number", "snapshot value", "Wilson 95% lower bound."), ("late_rate_hi", "Decimal number", "snapshot value", "Wilson 95% upper bound."),
        ("reference_late_rate", "Decimal number", "snapshot value", "Level population late rate (states 6.79%, lanes/sellers 6.87%)."),
        ("expected_late", "Decimal number", "snapshot value", "n_delivered x reference rate."), ("excess_late", "Decimal number", "snapshot value", "n_late - expected_late."),
        ("excess_late_lo", "Decimal number", "snapshot value", "n_delivered x (Wilson lower - reference)."), ("excess_late_hi", "Decimal number", "snapshot value", "n_delivered x (Wilson upper - reference)."),
        ("oe_s1", "Decimal number", "snapshot value", "Adjusted observed/expected from month x promised-lead strata (secondary context)."),
        ("n_h1", "Whole number", "snapshot value", "Orders in H1 (purchases 2017-01..2017-10)."), ("rate_h1", "Decimal number", "snapshot value", "Late rate in H1."),
        ("n_h2", "Whole number", "snapshot value", "Orders in H2 (2017-11..2018-08)."), ("rate_h2", "Decimal number", "snapshot value", "Late rate in H2."),
        ("episode_late_share", "Decimal number", "snapshot value", "Share of the segment's late orders purchased in 2017-11, 2018-02, 2018-03."),
        ("rank_excess_late", "Whole number", "snapshot value", "Rank by excess late orders among eligible segments of the level."),
        ("n_reviewed_p0", "Whole number", "snapshot value", "Reviewed (single-review, P0) orders."), ("p0_share_of_delivered", "Decimal number", "snapshot value", "n_reviewed_p0 / n_delivered (single-review share, not 'any review')."),
        ("n_low", "Whole number", "snapshot value", "1-2 star reviewed orders."), ("low_rate", "Decimal number", "snapshot value", "n_low / n_reviewed_p0."),
        ("excess_low", "Decimal number", "snapshot value", "Excess low-score orders over the level's reference low-score share (all P0 reviews)."),
        ("excess_low_after", "Decimal number", "snapshot value", "Same, using reviews created on or after delivery only (P1)."),
        ("early_review_share", "Decimal number", "snapshot value", "Share of the segment's P0 reviews created before the recorded delivery date."),
        ("low_ontime_rate", "Decimal number", "snapshot value", "Low-score rate among ON-TIME reviewed orders."), ("low_ontime_rate_lo", "Decimal number", "snapshot value", "Wilson lower bound."), ("low_ontime_rate_hi", "Decimal number", "snapshot value", "Wilson upper bound."),
        ("excess_low_ontime", "Decimal number", "snapshot value", "Excess low-score orders among on-time reviewed orders."),
    ],
    "snap_tier_counts": [
        ("snapshot_level", "Text", "attribute", "state, lane or seller."), ("min_excess_late", "Whole number", "attribute", "Screening threshold: 10, 20 (provisional policy) or 30."),
        ("is_primary", "Whole number", "flag", "1 = the provisional 20-order policy."), ("investigate", "Whole number", "snapshot value", "Segments in tier Investigate."),
        ("watch", "Whole number", "snapshot value", "Watch."), ("no_signal", "Whole number", "snapshot value", "No Signal."), ("insufficient_data", "Whole number", "snapshot value", "Insufficient Data."),
    ],
    "snap_overlap": [
        ("row_type", "Text", "attribute", "membership (each order counted once) or summary."), ("label", "Text", "attribute", "Row description."),
        ("in_state", "Whole number", "flag", "Membership rows: order is in an Investigate state."), ("in_lane", "Whole number", "flag", "In an Investigate lane."), ("in_seller", "Whole number", "flag", "In an Investigate seller."),
        ("n_orders", "Whole number", "snapshot value", "Single-seller orders."), ("n_late", "Whole number", "snapshot value", "Late orders."),
        ("excess_vs_ref", "Decimal number", "snapshot value", "Late orders minus orders x single-seller reference rate; summary rows show the double-counting of adding levels."),
    ],
    "snap_metadata": [
        ("key", "Text", "key", "Metadata key."), ("value", "Text", "attribute", "Value."), ("description", "Text", "attribute", "What it means / how to use it."),
    ],
}


# --------------------------------------------------------------------------------------------- exports
def _copy(con: duckdb.DuckDBPyConnection, sql: str, path: Path) -> None:
    sql = sql.strip().rstrip(";")
    con.execute(f"COPY (\n{sql}\n) TO '{path.as_posix()}' (HEADER, DELIMITER ',', QUOTE '\"', ESCAPE '\"')")


def export_model_tables(con: duckdb.DuckDBPyConnection, out: Path) -> dict:
    q = con.execute(DISTANCE_SQL.read_text(encoding="utf-8")).fetchone()
    cuts = {"{{Q1}}": repr(float(q[0])), "{{Q2}}": repr(float(q[1])), "{{Q3}}": repr(float(q[2]))}
    for name in MODEL_TABLES:
        sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
        for k, v in cuts.items():
            sql = sql.replace(k, v)
        _copy(con, sql, out / f"{name}.csv")
    return {"distance_quartile_cut_points_km": [float(x) for x in q[:3]]}


def _as_int(s: pd.Series) -> pd.Series:
    return s.astype("Int64")


def build_snapshots(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    res = op.compute_all(con)
    parts = []
    for level in op.LEVELS:
        t = res[f"candidates_{level}"].copy()
        ex = res[f"candidates_{level}_excl_episodes"].set_index("segment").tier
        d = pd.DataFrame({"snapshot_level": level, "segment": t.segment})
        d["segment_label"] = t.segment.str[:8] if level == "seller" else t.segment
        d["origin_state"] = t.origin_state if level == "lane" else None
        d["destination_state"] = t.destination_state if level == "lane" else None
        d["display_region"] = t.region if level == "state" else (t.destination_region if level == "lane" else None)
        d["northeast_bound"] = _as_int(t.northeast_bound.astype(int)) if level == "lane" else pd.array([pd.NA] * len(t), dtype="Int64")
        d["tier"], d["tier_sort"] = t.tier, t.tier.map(TIER_SORT)
        d["tier_x10"], d["tier_x30"] = t.tier_x10, t.tier_x30
        d["tier_excl_episodes"] = t.segment.map(ex).fillna("absent")
        d["consistency"] = t.consistency
        d["eligible"], d["low_confidence"], d["signal"] = (t[c].astype(int) for c in ("eligible", "low_confidence", "signal"))
        for c in ("n_delivered", "n_late", "late_rate", "late_rate_lo", "late_rate_hi"):
            d[c] = t[c]
        d["reference_late_rate"] = res["references"][level]["late"]
        for c in ("expected_late", "excess_late", "excess_late_lo", "excess_late_hi", "oe_s1", "n_h1", "rate_h1", "n_h2", "rate_h2", "episode_late_share", "rank_excess_late"):
            d[c] = t[c]
        d["n_reviewed_p0"], d["p0_share_of_delivered"] = t.n_rev, t.coverage
        d["n_low"], d["low_rate"] = t.n_low, t.low_rate
        for c in ("excess_low", "excess_low_after", "early_review_share", "low_ontime_rate", "low_ontime_rate_lo", "low_ontime_rate_hi", "excess_low_ontime"):
            d[c] = t[c]
        parts.append(d)
    cand = pd.concat(parts, ignore_index=True)
    for c in ("n_delivered", "n_late", "n_h1", "n_h2", "n_reviewed_p0", "n_low", "tier_sort", "eligible", "low_confidence", "signal"):
        cand[c] = cand[c].astype("Int64")
    cand["rank_excess_late"] = cand["rank_excess_late"].astype("Int64")

    tc = res["tier_counts"].rename(columns={"level": "snapshot_level", "Investigate": "investigate", "Watch": "watch", "No Signal": "no_signal", "Insufficient Data": "insufficient_data"})
    tc["is_primary"] = (tc.min_excess_late == op.PRIMARY_X).astype(int)
    tc = tc[["snapshot_level", "min_excess_late", "is_primary", "investigate", "watch", "no_signal", "insufficient_data"]]

    cells = res["overlap_cells"].copy()
    names = {"in_state": "state", "in_lane": "lane", "in_seller": "seller"}
    mem = pd.DataFrame({"row_type": "membership",
                        "label": cells.apply(lambda r: " + ".join(v for k, v in names.items() if r[k]) or "none of the three Investigate sets", axis=1),
                        "in_state": cells.in_state.astype(int), "in_lane": cells.in_lane.astype(int), "in_seller": cells.in_seller.astype(int),
                        "n_orders": cells.n_orders, "n_late": cells.n_late, "excess_vs_ref": cells.excess_vs_ref})
    ov = res["overlap"]
    summ_rows = [("summary", f"Investigate {lvl} set (own level)", ov["per_level"][lvl]["n_orders"], ov["per_level"][lvl]["n_late"], ov["per_level"][lvl]["excess"]) for lvl in op.LEVELS]
    summ_rows += [("summary", "Union of the three sets (each order once)", ov["union"]["n_orders"], ov["union"]["n_late"], ov["union"]["excess"]),
                  ("summary", "Naive sum of the three levels (double counted; never use)", None, ov["late_orders_naive_sum"], ov["naive_sum_of_level_excess"]),
                  ("summary", "Double-counted excess (naive sum minus union)", None, None, ov["double_counted_excess"])]
    summ = pd.DataFrame(summ_rows, columns=["row_type", "label", "n_orders", "n_late", "excess_vs_ref"])
    overlap = pd.concat([mem, summ], ignore_index=True)[["row_type", "label", "in_state", "in_lane", "in_seller", "n_orders", "n_late", "excess_vs_ref"]]
    for c in ("in_state", "in_lane", "in_seller", "n_orders", "n_late"):
        overlap[c] = overlap[c].astype("Int64")

    R = res["references"]
    meta = [
        ("snapshot_scope", "Fixed-period snapshot", "All snap_* tables are precomputed for purchases 2017-01..2018-08. They are NOT recalculated by date, state or seller slicers."),
        ("window", "2017-01-01 to 2018-08-31", "Purchase-date window of the analysis."),
        ("reference_date", "2018-10-17", "Latest event timestamp in the extract (used to classify open orders)."),
        ("reference_late_rate_state", f"{R['state']['late']:.6f}", "Late rate of all delivered-in-window orders (state level reference)."),
        ("reference_late_rate_single_seller", f"{R['lane']['late']:.6f}", "Late rate of single-seller delivered-in-window orders (lane and seller reference)."),
        ("reference_low_score_rate_on_time", f"{R['lane']['low_ontime']:.6f}", "Low-score (1-2 star) share among on-time reviewed single-seller orders."),
        ("primary_excess_threshold", str(op.PRIMARY_X), "Minimum excess late orders for 'Investigate'. An OPERATIONAL SCREENING POLICY, not statistical significance."),
        ("sensitivity_thresholds", "10, 20, 30", "Thresholds reported in snap_tier_counts."),
        ("minimum_n", "state/lane >= 100 orders; seller >= 50 (30-49 low-confidence)", "Blueprint rule R8."),
        ("consistency_rule", "H1 = 2017-01..2017-10, H2 = 2017-11..2018-08", "A half with fewer than 30 orders is 'unverified', which is not a negative result."),
        ("high_delay_months", "2017-11, 2018-02, 2018-03", "Excluded in the tier_excl_episodes column."),
        ("lane_notation", "seller state > customer state", "Northeast-bound means the DESTINATION (customer) state is in the Northeast; origin is irrelevant."),
        ("seller_evidence", "Secondary screening evidence",
         f"Only {len(res['episode_comparison']['seller']['investigate_kept'])} of {len(res['episode_comparison']['seller']['investigate_full'])} Investigate sellers remain Investigate when the three high-delay months are excluded."),
        ("review_measures", "Association only", "Low-score measures use single-review orders; some reviews are written before delivery; never read as causal."),
        ("source", "scripts/export_powerbi.py -> operational_prioritization.compute_all", "Regenerate with the script; do not edit by hand."),
    ]
    metadata = pd.DataFrame(meta, columns=["key", "value", "description"])
    return {"snap_priority_candidates": cand, "snap_tier_counts": tc, "snap_overlap": overlap, "snap_metadata": metadata}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_dictionary(out: Path) -> None:
    rows = [{"table": t, "column": c, "power_bi_type": ty, "role": role, "description": desc} for t, cols in DICT.items() for c, ty, role, desc in cols]
    pd.DataFrame(rows).to_csv(out / "data_dictionary.csv", index=False, lineterminator="\n")


def write_power_query(path: Path) -> None:
    """One typed Power Query (M) query per CSV, generated from the data dictionary so types cannot drift."""
    lines = ["// Power Query (M) for the Olist Operations Intelligence import package. GENERATED by scripts/export_powerbi.py; do not edit by hand.",
             "// 1) Home > Manage Parameters > New Parameter: name DataFolder, type Text, value = full path of powerbi\\data\\ INCLUDING a trailing backslash.",
             "// 2) For each block below: Home > New Source > Blank Query > Advanced Editor, paste the block, name the query exactly as in the header comment.",
             "// All queries parse numbers and dates with the en-US culture, so the result does not depend on the Windows regional settings.", ""]
    for table in MODEL_TABLES + SNAPSHOT_TABLES:
        types = ", ".join(f'{{"{c}", {M_TYPE[ty]}}}' for c, ty, _, _ in DICT[table])
        lines += [f"// ---------------- query name: {table} ----------------", "let",
                  f'    Source   = Csv.Document(File.Contents(DataFolder & "{table}.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
                  "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
                  f'    Typed    = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")', "in", "    Typed", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def export_all(db_path: Path = DEFAULT_DB, out_dir: Path = DEFAULT_OUT, verbose: bool = True, write_docs: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        info = export_model_tables(con, out_dir)
        snaps = build_snapshots(con)
    finally:
        con.close()
    for name, df in snaps.items():
        df.to_csv(out_dir / f"{name}.csv", index=False, lineterminator="\n")
    write_dictionary(out_dir)
    manifest = {"format": "csv, UTF-8, comma-separated, ISO dates, '.' decimals, booleans as 0/1, empty = missing", **info, "tables": {}}
    for name in MODEL_TABLES + SNAPSHOT_TABLES:
        path = out_dir / f"{name}.csv"
        df = pd.read_csv(path, nrows=0)
        n_rows = sum(1 for _ in open(path, encoding="utf-8")) - 1
        manifest["tables"][name] = {"rows": n_rows, "columns": list(df.columns), "bytes": path.stat().st_size, "sha256": _sha256(path),
                                    "kind": "snapshot" if name.startswith("snap_") else ("fact" if name.startswith("fact_") else "dimension")}
        if verbose:
            print(f"{name}: {n_rows:,} rows, {path.stat().st_size / 1e6:.2f} MB")
    manifest["relationships"] = [{"id": r[0], "one": f"{r[1]}[{r[2]}]", "many": f"{r[3]}[{r[4]}]", "cardinality": "one-to-many", "cross_filter": "single (dimension to fact)", "active": True} for r in RELATIONSHIPS]
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if write_docs:
        write_power_query(out_dir.parent / "power_query.m")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()
    if not a.db.exists():
        print("Model not found. Run: python scripts/build_model.py", file=sys.stderr)
        return 1
    export_all(a.db, a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
