"""Automated validation of the Olist analytical data model (Phase 1).

Three layers:
  1. build assertions (grain, keys, referential integrity, invariants) - run inside the build;
  2. reconciliation against anchors documented in reports/kpi_validation*.json / dataset_feasibility.md
     (exact integer counts; rates compared with numeric tolerances, never rounded-string equality);
  3. parity against an independent pandas implementation (tests/reference_pandas.py).
Expected values come from earlier reports and must never be edited to make a test pass.
"""
from __future__ import annotations

import math

import duckdb
import pandas as pd
import pytest

import build_model

from anchors import ANCHORS, LATE_RATE_WINDOW_DOC  # noqa: E402


def q1(con, sql):
    return con.execute(sql).fetchone()[0]


# =============================== 1. build assertions ===============================
def test_all_build_assertions_pass(build_results):
    _, results = build_results
    assert len(results) >= 30
    assert {k: v for k, v in results.items() if v != 0} == {}


# =============================== 2. anchors ===============================
def test_staging_row_counts_match_profile(con):
    expected = dict(stg_orders=99_441, stg_customers=99_441, stg_order_items=112_650, stg_reviews=99_224,
                    stg_products=32_951, stg_sellers=3_095, stg_geolocation=1_000_163, stg_category_translation=71)
    got = {t: q1(con, f"SELECT count(*) FROM {t}") for t in expected}
    assert got == expected


def test_fact_table_grains(con):
    assert q1(con, "SELECT count(*) FROM fact_orders") == ANCHORS["orders"]
    assert q1(con, "SELECT count(*) FROM fact_order_items") == ANCHORS["items"]
    assert q1(con, "SELECT count(*) FROM fact_reviews") == 99_224
    assert q1(con, "SELECT count(*) FROM bridge_order_seller") == q1(
        con, "SELECT count(*) FROM (SELECT DISTINCT order_id, seller_id FROM stg_order_items)")
    assert q1(con, "SELECT count(DISTINCT order_id) FROM fact_orders") == ANCHORS["orders"]


def test_multi_seller_orders(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE n_sellers > 1") == ANCHORS["multi_seller_orders"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE n_items = 0") == 775   # orders without items
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND n_sellers = 1") == ANCHORS["delivered_one_seller_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND n_sellers > 1") == ANCHORS["delivered_multi_seller_all"]


def test_delivered_populations(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated") == ANCHORS["delivered_dated_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible") == ANCHORS["delivered_dated_window"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_seller_kpi_eligible") == ANCHORS["single_seller_window"]


def test_late_rate_calendar_and_exact(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_late_calendar") == ANCHORS["late_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_late_exact") == ANCHORS["late_exact_all"]
    late, n = con.execute("SELECT sum(is_late_calendar::INT), count(*) FROM fact_orders WHERE is_delivery_kpi_eligible").fetchone()
    assert n == ANCHORS["delivered_dated_window"]
    assert math.isclose(late / n, LATE_RATE_WINDOW_DOC, abs_tol=5e-5)     # rounds to 6.79%
    # exact-timestamp definition counts any same-day delivery as late: strictly more late orders
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_late_exact AND NOT is_late_calendar") == 1_292


def test_timestamp_anomalies_preserved_not_deleted(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE ts_sequence_violation") == ANCHORS["ts_violations"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE has_status_date_conflict") == ANCHORS["status_date_conflicts"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE carrier_ts < purchase_ts") == 166
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE carrier_ts < approved_ts") == 1_359
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE delivered_customer_ts < carrier_ts") == 23
    # 1,373 of the 1,382 violations lie in the delivered-with-date population (kpi_validation q5)
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND ts_sequence_violation") == 1_373
    # primary-KPI inputs are not affected by the anomalies
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND lead_time_days <= 0") == 0


def test_window_late_rate_is_stable_vs_all_months(con):
    r = q1(con, "SELECT avg(is_late_calendar::INT) FROM fact_orders WHERE is_delivered_dated")
    assert math.isclose(r, 0.0677, abs_tol=5e-5)


# ---- fulfilment classes ----
CLASSES = {"delivered_on_time", "delivered_late", "cancelled_unavailable", "open_past_promise",
           "open_not_yet_due", "delivered_status_no_date"}


def test_six_mutually_exclusive_fulfilment_classes(con):
    rows = dict(con.execute("SELECT fulfilment_class, count(*) FROM fact_orders GROUP BY 1").fetchall())
    assert set(rows) <= CLASSES and "unclassified" not in rows
    assert sum(rows.values()) == ANCHORS["orders"]
    win = dict(con.execute("SELECT fulfilment_class, count(*) FROM fact_orders WHERE in_window GROUP BY 1").fetchall())
    assert sum(win.values()) == q1(con, "SELECT count(*) FROM fact_orders WHERE in_window")
    assert win["delivered_on_time"] + win["delivered_late"] == ANCHORS["delivered_dated_window"]


def test_cancelled_unavailable_never_overdue(con):
    assert q1(con, """SELECT count(*) FROM fact_orders WHERE order_status IN ('canceled','unavailable')
                      AND fulfilment_class <> 'cancelled_unavailable'""") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE fulfilment_class='cancelled_unavailable' AND order_status NOT IN ('canceled','unavailable')") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE order_status IN ('canceled','unavailable') AND fulfilment_class LIKE 'open%'") == 0
    # all-month canceled + unavailable = 1,234 (feasibility report)
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE fulfilment_class='cancelled_unavailable'") == 1_234


def test_original_order_status_preserved_for_open_orders(con):
    src = dict(con.execute("SELECT order_status, count(*) FROM stg_orders GROUP BY 1").fetchall())
    mdl = dict(con.execute("SELECT order_status, count(*) FROM fact_orders GROUP BY 1").fetchall())
    assert src == mdl
    assert mdl["delivered"] == 96_478 and mdl["shipped"] == 1_107 and mdl["canceled"] == 625 and mdl["unavailable"] == 609
    # each open status maps to an open class only
    bad = q1(con, """SELECT count(*) FROM fact_orders WHERE order_status IN ('created','approved','invoiced','processing','shipped')
                     AND fulfilment_class NOT IN ('open_past_promise','open_not_yet_due')""")
    assert bad == 0


def test_reference_date_is_extract_end(con):
    import datetime
    assert q1(con, "SELECT reference_date FROM model_params") == datetime.date(2018, 10, 17)


# ---- reviews ----
def test_review_score_only_when_exactly_one_row(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE review_row_count > 1 AND review_score IS NOT NULL") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE review_row_count = 0 AND review_score IS NOT NULL") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE review_row_count > 1") == ANCHORS["multi_review_orders"]
    cols = {r[0] for r in con.execute("DESCRIBE fact_reviews").fetchall()} | {r[0] for r in con.execute("DESCRIBE fact_orders").fetchall()}
    assert not any("latest" in c or "is_last" in c for c in cols)      # latest review is never treated as authoritative


def test_review_populations_all_months(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND review_row_count = 1") == ANCHORS["review_p0_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND review_row_count = 0") == ANCHORS["review_zero_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND review_row_count > 1") == ANCHORS["review_multi_all"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE review_before_delivery_flag") == ANCHORS["early_review_all"]


def test_review_kpi_anchors_all_months(con):
    on = con.execute("""SELECT count(*), avg(review_score), avg((review_score <= 2)::INT) FROM fact_orders
                        WHERE is_delivered_dated AND review_row_count = 1 AND NOT is_late_calendar""").fetchone()
    late = con.execute("""SELECT count(*), avg(review_score), avg((review_score <= 2)::INT) FROM fact_orders
                          WHERE is_delivered_dated AND review_row_count = 1 AND is_late_calendar""").fetchone()
    assert on[0] == ANCHORS["ontime_n_all"] and late[0] == ANCHORS["late_n_all"]
    assert math.isclose(on[1], 4.291, abs_tol=5e-4) and math.isclose(late[1], 2.273, abs_tol=5e-4)
    assert math.isclose(on[2], 0.0925, abs_tol=5e-5) and math.isclose(late[2], 0.6236, abs_tol=5e-5)


def test_review_window_populations_partition(con):
    p0, zero, multi, total = con.execute("""SELECT count(*) FILTER (WHERE review_row_count = 1),
        count(*) FILTER (WHERE review_row_count = 0), count(*) FILTER (WHERE review_row_count > 1), count(*)
        FROM fact_orders WHERE is_delivery_kpi_eligible""").fetchone()
    assert p0 + zero + multi == total == ANCHORS["delivered_dated_window"]
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_review_kpi_eligible") == p0
    p1 = q1(con, "SELECT count(*) FROM fact_orders WHERE is_review_p1_eligible")
    early = q1(con, "SELECT count(*) FROM fact_orders WHERE is_review_kpi_eligible AND review_before_delivery_flag")
    assert p1 + early == p0


# ---- single-seller view ----
def test_single_seller_view(con):
    assert q1(con, "SELECT count(*) FROM v_single_seller_orders") == ANCHORS["single_seller_window"]
    assert q1(con, "SELECT count(DISTINCT order_id) FROM v_single_seller_orders") == ANCHORS["single_seller_window"]
    assert q1(con, "SELECT count(DISTINCT seller_id) FROM v_single_seller_orders") == ANCHORS["sellers_window"]
    assert q1(con, "SELECT count(*) FROM v_single_seller_orders WHERE seller_id IS NULL") == 0
    assert q1(con, """SELECT count(*) FROM v_single_seller_orders v JOIN bridge_order_seller b USING (order_id)
                      WHERE b.n_sellers_in_order <> 1 OR b.seller_id <> v.seller_id""") == 0
    n = dict(con.execute("""SELECT 30 AS k, count(*) FROM (SELECT seller_id FROM v_single_seller_orders GROUP BY 1 HAVING count(*) >= 30)
                            UNION ALL SELECT 50, count(*) FROM (SELECT seller_id FROM v_single_seller_orders GROUP BY 1 HAVING count(*) >= 50)
                            UNION ALL SELECT 100, count(*) FROM (SELECT seller_id FROM v_single_seller_orders GROUP BY 1 HAVING count(*) >= 100)""").fetchall())
    assert (n[30], n[50], n[100]) == (ANCHORS["sellers_ge30"], ANCHORS["sellers_ge50"], ANCHORS["sellers_ge100"])


def test_state_volume_thresholds(con):
    st = dict(con.execute("SELECT customer_state, count(*) FROM fact_orders WHERE is_delivery_kpi_eligible GROUP BY 1").fetchall())
    assert len(st) == 27 and sum(1 for v in st.values() if v >= 100) == 24
    assert (st["AC"], st["AP"], st["RR"]) == (80, 67, 40)


# ---- dimensions ----
def test_macro_region_mapping(con):
    regions = dict(con.execute("SELECT macro_region, count(*) FROM dim_state GROUP BY 1").fetchall())
    assert regions == {"North": 7, "Northeast": 9, "Central-West": 4, "Southeast": 4, "South": 3}
    assert q1(con, "SELECT count(DISTINCT state_code) FROM dim_state") == 27
    # every state used by customers/sellers maps to exactly one region
    assert q1(con, "SELECT count(*) FROM stg_customers WHERE customer_state NOT IN (SELECT state_code FROM dim_state)") == 0
    assert q1(con, "SELECT count(*) FROM stg_sellers WHERE seller_state NOT IN (SELECT state_code FROM dim_state)") == 0
    # spot checks of the external mapping
    m = dict(con.execute("SELECT state_code, macro_region FROM dim_state").fetchall())
    assert m["SP"] == "Southeast" and m["DF"] == "Central-West" and m["BA"] == "Northeast" and m["TO"] == "North" and m["RS"] == "South"


def test_geolocation_aggregation_prevents_row_multiplication(con):
    assert q1(con, "SELECT count(*) - count(DISTINCT zip_code_prefix) FROM dim_zip_geo") == 0
    joined = q1(con, "SELECT count(*) FROM stg_customers c LEFT JOIN dim_zip_geo g ON g.zip_code_prefix = c.customer_zip_code_prefix")
    assert joined == ANCHORS["orders"]            # raw geolocation join would give 15,083,733 rows
    assert q1(con, "SELECT count(*) FROM dim_zip_geo WHERE lat NOT BETWEEN -33.75 AND 5.27 OR lng NOT BETWEEN -73.99 AND -28.0") == 0


def test_distance_formula_against_known_pair(con):
    # Sao Paulo (-23.5505,-46.6333) to Rio de Janeiro (-22.9068,-43.1729): about 360 km great-circle
    d = q1(con, """SELECT 2 * 6371.0088 * asin(sqrt(power(sin(radians(-22.9068 - -23.5505) / 2), 2)
              + cos(radians(-23.5505)) * cos(radians(-22.9068)) * power(sin(radians(-43.1729 - -46.6333) / 2), 2)))""")
    assert 355 < d < 365
    mx, mn = con.execute("SELECT max(distance_km), min(distance_km) FROM fact_order_items").fetchone()
    assert mn >= 0 and mx < 5_500


def test_distance_coverage_close_to_feasibility(con):
    nulls = q1(con, "SELECT count(*) FROM fact_order_items WHERE distance_km IS NULL")
    # feasibility: 112,096 item rows had both centroids (554 without); this model drops out-of-Brazil points
    # so one more item (customer ZIP 83252, only Spanish coordinates) lacks a distance. See report.
    assert nulls == 555                      # deterministic; feasibility anchor was 554 (see comment above)
    assert math.isclose(1 - nulls / ANCHORS["items"], 0.9951, abs_tol=1e-4)


def test_severe_late_and_band_consistency(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_severe_late <> (days_late_band = '8+')") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_severe_late <> (promise_error_days > 7)") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE promise_error_days = 7 AND is_severe_late") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE promise_error_days = 8 AND NOT is_severe_late") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_late_calendar <> (days_late_band <> '<=0')") == 0


def test_null_and_range_edge_cases(con):
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE customer_unique_id IS NULL OR purchase_ts IS NULL") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE n_items = 0 AND (primary_seller_id IS NOT NULL OR order_category IS NOT NULL)") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE order_category NOT IN ('mixed','unknown') AND n_items = 0") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE items_value < 0 OR freight_value < 0") == 0
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE n_items > 0 AND items_value IS NULL") == 0


# =============================== 3. pandas parity ===============================
def test_pandas_parity_counts(con, ref):
    pairs = {
        "n_orders": "SELECT count(*) FROM fact_orders",
        "n_items": "SELECT count(*) FROM fact_order_items",
        "multi_seller_orders": "SELECT count(*) FROM fact_orders WHERE n_sellers > 1",
        "delivered_dated_all": "SELECT count(*) FROM fact_orders WHERE is_delivered_dated",
        "delivered_dated_window": "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible",
        "late_all": "SELECT count(*) FROM fact_orders WHERE is_late_calendar",
        "late_exact_all": "SELECT count(*) FROM fact_orders WHERE is_late_exact",
        "late_window": "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible AND is_late_calendar",
        "orders_in_window": "SELECT count(*) FROM fact_orders WHERE in_window",
        "single_seller_window": "SELECT count(*) FROM v_single_seller_orders",
        "n_sellers_window": "SELECT count(DISTINCT seller_id) FROM v_single_seller_orders",
        "review_p0_window": "SELECT count(*) FROM fact_orders WHERE is_review_kpi_eligible",
        "review_zero_window": "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible AND review_row_count = 0",
        "review_multi_window": "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible AND review_row_count > 1",
        "review_p1_window": "SELECT count(*) FROM fact_orders WHERE is_review_p1_eligible",
        "early_review_window": "SELECT count(*) FROM fact_orders WHERE is_review_kpi_eligible AND review_before_delivery_flag",
        "late_n_p0_window": "SELECT count(*) FROM fact_orders WHERE is_review_kpi_eligible AND is_late_calendar",
        "ts_violations": "SELECT count(*) FROM fact_orders WHERE ts_sequence_violation",
        "status_date_conflicts": "SELECT count(*) FROM fact_orders WHERE has_status_date_conflict",
        "severe_late_window": "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible AND is_severe_late",
        "n_zip_centroids": "SELECT count(*) FROM dim_zip_geo",
        "items_without_distance": "SELECT count(*) FROM fact_order_items WHERE distance_km IS NULL",
        "review_p0_all": "SELECT count(*) FROM fact_orders WHERE is_delivered_dated AND review_row_count = 1",
        "early_review_all": "SELECT count(*) FROM fact_orders WHERE review_before_delivery_flag",
    }
    for key, sql in pairs.items():
        assert q1(con, sql) == ref[key], key
    assert str(q1(con, "SELECT reference_date FROM model_params")) == str(ref["reference_date"])


def test_pandas_parity_seller_thresholds_and_states(con, ref):
    per = con.execute("SELECT count(*) AS n FROM v_single_seller_orders GROUP BY seller_id").df().n
    assert (int((per >= 30).sum()), int((per >= 50).sum()), int((per >= 100).sum())) == (
        ref["sellers_ge30"], ref["sellers_ge50"], ref["sellers_ge100"])
    st = dict(con.execute("SELECT customer_state, count(*) FROM fact_orders WHERE is_delivery_kpi_eligible GROUP BY 1").fetchall())
    assert st == ref["state_n"]


def test_pandas_parity_fulfilment_classes(con, ref):
    assert dict(con.execute("SELECT fulfilment_class, count(*) FROM fact_orders GROUP BY 1").fetchall()) == ref["fclass_all"]
    assert dict(con.execute("SELECT fulfilment_class, count(*) FROM fact_orders WHERE in_window GROUP BY 1").fetchall()) == ref["fclass_window"]
    got = {(s, c): n for s, c, n in con.execute("""SELECT order_status, fulfilment_class, count(*) FROM fact_orders
        WHERE in_window AND fulfilment_class LIKE 'open%' GROUP BY 1, 2""").fetchall()}
    assert got == ref["open_status_window"]


def test_pandas_parity_rates_and_scores(con, ref):
    late_rate = q1(con, "SELECT avg(is_late_calendar::INT) FROM fact_orders WHERE is_delivery_kpi_eligible")
    assert math.isclose(late_rate, ref["late_rate_window"], rel_tol=0, abs_tol=1e-12)
    assert math.isclose(q1(con, "SELECT median(lead_time_days) FROM fact_orders WHERE is_delivery_kpi_eligible"),
                        ref["median_lead_window"], abs_tol=1e-9)
    assert math.isclose(q1(con, "SELECT quantile_cont(lead_time_days, 0.95) FROM fact_orders WHERE is_delivery_kpi_eligible"),
                        ref["p95_lead_window"], abs_tol=1e-9)
    s_on = q1(con, "SELECT avg(review_score) FROM fact_orders WHERE is_review_kpi_eligible AND NOT is_late_calendar")
    s_late = q1(con, "SELECT avg(review_score) FROM fact_orders WHERE is_review_kpi_eligible AND is_late_calendar")
    assert math.isclose(s_on, ref["mean_score_ontime_window"], abs_tol=1e-12)
    assert math.isclose(s_late, ref["mean_score_late_window"], abs_tol=1e-12)
    for key, sql in {"mean_score_ontime_all": "AND NOT is_late_calendar", "mean_score_late_all": "AND is_late_calendar"}.items():
        got = q1(con, f"SELECT avg(review_score) FROM fact_orders WHERE is_delivered_dated AND review_row_count = 1 {sql}")
        assert math.isclose(got, ref[key], abs_tol=1e-12), key


def test_pandas_parity_order_distance(con, ref):
    duck = con.execute("SELECT order_id, distance_km_median FROM fact_orders WHERE n_sellers = 1").df().set_index("order_id").distance_km_median
    pdv = ref["order_distance_median"].reindex(duck.index)
    assert duck.isna().equals(pdv.isna())
    both = ~duck.isna()
    assert (duck[both] - pdv[both]).abs().max() < 1e-6
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE n_sellers <> 1 AND distance_km_median IS NOT NULL") == 0


def test_cross_state_flag_matches_pandas(con, ref):
    items = ref["items_df"]
    sl = pd.read_csv(build_model.DEFAULT_RAW / "olist_sellers_dataset.csv")[["seller_id", "seller_state"]]
    cust = pd.read_csv(build_model.DEFAULT_RAW / "olist_customers_dataset.csv")[["customer_id", "customer_state"]]
    it = items[["order_id", "seller_id", "customer_id"]].merge(sl, on="seller_id").merge(cust, on="customer_id")
    ns = it.groupby("order_id").seller_id.nunique()
    one = it[it.order_id.isin(ns[ns == 1].index)].drop_duplicates("order_id")
    expected = int((one.seller_state != one.customer_state).sum())
    assert q1(con, "SELECT count(*) FROM fact_orders WHERE is_cross_state") == expected


# =============================== 4. reproducibility ===============================
def test_rebuild_is_deterministic(build_results, tmp_path):
    db1, _ = build_results
    db2 = tmp_path / "second.duckdb"
    build_model.build(build_model.DEFAULT_RAW, db2, verbose=False)
    a, b = duckdb.connect(str(db1), read_only=True), duckdb.connect(str(db2), read_only=True)
    try:
        for table, key in [("fact_orders", "order_id"), ("fact_order_items", "order_id, order_item_id"),
                           ("fact_reviews", "order_id, review_id"), ("bridge_order_seller", "order_id, seller_id")]:
            sql = f"SELECT * FROM {table} ORDER BY {key}"
            pd.testing.assert_frame_equal(a.execute(sql).df(), b.execute(sql).df())
    finally:
        a.close(); b.close()
