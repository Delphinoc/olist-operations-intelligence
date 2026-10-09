"""Validation of the Customer Satisfaction workstream (analysis/customer_satisfaction.py).

SQL aggregations (DuckDB) are recomputed independently in pandas from the raw CSVs (tests/reference_pandas.py);
effect sizes are checked against SciPy; the regression is re-fitted with a hand-written logistic IRLS and a
cluster-robust sandwich estimator that share no code with statsmodels. Expected values never come from the analysis output.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "analysis"))
import customer_satisfaction as cs  # noqa: E402
import delivery_reliability as dr  # noqa: E402


@pytest.fixture(scope="session")
def res(con):
    return cs.compute_all(con, n_boot=200, leave_one_out=True)


@pytest.fixture(scope="session")
def obs(ref):
    """Independent order-level frame (delivered in window) built from the raw CSVs."""
    o = ref["orders_df"]
    o = o[o.in_window & o.dated].copy()
    o["err"] = (o.deliv_date - o.est_date).dt.days
    o["band"] = np.select([o.err <= 0, o.err <= 3, o.err <= 7], ["<=0", "1-3", "4-7"], "8+")
    o["early"] = o.review_creation_date < o.deliv_date
    o["same_or_early"] = o.review_creation_date <= o.deliv_date
    o["after_promise"] = o.review_creation_date > o.est_date
    return o


@pytest.fixture(scope="session")
def p0(obs):
    return obs[obs.review_row_count == 1].copy()


def q(res_table, **filters):
    t = res_table
    for k, v in filters.items():
        t = t[t[k] == v]
    return t


# ------------------------------------------------------------------ populations
def test_populations_match_pandas(res, obs, p0):
    pop = res["populations"]
    assert pop["delivered_window"] == len(obs) == 96_203
    assert pop["one_review_p0"] == len(p0) == 95_037
    assert pop["no_review"] == int((obs.review_row_count == 0).sum()) == 643
    assert pop["multi_review"] == int((obs.review_row_count > 1).sum()) == 523
    assert pop["early_reviews"] == int(p0.early.sum()) == 4_934
    assert pop["p1_after_removing_early"] == int((~p0.early).sum()) == 90_103
    assert pop["p1b_after_removing_same_day"] == int((~p0.same_or_early).sum())
    assert pop["n_customers_p0"] == p0.customer_unique_id.nunique()


# ------------------------------------------------------------------ SQL summaries vs pandas
@pytest.mark.parametrize("variant,mask", [("P0", lambda p: p.index == p.index), ("P1:", lambda p: ~p.early), ("P1b", lambda p: ~p.same_or_early)])
def test_group_summary_matches_pandas(res, p0, variant, mask):
    gs = res["group_summary"]
    gs = gs[gs.variant.str.startswith(variant)].set_index("is_late")
    sub = p0[mask(p0)]
    for late in (False, True):
        s = sub[sub.late == late].review_score
        row = gs.loc[late]
        assert int(row.n) == len(s) and int(row.sum_score) == int(s.sum()) and int(row.n_low) == int((s <= 2).sum())
        assert math.isclose(row.mean_score, s.mean(), abs_tol=1e-12)
        assert math.isclose(row.sd_score, s.std(ddof=1), abs_tol=1e-9)
        for k in range(1, 6):
            assert int(row[f"n{k}"]) == int((s == k).sum())
        lo, hi = dr.wilson(int((s <= 2).sum()), len(s))
        assert math.isclose(row.low_share_lo, lo, abs_tol=1e-12) and math.isclose(row.low_share_hi, hi, abs_tol=1e-12)


def test_primary_anchor_values(res):
    gs = q(res["group_summary"], variant="P0 primary: one review row").set_index("is_late")
    assert (int(gs.loc[False, "n"]), int(gs.loc[True, "n"])) == (88_687, 6_350)
    assert math.isclose(gs.loc[False, "mean_score"], 4.292, abs_tol=5e-4) and math.isclose(gs.loc[True, "mean_score"], 2.273, abs_tol=5e-4)
    assert res["overall"]["n"] == 95_037 and res["overall"]["n_low"] == 12_145


def test_band_summary_matches_pandas(res, p0):
    b = res["bands"]
    for variant, sub in (("P0", p0), ("P1:", p0[~p0.early])):
        t = b[b.variant.str.startswith(variant)].set_index("days_late_band")
        for band, g in sub.groupby("band"):
            assert int(t.loc[band, "n"]) == len(g)
            assert int(t.loc[band, "n_low"]) == int((g.review_score <= 2).sum())
            assert math.isclose(t.loc[band, "mean_score"], g.review_score.mean(), abs_tol=1e-12)
    t0 = b[b.variant.str.startswith("P0")].set_index("days_late_band")
    assert list(b[b.variant.str.startswith("P0")].days_late_band) == cs.BAND_ORDER           # presented in severity order
    assert t0.n.sum() == len(p0)
    # dose-response in the primary population
    assert t0.loc["<=0", "low_share"] < t0.loc["1-3", "low_share"] < t0.loc["4-7", "low_share"] < t0.loc["8+", "low_share"]


# ------------------------------------------------------------------ effect sizes
def test_effect_sizes_vs_scipy(res, p0):
    from scipy.stats import mannwhitneyu
    e = q(res["effects"], variant="P0 primary: one review row").iloc[0]
    on, late = p0[~p0.late].review_score.to_numpy(), p0[p0.late].review_score.to_numpy()
    U = mannwhitneyu(on, late).statistic
    auc = U / (len(on) * len(late))
    assert math.isclose(e.prob_superiority, auc, abs_tol=1e-9)
    assert math.isclose(e.cliffs_delta, 2 * auc - 1, abs_tol=1e-9)
    assert math.isclose(e.mean_diff, late.mean() - on.mean(), abs_tol=1e-12)
    pooled = math.sqrt(((len(on) - 1) * on.var(ddof=1) + (len(late) - 1) * late.var(ddof=1)) / (len(on) + len(late) - 2))
    assert math.isclose(e.cohens_d, (late.mean() - on.mean()) / pooled, abs_tol=1e-9)
    pl, po = (late <= 2).mean(), (on <= 2).mean()
    assert math.isclose(e.low_diff, pl - po, abs_tol=1e-12) and math.isclose(e.risk_ratio, pl / po, abs_tol=1e-9)
    assert math.isclose(e.odds_ratio, (pl / (1 - pl)) / (po / (1 - po)), abs_tol=1e-9)
    for m in ("mean_diff", "low_diff", "cohens_d", "risk_ratio", "odds_ratio", "cliffs_delta"):
        assert e[m + "_lo"] <= e[m] <= e[m + "_hi"], m


def test_cluster_bootstrap_is_seeded_and_clusters():
    rng = np.random.default_rng(3)
    n_c, per = 400, 6
    cust = np.repeat(np.arange(n_c), per)
    late = np.repeat(rng.integers(0, 2, n_c), per)                       # lateness constant within customer
    base = np.repeat(rng.normal(0, 1.2, n_c), per)                       # strong within-customer correlation
    score = np.clip(np.round(3.5 - 0.5 * late + base + rng.normal(0, 0.4, n_c * per)), 1, 5)
    a = cs.cluster_bootstrap(cust, late, score, 300, np.random.default_rng(1))
    b = cs.cluster_bootstrap(cust, late, score, 300, np.random.default_rng(1))
    pd.testing.assert_frame_equal(a, b)
    eff = cs.effect_row("synthetic", a, int((late == 0).sum()), int((late == 1).sum()))
    assert eff["design_effect_ratio_low_diff"] > 1.5          # clustered data: cluster SE must exceed the independent-orders SE


def test_design_effect_close_to_one_for_real_data(res):
    r = res["effects"].design_effect_ratio_low_diff
    assert ((r > 0.85) & (r < 1.25)).all()                     # customers rarely repeat: clustering barely matters here


# ------------------------------------------------------------------ timing sensitivity
def test_timing_sensitivity_results(res, p0):
    e = res["effects"].set_index("variant")
    prim, p1, p1b = e.loc["P0 primary: one review row"], e.loc["P1: reviews created before delivery removed"], e.loc["P1b: reviews created on or before delivery day removed"]
    assert p1.low_diff < prim.low_diff and p1b.low_diff < p1.low_diff
    sub = p0[~p0.early]
    assert math.isclose(p1.low_diff, (sub[sub.late].review_score <= 2).mean() - (sub[~sub.late].review_score <= 2).mean(), abs_tol=1e-12)
    assert p1.n_late == int(sub.late.sum()) == 1_625


def test_early_review_composition_and_promise_timing(res, p0, obs):
    ec = res["early_review_composition"].set_index(["created_before_delivery", "is_late"])
    for early in (False, True):
        for late in (False, True):
            g = p0[(p0.early == early) & (p0.late == late)]
            assert int(ec.loc[(early, late), "n"]) == len(g) and int(ec.loc[(early, late), "n_low"]) == int((g.review_score <= 2).sum())
    rates = res["early_review_rates"]
    assert rates["late"][0] == int(p0[p0.late].early.sum()) and rates["on_time"][0] == int(p0[~p0.late].early.sum())
    et = res["early_timing"].set_index("days_late_band")
    for band, g in p0.groupby("band"):
        assert int(et.loc[band, "n_early"]) == int(g.early.sum())
        assert int(et.loc[band, "n_early_after_promise"]) == int((g.early & g.after_promise).sum())
        assert int(et.loc[band, "n_early_on_or_before_promise"]) == int((g.early & ~g.after_promise).sum())
    lt = res["early_late_orders_timing"]
    late_early = p0[p0.late & p0.early]
    assert lt["n_early_late_orders"] == len(late_early) and lt["after_promise"] == int(late_early.after_promise.sum())
    assert lt["after_promise"] + lt["on_or_before_promise"] == lt["n_early_late_orders"]


# ------------------------------------------------------------------ multi-review handling
def test_multi_review_rules_match_pandas(res, ref):
    rv = pd.read_csv(RAW / "olist_order_reviews_dataset.csv", parse_dates=["review_creation_date"])
    o = ref["orders_df"]
    o = o[o.in_window & o.dated][["order_id", "late"]]
    rv = rv.merge(o, on="order_id")
    agg = rv.groupby("order_id").agg(rows=("review_score", "size"), mn=("review_score", "min"), mx=("review_score", "max"),
                                     me=("review_score", "mean"), nd=("review_creation_date", "nunique")).reset_index().merge(o, on="order_id")
    first = rv.sort_values("review_creation_date").groupby("order_id").review_score.first()
    last = rv.sort_values("review_creation_date").groupby("order_id").review_score.last()
    agg["earliest"], agg["latest"] = agg.order_id.map(first), agg.order_id.map(last)
    distinct = agg.nd == agg.rows
    tbl = res["multi_review_rules"].set_index(["rule", "is_late"])

    def check(rule, d, col, low_fn=lambda v: v <= 2):
        for late in (False, True):
            g = d[d.late == late][col]
            r = tbl.loc[(rule, late)]
            assert int(r.n_units) == len(g) and math.isclose(r.sum_score, g.sum(), abs_tol=1e-6) and int(r.n_low) == int(low_fn(g).sum()), (rule, late)

    check("1 primary: single-review orders only", agg[agg.rows == 1], "mn")
    check("2 lowest score per order", agg, "mn")
    check("3 highest score per order", agg, "mx")
    check("4 mean score per order", agg, "me")
    check("5 earliest-dated review (orders with distinct dates)", agg[distinct], "earliest")
    check("6 latest-dated review (orders with distinct dates; comparison only)", agg[distinct], "latest")
    for late in (False, True):
        r = tbl.loc[("7 every review row as a unit (units = rows)", late)]
        g = rv[rv.late == late].review_score
        assert int(r.n_units) == len(g) and int(r.sum_score) == int(g.sum()) and int(r.n_low) == int((g <= 2).sum())


def test_multi_review_rules_bound_each_other_and_primary(res):
    e = res["effects"].set_index("variant")
    lo, hi = e.loc["Multi-review rule: lowest score per order"], e.loc["Multi-review rule: highest score per order"]
    # lowest/highest score rules bound every per-order choice for the late-minus-on-time gap in each group's low share
    assert lo.low_late >= hi.low_late - 1e-12 and lo.low_on >= hi.low_on - 1e-12
    prim = e.loc["P0 primary: one review row"]
    for name in e.index[3:]:
        assert abs(e.loc[name, "low_diff"] - prim.low_diff) < 0.01      # 523 multi-review orders cannot move the gap much
    assert "comparison only" in " ".join(e.index)                       # 'latest' is never presented as authoritative


# ------------------------------------------------------------------ non-response
def test_nonresponse_and_bounds(res, obs):
    nr = res["nonresponse"].set_index("group")
    for name, late in (("on time", False), ("late", True)):
        g = obs[obs.late == late]
        assert int(nr.loc[name, "n_total"]) == len(g)
        assert int(nr.loc[name, "n_no_review"]) == int((g.review_row_count == 0).sum())
        assert int(nr.loc[name, "n_multi_review"]) == int((g.review_row_count > 1).sum())
    assert nr.loc["late", "no_review_rate"] > nr.loc["on time", "no_review_rate"]                  # differential non-response
    wc = res["worst_case_bounds"]
    lo_lt, hi_lt = wc["low_share_late"]
    lo_on, hi_on = wc["low_share_on_time"]
    prim_gap = q(res["effects"], variant="P0 primary: one review row").low_diff.iloc[0]
    assert math.isclose(wc["low_diff_bounds"][0], lo_lt - hi_on, abs_tol=1e-12) and math.isclose(wc["low_diff_bounds"][1], hi_lt - lo_on, abs_tol=1e-12)
    assert wc["low_diff_bounds"][0] < prim_gap < wc["low_diff_bounds"][1]
    assert wc["mean_diff_bounds"][0] < -1.5 and wc["unknown_late"] == int(((obs.late) & (obs.review_row_count != 1)).sum())
    sc = res["missing_review_scenarios"]
    obs_row = sc[(sc.assumption_missing_late == "same as reviewed late orders") & (sc.assumption_missing_on_time == "same as reviewed on-time orders")].iloc[0]
    assert math.isclose(obs_row.low_diff_pp / 100, prim_gap, abs_tol=2e-3)            # missing-at-random scenario reproduces the observed gap
    assert sc.low_diff_pp.min() > 45                                                    # no scenario removes the gap (tiny missingness)


# ------------------------------------------------------------------ C8
def test_c8_share_of_low_score_orders_that_were_late(res, p0):
    low = p0[p0.review_score <= 2]
    row = q(res["c8"], variant="P0 primary: one review row").iloc[0]
    assert int(row.low_score_orders) == len(low) and int(row.of_which_late) == int(low.late.sum())
    assert math.isclose(row.share_late, low.late.mean(), abs_tol=1e-12)
    lo, hi = dr.wilson(int(low.late.sum()), len(low))
    assert math.isclose(row.share_lo, lo, abs_tol=1e-12) and math.isclose(row.share_hi, hi, abs_tol=1e-12)
    assert math.isclose(row.late_share_among_reviewed, p0.late.mean(), abs_tol=1e-12)
    assert int(row.low_orders_on_time) == int((~low.late).sum())
    comp = res["low_score_band_composition"]
    assert math.isclose(comp.share_of_low_score_orders.sum(), 1.0) and math.isclose(comp.share_of_reviewed_orders.sum(), 1.0)


# ------------------------------------------------------------------ regression: independent IRLS + cluster-robust SE
def _design(p0: pd.DataFrame, ref) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    items = ref["items_df"].groupby("order_id").agg(value=("price", "sum"), freight=("freight_value", "sum"), n_items=("price", "size"))
    d = p0.merge(items, left_on="order_id", right_index=True)
    d["promised"] = (d.est_date - d.purchase_date).dt.days
    X = pd.DataFrame({"Intercept": 1.0, "late": d.late.astype(float)}, index=d.index)
    prom = pd.cut(d.promised, [-1, 14, 21, 28, 35, 10_000], labels=["p1", "p2", "p3", "p4", "p5"])
    for k in ("p1", "p2", "p4", "p5"):
        X["prom_" + k] = (prom == k).astype(float)
    month = d.purchase_date.dt.to_period("M").astype(str)
    for m in sorted(month.unique())[1:]:
        X["m_" + m] = (month == m).astype(float)
    base = X.copy()
    n_state = d.customer_state.value_counts()
    st = d.customer_state.where(d.customer_state.isin(n_state[n_state >= 100].index), "OtherLowVolume")
    for s in sorted(st.unique()):
        if s != "SP":
            X["s_" + s] = (st == s).astype(float)
    X["log_value"] = np.log(d.value)
    X["freight_share"] = d.freight / (d.value + d.freight)
    X["i2"] = (d.n_items == 2).astype(float)
    X["i3plus"] = (d.n_items >= 3).astype(float)
    y = (d.review_score <= 2).astype(float).to_numpy()
    cl = pd.factorize(d.customer_unique_id)[0]
    return d, {"M1": base, "M2": X}, y, cl


def _irls(X: np.ndarray, y: np.ndarray, cl: np.ndarray):
    beta = np.zeros(X.shape[1])
    for _ in range(100):
        eta = X @ beta
        p = 1 / (1 + np.exp(-eta))
        W = p * (1 - p)
        step = np.linalg.solve((X * W[:, None]).T @ X, X.T @ (y - p))
        beta = beta + step
        if np.max(np.abs(step)) < 1e-11:
            break
    p = 1 / (1 + np.exp(-(X @ beta)))
    W = p * (1 - p)
    Hinv = np.linalg.inv((X * W[:, None]).T @ X)
    scores = X * (y - p)[:, None]
    G = cl.max() + 1
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, cl, scores)
    meat = S.T @ S
    n, k = X.shape
    corr = G / (G - 1) * (n - 1) / (n - k)
    cov = corr * Hinv @ meat @ Hinv
    return beta, np.sqrt(np.diag(cov)), p


@pytest.fixture(scope="session")
def independent_fit(p0, ref):
    d, X, y, cl = _design(p0, ref)
    out = {}
    for name, df in X.items():
        beta, se, p = _irls(df.to_numpy(), y, cl)
        out[name] = (df.columns.tolist(), beta, se, p)
    return d, X, y, cl, out


def test_unadjusted_model_equals_crude_odds_ratio(res, p0):
    m0 = res["models"].iloc[0]
    late, on = p0[p0.late].review_score <= 2, p0[~p0.late].review_score <= 2
    crude = (late.sum() / (~late).sum()) / (on.sum() / (~on).sum())
    assert math.isclose(m0.odds_ratio, crude, rel_tol=1e-9)
    assert int(m0.n) == len(p0) and int(m0.events) == int((p0.review_score <= 2).sum())


@pytest.mark.parametrize("name,idx", [("M1", 1), ("M2", 2)])
def test_adjusted_logit_matches_independent_irls(res, independent_fit, name, idx):
    _, _, _, _, out = independent_fit
    cols, beta, se, _ = out[name]
    j = cols.index("late")
    row = res["models"].iloc[idx]
    assert math.isclose(row.odds_ratio, math.exp(beta[j]), rel_tol=1e-6)
    assert math.isclose(row.or_lo, math.exp(beta[j] - dr.Z95 * se[j]), rel_tol=1e-4)
    assert math.isclose(row.or_hi, math.exp(beta[j] + dr.Z95 * se[j]), rel_tol=1e-4)
    assert bool(row.converged)


def test_average_marginal_effect_matches_independent(res, independent_fit):
    d, X, y, cl, out = independent_fit
    cols, beta, _, _ = out["M2"]
    X1, X0 = X["M2"].copy(), X["M2"].copy()
    X1["late"], X0["late"] = 1.0, 0.0
    p1 = 1 / (1 + np.exp(-(X1.to_numpy() @ beta)))
    p0_ = 1 / (1 + np.exp(-(X0.to_numpy() @ beta)))
    ame = float((p1 - p0_).mean()) * 100
    lo, hi = res["regression_extra"]["ame_late_pp"][1:]
    assert math.isclose(res["regression_extra"]["ame_late_pp"][0], ame, abs_tol=1e-4)
    assert lo < ame < hi


def test_p1_stability_model_matches_independent(res, independent_fit, p0):
    d, X, y, cl, _ = independent_fit
    keep = (~d.early).to_numpy()
    Xs = X["M2"][keep]
    # month dummies absent in the subset are not an issue (P1 keeps all 20 months); drop any all-zero column defensively
    Xs = Xs.loc[:, (Xs != 0).any(axis=0)]
    beta, se, _ = _irls(Xs.to_numpy(), y[keep], pd.factorize(cl[keep])[0])
    j = Xs.columns.tolist().index("late")
    row = res["stability"].set_index("model").loc["P1: reviews created before delivery removed"]
    assert int(row.n) == int(keep.sum())
    assert math.isclose(row.odds_ratio, math.exp(beta[j]), rel_tol=1e-6)


def test_regression_diagnostics(res, independent_fit, p0):
    ex = res["regression_extra"]
    assert all(v == 0 for v in ex["missingness"].values())
    d, X, y, cl, _ = independent_fit
    Xn = X["M2"].drop(columns="Intercept")
    vif_late = float(np.diag(np.linalg.inv(np.corrcoef(Xn.to_numpy(), rowvar=False)))[Xn.columns.tolist().index("late")])
    assert math.isclose(ex["collinearity"]["vif_late"], vif_late, rel_tol=1e-6)
    assert ex["collinearity"]["vif_late"] < 2
    sp = res["sparsity"].set_index("factor")
    assert sp.loc["customer state", "levels"] == 25            # 24 states with >= 100 delivered orders + pooled low-volume
    assert sp.loc["purchase month", "levels"] == 20
    assert (res["state_levels"].n.sum() == len(p0)) and (res["category_levels"].n.sum() == len(p0))
    assert res["models"].converged.all() and res["stability"].converged.all()
    assert res["models"].warnings.fillna("").eq("").all()       # no separation / convergence warnings


def test_leave_one_month_out_and_band_models(res):
    loo = res["leave_one_month_out"]
    assert len(loo) == 20 and (loo.or_lo <= loo.odds_ratio).all() and (loo.odds_ratio <= loo.or_hi).all()
    primary = res["models"].iloc[2].odds_ratio
    assert loo.odds_ratio.min() > 0.8 * primary and loo.odds_ratio.max() < 1.2 * primary
    b = res["bands_adj"].set_index("band")
    assert b.loc["late_1_3", "odds_ratio"] < b.loc["late_4_7", "odds_ratio"] < b.loc["late_8plus", "odds_ratio"]


def test_no_causal_language_in_outputs(res):
    assert all("effect of" not in str(v).lower() for v in res["models"].model)


# ------------------------------------------------------------------ outputs
def test_outputs_written_and_figures_nonempty(res, tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(cs, "FIG_DIR", tmp_path / "figures")
    monkeypatch.setattr(cs, "STATS_JSON", tmp_path / "stats.json")
    cs.write_outputs(res)
    figs = cs.make_figures(res)
    assert len(figs) == 7 and all(p.stat().st_size > 10_000 for p in figs)
    assert (tmp_path / "stats.json").stat().st_size > 1_000
    assert (tmp_path / "tables" / "satisfaction_effects.csv").exists()
