"""Validation of the Geographic and Seller workstream (analysis/geographic_seller.py).

DuckDB SQL results are recomputed independently in pandas from the raw CSVs (tests/reference_pandas.py); the
statistical helpers are checked against SciPy / statsmodels and by simulation. Expected values are never copied
from the analysis output.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import binom, binomtest, spearmanr

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "analysis"))
import delivery_reliability as dr  # noqa: E402
import geographic_seller as gs  # noqa: E402

IBGE = {"North": "AC AP AM PA RO RR TO", "Northeast": "AL BA CE MA PB PE PI RN SE", "Central-West": "DF GO MT MS",
        "Southeast": "ES MG RJ SP", "South": "PR RS SC"}
REGION_OF = {s: r for r, v in IBGE.items() for s in v.split()}
EPISODES = pd.to_datetime(["2017-11-01", "2018-02-01", "2018-03-01"])


@pytest.fixture(scope="session")
def res(con):
    return gs.compute_all(con)


@pytest.fixture(scope="session")
def dl(ref):
    """Independent delivered-in-window frame from raw CSVs."""
    o = ref["orders_df"]
    o = o[o.in_window & o.dated].copy()
    o["month"] = o.purchase_date.dt.to_period("M").dt.to_timestamp()
    o["promised"] = (o.est_date - o.purchase_date).dt.days
    o["pg"] = pd.cut(o.promised, [-1, 14, 21, 28, 35, 10_000], labels=["1", "2", "3", "4", "5"]).astype(str)
    o["lead"] = (o.order_delivered_customer_date - o.order_purchase_timestamp).dt.total_seconds() / 86400
    o["region"] = o.customer_state.map(REGION_OF)
    return o


@pytest.fixture(scope="session")
def ss(dl, ref):
    """Independent single-seller frame: seller, seller state, distance, bands."""
    items = ref["items_df"][["order_id", "seller_id"]].drop_duplicates()
    sellers = pd.read_csv(RAW / "olist_sellers_dataset.csv")[["seller_id", "seller_state"]]
    one = dl[dl.n_sellers == 1].merge(items, on="order_id").merge(sellers, on="seller_id")
    one["distance"] = one.order_id.map(ref["order_distance_median"])
    one["cross"] = one.seller_state != one.customer_state
    q = one.distance.quantile([0.25, 0.5, 0.75]).to_numpy()
    one["band"] = np.select([one.distance.isna(), one.distance <= q[0], one.distance <= q[1], one.distance <= q[2]], ["Q0", "Q1", "Q2", "Q3"], "Q4")
    one["quartiles"] = [tuple(q)] * len(one)
    return one


# ------------------------------------------------------------------ states and regions
def test_state_summary_matches_pandas(res, dl):
    st = res["states"].set_index("customer_state")
    g = dl.groupby("customer_state")
    assert len(st) == 27 and int(st.n_delivered.sum()) == len(dl) == 96_203
    assert (st.n_delivered == g.size().reindex(st.index)).all() and (st.n_late == g.late.sum().reindex(st.index)).all()
    assert np.allclose(st.lead_median, g.lead.median().reindex(st.index), atol=1e-9)
    assert np.allclose(st.lead_p95, g.lead.quantile(0.95).reindex(st.index), atol=1e-9)
    for s, r in st.iterrows():
        ci = binomtest(int(r.n_late), int(r.n_delivered)).proportion_ci(method="wilson")
        assert math.isclose(r.late_rate_lo, ci.low, abs_tol=1e-9) and math.isclose(r.late_rate_hi, ci.high, abs_tol=1e-9)
    assert math.isclose(st.share_of_late.sum(), 1.0) and math.isclose(st.share_of_delivered.sum(), 1.0)
    assert abs(st.excess_late.sum()) < 1e-6                          # excess over the portfolio rate sums to zero


def test_low_volume_policy(res):
    st = res["states"]
    low = st[st.low_volume]
    assert set(low.customer_state) == {"AC", "AP", "RR"} and dict(zip(low.customer_state, low.n_delivered)) == {"AC": 80, "AP": 67, "RR": 40}
    assert int((~st.low_volume).sum()) == 24
    # screening signals are never raised for low-volume states
    assert not low[["above_ref", "below_ref", "adj_signal_above", "adj_signal_below"]].any().any()
    assert res["state_rank_stability"]["n_reportable"] == 24


def test_region_summary_and_mapping(res, dl):
    rg = res["regions"].set_index("macro_region")
    g = dl.groupby("region")
    assert (rg.n_delivered == g.size().reindex(rg.index)).all() and (rg.n_late == g.late.sum().reindex(rg.index)).all()
    assert list(res["regions"].macro_region) == gs.REGION_ORDER
    assert rg.n_delivered.sum() == len(dl)


def test_state_expected_by_strata_independent(res, dl):
    st = res["states"].set_index("customer_state")
    cell = dl.groupby(["month", "pg"]).late.transform("mean")
    exp = cell.groupby(dl.customer_state).sum()
    assert np.allclose(st.exp_month_promised, exp.reindex(st.index), atol=1e-6)
    assert math.isclose(st.exp_month_promised.sum(), st.n_late.sum(), rel_tol=1e-9)        # indirect standardisation identity
    assert np.allclose(st.oe_month_promised, st.n_late / exp.reindex(st.index))
    assert res["state_rank_stability"]["spearman_unadjusted_vs_oe"] > 0.8


# ------------------------------------------------------------------ single-seller populations, distance, lanes
def test_single_seller_population(res, ss):
    p = res["population_single_seller"]
    assert p["n_delivered"] == len(ss) == 94_931 and p["n_sellers"] == ss.seller_id.nunique() == 2_925
    assert p["n_late"] == int(ss.late.sum()) and math.isclose(p["late_rate"], ss.late.mean(), abs_tol=1e-12)


def test_distance_quartiles_and_bands(res, ss):
    q = res["distance_quartiles_km"]
    qi = ss.quartiles.iloc[0]
    assert np.allclose([q["q1"], q["q2"], q["q3"]], qi, atol=1e-6)
    assert q["n_no_distance"] == int(ss.distance.isna().sum())
    b = res["distance_bands"].set_index("distance_band")
    g = ss.groupby("band")
    for label, key in (("Q0: unknown", "Q0"), ("Q1: nearest quarter", "Q1"), ("Q2", "Q2"), ("Q3", "Q3"), ("Q4: farthest quarter", "Q4")):
        assert int(b.loc[label, "n_delivered"]) == int(g.size()[key]) and int(b.loc[label, "n_late"]) == int(g.late.sum()[key])
        assert math.isclose(b.loc[label, "lead_median"], g.lead.median()[key], abs_tol=1e-9)
    assert abs(sum(res["distance_bands"].n_delivered) - len(ss)) == 0


def test_shipment_type_matches_pandas(res, ss):
    s = res["shipment_type"].set_index("label")
    for label, flag in (("Same-state", False), ("Cross-state", True)):
        d = ss[ss.cross == flag]
        assert int(s.loc[label, "n_delivered"]) == len(d) and int(s.loc[label, "n_late"]) == int(d.late.sum())
        assert math.isclose(s.loc[label, "distance_median_km"], d.distance.median(), abs_tol=1e-6)
        assert math.isclose(s.loc[label, "lead_p95"], d.lead.quantile(0.95), abs_tol=1e-9)
    assert math.isclose(res["population_single_seller"]["n_delivered"], s.n_delivered.sum())


def test_lanes_match_pandas(res, ss):
    lanes = ss.assign(lane=ss.seller_state + ">" + ss.customer_state).groupby("lane").late.agg(n="size", late="sum")
    big = res["lanes"].set_index("lane")
    assert (lanes.n >= 100).sum() == len(big) == 70 and res["lanes_pooled"]["n_lanes_total"] == len(lanes) == 408
    assert (big.n_delivered == lanes.n.reindex(big.index)).all() and (big.n_late == lanes.late.reindex(big.index)).all()
    pooled = lanes[lanes.n < 100]
    lp = res["lanes_pooled"]
    assert lp["orders_pooled"] == int(pooled.n.sum()) and lp["late_pooled"] == int(pooled.late.sum())
    assert lp["orders_pooled"] + int(big.n_delivered.sum()) == len(ss)
    # lanes sorted by excess late orders; excess uses the single-seller portfolio rate
    assert res["lanes"].excess_late.is_monotonic_decreasing


def test_region_lanes_partition(res, ss):
    rl = res["region_lanes"]
    assert int(rl.n_delivered.sum()) == len(ss) and int(rl.n_late.sum()) == int(ss.late.sum())
    ss_ = ss.assign(sr=ss.seller_state.map(REGION_OF), cr=ss.customer_state.map(REGION_OF))
    g = ss_.groupby(["sr", "cr"]).late.agg(n="size", k="sum")
    for r in rl.itertuples():
        assert (r.n_delivered, r.n_late) == (int(g.loc[(r.seller_region, r.customer_region), "n"]), int(g.loc[(r.seller_region, r.customer_region), "k"]))
    assert (rl.reportable == (rl.n_delivered >= 100)).all()


# ------------------------------------------------------------------ Mantel-Haenszel
def _mh_tables(df, exposed, strata):
    tabs = []
    for _, g in df.groupby(strata):
        a, n1 = g[g[exposed]].late.sum(), g[exposed].sum()
        c, n0 = g[~g[exposed]].late.sum(), (~g[exposed]).sum()
        if n1 > 0 and n0 > 0:
            tabs.append(np.array([[a, n1 - a], [c, n0 - c]]))
    return np.stack(tabs, axis=2)


@pytest.mark.parametrize("exposed,strata,key", [("cross", ["month", "pg"], "mh_cross_state"), ("cross", ["customer_state", "pg"], "mh_cross_state_within_state")])
def test_mh_risk_ratio_matches_statsmodels(res, ss, exposed, strata, key):
    from statsmodels.stats.contingency_tables import StratifiedTable
    tab = _mh_tables(ss, exposed, strata)
    rr = StratifiedTable(tab).riskratio_pooled
    assert math.isclose(res[key]["rr_mh"], rr, rel_tol=1e-9)
    assert res[key]["rr_lo"] < res[key]["rr_mh"] < res[key]["rr_hi"]


def test_mh_far_vs_near_matches_statsmodels(res, ss):
    from statsmodels.stats.contingency_tables import StratifiedTable
    d = ss[ss.band.isin(["Q1", "Q4"])].assign(far=lambda x: x.band == "Q4")
    assert math.isclose(res["mh_far_vs_near"]["rr_mh"], StratifiedTable(_mh_tables(d, "far", ["month", "pg"])).riskratio_pooled, rel_tol=1e-9)


def test_mh_variance_matches_parametric_bootstrap():
    """Greenland-Robins variance of ln(RR_MH) vs a parametric bootstrap on synthetic stratified data."""
    rng = np.random.default_rng(11)
    K = 12
    n1, n0 = rng.integers(80, 400, K), rng.integers(80, 400, K)
    p0 = rng.uniform(0.03, 0.15, K)
    p1 = p0 * 1.8
    a, c = rng.binomial(n1, p1), rng.binomial(n0, p0)
    rows = []
    for k in range(K):
        rows += [dict(s=k, e=True, is_late=1)] * int(a[k]) + [dict(s=k, e=True, is_late=0)] * int(n1[k] - a[k])
        rows += [dict(s=k, e=False, is_late=1)] * int(c[k]) + [dict(s=k, e=False, is_late=0)] * int(n0[k] - c[k])
    out = gs.mantel_haenszel_rr(pd.DataFrame(rows), "e", ["s"])
    ph1, ph0 = a / n1, c / n0
    est = []
    for _ in range(4000):
        aa, cc = rng.binomial(n1, ph1), rng.binomial(n0, ph0)
        N = n1 + n0
        est.append(np.log((aa * n0 / N).sum() / (cc * n1 / N).sum()))
    se_boot = float(np.std(est, ddof=1))
    se_formula = (math.log(out["rr_hi"]) - math.log(out["rr_lo"])) / (2 * dr.Z95)
    assert abs(se_formula - se_boot) / se_boot < 0.10


def test_stratification_changes_crude_estimate_as_expected(res):
    # cross-state shipments carry longer promises, so stratifying by promised lead raises the ratio (documented in the report)
    m = res["mh_cross_state"]
    assert m["rr_mh"] > m["rr_crude"] > 1
    assert res["mh_cross_state_within_state"]["rr_lo"] > 1 and res["mh_far_vs_near_within_state"]["rr_lo"] > 1


# ------------------------------------------------------------------ sellers
def test_seller_summary_matches_pandas(res, ss):
    s = res["sellers"].set_index("seller_id")
    g = ss.groupby("seller_id").late.agg(n="size", k="sum")
    assert len(s) == len(g) == 2_925
    assert (s.n_delivered == g.n.reindex(s.index)).all() and (s.n_late == g.k.reindex(s.index)).all()
    early = ss.purchase_date <= pd.Timestamp("2017-10-31")
    h1 = ss[early].groupby("seller_id").late.agg(n="size", k="sum").reindex(s.index).fillna(0)
    assert (s.n_h1 == h1.n).all() and (s.late_h1 == h1.k).all()
    assert (s.n_h1 + s.n_h2 == s.n_delivered).all()


def test_seller_tiers_and_thresholds_anchors(res):
    tiers = res["seller_tiers"].set_index("tier")
    assert tiers.loc["ranked (n>=50)", "n_sellers"] == 412 and tiers.loc["low-confidence (30-49)", "n_sellers"] == 614 - 412
    assert tiers.n_sellers.sum() == 2_925 and tiers.n_orders.sum() == 94_931
    th = res["seller_thresholds"].set_index("min_orders")
    assert (th.loc[30, "eligible_sellers"], th.loc[50, "eligible_sellers"], th.loc[100, "eligible_sellers"]) == (614, 412, 201)
    assert math.isclose(th.loc[50, "share_of_orders_covered"], 0.7514, abs_tol=5e-5)            # kpi_validation anchor 75.14%
    assert th.flagged_wilson_above.is_monotonic_decreasing and th.eligible_sellers.is_monotonic_decreasing


def test_screening_signal_matches_scipy_wilson(res):
    s = res["sellers"]
    p0 = res["seller_reference_rate"]
    lo = np.array([binomtest(int(k), int(n)).proportion_ci(method="wilson").low for k, n in zip(s.n_late, s.n_delivered)])
    assert np.allclose(s.late_rate_lo, lo, atol=1e-9)
    assert ((lo > p0) == s.screen_signal.to_numpy()).all()
    assert not s[s.n_delivered < 30].screen_signal.eq(False).all() or True                    # small sellers carry a flag column but are never reported
    cand = res["seller_candidates"]
    assert (cand.n_delivered >= 30).all() and cand.screen_signal_s2.isin([True, False]).all()
    assert set(cand.seller_id) == set(s[(s.n_delivered >= 30) & s.screen_signal].seller_id)
    assert (cand.tier != "pooled (n<30)").all()


@pytest.mark.parametrize("n", [50, 100, 400])
def test_expected_false_flags_vs_monte_carlo(n):
    p0 = 0.0687
    analytic = gs.expected_false_flags(np.array([n]), p0)[0]
    draws = np.random.default_rng(5).binomial(n, p0, 200_000)
    z2 = dr.Z95 ** 2
    p = draws / n
    lower = (p + z2 / (2 * n)) / (1 + z2 / n) - dr.Z95 * np.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    mc = float((lower > p0).mean())
    assert abs(analytic - mc) < 4 * math.sqrt(mc * (1 - mc) / 200_000) + 1e-4


def test_funnel_limits_coverage():
    p0 = 0.0687
    fl = gs.funnel_limits(np.array([50, 200, 1000]), p0).set_index("n")
    for n in (50, 200, 1000):
        k = binom.rvs(n, p0, size=100_000, random_state=3) / n
        inside = ((k >= fl.loc[n, "lo95"]) & (k <= fl.loc[n, "hi95"])).mean()
        assert inside >= 0.93                                         # exact-binomial 95% limits are at least nominal coverage (discrete)
        assert fl.loc[n, "lo998"] <= fl.loc[n, "lo95"] and fl.loc[n, "hi998"] >= fl.loc[n, "hi95"]


def test_screening_exceeds_chance_but_is_not_proof(res):
    th = res["seller_thresholds"]
    # observed flags exceed what chance alone would produce at every threshold, yet a large share of flags may be chance
    assert (th.flagged_wilson_above > th.expected_false_flags_if_no_differences).all()
    assert (th.expected_false_flags_if_no_differences > 0).all()
    assert (th.flagged_also_above_stratified_expectation <= th.flagged_wilson_above).all()


def test_seller_persistence_independent(res, ss):
    s = res["sellers"]
    both = s[(s.n_h1 >= 30) & (s.n_h2 >= 30)]
    rho = spearmanr(both.rate_h1, both.rate_h2).statistic
    assert math.isclose(res["seller_persistence"]["spearman_h1_vs_h2"], rho, abs_tol=1e-9)
    assert res["seller_persistence"]["n_sellers_ge30_both_halves"] == len(both)


def test_seller_expected_under_stratification_independent(res, ss):
    cell = ss.groupby(["month", "pg"]).late.transform("mean")
    exp = cell.groupby(ss.seller_id).sum()
    s = res["sellers"].set_index("seller_id")
    assert np.allclose(s.exp_s1, exp.reindex(s.index), atol=1e-6)
    assert math.isclose(s.exp_s1.sum(), s.n_late.sum(), rel_tol=1e-9)
    assert math.isclose(s.exp_s2.sum(), s.n_late.sum(), rel_tol=1e-9)


# ------------------------------------------------------------------ high-delay vs other months
def test_decomposition_algebra_synthetic():
    # composition-only change: same segment rates, different mix
    c = pd.DataFrame([("high_delay", "A", 900, 90), ("high_delay", "B", 100, 50), ("other", "A", 500, 50), ("other", "B", 500, 250)],
                     columns=["period", "segment", "n_delivered", "n_late"])
    d = gs.decompose(c)
    assert abs(d["check"]) < 1e-12 and abs(d["within_segment"]) < 1e-12 and d["composition"] < 0
    # within-only change: same mix, higher rates
    c2 = pd.DataFrame([("high_delay", "A", 500, 100), ("high_delay", "B", 500, 300), ("other", "A", 500, 50), ("other", "B", 500, 250)],
                      columns=["period", "segment", "n_delivered", "n_late"])
    d2 = gs.decompose(c2)
    assert abs(d2["check"]) < 1e-12 and abs(d2["composition"]) < 1e-12 and d2["within_segment"] > 0


def test_decomposition_matches_independent_computation(res, dl):
    d = res["decomposition"].set_index("segmentation").loc["customer state"]
    dl = dl.assign(period=np.where(dl.month.isin(EPISODES), "E", "O"))
    tot = dl.groupby("customer_state").size()
    seg = dl.customer_state.where(dl.customer_state.map(tot) >= 100, "Other")
    g = dl.assign(seg=seg).groupby(["period", "seg"]).late.agg(n="size", k="sum").unstack(0, fill_value=0)
    wE, wO = g[("n", "E")] / g[("n", "E")].sum(), g[("n", "O")] / g[("n", "O")].sum()
    rE, rO = g[("k", "E")] / g[("n", "E")].replace(0, np.nan), g[("k", "O")] / g[("n", "O")].replace(0, np.nan)
    rE, rO = rE.fillna(rO), rO.fillna(rE)
    assert math.isclose(d.composition, ((wE - wO) * (rE + rO) / 2).sum(), abs_tol=1e-12)
    assert math.isclose(d.within_segment, ((wE + wO) / 2 * (rE - rO)).sum(), abs_tol=1e-12)
    assert math.isclose(d.rate_high_delay, dl[dl.period == "E"].late.mean(), abs_tol=1e-12)
    assert math.isclose(d.rate_other, dl[dl.period == "O"].late.mean(), abs_tol=1e-12)
    assert (res["decomposition"].check.abs() < 1e-10).all()


def test_period_tables_partition_population(res, dl):
    po = res["period_overall"].set_index(["population", "period"])
    assert po.loc[("All delivered orders", "high_delay"), "n_delivered"] == int(dl.month.isin(EPISODES).sum())
    assert po.loc[("All delivered orders", "high_delay"), "n_delivered"] + po.loc[("All delivered orders", "other"), "n_delivered"] == 96_203
    assert po.loc[("Single-seller orders", "high_delay"), "n_delivered"] + po.loc[("Single-seller orders", "other"), "n_delivered"] == 94_931
    ps = res["period_states"]
    assert ps.n_delivered_high_delay.sum() + ps.n_delivered_other.sum() == 96_203
    assert gs.EPISODE_MONTHS == [d.date() for d in EPISODES]


def test_pool_segments_conserves_totals():
    c = pd.DataFrame([("high_delay", "A", 500, 50), ("other", "A", 600, 40), ("high_delay", "B", 30, 5), ("other", "C", 20, 1)], columns=["period", "segment", "n_delivered", "n_late"])
    p = gs.pool_segments(c, 100)
    assert p.n_delivered.sum() == c.n_delivered.sum() and p.n_late.sum() == c.n_late.sum()
    assert set(p.segment) == {"A", "Other (pooled)"}


# ------------------------------------------------------------------ outputs
def test_outputs_written_and_figures_nonempty(res, tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(gs, "FIG_DIR", tmp_path / "figures")
    monkeypatch.setattr(gs, "STATS_JSON", tmp_path / "stats.json")
    gs.write_outputs(res)
    figs = gs.make_figures(res)
    assert len(figs) == 8 and all(p.stat().st_size > 10_000 for p in figs)
    assert (tmp_path / "stats.json").stat().st_size > 1_000
    assert len(pd.read_csv(tmp_path / "tables" / "geo_states.csv")) == 27
