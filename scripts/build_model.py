"""Build the Olist analytical data model in DuckDB from the raw CSVs (read-only).

Usage (from anywhere):
    python scripts/build_model.py [--raw data/raw] [--db data/processed/olist_model.duckdb]

Runs sql/01_staging.sql ... sql/03_facts.sql in order, then runs the structural build assertions
(grain, unique keys, referential integrity, logical invariants). Any violation aborts the build
with a non-zero exit code and the database is removed so a half-valid model is never left behind.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
DEFAULT_RAW = ROOT / "data" / "raw"
DEFAULT_DB = ROOT / "data" / "processed" / "olist_model.duckdb"
SQL_FILES = ["01_staging.sql", "02_dimensions.sql", "03_facts.sql"]

# Each assertion is a query returning the NUMBER OF VIOLATIONS; it must be 0.
BUILD_ASSERTIONS: dict[str, str] = {
    # ---- grain / row counts vs staging ----
    "fact_orders rows = stg_orders rows":
        "SELECT abs((SELECT count(*) FROM fact_orders) - (SELECT count(*) FROM stg_orders))",
    "fact_order_items rows = stg_order_items rows":
        "SELECT abs((SELECT count(*) FROM fact_order_items) - (SELECT count(*) FROM stg_order_items))",
    "fact_reviews rows = stg_reviews rows":
        "SELECT abs((SELECT count(*) FROM fact_reviews) - (SELECT count(*) FROM stg_reviews))",
    "bridge covers every order with items":
        "SELECT abs((SELECT count(DISTINCT order_id) FROM bridge_order_seller)"
        " - (SELECT count(DISTINCT order_id) FROM stg_order_items))",
    # ---- unique keys ----
    "fact_orders unique order_id":
        "SELECT count(*) - count(DISTINCT order_id) FROM fact_orders",
    "fact_order_items unique (order_id, order_item_id)":
        "SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT order_id, order_item_id FROM fact_order_items)) FROM fact_order_items",
    "fact_reviews unique (order_id, review_id)":
        "SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT order_id, review_id FROM fact_reviews)) FROM fact_reviews",
    "bridge unique (order_id, seller_id)":
        "SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT order_id, seller_id FROM bridge_order_seller)) FROM bridge_order_seller",
    "dim_zip_geo unique zip_code_prefix":
        "SELECT count(*) - count(DISTINCT zip_code_prefix) FROM dim_zip_geo",
    "dim_seller unique seller_id":
        "SELECT count(*) - count(DISTINCT seller_id) FROM dim_seller",
    "dim_product unique product_id":
        "SELECT count(*) - count(DISTINCT product_id) FROM dim_product",
    "dim_state unique state_code":
        "SELECT count(*) - count(DISTINCT state_code) FROM dim_state",
    "dim_date unique date_key":
        "SELECT count(*) - count(DISTINCT date_key) FROM dim_date",
    "v_single_seller_orders unique order_id":
        "SELECT count(*) - count(DISTINCT order_id) FROM v_single_seller_orders",
    # ---- referential integrity ----
    "orders.customer_id exists in customers":
        "SELECT count(*) FROM stg_orders WHERE customer_id NOT IN (SELECT customer_id FROM stg_customers)",
    "items.order_id exists in orders":
        "SELECT count(*) FROM stg_order_items WHERE order_id NOT IN (SELECT order_id FROM stg_orders)",
    "items.product_id exists in dim_product":
        "SELECT count(*) FROM fact_order_items WHERE product_id NOT IN (SELECT product_id FROM dim_product)",
    "items.seller_id exists in dim_seller":
        "SELECT count(*) FROM fact_order_items WHERE seller_id NOT IN (SELECT seller_id FROM dim_seller)",
    "reviews.order_id exists in orders":
        "SELECT count(*) FROM stg_reviews WHERE order_id NOT IN (SELECT order_id FROM stg_orders)",
    "fact_orders.customer_state exists in dim_state":
        "SELECT count(*) FROM fact_orders WHERE customer_state IS NULL OR customer_state NOT IN (SELECT state_code FROM dim_state)",
    "dim_seller.seller_state exists in dim_state":
        "SELECT count(*) FROM dim_seller WHERE seller_state NOT IN (SELECT state_code FROM dim_state)",
    "fact_orders purchase/estimated dates exist in dim_date":
        "SELECT count(*) FROM fact_orders WHERE purchase_date NOT IN (SELECT date_key FROM dim_date)"
        " OR estimated_date NOT IN (SELECT date_key FROM dim_date)",
    "v_single_seller_orders.seller_id exists in dim_seller":
        "SELECT count(*) FROM v_single_seller_orders WHERE seller_id IS NULL OR seller_id NOT IN (SELECT seller_id FROM dim_seller)",
    # ---- logical invariants ----
    "no unclassified fulfilment_class":
        "SELECT count(*) FROM fact_orders WHERE fulfilment_class = 'unclassified' OR fulfilment_class IS NULL",
    "cancelled/unavailable never open_past_promise":
        "SELECT count(*) FROM fact_orders WHERE order_status IN ('canceled','unavailable')"
        " AND fulfilment_class <> 'cancelled_unavailable'",
    "open_past_promise only for open statuses with estimate before reference date":
        "SELECT count(*) FROM fact_orders f CROSS JOIN model_params p WHERE fulfilment_class = 'open_past_promise'"
        " AND (order_status NOT IN ('created','approved','invoiced','processing','shipped') OR estimated_date >= p.reference_date)",
    "delivery measures NULL unless delivered with date":
        "SELECT count(*) FROM fact_orders WHERE NOT is_delivered_dated AND (lead_time_days IS NOT NULL"
        " OR is_late_calendar IS NOT NULL OR promise_error_days IS NOT NULL OR is_severe_late IS NOT NULL OR days_late_band IS NOT NULL)",
    "review_score only when exactly one review row":
        "SELECT count(*) FROM fact_orders WHERE (review_row_count = 1) <> (review_score IS NOT NULL)",
    "primary_seller_id only when exactly one seller":
        "SELECT count(*) FROM fact_orders WHERE (n_sellers = 1) <> (primary_seller_id IS NOT NULL)",
    "eligibility flags nested (seller/review/p1 within delivery)":
        "SELECT count(*) FROM fact_orders WHERE (is_seller_kpi_eligible AND NOT is_delivery_kpi_eligible)"
        " OR (is_review_kpi_eligible AND NOT is_delivery_kpi_eligible)"
        " OR (is_review_p1_eligible AND NOT is_review_kpi_eligible)",
    "v_single_seller_orders rows = is_seller_kpi_eligible orders":
        "SELECT abs((SELECT count(*) FROM v_single_seller_orders) - (SELECT count(*) FROM fact_orders WHERE is_seller_kpi_eligible))",
    "no multi-seller order in v_single_seller_orders":
        "SELECT count(*) FROM v_single_seller_orders v JOIN bridge_order_seller b USING (order_id) WHERE b.n_sellers_in_order <> 1",
    "anomaly/status flags are never NULL":
        "SELECT count(*) FROM fact_orders WHERE ts_sequence_violation IS NULL OR carrier_leg_unreliable IS NULL"
        " OR has_status_date_conflict IS NULL OR in_window IS NULL OR is_delivered_dated IS NULL",
    "late flag consistent with promise_error_days":
        "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND (is_late_calendar <> (promise_error_days > 0))",
}


def build(raw_dir: Path = DEFAULT_RAW, db_path: Path = DEFAULT_DB, verbose: bool = True) -> dict[str, int]:
    """Build the model; return {assertion name: violation count}. Raises on any violation."""
    raw_dir, db_path = Path(raw_dir).resolve(), Path(db_path).resolve()
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Raw data directory not found: {raw_dir}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", ".wal"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        for name in SQL_FILES:
            sql = (SQL_DIR / name).read_text(encoding="utf-8").replace("{{RAW_DIR}}", raw_dir.as_posix())
            con.execute(sql)
            if verbose:
                print(f"ran {name}")
        results = {label: con.execute(q).fetchone()[0] for label, q in BUILD_ASSERTIONS.items()}
    finally:
        con.close()

    failed = {k: v for k, v in results.items() if v != 0}
    if verbose:
        print(f"build assertions: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        db_path.unlink(missing_ok=True)
        raise AssertionError(f"Build assertions failed (violation counts): {failed}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW, help="directory with the raw CSVs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="output DuckDB file")
    args = parser.parse_args()
    try:
        build(args.raw, args.db)
    except (AssertionError, FileNotFoundError) as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"model written to {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
