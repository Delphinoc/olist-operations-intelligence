"""Validation of the Delivery Reliability workstream (analysis/delivery_reliability.py).

SQL results (DuckDB) are compared with an independent pandas recomputation from the raw CSVs
(tests/reference_pandas.py), and the statistical helpers are checked against known values / scipy.
Expected values are never copied from the analysis output.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import delivery_reliability as dr  # noqa: E402

EDGES = [(14, "1: <=14 days"), (21, "2: 15-21 days"), (28, "3: 22-28 days"), (35, "4: 29-35 days")]


@pytest.fixture(scope="session")
def res(con):
    return dr.compute_all(con, n_boot_overall=200, n_boot_month=100)


@pytest.fixture(scope="session")
def base(ref):
    """Independent pandas base population: delivered with date, purchases 2017-01..2018-08."""
    o = ref["orders_df"].copy()
    o["month"] = o.purchase_date.dt.to_period("M").dt.to_timestamp()
    o["err"] = (o.deliv_date - o.est_date).dt.days
    o["lead"] = (o.order_delivered_customer_date - o.order_purchase_timestamp).dt.total_seconds() / 86400
    o["promised"] = (o.est_date - o.purchase_date).dt.days
    return o[o.in_window & o.dated].copy(), o[o.in_window].copy()


def promised_group(days: pd.Series) -> pd.Series:
    out = pd.Series("5: 36+ days", index=days.index)
    for edge, label in reversed(EDGES):
        out[days <= edge] = label
    return out


# ------------------------------------------------------------------ statistical helpers
@pytest.mark.parametrize("k,n", [(0, 10), (5, 10), (10, 10), (81, 263), (6531, 96203), (1, 1000)])
def test_wilson_matches_scipy(k, n):
    from scipy.stats import binomtest
    ci = binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    lo, hi = dr.wilson(k, n)
    assert math.isclose(lo, ci.low, abs_tol=1e-9) and math.isclose(hi, ci.high, abs_tol=1e-9)


def test_wilson_edge_cases():
    assert all(math.isnan(v) for v in dr.wilson(0, 0))
    lo, hi = dr.wilson(0, 50)
    assert math.isclose(lo, 0.0, abs_tol=1e-12) and 0 < hi < 0.1


def test_newcombe_published_example():
    # Newcombe (1998), 56/70 vs 48/80: difference 0.2000, method 10 interval (0.0524, 0.3339)
    d, lo, hi = dr.newcombe_diff(56, 70, 48, 80)
    assert math.isclose(d, 0.2, abs_tol=1e-12)
    assert math.isclose(lo, 0.0524, abs_tol=5e-4) and math.isclose(hi, 0.3339, abs_tol=5e-4)


def test_bootstrap_is_seeded_and_brackets_estimate():
    x = np.random.default_rng(1).gamma(4, 3, size=2000)
    a = dr.bootstrap_quantiles(x, (0.5, 0.95), 300, np.random.default_rng(7))
    b = dr.bootstrap_quantiles(x, (0.5, 0.95), 300, np.random.default_rng(7))
    assert np.array_equal(a, b)
    for i, q in enumerate((0.5, 0.95)):
        assert a[i, 0] <= np.quantile(x, q) <= a[i, 1]


# ------------------------------------------------------------------ SQL KPIs vs pandas
def test_base_population_and_overall(res, base):
    b, _ = base
    assert res["overall"]["n_delivered"] == len(b) == 96_203
    assert res["overall"]["n_late"] == int(b.late.sum()) == 6_531
    assert res["overall"]["n_severe"] == int((b.err > 7).sum())
    assert res["overall"]["n_late_exact"] == int(b.late_exact.sum())
    lo, hi = res["overall"]["late_ci"]
    assert lo < res["overall"]["late_rate"] < hi


def test_monthly_kpis_match_pandas(res, base):
    b, _ = base
    m = res["monthly"].set_index(pd.to_datetime(res["monthly"].purchase_month))
    g = b.groupby("month")
    assert list(m.index) == list(g.size().index) and len(m) == 20
    assert (m.n_delivered.to_numpy() == g.size().to_numpy()).all()
    assert (m.n_late.to_numpy() == g.late.sum().to_numpy()).all()
    assert (m.n_severe.to_numpy() == g.err.apply(lambda s: int((s > 7).sum())).to_numpy()).all()
    assert (m.n_late_exact.to_numpy() == g.late_exact.sum().to_numpy()).all()
    for col, q in (("lead_median", 0.5), ("lead_p90", 0.9), ("lead_p95", 0.95)):
        assert np.allclose(m[col].to_numpy(), g.lead.quantile(q).to_numpy(), atol=1e-9), col
    assert np.allclose(m.promised_lead_median.to_numpy(), g.promised.median().to_numpy())
    assert np.allclose(m.promise_error_median.to_numpy(), g.err.median().to_numpy())
    # intervals bracket the point estimates
    assert ((m.late_rate_lo <= m.late_rate) & (m.late_rate <= m.late_rate_hi)).all()
    assert ((m.lead_median_lo <= m.lead_median) & (m.lead_median <= m.lead_median_hi)).all()


def test_overall_lead_time_quantiles(res, base):
    b, _ = base
    lo = res["lead_overall"]
    for name, q in (("median", 0.5), ("p90", 0.9), ("p95", 0.95)):
        assert math.isclose(lo[name], float(b.lead.quantile(q)), abs_tol=1e-9)
        assert lo[f"{name}_ci"][0] <= lo[name] <= lo[f"{name}_ci"][1]


def test_promise_error_distribution_and_summary(res, base):
    b, _ = base
    hist = res["promise_hist"].set_index("promise_error_days").n_orders
    assert hist.sum() == len(b)
    assert (hist == b.err.value_counts().sort_index()).all()
    s = res["promise_summary"]
    for key, q in (("error_p01", .01), ("error_p05", .05), ("error_p25", .25), ("error_p50", .5), ("error_p75", .75), ("error_p95", .95), ("error_p99", .99)):
        assert math.isclose(s[key], float(b.err.quantile(q)), abs_tol=1e-9), key
    assert s["n_on_promised_day"] == int((b.err == 0).sum())
    assert s["n_before_promised_day"] == int((b.err < 0).sum())
    assert s["n_7plus_days_early"] == int((b.err <= -7).sum()) and s["n_14plus_days_early"] == int((b.err <= -14).sum())
    assert math.isclose(s["slack_median_on_time"], float((-b.err[b.err <= 0]).median()))
    assert math.isclose(s["lead_to_promise_ratio_median"], float((b.lead / b.promised).median()), abs_tol=1e-9)


def test_severity_bands_partition(res, base):
    b, _ = base
    bands = res["severity_bands"].set_index("days_late_band").n_orders
    assert bands.sum() == len(b)
    assert bands["1-3"] == int(b.err.between(1, 3).sum()) and bands["4-7"] == int(b.err.between(4, 7).sum())
    assert bands["8+"] == int((b.err > 7).sum()) == res["overall"]["n_severe"]
    assert bands["1-3"] + bands["4-7"] + bands["8+"] == res["overall"]["n_late"]
    sl = res["severity_among_late"]
    assert sl["n_late"] == res["overall"]["n_late"] and sl["days_late_median"] == float(b.err[b.err > 0].median())


def test_fulfilment_outcomes_match_pandas_and_stay_separate(res, base):
    _, allw = base
    f = res["fulfilment_monthly"].set_index(pd.to_datetime(res["fulfilment_monthly"].purchase_month))
    exp = allw.groupby(["month", "fclass"]).size().unstack(fill_value=0).reindex(columns=dr.CLASSES, fill_value=0)
    assert (f[dr.CLASSES].to_numpy() == exp.to_numpy()).all()
    assert (f.n_orders.to_numpy() == exp.sum(axis=1).to_numpy()).all()
    assert np.allclose(f[[c + "_share" for c in dr.CLASSES]].sum(axis=1), 1.0)
    tot = res["fulfilment_total"]
    assert tot["n_orders"] == len(allw) == 99_092
    assert tot["open_not_yet_due"] == 0 and tot["delivered_status_no_date"] == 8
    # cancelled/unavailable are never mixed into the open class (by original status)
    sc = res["status_by_class"]
    assert set(sc[sc.fulfilment_class == "cancelled_unavailable"].order_status) == {"canceled", "unavailable"}
    assert set(sc[sc.fulfilment_class == "open_past_promise"].order_status) <= {"created", "approved", "invoiced", "processing", "shipped"}
    # delivered-only late rate (delivered KPIs) is not the same quantity as the share of all orders
    assert tot["delivered_late"] / tot["n_orders"] < res["overall"]["late_rate"]


def test_scenario_bounds_are_ordered_and_labelled_as_bounds(res):
    sb = res["scenario_bounds"]
    assert sb["late_rate_if_no_open_order_late"] < sb["late_rate_observed_delivered_only"] < sb["late_rate_if_all_open_orders_late"]
    assert sb["open_past_promise"] == res["fulfilment_total"]["open_past_promise"]


def test_promised_lead_groups_match_pandas(res, base):
    b, _ = base
    g = b.groupby(promised_group(b.promised))
    pg = res["promise_groups"].set_index("promised_group")
    assert (pg.n_delivered == g.size()).all() and pg.n_delivered.sum() == len(b)
    assert (pg.n_late == g.late.sum()).all()
    assert (pg.n_severe == g.err.apply(lambda s: int((s > 7).sum()))).all()
    assert np.allclose(pg.lead_median, g.lead.median(), atol=1e-9)
    assert ((pg.late_rate_lo <= pg.late_rate) & (pg.late_rate <= pg.late_rate_hi)).all()


def test_standardisation_identity_and_independent_expected(res, base):
    b, _ = base
    m = res["monthly"]
    assert math.isclose(m.expected_late.sum(), m.n_late.sum(), rel_tol=1e-9)       # sum of expected = sum of observed
    b = b.assign(grp=promised_group(b.promised))
    rate = b.groupby("grp").late.mean()
    exp = b.assign(r=b.grp.map(rate)).groupby("month").r.sum()
    assert np.allclose(m.expected_late.to_numpy(), exp.to_numpy(), atol=1e-6)
    assert np.allclose(m.oe_ratio, m.n_late / m.expected_late)


def test_high_volume_rule_and_comparison(res, base):
    b, _ = base
    counts = b.groupby("month").size().sort_values(ascending=False)
    top = set(counts.index[:5].strftime("%Y-%m-%d"))
    hv = res["high_volume"]
    assert set(hv["months"]) == top and hv["n_months"] == 20
    assert hv["n_high"] == int(counts.iloc[:5].sum())
    assert hv["late_high"] == int(b[b.month.isin(counts.index[:5])].late.sum())
    assert hv["diff_ci"][0] < hv["diff"] < hv["diff_ci"][1]
    assert hv["n_high"] + hv["n_other"] == len(b)
    pv = res["peak_vs_neighbours"]
    assert pv["peak_n"] == int((b.month == "2017-11-01").sum())
    assert pv["neighbour_n"] == int(b.month.isin(pd.to_datetime(["2017-10-01", "2017-12-01"])).sum())


# ------------------------------------------------------------------ sensitivity variants vs pandas
def _pandas_variant(b, end_excl, excl_anom, maturity, ref_date):
    d = b[b.purchase_date < pd.Timestamp(end_excl)]
    if excl_anom:
        d = d[~d.ts_violation]
    month_last = d.month + pd.offsets.MonthEnd(0)
    d = d[(month_last + pd.Timedelta(days=maturity)).dt.date <= ref_date]
    return len(d), int(d.late.sum()), int((d.err > 7).sum()), float(d.lead.median()), float(d.lead.quantile(0.95))


@pytest.mark.parametrize("label,override", list(dr.VARIANTS.items()))
def test_sensitivity_variants_match_pandas(res, base, ref, label, override):
    b, _ = base
    p = {**dr.BASE, **override}
    n, late, sev, med, p95 = _pandas_variant(b, p["end_excl"], p["excl_anom"], p["maturity_days"], ref["reference_date"])
    row = res["sensitivity"].set_index("variant").loc[label]
    assert (int(row.n_delivered), int(row.n_late)) == (n, late)
    assert math.isclose(row.severe_rate, sev / n, abs_tol=1e-12)
    assert math.isclose(row.lead_median, med, abs_tol=1e-9) and math.isclose(row.lead_p95, p95, abs_tol=1e-9)


def test_sensitivity_anchor_counts(res):
    s = res["sensitivity"].set_index("variant")
    assert s.loc["Base: purchases 2017-01..2018-08", "n_delivered"] == 96_203
    assert s.loc["Window ends 2018-07", "n_delivered"] == 89_852                    # kpi_validation q6 anchor
    assert math.isclose(s.loc["Window ends 2018-07", "late_rate"], 0.0683, abs_tol=5e-5)
    assert s.loc["Cohort maturity 60 days", "n_delivered"] == s.loc["Window ends 2018-07", "n_delivered"]
    assert s.loc["Cohort maturity 30 days", "n_delivered"] == 96_203               # 2018-08 ended > 30 days before extract end
    assert res["n_anomaly_orders_in_base"] == 96_203 - s.loc["Excluding timestamp-sequence anomalies", "n_delivered"] == 1_369


def test_anomaly_exclusion_keeps_non_delivered_orders(res):
    """Regression: NULL anomaly flags once dropped every non-delivered order from this sensitivity."""
    s = res["sensitivity"].set_index("variant")
    assert s.loc["Excluding timestamp-sequence anomalies", "n_orders_all"] > 97_000
    assert s.loc["Excluding timestamp-sequence anomalies", "open_past_promise_share"] > 0


# ------------------------------------------------------------------ outputs
def test_outputs_written_and_figures_nonempty(res, tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(dr, "FIG_DIR", tmp_path / "figures")
    monkeypatch.setattr(dr, "STATS_JSON", tmp_path / "stats.json")
    dr.write_outputs(res)
    figs = dr.make_figures(res)
    assert len(figs) == 7 and all(p.stat().st_size > 10_000 for p in figs)
    assert (tmp_path / "stats.json").stat().st_size > 1_000
    assert (tmp_path / "tables" / "delivery_monthly.csv").exists()
    assert len(pd.read_csv(tmp_path / "tables" / "delivery_monthly.csv")) == 20
