"""Profile the raw Olist CSVs (read-only) and write computed statistics to JSON.

Usage (from project root):  python scripts/profile_dataset.py
Outputs: reports/profile_stats.json  (consumed by scripts/build_feasibility_report.py)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "reports" / "profile_stats.json"

FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}
CANDIDATE_KEYS = {
    "customers": [["customer_id"], ["customer_unique_id"]],
    "geolocation": [["geolocation_zip_code_prefix"]],
    "order_items": [["order_id", "order_item_id"], ["order_id"]],
    "payments": [["order_id", "payment_sequential"], ["order_id"]],
    "reviews": [["review_id"], ["order_id"], ["review_id", "order_id"]],
    "orders": [["order_id"]],
    "products": [["product_id"]],
    "sellers": [["seller_id"]],
    "category_translation": [["product_category_name"]],
}
TS_COLS = [
    "order_purchase_timestamp", "order_approved_at",
    "order_delivered_carrier_date", "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def pct(n: float, d: float) -> float:
    return round(100 * n / d, 2) if d else float("nan")


def profile_table(name: str, df: pd.DataFrame) -> dict:
    n = len(df)
    miss = df.isna().sum()
    cols = {
        c: {"dtype": str(df[c].dtype), "missing": int(miss[c]),
            "missing_pct": pct(miss[c], n), "nunique": int(df[c].nunique())}
        for c in df.columns
    }
    keys = []
    for k in CANDIDATE_KEYS[name]:
        keys.append({"key": k, "unique": bool(not df.duplicated(k).any()),
                     "duplicated_rows": int(df.duplicated(k, keep=False).sum())})
    return {"rows": n, "n_cols": df.shape[1], "columns": cols,
            "full_duplicate_rows": int(df.duplicated().sum()), "keys": keys}


def main() -> None:
    t = {k: pd.read_csv(RAW / v) for k, v in FILES.items()}
    s: dict = {"tables": {k: profile_table(k, df) for k, df in t.items()}}
    o, c, it, pay, rev, prod, sel, geo = (
        t["orders"], t["customers"], t["order_items"], t["payments"],
        t["reviews"], t["products"], t["sellers"], t["geolocation"])
    n_orders = len(o)

    # ---- relationships / join cardinality ----
    r: dict = {}
    r["orders_customer_id_not_in_customers"] = int((~o.customer_id.isin(c.customer_id)).sum())
    r["customers_without_orders"] = int((~c.customer_id.isin(o.customer_id)).sum())
    r["customers_rows"] = len(c)
    r["customers_unique_ids"] = int(c.customer_unique_id.nunique())
    per_uid = c.groupby("customer_unique_id").size()
    r["unique_customers_with_multiple_customer_ids"] = int((per_uid > 1).sum())
    r["orders_without_items"] = int((~o.order_id.isin(it.order_id)).sum())
    r["items_order_not_in_orders"] = int((~it.order_id.isin(o.order_id)).sum())
    r["items_product_not_in_products"] = int((~it.product_id.isin(prod.product_id)).sum())
    r["items_seller_not_in_sellers"] = int((~it.seller_id.isin(sel.seller_id)).sum())
    r["products_never_sold"] = int((~prod.product_id.isin(it.product_id)).sum())
    r["sellers_without_items"] = int((~sel.seller_id.isin(it.seller_id)).sum())
    r["orders_without_items_by_status"] = o.loc[~o.order_id.isin(it.order_id), "order_status"].value_counts().to_dict()
    items_per_order = it.groupby("order_id").size()
    r["orders_with_multiple_items"] = int((items_per_order > 1).sum())
    r["max_items_per_order"] = int(items_per_order.max())
    sellers_per_order = it.groupby("order_id").seller_id.nunique()
    r["orders_with_multiple_sellers"] = int((sellers_per_order > 1).sum())
    r["orders_with_items"] = int(len(sellers_per_order))
    r["row_multiplication_orders_x_items"] = int(len(o.merge(it, on="order_id", how="left")))
    pay_per_order = pay.groupby("order_id").size()
    r["orders_with_multiple_payment_rows"] = int((pay_per_order > 1).sum())
    r["orders_without_payments"] = int((~o.order_id.isin(pay.order_id)).sum())
    r["payments_order_not_in_orders"] = int((~pay.order_id.isin(o.order_id)).sum())
    r["row_multiplication_orders_x_payments"] = int(len(o.merge(pay, on="order_id", how="left")))
    r["payment_value_zero_rows"] = int((pay.payment_value == 0).sum())
    r["payment_type_counts"] = pay.payment_type.value_counts().to_dict()
    rev_per_order = rev.groupby("order_id").size()
    r["orders_without_reviews"] = int((~o.order_id.isin(rev.order_id)).sum())
    r["orders_with_multiple_reviews"] = int((rev_per_order > 1).sum())
    r["reviews_order_not_in_orders"] = int((~rev.order_id.isin(o.order_id)).sum())
    r["row_multiplication_orders_x_reviews"] = int(len(o.merge(rev, on="order_id", how="left")))
    r["review_score_counts"] = {int(k): int(v) for k, v in rev.review_score.value_counts().sort_index().items()}
    r["reviews_with_comment_text"] = int(rev.review_comment_message.notna().sum())
    # payments vs item totals (order level)
    item_tot = it.groupby("order_id").apply(lambda g: (g.price + g.freight_value).sum(), include_groups=False)
    pay_tot = pay.groupby("order_id").payment_value.sum()
    cmp_ = pd.concat([item_tot.rename("item_total"), pay_tot.rename("pay")], axis=1).dropna()
    r["orders_payment_vs_items_gap_gt_1"] = int(((cmp_.item_total - cmp_.pay).abs() > 1).sum())
    r["orders_compared_payment_vs_items"] = int(len(cmp_))
    # geolocation
    zips_geo = set(geo.geolocation_zip_code_prefix)
    gz = geo.groupby("geolocation_zip_code_prefix")
    r["geo_rows"] = len(geo)
    r["geo_unique_zip_prefixes"] = int(len(zips_geo))
    r["geo_full_duplicate_rows"] = int(geo.duplicated().sum())
    r["geo_prefixes_with_multiple_rows"] = int((gz.size() > 1).sum())
    r["geo_median_rows_per_prefix"] = float(gz.size().median())
    r["geo_max_rows_per_prefix"] = int(gz.size().max())
    r["geo_prefixes_with_multiple_distinct_coords"] = int((gz.apply(lambda g: g[["geolocation_lat", "geolocation_lng"]].drop_duplicates().shape[0], include_groups=False) > 1).sum())
    r["geo_prefixes_with_multiple_city_spellings"] = int((gz.geolocation_city.nunique() > 1).sum())
    r["geo_prefixes_with_multiple_states"] = int((gz.geolocation_state.nunique() > 1).sum())
    lat, lng = geo.geolocation_lat, geo.geolocation_lng
    r["geo_coords_outside_brazil_bbox"] = int((~lat.between(-34, 6) | ~lng.between(-74, -33)).sum())
    r["customer_zip_not_in_geo"] = int((~c.customer_zip_code_prefix.isin(zips_geo)).sum())
    r["seller_zip_not_in_geo"] = int((~sel.seller_zip_code_prefix.isin(zips_geo)).sum())
    r["customer_zip_unique"] = int(c.customer_zip_code_prefix.nunique())
    r["seller_zip_unique"] = int(sel.seller_zip_code_prefix.nunique())
    r["row_multiplication_customers_x_geo_raw"] = int(len(c.merge(geo, left_on="customer_zip_code_prefix", right_on="geolocation_zip_code_prefix", how="left")))
    r["customer_states"] = int(c.customer_state.nunique())
    r["seller_states"] = int(sel.seller_state.nunique())
    top_states = c.merge(o[["customer_id"]], on="customer_id").customer_state.value_counts()
    r["order_share_top_state"] = {"state": top_states.index[0], "pct": pct(top_states.iloc[0], top_states.sum())}
    r["order_share_sp_rj_mg"] = pct(top_states.reindex(["SP", "RJ", "MG"]).sum(), top_states.sum())
    sp_sellers = sel.seller_state.value_counts()
    r["seller_share_sp"] = pct(sp_sellers.get("SP", 0), len(sel))
    # product category
    r["products_missing_category"] = int(prod.product_category_name.isna().sum())
    r["categories_without_translation"] = int((~prod.product_category_name.dropna().drop_duplicates().isin(t["category_translation"].product_category_name)).sum())
    r["product_missing_dimensions"] = int(prod[["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]].isna().any(axis=1).sum())
    r["product_zero_weight"] = int((prod.product_weight_g == 0).sum())
    s["relationships"] = r

    # ---- orders: status, timestamps, coverage ----
    od = o.copy()
    for col in TS_COLS + ["order_delivered_carrier_date", "order_delivered_customer_date"]:
        od[col] = pd.to_datetime(od[col], errors="coerce")
    d: dict = {"orders": n_orders}
    d["status_counts"] = od.order_status.value_counts().to_dict()
    d["status_pct"] = {k: pct(v, n_orders) for k, v in d["status_counts"].items()}
    d["timestamp_missing"] = {col: int(od[col].isna().sum()) for col in TS_COLS}
    d["timestamp_unparseable_extra"] = {col: int((o[col].notna() & od[col].isna()).sum()) for col in TS_COLS}
    d["timestamp_ranges"] = {col: [str(od[col].min().date()), str(od[col].max().date())] for col in TS_COLS}
    # missing by status
    d["delivered_status_missing_customer_date"] = int(((od.order_status == "delivered") & od.order_delivered_customer_date.isna()).sum())
    d["non_delivered_with_customer_date"] = int(((od.order_status != "delivered") & od.order_delivered_customer_date.notna()).sum())
    d["delivered_status_missing_approved"] = int(((od.order_status == "delivered") & od.order_approved_at.isna()).sum())
    d["delivered_status_missing_carrier"] = int(((od.order_status == "delivered") & od.order_delivered_carrier_date.isna()).sum())
    dl = od[od.order_status == "delivered"]
    d["delivered_orders"] = len(dl)
    d["delivered_with_both_dates"] = int(dl.order_delivered_customer_date.notna().sum())
    # sequence checks
    pur, app, car, cus, est = (od.order_purchase_timestamp, od.order_approved_at,
                               od.order_delivered_carrier_date, od.order_delivered_customer_date,
                               od.order_estimated_delivery_date)
    d["approved_before_purchase"] = int((app < pur).sum())
    d["carrier_before_purchase"] = int((car < pur).sum())
    d["carrier_before_approved"] = int((car < app).sum())
    d["customer_before_purchase"] = int((cus < pur).sum())
    d["customer_before_carrier"] = int((cus < car).sum())
    d["estimated_before_purchase"] = int((est < pur).sum())
    d["any_sequence_violation_checked_pairs"] = int(((app < pur) | (car < pur) | (car < app) | (cus < pur) | (cus < car)).sum())
    # delivery metrics on delivered w/ date
    both = dl.dropna(subset=["order_delivered_customer_date"])
    days = (both.order_delivered_customer_date - both.order_purchase_timestamp).dt.total_seconds() / 86400
    late = both.order_delivered_customer_date > both.order_estimated_delivery_date
    d["delivery_days_describe"] = {k: round(float(v), 2) for k, v in days.describe(percentiles=[.5, .95, .99]).items()}
    d["delivery_days_gt_60"] = int((days > 60).sum())
    d["late_pct_delivered"] = pct(late.sum(), len(both))
    est_days = (both.order_estimated_delivery_date - both.order_purchase_timestamp).dt.total_seconds() / 86400
    d["estimated_days_median"] = round(float(est_days.median()), 1)
    # monthly coverage
    m = od.groupby(od.order_purchase_timestamp.dt.to_period("M")).size()
    full = pd.period_range(m.index.min(), m.index.max(), freq="M")
    m = m.reindex(full, fill_value=0)
    d["months_span"] = [str(full[0]), str(full[-1])]
    d["months_total"] = int(len(full))
    d["months_with_zero_orders"] = [str(p) for p in m.index[m == 0]]
    d["monthly_orders"] = {str(k): int(v) for k, v in m.items()}
    d["months_lt_100_orders"] = [str(k) for k, v in m.items() if 0 < v < 100]
    mdel = both.groupby(both.order_purchase_timestamp.dt.to_period("M")).size()
    d["peak_month"] = {"month": str(m.idxmax()), "orders": int(m.max())}
    # orders purchased but undelivered by month for last months (selection)
    d["canceled_or_unavailable"] = int(od.order_status.isin(["canceled", "unavailable"]).sum())
    # reviews timing & selection
    rv = rev.copy()
    rv["review_creation_date"] = pd.to_datetime(rv.review_creation_date, errors="coerce")
    rv["review_answer_timestamp"] = pd.to_datetime(rv.review_answer_timestamp, errors="coerce")
    rj = rv.merge(od[["order_id", "order_status", "order_delivered_customer_date", "order_purchase_timestamp"]], on="order_id", how="left")
    d["reviews_created_before_delivery"] = int((rj.review_creation_date < rj.order_delivered_customer_date.dt.normalize()).sum())
    d["reviews_for_undelivered_orders"] = int((rj.order_status != "delivered").sum())
    d["reviews_created_before_purchase"] = int((rj.review_creation_date < rj.order_purchase_timestamp.dt.normalize()).sum())
    d["review_answer_before_creation"] = int((rv.review_answer_timestamp < rv.review_creation_date).sum())
    d["delivered_orders_with_review_pct"] = pct(dl.order_id.isin(rev.order_id).sum(), len(dl))
    # review score by lateness (descriptive only, selection caveat)
    one_rev = rev.groupby("order_id").review_score.mean()
    bl = both.assign(late=late.values).merge(one_rev.rename("score"), left_on="order_id", right_index=True)
    d["mean_score_on_time"] = round(float(bl.loc[~bl.late, "score"].mean()), 2)
    d["mean_score_late"] = round(float(bl.loc[bl.late, "score"].mean()), 2)
    s["orders_profile"] = d

    # ---- attribution / distance feasibility ----
    f: dict = {}
    multi = sellers_per_order[sellers_per_order > 1].index
    f["delivered_with_date_multi_seller_orders"] = int(both.order_id.isin(multi).sum())
    f["orders_multi_seller_pct_of_items_orders"] = pct(len(multi), len(sellers_per_order))
    # seller-level: delivered orders per seller
    sd = it[it.order_id.isin(both.order_id)].drop_duplicates(["order_id", "seller_id"]).groupby("seller_id").size()
    f["sellers_with_delivered_orders"] = int(len(sd))
    for th in (10, 30, 100):
        f[f"sellers_ge_{th}_delivered_orders"] = int((sd >= th).sum())
    f["top_10pct_sellers_share_of_seller_orders"] = pct(sd.sort_values(ascending=False).head(max(1, int(len(sd) * .1))).sum(), sd.sum())
    # distance feasibility: orders where both zips have centroid
    cent = geo.groupby("geolocation_zip_code_prefix")[["geolocation_lat", "geolocation_lng"]].median()
    ci = it.merge(o[["order_id", "customer_id"]], on="order_id").merge(c[["customer_id", "customer_zip_code_prefix"]], on="customer_id").merge(sel[["seller_id", "seller_zip_code_prefix"]], on="seller_id")
    ok = ci.customer_zip_code_prefix.isin(cent.index) & ci.seller_zip_code_prefix.isin(cent.index)
    f["item_rows"] = len(ci)
    f["item_rows_with_both_zip_centroids"] = int(ok.sum())
    f["item_rows_with_both_zip_centroids_pct"] = pct(ok.sum(), len(ci))
    # sensitivity of centroid choice: spread of coordinates within zip (km, approximate)
    sp = geo.groupby("geolocation_zip_code_prefix").agg(lat_sd=("geolocation_lat", "std"), lng_sd=("geolocation_lng", "std"))
    f["geo_zip_median_coord_sd_km"] = round(float(np.nanmedian(np.hypot(sp.lat_sd * 111, sp.lng_sd * 111 * np.cos(np.radians(-15))))), 2)
    f["same_state_item_pct"] = pct((ci.merge(c[["customer_id", "customer_state"]], on="customer_id").merge(sel[["seller_id", "seller_state"]], on="seller_id").pipe(lambda x: x.customer_state == x.seller_state)).sum(), len(ci))
    s["feasibility_inputs"] = f

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
