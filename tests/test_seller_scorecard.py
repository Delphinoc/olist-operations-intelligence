"""Validation of the seller scorecard workstream (analysis/seller_scorecard.py).

Seller-quarter metrics are recomputed independently in pandas from the raw CSVs (tests/reference_pandas.py plus the
raw items file); status, quarter-on-quarter and QBR logic are checked against hand-built cases. Expected values are
never copied from the analysis output.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "analysis"))
import seller_scorecard as sc  # noqa: E402


@pytest.fixture(scope="session")
def res(con):
    return sc.compute_all(con)


@pytest.fixture(scope="session")
def pd_seller_quarter(ref):
    """Independent seller x quarter aggregation from the raw CSVs."""
    o = ref["orders_df"]
    o = o[o.in_window & o.dated & (o.n_sellers == 1)].copy()
    it = pd.read_csv(RAW / "olist_order_items_dataset.csv")
    items = it.groupby("order_id").agg(seller_id=("seller_id", "first"), gmv=("price", "sum")).reset_index()
    o = o.merge(items, on="order_id", how="left")
    o["quarter_start"] = o.purchase_date.dt.to_period("Q").dt.start_time.dt.date
    o["p0"] = o.review_row_count == 1
    o["p0_score"] = o.review_score.where(o.p0)
    g = o.groupby(["seller_id", "quarter_start"]).agg(
        orders=("order_id", "size"), gmv=("gmv", "sum"), late_orders=("late", "sum"),
        review_orders=("p0_score", "count"), review_score_sum=("p0_score", "sum"),
        customers=("customer_unique_id", "nunique"))
    return g.reset_index()


def test_population_total(res, ref):
    s = res["seller_quarter"]
    assert s.orders.sum() == ref["single_seller_window"] == 94_931
    assert res["marketplace"].orders.sum() == ref["single_seller_window"]


def test_seller_quarter_matches_pandas(res, pd_seller_quarter):
    s = res["seller_quarter"].set_index(["seller_id", "quarter_start"]).sort_index()
    p = pd_seller_quarter.set_index(["seller_id", "quarter_start"]).sort_index()
    assert len(s) == len(p)
    assert (s.index == p.index).all()
    for col in ["orders", "late_orders", "review_orders", "customers"]:
        assert (s[col].astype(int) == p[col].astype(int)).all(), col
    assert np.allclose(s.gmv, p.gmv, atol=0.01)
    assert np.allclose(s.review_score_sum.fillna(0), p.review_score_sum, atol=1e-9)


def test_marketplace_equals_sum_of_sellers(res):
    s, m = res["seller_quarter"], res["marketplace"].set_index("quarter_start")
    g = s.groupby("quarter_start")[["orders", "gmv", "late_orders"]].sum()
    assert (g.orders == m.orders).all()
    assert (g.late_orders == m.late_orders).all()
    assert np.allclose(g.gmv, m.gmv, atol=0.05)
    assert np.allclose(s.groupby("quarter_start").gmv_share_of_marketplace.sum(), 1.0, atol=1e-6)


def test_quarters_and_partial_flag(res):
    m = res["marketplace"]
    assert list(m.quarter_label) == ["2017-Q1", "2017-Q2", "2017-Q3", "2017-Q4", "2018-Q1", "2018-Q2", "2018-Q3"]
    assert m.is_partial_quarter.tolist() == [False] * 6 + [True]
    assert pd.Timestamp(m.last_purchase.max()) < pd.Timestamp("2018-09-01")


@pytest.mark.parametrize("on_time,review,expected", [
    (True, True, "On track"), (True, False, "Watch"), (False, True, "Watch"), (False, False, "Review")])
def test_status_rule(on_time, review, expected):
    assert sc.status_of(on_time, review) == expected


def test_status_assignment_against_rule(res):
    s = res["seller_quarter"]
    scored = s[s.is_scored]
    assert (scored.orders >= sc.MIN_SCORED_ORDERS).all()
    assert s.loc[~s.is_scored, "status"].isna().all()
    ok_t = scored.on_time_vs_bench_pp >= -sc.TOL_ON_TIME_PP
    ok_r = scored.review_vs_bench.fillna(-np.inf) >= -sc.TOL_REVIEW
    expected = np.select([ok_t & ok_r, ok_t | ok_r], ["On track", "Watch"], "Review")
    assert (scored.status.to_numpy() == expected).all()


def test_qoq_uses_previous_calendar_quarter(res):
    s = res["seller_quarter"].set_index(["seller_id", "quarter_start"])
    qs = sorted(res["marketplace"].quarter_start)
    checked = 0
    for (sid, q), r in s[s.has_prev_quarter].sample(300, random_state=1).iterrows():
        prev = s.loc[(sid, qs[qs.index(q) - 1])]
        assert r.prev_gmv == pytest.approx(prev.gmv)
        assert r.gmv_qoq_pct == pytest.approx(r.gmv / prev.gmv - 1)
        if r.is_scored and prev.is_scored:
            assert r.on_time_qoq_pp == pytest.approx((r.on_time_rate - prev.on_time_rate) * 100)
        else:
            assert np.isnan(r.on_time_qoq_pp)
        checked += 1
    assert checked == 300
    # A seller absent in the previous quarter has no comparison.
    first = s.reset_index().groupby("seller_id").quarter_start.transform("min") == s.reset_index().quarter_start
    assert not s.reset_index()[first.to_numpy()].has_prev_quarter.any()


def test_qbr_snapshot(res):
    q, s = res["qbr"], res["seller_quarter"]
    cur = s[(s.quarter_start == sc.QBR_QUARTER) & s.is_scored]
    st = q["stats"]
    assert st["scored_sellers"] == len(cur)
    assert sum(st["status_counts"].values()) == len(cur)
    assert sum(st["status_gmv_share"].values()) == pytest.approx(1.0)
    top = q["top"]
    assert len(top) == sc.TOP_N
    assert top.gmv.is_monotonic_decreasing
    assert top.gmv.iloc[-1] >= cur.gmv.nlargest(sc.TOP_N).iloc[-1] - 1e-9
    assert (q["review_list"].status == "Review").all()
    assert st["marketplace_gmv_qoq_pct"] == pytest.approx(st["marketplace_gmv"] / st["marketplace_gmv_prev"] - 1)
