"""Independent pandas re-implementation of the headline model quantities.

Deliberately does NOT read the DuckDB model or reuse any SQL: it recomputes everything from the raw CSVs
so that agreement with DuckDB is evidence the SQL is correct. Parameters mirror blueprint rules R1-R10.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

WINDOW_START = pd.Timestamp("2017-01-01")
WINDOW_END_EXCL = pd.Timestamp("2018-09-01")
SEVERE_DAYS = 7
OPEN_STATUSES = ["created", "approved", "invoiced", "processing", "shipped"]
BBOX = dict(lat_min=-33.75, lat_max=5.27, lng_min=-73.99, lng_max=-28.0)
EARTH_RADIUS_KM = 6371.0088
TS_COLS = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
           "order_delivered_customer_date", "order_estimated_delivery_date"]


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(lng2 - lng1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def compute_reference(raw_dir: Path) -> dict:
    raw = Path(raw_dir)
    o = pd.read_csv(raw / "olist_orders_dataset.csv", parse_dates=TS_COLS)
    c = pd.read_csv(raw / "olist_customers_dataset.csv")
    it = pd.read_csv(raw / "olist_order_items_dataset.csv")
    rv = pd.read_csv(raw / "olist_order_reviews_dataset.csv", parse_dates=["review_creation_date"])
    sl = pd.read_csv(raw / "olist_sellers_dataset.csv")
    geo = pd.read_csv(raw / "olist_geolocation_dataset.csv")

    # ---- order level ----
    o["purchase_date"] = o.order_purchase_timestamp.dt.normalize()
    o["est_date"] = o.order_estimated_delivery_date.dt.normalize()
    o["deliv_date"] = o.order_delivered_customer_date.dt.normalize()
    o["in_window"] = (o.purchase_date >= WINDOW_START) & (o.purchase_date < WINDOW_END_EXCL)
    o["dated"] = (o.order_status == "delivered") & o.order_delivered_customer_date.notna()
    o["late"] = o.dated & (o.deliv_date > o.est_date)
    o["late_exact"] = o.dated & (o.order_delivered_customer_date > o.order_estimated_delivery_date)
    ref_date = max(o[col].max() for col in
                   ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
                    "order_delivered_customer_date"]).normalize()
    is_open = o.order_status.isin(OPEN_STATUSES)
    o["fclass"] = np.select(
        [o.dated & o.late, o.dated, o.order_status == "delivered",
         o.order_status.isin(["canceled", "unavailable"]), is_open & (o.est_date < ref_date),
         is_open & (o.est_date >= ref_date)],
        ["delivered_late", "delivered_on_time", "delivered_status_no_date", "cancelled_unavailable",
         "open_past_promise", "open_not_yet_due"], default="unclassified")

    # ---- items -> per-order seller counts ----
    seller_counts = it.groupby("order_id").seller_id.nunique().rename("n_sellers")
    o = o.merge(seller_counts, on="order_id", how="left")
    o["n_sellers"] = o.n_sellers.fillna(0).astype(int)

    # ---- reviews -> per-order ----
    rv_counts = rv.groupby("order_id").size().rename("review_row_count")
    o = o.merge(rv_counts, on="order_id", how="left")
    o["review_row_count"] = o.review_row_count.fillna(0).astype(int)
    one = rv[rv.order_id.isin(o.loc[o.review_row_count == 1, "order_id"])][
        ["order_id", "review_score", "review_creation_date"]]
    o = o.merge(one, on="order_id", how="left")
    o["early_review"] = (o.review_row_count == 1) & o.dated & (o.review_creation_date < o.deliv_date)

    o = o.merge(c[["customer_id", "customer_state", "customer_unique_id"]], on="customer_id", how="left")

    ref: dict = {"n_orders": len(o), "n_items": len(it), "reference_date": ref_date.date()}
    ref["multi_seller_orders"] = int((o.n_sellers > 1).sum())
    d_all, d_win = o[o.dated], o[o.dated & o.in_window]
    ref.update(delivered_dated_all=len(d_all), delivered_dated_window=len(d_win),
               late_all=int(d_all.late.sum()), late_exact_all=int(d_all.late_exact.sum()),
               late_window=int(d_win.late.sum()),
               late_rate_window=float(d_win.late.mean()),
               orders_in_window=int(o.in_window.sum()))
    ref["fclass_all"] = o.fclass.value_counts().to_dict()
    ref["fclass_window"] = o[o.in_window].fclass.value_counts().to_dict()
    ref["open_status_window"] = (o[o.in_window & o.fclass.isin(["open_past_promise", "open_not_yet_due"])]
                                 .groupby(["order_status", "fclass"]).size().to_dict())

    # seller population
    single = d_win[d_win.n_sellers == 1]
    ref["single_seller_window"] = len(single)
    first_seller = it.drop_duplicates("order_id")[["order_id", "seller_id"]]
    single = single.merge(first_seller, on="order_id")
    per_seller = single.groupby("seller_id").size()
    ref.update(n_sellers_window=int(per_seller.size), sellers_ge30=int((per_seller >= 30).sum()),
               sellers_ge50=int((per_seller >= 50).sum()), sellers_ge100=int((per_seller >= 100).sum()))
    per_state = d_win.groupby("customer_state").size()
    ref["state_n"] = per_state.to_dict()

    # review populations
    p0 = d_win[d_win.review_row_count == 1]
    ref.update(review_p0_window=len(p0),
               review_zero_window=int((d_win.review_row_count == 0).sum()),
               review_multi_window=int((d_win.review_row_count > 1).sum()),
               review_p1_window=int((~p0.early_review).sum()),
               early_review_window=int(p0.early_review.sum()),
               mean_score_ontime_window=float(p0[~p0.late].review_score.mean()),
               mean_score_late_window=float(p0[p0.late].review_score.mean()),
               late_n_p0_window=int(p0.late.sum()))
    p0a = d_all[d_all.review_row_count == 1]  # all months, KPI-validation population
    ref.update(review_p0_all=len(p0a), review_zero_all=int((d_all.review_row_count == 0).sum()),
               review_multi_all=int((d_all.review_row_count > 1).sum()),
               early_review_all=int(p0a.early_review.sum()),
               mean_score_ontime_all=float(p0a[~p0a.late].review_score.mean()),
               mean_score_late_all=float(p0a[p0a.late].review_score.mean()),
               ontime_n_all=int((~p0a.late).sum()), late_n_all=int(p0a.late.sum()),
               low_share_ontime_all=float((p0a[~p0a.late].review_score <= 2).mean()),
               low_share_late_all=float((p0a[p0a.late].review_score <= 2).mean()))

    # timestamp anomalies
    t = o
    viol = ((t.order_approved_at < t.order_purchase_timestamp) | (t.order_delivered_carrier_date < t.order_purchase_timestamp)
            | (t.order_delivered_carrier_date < t.order_approved_at)
            | (t.order_delivered_customer_date < t.order_purchase_timestamp)
            | (t.order_delivered_customer_date < t.order_delivered_carrier_date)
            | (t.order_estimated_delivery_date < t.order_purchase_timestamp))
    ref["ts_violations"] = int(viol.sum())
    ref["status_date_conflicts"] = int(((t.order_status == "delivered") != t.order_delivered_customer_date.notna()).sum())

    # lead time / promise error
    lead = (d_win.order_delivered_customer_date - d_win.order_purchase_timestamp).dt.total_seconds() / 86400
    ref["median_lead_window"] = float(lead.median())
    ref["p95_lead_window"] = float(lead.quantile(0.95))
    perr = (d_win.deliv_date - d_win.est_date).dt.days
    ref["severe_late_window"] = int((perr > SEVERE_DAYS).sum())

    # ---- geography: distance per single-seller order ----
    g = geo.drop_duplicates(["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng"])
    g = g[g.geolocation_lat.between(BBOX["lat_min"], BBOX["lat_max"]) & g.geolocation_lng.between(BBOX["lng_min"], BBOX["lng_max"])]
    cen = g.groupby("geolocation_zip_code_prefix")[["geolocation_lat", "geolocation_lng"]].median()
    ref["n_zip_centroids"] = len(cen)
    items = it.merge(o[["order_id", "customer_id"]], on="order_id").merge(
        c[["customer_id", "customer_zip_code_prefix"]], on="customer_id").merge(
        sl[["seller_id", "seller_zip_code_prefix"]], on="seller_id")
    items = items.merge(cen.add_prefix("c_"), left_on="customer_zip_code_prefix", right_index=True, how="left")
    items = items.merge(cen.add_prefix("s_"), left_on="seller_zip_code_prefix", right_index=True, how="left")
    items["dist"] = haversine_km(items.c_geolocation_lat, items.c_geolocation_lng,
                                 items.s_geolocation_lat, items.s_geolocation_lng)
    ref["items_without_distance"] = int(items.dist.isna().sum())
    ref["items_df"] = items
    ns = items.groupby("order_id").seller_id.nunique()
    single_ids = ns[ns == 1].index
    ref["order_distance_median"] = items[items.order_id.isin(single_ids)].groupby("order_id").dist.median()
    ref["orders_df"] = o
    return ref
