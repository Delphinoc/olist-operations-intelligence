"""Targeted KPI / methodology validation for the Olist dataset (raw CSVs read-only).

Usage (project root): python scripts/validate_kpis.py
Output: reports/kpi_validation_stats.json (consumed by scripts/build_kpi_report.py)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "reports" / "kpi_validation_stats.json"
TS = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
      "order_delivered_customer_date", "order_estimated_delivery_date"]


def pct(n: float, d: float) -> float:
    return round(100 * n / d, 2) if d else float("nan")


def main() -> None:
    o = pd.read_csv(RAW / "olist_orders_dataset.csv", parse_dates=TS)
    it = pd.read_csv(RAW / "olist_order_items_dataset.csv")
    rev = pd.read_csv(RAW / "olist_order_reviews_dataset.csv")
    cust = pd.read_csv(RAW / "olist_customers_dataset.csv")
    s: dict = {}

    # ---------- Q1: late-delivery rate ----------
    deliv = o[(o.order_status == "delivered") & o.order_delivered_customer_date.notna()].copy()
    N = len(deliv)
    est, act = deliv.order_estimated_delivery_date, deliv.order_delivered_customer_date
    deliv["late_date"] = act.dt.normalize() > est.dt.normalize()
    deliv["late_exact"] = act > est
    q1 = {"denominator": N,
          "late_calendar_n": int(deliv.late_date.sum()), "late_calendar_pct": pct(deliv.late_date.sum(), N),
          "late_exact_n": int(deliv.late_exact.sum()), "late_exact_pct": pct(deliv.late_exact.sum(), N),
          "late_exact_but_not_calendar": int((deliv.late_exact & ~deliv.late_date).sum()),
          "estimated_has_nonmidnight_time": int((est != est.dt.normalize()).sum()),
          "estimated_time_values": sorted({str(t) for t in est.dt.time.unique()})[:5]}
    s["q1"] = q1

    # ---------- Q2: multiple reviews ----------
    rev_n = rev.groupby("order_id").size()
    multi_ids = rev_n[rev_n > 1].index
    rm = rev[rev.order_id.isin(multi_ids)].copy()
    q2: dict = {"orders_with_multiple_review_rows": int(len(multi_ids)),
                "rows_in_those_orders": int(len(rm)),
                "max_rows_per_order": int(rev_n.max()),
                "exact_duplicate_rows_whole_table": int(rev.duplicated().sum()),
                "exact_duplicate_rows_within_multi_orders": int(rm.duplicated().sum()),
                "repeated_review_id_within_same_order": int(rm.duplicated(["order_id", "review_id"]).sum()),
                "review_ids_appearing_in_multiple_orders": int((rev.groupby("review_id").order_id.nunique() > 1).sum()),
                "rows_with_shared_review_id_across_orders": int(rev.review_id.duplicated(keep=False).sum())}
    content = ["review_score", "review_comment_title", "review_comment_message"]
    cls = {"same_score_and_text": 0, "same_score_different_text": 0, "different_score": 0}
    later_diff, same_creation_date = 0, 0
    score_change_up, score_change_down = 0, 0
    for oid, g in rm.groupby("order_id"):
        g = g.sort_values(["review_creation_date", "review_answer_timestamp"])
        if g.review_score.nunique() > 1:
            cls["different_score"] += 1
            d = g.review_score.iloc[-1] - g.review_score.iloc[0]
            score_change_up += int(d > 0)
            score_change_down += int(d < 0)
        elif g[content[1:]].fillna("").drop_duplicates().shape[0] > 1:
            cls["same_score_different_text"] += 1
        else:
            cls["same_score_and_text"] += 1
        if g.review_creation_date.nunique() == 1:
            same_creation_date += 1
        else:
            later_diff += 1
    q2["classification_of_multi_review_orders"] = cls
    q2["multi_orders_with_distinct_creation_dates"] = later_diff
    q2["multi_orders_with_identical_creation_date"] = same_creation_date
    q2["different_score_later_higher"] = score_change_up
    q2["different_score_later_lower"] = score_change_down
    q2["different_score_ordering_by_creation_date_is_ambiguous_when_dates_tie"] = True
    # delivered population review status
    deliv["n_reviews"] = deliv.order_id.map(rev_n).fillna(0).astype(int)
    q2["delivered_with_date_denominator"] = N
    q2["delivered_zero_reviews"] = int((deliv.n_reviews == 0).sum())
    q2["delivered_exactly_one_review"] = int((deliv.n_reviews == 1).sum())
    q2["delivered_multiple_reviews_ambiguous"] = int((deliv.n_reviews > 1).sum())
    all_deliv = o[o.order_status == "delivered"]
    q2["all_delivered_status_orders"] = len(all_deliv)
    q2["all_delivered_exactly_one_review"] = int((all_deliv.order_id.map(rev_n).fillna(0) == 1).sum())
    q2["all_delivered_multiple_reviews"] = int((all_deliv.order_id.map(rev_n).fillna(0) > 1).sum())
    s["q2"] = q2

    # ---------- Q3: on-time vs late review score ----------
    one = deliv[deliv.n_reviews == 1].merge(rev[["order_id", "review_score"]], on="order_id")
    grp = one.groupby("late_date").review_score.agg(["size", "mean", "std"])
    q3 = {"population": int(len(one)), "excluded_zero_reviews": q2["delivered_zero_reviews"],
          "excluded_multiple_reviews": q2["delivered_multiple_reviews_ambiguous"],
          "on_time_n": int(grp.loc[False, "size"]), "on_time_mean": round(float(grp.loc[False, "mean"]), 3),
          "late_n": int(grp.loc[True, "size"]), "late_mean": round(float(grp.loc[True, "mean"]), 3),
          "mean_difference": round(float(grp.loc[False, "mean"] - grp.loc[True, "mean"]), 3),
          "late_share_1_2_star_pct": pct(((one.late_date) & (one.review_score <= 2)).sum(), one.late_date.sum()),
          "ontime_share_1_2_star_pct": pct(((~one.late_date) & (one.review_score <= 2)).sum(), (~one.late_date).sum()),
          "missing_review_rate_late_pct": pct(((deliv.n_reviews == 0) & deliv.late_date).sum(), deliv.late_date.sum()),
          "missing_review_rate_ontime_pct": pct(((deliv.n_reviews == 0) & ~deliv.late_date).sum(), (~deliv.late_date).sum()),
          "multi_review_rate_late_pct": pct(((deliv.n_reviews > 1) & deliv.late_date).sum(), deliv.late_date.sum()),
          "multi_review_rate_ontime_pct": pct(((deliv.n_reviews > 1) & ~deliv.late_date).sum(), (~deliv.late_date).sum()),
          "late_total_n": int(deliv.late_date.sum()), "ontime_total_n": int((~deliv.late_date).sum())}
    # review created before delivery (timing) within this population
    one["rcd"] = pd.to_datetime(rev.set_index("order_id").loc[one.order_id, "review_creation_date"].values)
    q3["reviews_created_before_delivery_date_n"] = int((one.rcd < one.order_delivered_customer_date.dt.normalize()).sum())
    s["q3"] = q3

    # ---------- Q4: multi-seller reconciliation ----------
    n_orders = len(o)
    sellers = it.groupby("order_id").seller_id.nunique()
    ow = len(sellers)
    multi_s = int((sellers > 1).sum())
    deliv["n_sellers"] = deliv.order_id.map(sellers).fillna(0).astype(int)
    all_deliv = all_deliv.assign(n_sellers=all_deliv.order_id.map(sellers).fillna(0).astype(int))
    s["q4"] = {"all_orders": n_orders, "orders_with_items": ow, "multi_seller_orders": multi_s,
               "multi_seller_pct_all_orders": pct(multi_s, n_orders),
               "multi_seller_pct_orders_with_items": pct(multi_s, ow),
               "single_seller_orders": int((sellers == 1).sum()),
               "delivered_with_date_denominator": N,
               "delivered_exactly_one_seller": int((deliv.n_sellers == 1).sum()),
               "delivered_multi_seller": int((deliv.n_sellers > 1).sum()),
               "delivered_no_items": int((deliv.n_sellers == 0).sum()),
               "delivered_multi_seller_pct": pct((deliv.n_sellers > 1).sum(), N),
               "all_delivered_status_exactly_one_seller": int((all_deliv.n_sellers == 1).sum())}

    # ---------- Q5: timestamp sequence violations ----------
    P, A, C, D_, E = (o.order_purchase_timestamp, o.order_approved_at, o.order_delivered_carrier_date,
                      o.order_delivered_customer_date, o.order_estimated_delivery_date)
    v = pd.DataFrame({"approved_lt_purchase": A < P, "carrier_lt_purchase": C < P,
                      "carrier_lt_approved": C < A, "customer_lt_purchase": D_ < P,
                      "customer_lt_carrier": D_ < C, "estimated_lt_purchase": E < P,
                      "estimated_lt_approved": E < A})
    any_v = v[["approved_lt_purchase", "carrier_lt_purchase", "carrier_lt_approved",
               "customer_lt_purchase", "customer_lt_carrier"]].any(axis=1)
    q5 = {"pair_counts": {k: int(x.sum()) for k, x in v.items()},
          "orders_with_any_of_five_checked": int(any_v.sum()),
          "carrier_lt_purchase_or_approved": int((v.carrier_lt_purchase | v.carrier_lt_approved).sum()),
          "carrier_lt_purchase_and_approved_both": int((v.carrier_lt_purchase & v.carrier_lt_approved).sum()),
          "customer_lt_carrier_only_violation": int((v.customer_lt_carrier & ~(v.carrier_lt_purchase | v.carrier_lt_approved)).sum())}
    pop_mask = o.order_id.isin(deliv.order_id)
    q5["violations_inside_primary_population"] = int((any_v & pop_mask).sum())
    q5["violations_outside_primary_population"] = int((any_v & ~pop_mask).sum())
    q5["violations_involving_purchase_or_customer_delivery_in_pop"] = int(((v.customer_lt_purchase | v.carrier_lt_purchase) & pop_mask).sum())
    # approved-to-carrier: magnitude of carrier<approved
    gap_h = ((A - C)[v.carrier_lt_approved]).dt.total_seconds() / 3600
    q5["carrier_lt_approved_gap_hours_median"] = round(float(gap_h.median()), 2)
    q5["carrier_lt_approved_gap_hours_p95"] = round(float(gap_h.quantile(.95)), 2)
    # KPI impact
    dur = (deliv.order_delivered_customer_date - deliv.order_purchase_timestamp).dt.total_seconds() / 86400
    bad = o.set_index("order_id").loc[deliv.order_id, :]
    bad_flag = any_v.set_axis(o.order_id).loc[deliv.order_id].values
    q5["primary_kpi_inputs"] = {"purchase_to_delivery_nonpositive": int((dur <= 0).sum()),
                                "estimated_before_purchase_in_pop": int((deliv.order_estimated_delivery_date < deliv.order_purchase_timestamp).sum())}
    q5["late_rate_calendar_all_pct"] = pct(deliv.late_date.sum(), N)
    q5["late_rate_calendar_excl_violations_pct"] = pct(deliv.late_date[~bad_flag].sum(), (~bad_flag).sum())
    q5["late_rate_calendar_excl_violations_denominator"] = int((~bad_flag).sum())
    q5["median_days_all"] = round(float(dur.median()), 3)
    q5["median_days_excl_violations"] = round(float(dur[~bad_flag].median()), 3)
    q5["mean_days_all"] = round(float(dur.mean()), 3)
    q5["mean_days_excl_violations"] = round(float(dur[~bad_flag].mean()), 3)
    # status/timestamp conflicts
    q5["status_conflicts"] = {
        "delivered_status_no_customer_date": int(((o.order_status == "delivered") & D_.isna()).sum()),
        "non_delivered_with_customer_date": int(((o.order_status != "delivered") & D_.notna()).sum())}
    s["q5"] = q5

    # ---------- Q6: populations, windows, minimum N ----------
    deliv["month"] = deliv.order_purchase_timestamp.dt.to_period("M")
    mo = o.assign(month=o.order_purchase_timestamp.dt.to_period("M")).groupby("month").agg(
        orders=("order_id", "size"), delivered=("order_status", lambda x: (x == "delivered").sum()))
    mo["delivered_pct"] = (100 * mo.delivered / mo.orders).round(1)
    mo["with_date"] = deliv.groupby("month").size().reindex(mo.index, fill_value=0)
    s["q6_months"] = {str(k): {"orders": int(r.orders), "delivered_with_date": int(r.with_date),
                                "delivered_pct": float(r.delivered_pct)} for k, r in mo.iterrows()}
    # latest delivery date observed vs max purchase: censoring check
    s["q6_latest_purchase"] = str(o.order_purchase_timestamp.max())
    s["q6_latest_customer_delivery"] = str(D_.max())
    # window sensitivity
    def window(lo: str, hi: str) -> dict:
        m = deliv[(deliv.month >= pd.Period(lo)) & (deliv.month <= pd.Period(hi))]
        return {"n": int(len(m)), "late_calendar_pct": pct(m.late_date.sum(), len(m))}
    s["q6_window_candidates"] = {"2017-01..2018-08": window("2017-01", "2018-08"),
                                 "2017-01..2018-07": window("2017-01", "2018-07"),
                                 "all": window("2016-09", "2018-10")}
    # state / seller sample sizes in candidate window
    w = deliv[(deliv.month >= pd.Period("2017-01")) & (deliv.month <= pd.Period("2018-08"))]
    w = w.merge(cust[["customer_id", "customer_state"]], on="customer_id")
    sc = w.groupby("customer_state").size()
    s["q6_state_n"] = {"states": int(len(sc)), "ge_30": int((sc >= 30).sum()), "ge_100": int((sc >= 100).sum()),
                       "ge_300": int((sc >= 300).sum()), "min": int(sc.min()),
                       "below_100": {k: int(v) for k, v in sc[sc < 100].items()}}
    single = w[w.n_sellers == 1].merge(it[["order_id", "seller_id"]].drop_duplicates(), on="order_id")
    sn = single.groupby("seller_id").size()
    s["q6_seller_n"] = {"window_single_seller_delivered_orders": int(len(single)),
                        "sellers": int(len(sn)), "ge_30": int((sn >= 30).sum()), "ge_50": int((sn >= 50).sum()),
                        "ge_100": int((sn >= 100).sum()),
                        "orders_in_sellers_ge_30_pct": pct(sn[sn >= 30].sum(), sn.sum()),
                        "orders_in_sellers_ge_50_pct": pct(sn[sn >= 50].sum(), sn.sum())}
    # share of late-rate noise: approx binomial half-width at p=0.08 for n=30/100/300
    s["q6_ci_halfwidth_pp_at_p08"] = {str(n_): round(196 * np.sqrt(.08 * .92 / n_), 1) for n_ in (30, 50, 100, 300)}

    OUT.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
