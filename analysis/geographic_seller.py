"""Workstream 2 - Geographic and seller performance: where do late deliveries concentrate? (descriptive screening)

Usage (project root, after `python scripts/build_model.py`):
    python analysis/geographic_seller.py

Primary aggregations are DuckDB SQL (sql/analysis/geography/*.sql) on the validated model; Python adds Wilson
intervals, standardisation, Mantel-Haenszel comparisons, funnel limits, a decomposition and charts.
Populations: customer state / region = delivered-in-window orders (96,203, incl. multi-seller);
lanes, distance and sellers = `v_single_seller_orders` (94,931 single-seller orders).
Everything is observational and descriptive: a flagged state, lane or seller is a candidate for operational
investigation, never evidence that a seller or region *caused* delays. No priority tiers are produced here.
Outputs: reports/tables/geo_*.csv, reports/geo_stats.json, reports/figures/geo_*.png.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import binom

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_reliability as dr  # noqa: E402  (Wilson / Newcombe helpers and chart style)

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "analysis" / "geography"
DB = ROOT / "data" / "processed" / "olist_model.duckdb"
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
STATS_JSON = ROOT / "reports" / "geo_stats.json"

MIN_STATE_N = 100                         # blueprint R8
SELLER_RANK_N, SELLER_LOW_CONF_N = 50, 30  # blueprint R8: rank at n>=50, low-confidence 30-49, pool below 30
THRESHOLDS = (30, 50, 100)
MIN_LANE_N = 100
EPISODE_MONTHS = [dt.date(2017, 11, 1), dt.date(2018, 2, 1), dt.date(2018, 3, 1)]   # must match sql period_*.sql
H1_LAST_MONTH = dt.date(2017, 10, 1)       # must match seller_summary.sql
REGION_ORDER = ["North", "Northeast", "Central-West", "Southeast", "South"]


# ------------------------------------------------------------------ helpers
def run_sql(con, name: str, params: dict | None = None) -> pd.DataFrame:
    sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    df = con.execute(sql, params).df() if params else con.execute(sql).df()
    if "purchase_month" in df.columns:
        df["purchase_month"] = pd.to_datetime(df["purchase_month"]).dt.date
    return df


def rate_table(df: pd.DataFrame, n_col: str = "n_delivered", k_col: str = "n_late", ref_rate: float | None = None) -> pd.DataFrame:
    """Late rate, Wilson interval, shares of delivered / late orders and excess over the reference rate."""
    df = dr.add_wilson(df.copy(), k_col, n_col, "late_rate")
    df["share_of_delivered"] = df[n_col] / df[n_col].sum()
    df["share_of_late"] = df[k_col] / df[k_col].sum()
    ref = ref_rate if ref_rate is not None else df[k_col].sum() / df[n_col].sum()
    df["ref_rate"] = ref
    df["excess_late"] = df[k_col] - df[n_col] * ref
    df["above_ref"] = df.late_rate_lo > ref          # Wilson interval entirely above the reference rate
    df["below_ref"] = df.late_rate_hi < ref
    return df


def expected_by_strata(df: pd.DataFrame, group: str, strata: list[str], late: str = "is_late") -> pd.DataFrame:
    """Indirect standardisation: expected late orders per group from pooled stratum rates (strata = e.g. month x promised group)."""
    cell = df.groupby(strata)[late].mean().rename("cell_rate").reset_index()
    d = df.merge(cell, on=strata)
    out = d.groupby(group).agg(n=(late, "size"), observed=(late, "sum"), expected=("cell_rate", "sum")).reset_index()
    out["oe_ratio"] = out.observed / out.expected
    out["excess_vs_expected"] = out.observed - out.expected
    return out


def mantel_haenszel_rr(df: pd.DataFrame, exposed: str, strata: list[str], late: str = "is_late") -> dict:
    """Mantel-Haenszel risk ratio (Greenland-Robins variance) of late delivery, exposed vs unexposed, pooled over strata."""
    g = df.groupby(strata + [exposed])[late].agg(["sum", "size"]).unstack(exposed, fill_value=0)
    a, n1 = g[("sum", True)].to_numpy(float), g[("size", True)].to_numpy(float)
    c, n0 = g[("sum", False)].to_numpy(float), g[("size", False)].to_numpy(float)
    N = n1 + n0
    keep = N > 0
    a, c, n1, n0, N = a[keep], c[keep], n1[keep], n0[keep], N[keep]
    R, S = (a * n0 / N).sum(), (c * n1 / N).sum()
    rr = R / S
    var = ((n1 * n0 * (a + c) - a * c * N) / N ** 2).sum() / (R * S)
    se = math.sqrt(var)
    both = (n1 >= 30) & (n0 >= 30)
    crude = (a.sum() / n1.sum()) / (c.sum() / n0.sum())
    return {"rr_mh": rr, "rr_lo": math.exp(math.log(rr) - dr.Z95 * se), "rr_hi": math.exp(math.log(rr) + dr.Z95 * se),
            "rr_crude": crude, "n_exposed": int(n1.sum()), "n_unexposed": int(n0.sum()),
            "rate_exposed": a.sum() / n1.sum(), "rate_unexposed": c.sum() / n0.sum(),
            "strata_total": int(len(N)), "strata_both_ge30": int(both.sum()),
            "strata_exposed_higher": int(((a / np.where(n1 == 0, np.nan, n1) > c / np.where(n0 == 0, np.nan, n0)) & both).sum())}


def expected_false_flags(ns: np.ndarray, p0: float) -> np.ndarray:
    """P(Wilson 95% lower bound > p0 | true rate = p0) for each seller size n: the chance flag rate if no seller differed."""
    out = np.empty(len(ns))
    for i, n in enumerate(ns):
        k = np.arange(int(n) + 1)
        p = k / n
        z2 = dr.Z95 ** 2
        centre = (p + z2 / (2 * n)) / (1 + z2 / n)
        half = dr.Z95 * np.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
        above = np.nonzero(centre - half > p0)[0]
        out[i] = binom.sf(above[0] - 1, n, p0) if len(above) else 0.0
    return out


def funnel_limits(n_grid: np.ndarray, p0: float) -> pd.DataFrame:
    rows = []
    for n in n_grid:
        rows.append({"n": int(n), "lo95": binom.ppf(0.025, n, p0) / n, "hi95": binom.ppf(0.975, n, p0) / n,
                     "lo998": binom.ppf(0.001, n, p0) / n, "hi998": binom.ppf(0.999, n, p0) / n})
    return pd.DataFrame(rows)


def decompose(counts: pd.DataFrame) -> dict:
    """Two-factor (Kitagawa-style) decomposition of the high-delay vs other-month late-rate difference.
    counts: columns period ('high_delay'/'other'), segment, n_delivered, n_late. Segments absent from one period keep
    the other period's rate there (so they contribute to composition only)."""
    p = counts.pivot_table(index="segment", columns="period", values=["n_delivered", "n_late"], fill_value=0)
    nE, nO = p[("n_delivered", "high_delay")], p[("n_delivered", "other")]
    kE, kO = p[("n_late", "high_delay")], p[("n_late", "other")]
    wE, wO = nE / nE.sum(), nO / nO.sum()
    rE = (kE / nE.replace(0, np.nan))
    rO = (kO / nO.replace(0, np.nan))
    rE, rO = rE.fillna(rO), rO.fillna(rE)
    comp = float(((wE - wO) * (rE + rO) / 2).sum())
    within = float(((wE + wO) / 2 * (rE - rO)).sum())
    total = kE.sum() / nE.sum() - kO.sum() / nO.sum()
    return {"rate_high_delay": float(kE.sum() / nE.sum()), "rate_other": float(kO.sum() / nO.sum()), "difference": float(total),
            "composition": comp, "within_segment": within, "check": comp + within - float(total),
            "o_rate_with_e_mix": float((wE * rO).sum()), "e_rate_with_o_mix": float((wO * rE).sum()),
            "n_segments": int(len(p))}


def pool_segments(counts: pd.DataFrame, min_total: int) -> pd.DataFrame:
    """Pool segments whose total volume (both periods) is below min_total into 'Other (pooled)'."""
    tot = counts.groupby("segment").n_delivered.sum()
    small = set(tot[tot < min_total].index)
    c = counts.copy()
    c["segment"] = np.where(c.segment.isin(small), "Other (pooled)", c.segment)
    return c.groupby(["period", "segment"], as_index=False)[["n_delivered", "n_late"]].sum()


# ------------------------------------------------------------------ main computation
def compute_all(con: duckdb.DuckDBPyConnection) -> dict:
    res: dict = {}

    # ---------------- (1) states and regions -----------------------------------------------------------
    dl = run_sql(con, "delivered_orders")
    st = run_sql(con, "state_summary").merge(run_sql(con, "state_context"), on="customer_state", how="left")
    ref_all = float(st.n_late.sum() / st.n_delivered.sum())
    res["population_all"] = {"n_delivered": int(st.n_delivered.sum()), "n_late": int(st.n_late.sum()), "late_rate": ref_all}
    st = rate_table(st, ref_rate=ref_all)
    st["low_volume"] = st.n_delivered < MIN_STATE_N
    e_m = expected_by_strata(dl, "customer_state", ["purchase_month"]).rename(columns={"expected": "exp_month", "oe_ratio": "oe_month"})
    e_mp = expected_by_strata(dl, "customer_state", ["purchase_month", "promised_group"]).rename(columns={"expected": "exp_month_promised", "oe_ratio": "oe_month_promised"})
    st = st.merge(e_m[["customer_state", "exp_month", "oe_month"]], on="customer_state").merge(
        e_mp[["customer_state", "exp_month_promised", "oe_month_promised"]], on="customer_state")
    st["oe_month_promised_lo"] = st.late_rate_lo * st.n_delivered / st.exp_month_promised
    st["oe_month_promised_hi"] = st.late_rate_hi * st.n_delivered / st.exp_month_promised
    # screening signals are only defined for states meeting the n >= 100 reporting policy
    st["adj_signal_above"] = (st.oe_month_promised_lo > 1) & ~st.low_volume
    st["adj_signal_below"] = (st.oe_month_promised_hi < 1) & ~st.low_volume
    st["above_ref"] = st.above_ref & ~st.low_volume
    st["below_ref"] = st.below_ref & ~st.low_volume
    st = st.sort_values("late_rate", ascending=False).reset_index(drop=True)
    res["states"] = st
    rp = st[~st.low_volume]
    res["state_rank_stability"] = {
        "n_reportable": int(len(rp)), "n_low_volume": int(st.low_volume.sum()),
        "spearman_unadjusted_vs_oe": float(rp.late_rate.corr(rp.oe_month_promised, method="spearman")),
        "n_above_unadj": int(rp.above_ref.sum()), "n_above_adj": int(rp.adj_signal_above.sum()),
        "n_below_unadj": int(rp.below_ref.sum()), "n_below_adj": int(rp.adj_signal_below.sum()),
        "above_unadj_and_adj": int((rp.above_ref & rp.adj_signal_above).sum()),
        "low_volume_states": st[st.low_volume].customer_state.tolist(),
        "low_volume_orders": int(st[st.low_volume].n_delivered.sum()),
    }
    rg = rate_table(run_sql(con, "region_summary"), ref_rate=ref_all)
    e_r = expected_by_strata(dl, "macro_region", ["purchase_month", "promised_group"]).rename(columns={"expected": "exp_month_promised", "oe_ratio": "oe_month_promised"})
    rg = rg.merge(e_r[["macro_region", "exp_month_promised", "oe_month_promised"]], on="macro_region")
    rg["order"] = rg.macro_region.map({r: i for i, r in enumerate(REGION_ORDER)})
    res["regions"] = rg.sort_values("order").drop(columns="order").reset_index(drop=True)

    # ---------------- (2) shipment type, distance, lanes ---------------------------------------------------
    qd = run_sql(con, "distance_quartiles").iloc[0]
    qparams = {"q1": float(qd.q1), "q2": float(qd.q2), "q3": float(qd.q3)}
    res["distance_quartiles_km"] = {**qparams, "n_no_distance": int(qd.n_no_distance), "n": int(qd.n)}
    ss = run_sql(con, "single_seller_orders", qparams)
    ref_ss = float(ss.is_late.mean())
    res["population_single_seller"] = {"n_delivered": int(len(ss)), "n_late": int(ss.is_late.sum()), "late_rate": ref_ss,
                                       "n_sellers": int(ss.seller_id.nunique())}
    ship = rate_table(run_sql(con, "shipment_type"), ref_rate=ref_ss)
    ship["label"] = np.where(ship.is_cross_state, "Cross-state", "Same-state")
    res["shipment_type"] = ship
    strata_mp = ["purchase_month", "promised_group"]
    res["mh_cross_state"] = mantel_haenszel_rr(ss, "is_cross_state", strata_mp)
    band = rate_table(run_sql(con, "distance_band", qparams), ref_rate=ref_ss)
    res["distance_bands"] = band
    known = ss[ss.distance_band != "Q0: unknown"].copy()
    known["far"] = known.distance_band == "Q4: farthest quarter"
    q14 = known[known.distance_band.isin(["Q1: nearest quarter", "Q4: farthest quarter"])]
    res["mh_far_vs_near"] = mantel_haenszel_rr(q14, "far", strata_mp)
    cells = ss.groupby(["promised_group", "distance_band"]).is_late.agg(n="size", late="sum").reset_index()
    cells = dr.add_wilson(cells, "late", "n", "late_rate")
    res["distance_by_promised"] = cells
    sp = ss.assign(origin_sp=ss.seller_state == "SP")
    res["origin_sp"] = rate_table(sp.groupby("origin_sp").is_late.agg(n_delivered="size", n_late="sum").reset_index(), ref_rate=ref_ss)
    # persistence after stratifying by customer state (destination) and promised-lead group
    strata_sp = ["customer_state", "promised_group"]
    res["mh_cross_state_within_state"] = mantel_haenszel_rr(ss, "is_cross_state", strata_sp)
    res["mh_far_vs_near_within_state"] = mantel_haenszel_rr(q14.assign(far=q14.distance_band == "Q4: farthest quarter"), "far", strata_sp)
    res["mh_origin_sp_within_destination"] = mantel_haenszel_rr(sp.assign(origin_sp=sp.origin_sp), "origin_sp", strata_sp)

    lanes = run_sql(con, "lane_summary")
    lanes["lane"] = lanes.seller_state + ">" + lanes.customer_state
    ss["lane"] = ss.seller_state + ">" + ss.customer_state
    e_l = expected_by_strata(ss, "lane", strata_mp).rename(columns={"expected": "exp_month_promised", "oe_ratio": "oe_month_promised"})
    lanes = lanes.merge(e_l[["lane", "exp_month_promised", "oe_month_promised"]], on="lane")
    big = lanes[lanes.n_delivered >= MIN_LANE_N].copy()
    small = lanes[lanes.n_delivered < MIN_LANE_N]
    big = rate_table(big, ref_rate=ref_ss)
    big["oe_lo"] = big.late_rate_lo * big.n_delivered / big.exp_month_promised
    big["oe_hi"] = big.late_rate_hi * big.n_delivered / big.exp_month_promised
    big["adj_signal_above"] = big.oe_lo > 1
    res["lanes"] = big.sort_values("excess_late", ascending=False).reset_index(drop=True)
    res["lanes_pooled"] = {"n_lanes_total": int(len(lanes)), "n_lanes_reported": int(len(big)), "n_lanes_pooled": int(len(small)),
                           "orders_pooled": int(small.n_delivered.sum()), "late_pooled": int(small.n_late.sum()),
                           "late_rate_pooled": float(small.n_late.sum() / small.n_delivered.sum()),
                           "pooled_ci": dr.wilson(int(small.n_late.sum()), int(small.n_delivered.sum())),
                           "orders_reported": int(big.n_delivered.sum()), "share_orders_reported": float(big.n_delivered.sum() / lanes.n_delivered.sum())}
    rl = run_sql(con, "region_lane")
    rl = dr.add_wilson(rl, "n_late", "n_delivered", "late_rate")
    rl["reportable"] = rl.n_delivered >= MIN_LANE_N
    res["region_lanes"] = rl

    # ---------------- (3)(4)(7) sellers: screening, funnel, thresholds -------------------------------------
    sel = run_sql(con, "seller_summary")
    p0 = ref_ss
    sel = rate_table(sel, ref_rate=p0)
    sel["tier"] = np.where(sel.n_delivered >= SELLER_RANK_N, "ranked (n>=50)", np.where(sel.n_delivered >= SELLER_LOW_CONF_N, "low-confidence (30-49)", "pooled (n<30)"))
    # expected late orders under stratification: S1 = month x promised group; S2 = S1 + distance band
    ss_s = ss.copy()
    for lab, cols in (("s1", strata_mp), ("s2", strata_mp + ["distance_band"])):
        e = expected_by_strata(ss_s, "seller_id", cols)[["seller_id", "expected", "oe_ratio"]].rename(columns={"expected": f"exp_{lab}", "oe_ratio": f"oe_{lab}"})
        sel = sel.merge(e, on="seller_id")
    sel["screen_signal"] = sel.above_ref                                    # Wilson interval entirely above portfolio rate
    sel["screen_signal_s2"] = sel.late_rate_lo > sel.exp_s2 / sel.n_delivered   # ... above the seller's stratified expected rate
    sel["short_id"] = sel.seller_id.str[:8]
    for h, (n_, k_) in {"h1": ("n_h1", "late_h1"), "h2": ("n_h2", "late_h2")}.items():
        sel[f"rate_{h}"] = sel[k_] / sel[n_].replace(0, np.nan)
    h1_rate = float(sel.late_h1.sum() / sel.n_h1.sum())
    h2_rate = float(sel.late_h2.sum() / sel.n_h2.sum())
    res["half_rates"] = {"h1": h1_rate, "h2": h2_rate}
    res["sellers"] = sel.sort_values("excess_late", ascending=False).reset_index(drop=True)

    pooled = sel[sel.n_delivered < SELLER_LOW_CONF_N]
    res["seller_tiers"] = pd.DataFrame([
        {"tier": t, "n_sellers": int((sel.tier == t).sum()), "n_orders": int(sel[sel.tier == t].n_delivered.sum()),
         "share_of_orders": float(sel[sel.tier == t].n_delivered.sum() / sel.n_delivered.sum()),
         "n_late": int(sel[sel.tier == t].n_late.sum()), "late_rate": float(sel[sel.tier == t].n_late.sum() / sel[sel.tier == t].n_delivered.sum())}
        for t in ("ranked (n>=50)", "low-confidence (30-49)", "pooled (n<30)")])
    res["pooled_sellers"] = {"n_sellers": int(len(pooled)), "n_orders": int(pooled.n_delivered.sum()), "n_late": int(pooled.n_late.sum()),
                             "late_rate": float(pooled.n_late.sum() / pooled.n_delivered.sum()),
                             "late_ci": dr.wilson(int(pooled.n_late.sum()), int(pooled.n_delivered.sum()))}
    thr = []
    for T in THRESHOLDS:
        el = sel[sel.n_delivered >= T]
        flagged = el[el.screen_signal]
        thr.append({"min_orders": T, "eligible_sellers": int(len(el)), "share_of_orders_covered": float(el.n_delivered.sum() / sel.n_delivered.sum()),
                    "flagged_wilson_above": int(len(flagged)), "flagged_share_of_eligible": float(len(flagged) / len(el)),
                    "expected_false_flags_if_no_differences": float(expected_false_flags(el.n_delivered.to_numpy(), p0).sum()),
                    "flagged_also_above_stratified_expectation": int(flagged.screen_signal_s2.sum()),
                    "late_orders_in_flagged": int(flagged.n_late.sum()), "share_of_all_late_in_flagged": float(flagged.n_late.sum() / sel.n_late.sum()),
                    "excess_late_in_flagged": float(flagged.excess_late.sum()),
                    "n_below_signal": int(el.below_ref.sum())})
    res["seller_thresholds"] = pd.DataFrame(thr)
    fl = {T: set(sel[(sel.n_delivered >= T) & sel.screen_signal].seller_id) for T in THRESHOLDS}
    res["seller_flag_overlap"] = {"flagged_100_in_50": len(fl[100] & fl[50]), "flagged_100": len(fl[100]), "flagged_50_in_30": len(fl[50] & fl[30]), "flagged_50": len(fl[50]), "flagged_30": len(fl[30])}
    # persistence between window halves
    both = sel[(sel.n_h1 >= 30) & (sel.n_h2 >= 30)]
    flagged50 = sel[(sel.n_delivered >= SELLER_RANK_N) & sel.screen_signal]
    res["seller_persistence"] = {
        "n_sellers_ge30_both_halves": int(len(both)),
        "spearman_h1_vs_h2": float(both.rate_h1.corr(both.rate_h2, method="spearman")),
        "flagged50_n": int(len(flagged50)),
        "flagged50_above_half_rate_in_both_halves": int(((flagged50.rate_h1 > h1_rate) & (flagged50.rate_h2 > h2_rate)).sum()),
        "flagged50_with_both_halves_observed": int(((flagged50.n_h1 > 0) & (flagged50.n_h2 > 0)).sum())}
    ranked = sel[sel.n_delivered >= SELLER_RANK_N]
    pos = ranked.excess_late.clip(lower=0).sort_values(ascending=False)
    res["seller_concentration"] = {
        "ranked_sellers": int(len(ranked)), "positive_excess_total": float(pos.sum()),
        "top10_share_of_positive_excess": float(pos.head(10).sum() / pos.sum()), "top25_share_of_positive_excess": float(pos.head(25).sum() / pos.sum()),
        "n_with_positive_excess": int((ranked.excess_late > 0).sum()),
        "top10_share_of_all_late": float(ranked.sort_values("excess_late", ascending=False).head(10).n_late.sum() / sel.n_late.sum())}
    res["funnel_limits"] = funnel_limits(np.unique(np.round(np.geomspace(20, max(2000, int(sel.n_delivered.max())), 80)).astype(int)), p0)
    res["seller_candidates"] = res["sellers"][(res["sellers"].n_delivered >= SELLER_LOW_CONF_N) & res["sellers"].screen_signal][[
        "seller_id", "short_id", "tier", "seller_state", "seller_region", "n_delivered", "n_late", "late_rate", "late_rate_lo", "late_rate_hi",
        "excess_late", "exp_s1", "exp_s2", "oe_s1", "oe_s2", "screen_signal_s2", "cross_state_share", "distance_median_km", "n_destination_states", "rate_h1", "rate_h2", "n_h1", "n_h2"]]

    # ---------------- (5) high-delay periods vs other months -------------------------------------------------
    dec = {}
    ps = run_sql(con, "period_state")
    dec["customer state"] = decompose(pool_segments(ps, MIN_STATE_N))
    pr = ps.merge(dl[["customer_state", "macro_region"]].drop_duplicates(), left_on="segment", right_on="customer_state").groupby(["period", "macro_region"], as_index=False)[["n_delivered", "n_late"]].sum().rename(columns={"macro_region": "segment"})
    dec["customer macro-region"] = decompose(pr)
    pl = run_sql(con, "period_lane")
    dec["lane (seller state > customer state)"] = decompose(pool_segments(pl, MIN_LANE_N))
    pse = run_sql(con, "period_seller")
    dec["seller (n>=50 individually)"] = decompose(pool_segments(pse, SELLER_RANK_N))
    res["decomposition"] = pd.DataFrame([{"segmentation": k, **v} for k, v in dec.items()])
    pe = pool_segments(ps, MIN_STATE_N).pivot_table(index="segment", columns="period", values=["n_delivered", "n_late"], fill_value=0)
    pe.columns = [f"{a}_{b}" for a, b in pe.columns]
    pe = pe.reset_index()
    pe["rate_high_delay"] = pe.n_late_high_delay / pe.n_delivered_high_delay
    pe["rate_other"] = pe.n_late_other / pe.n_delivered_other
    pe["rate_diff"] = pe.rate_high_delay - pe.rate_other
    pe["share_high_delay"] = pe.n_delivered_high_delay / pe.n_delivered_high_delay.sum()
    pe["share_other"] = pe.n_delivered_other / pe.n_delivered_other.sum()
    pe["excess_late_in_high_delay"] = pe.n_late_high_delay - pe.n_delivered_high_delay * pe.rate_other
    res["period_states"] = pe.sort_values("excess_late_in_high_delay", ascending=False).reset_index(drop=True)
    big_ = pe[(pe.n_delivered_high_delay >= 100) & (pe.n_delivered_other >= 100) & (pe.segment != "Other (pooled)")]
    res["period_breadth"] = {"states_compared": int(len(big_)), "states_higher_in_high_delay": int((big_.rate_high_delay > big_.rate_other).sum()),
                             "min_rate_ratio": float((big_.rate_high_delay / big_.rate_other).min()), "max_rate_ratio": float((big_.rate_high_delay / big_.rate_other).max()),
                             "median_rate_ratio": float((big_.rate_high_delay / big_.rate_other).median())}
    sp_ = pool_segments(pse, SELLER_LOW_CONF_N)
    spv = sp_.pivot_table(index="segment", columns="period", values=["n_delivered", "n_late"], fill_value=0)
    spv.columns = [f"{a}_{b}" for a, b in spv.columns]
    spv = spv[(spv.n_delivered_high_delay >= 30) & (spv.n_delivered_other >= 30)]
    spv = spv[spv.index != "Other (pooled)"]
    r_e, r_o = spv.n_late_high_delay / spv.n_delivered_high_delay, spv.n_late_other / spv.n_delivered_other
    res["period_seller_breadth"] = {"sellers_compared": int(len(spv)), "sellers_higher_in_high_delay": int((r_e > r_o).sum()),
                                    "spearman_rate_high_vs_other": float(r_e.corr(r_o, method="spearman"))}
    periods = []
    for lab, df_ in (("All delivered orders", dl.assign(period=np.where(dl.purchase_month.isin(EPISODE_MONTHS), "high_delay", "other"))),
                     ("Single-seller orders", ss.assign(period=np.where(ss.purchase_month.isin(EPISODE_MONTHS), "high_delay", "other")))):
        g = df_.groupby("period").is_late.agg(n_delivered="size", n_late="sum").reset_index()
        g = dr.add_wilson(g, "n_late", "n_delivered", "late_rate")
        g.insert(0, "population", lab)
        periods.append(g)
    res["period_overall"] = pd.concat(periods, ignore_index=True)
    # does cross-state / distance mix differ between high-delay and other months?
    ss["period"] = np.where(ss.purchase_month.isin(EPISODE_MONTHS), "high_delay", "other")
    mix = ss.groupby("period").agg(cross_state_share=("is_cross_state", "mean"), distance_median_km=("distance_km", "median"),
                                   origin_sp_share=("seller_state", lambda s: (s == "SP").mean()), n=("is_late", "size")).reset_index()
    res["period_mix"] = mix

    res["strata_note"] = {"strata_mp": "purchase month x promised-lead group (100 cells)", "strata_s2": "month x promised group x distance band (+unknown)"}
    res["seller_reference_rate"] = p0
    res["ss_frame_rows"] = int(len(ss))
    return res


# ------------------------------------------------------------------ outputs
def write_outputs(res: dict) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    scalars = {}
    for key, val in res.items():
        if isinstance(val, pd.DataFrame):
            val.to_csv(TABLE_DIR / f"geo_{key}.csv", index=False)
        else:
            scalars[key] = val
    STATS_JSON.write_text(json.dumps(scalars, indent=2, default=dr._jsonable), encoding="utf-8")


# ------------------------------------------------------------------ charts
def make_figures(res: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.ticker import PercentFormatter
    dr._style()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    BLUE, ORANGE, AQUA, GREY, INK2 = dr.BLUE, dr.ORANGE, dr.AQUA, dr.GREY, dr.INK2
    region_colors = {"North": BLUE, "Northeast": ORANGE, "Central-West": AQUA, "Southeast": "#eda100", "South": "#e87ba4"}
    paths = []

    def save(fig, name):
        p = FIG_DIR / f"geo_{name}.png"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)

    st = res["states"]
    ref = res["population_all"]["late_rate"]
    # F1 states forest
    rep = st[~st.low_volume].sort_values("late_rate")
    low = st[st.low_volume].sort_values("late_rate")
    rows = list(rep.itertuples()) + [None] + list(low.itertuples())
    fig, ax = plt.subplots(figsize=(9, 8.2))
    ys = np.arange(len(rows))
    labels = []
    for y, r in zip(ys, rows):
        if r is None:
            labels.append("")
            continue
        col = region_colors[r.macro_region]
        ax.errorbar(r.late_rate, y, xerr=[[r.late_rate - r.late_rate_lo], [r.late_rate_hi - r.late_rate]], fmt="o" if not r.low_volume else "D",
                    color=col if not r.low_volume else GREY, capsize=2.5, mfc=col if not r.low_volume else "white")
        labels.append(f"{r.customer_state}  (n={int(r.n_delivered):,})")
    ax.axvline(ref, color=GREY, linestyle="--", linewidth=1)
    ax.text(ref, len(rows) - 0.3, f" all orders {ref:.1%}", color=INK2, fontsize=8.5, va="bottom")
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Late-delivery rate, delivered orders (95% Wilson)")
    ax.set_title("Late rate by customer state (hollow grey = fewer than 100 delivered orders, shown separately)")
    ax.grid(axis="y", visible=False)
    for reg, col in region_colors.items():
        ax.plot([], [], "o", color=col, label=reg)
    ax.legend(loc="lower right", title="IBGE macro-region")
    save(fig, "01_states")

    # F2 contribution to late orders vs share of orders
    fig, ax = plt.subplots(figsize=(7.2, 6))
    for r in st.itertuples():
        ax.scatter(r.share_of_delivered, r.share_of_late, s=45, color=region_colors[r.macro_region], edgecolor=dr.SURFACE, zorder=3)
        if abs(r.share_of_late - r.share_of_delivered) > 0.012 or r.share_of_delivered > 0.1:
            ax.annotate(r.customer_state, (r.share_of_delivered, r.share_of_late), xytext=(4, 3), textcoords="offset points", fontsize=8.5, color=INK2)
    m = max(st.share_of_delivered.max(), st.share_of_late.max()) * 1.05
    ax.plot([0, m], [0, m], color=GREY, linestyle="--", linewidth=1)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Share of all delivered orders")
    ax.set_ylabel("Share of all late orders")
    ax.set_title("Contribution to late orders vs contribution to volume (dashed = proportional)")
    for reg, col in region_colors.items():
        ax.plot([], [], "o", color=col, label=reg)
    ax.legend(loc="upper left")
    save(fig, "02_state_contribution")

    # F3 region -> region heatmap
    rl = res["region_lanes"]
    mat = rl.pivot(index="seller_region", columns="customer_region", values="late_rate").reindex(index=REGION_ORDER, columns=REGION_ORDER)
    nmat = rl.pivot(index="seller_region", columns="customer_region", values="n_delivered").reindex(index=REGION_ORDER, columns=REGION_ORDER).fillna(0)
    cmap = LinearSegmentedColormap.from_list("seq", ["#e8f0fb", BLUE, "#1c4f94"])
    fig, ax = plt.subplots(figsize=(7.4, 5))
    vals = np.where(nmat.to_numpy() >= MIN_LANE_N, mat.to_numpy(), np.nan)
    im = ax.imshow(vals, cmap=cmap, vmin=0, vmax=np.nanmax(vals))
    for i in range(5):
        for j in range(5):
            n_ = int(nmat.iloc[i, j])
            if n_ >= MIN_LANE_N:
                ax.text(j, i, f"{mat.iloc[i, j]:.1%}\nn={n_:,}", ha="center", va="center", fontsize=8, color="white" if vals[i, j] > np.nanmax(vals) * 0.55 else INK2)
            else:
                ax.text(j, i, f"n={n_}\n(suppressed)" if n_ else "no orders", ha="center", va="center", fontsize=7.5, color=GREY)
    ax.set_xticks(range(5))
    ax.set_xticklabels(REGION_ORDER, rotation=30, ha="right")
    ax.set_yticks(range(5))
    ax.set_yticklabels(REGION_ORDER)
    ax.set_xlabel("Customer region (destination)")
    ax.set_ylabel("Seller region (origin)")
    ax.grid(False)
    ax.set_title("Late rate by origin and destination macro-region (cells with n < 100 suppressed)")
    save(fig, "03_region_lanes")

    # F4 distance and shipment type
    band = res["distance_bands"]
    band = band[band.distance_band != "Q0: unknown"]
    ship = res["shipment_type"]
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(12.5, 4.2), gridspec_kw={"width_ratios": [1.3, 1.3, 0.8]})
    xs = np.arange(len(band))
    xl = [f"{r.distance_band.split(':')[0]}\n{r.distance_median_km:,.0f} km" for r in band.itertuples()]
    a1.errorbar(xs, band.late_rate, yerr=[band.late_rate - band.late_rate_lo, band.late_rate_hi - band.late_rate], fmt="o", color=BLUE, capsize=3)
    for xi, yi in zip(xs, band.late_rate):
        a1.text(xi + 0.08, yi, f"{yi:.1%}", fontsize=8.5, color=INK2, va="center")
    a1.set_xticks(xs)
    a1.set_xticklabels(xl, fontsize=8)
    a1.set_xlim(-0.4, len(band) - 0.4)
    a1.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a1.set_ylim(bottom=0)
    a1.set_title("Late rate by distance quartile")
    a2.plot(xs, band.lead_median, "o-", color=BLUE, label="median")
    a2.plot(xs, band.lead_p95, "o-", color=ORANGE, label="p95")
    a2.set_xticks(xs)
    a2.set_xticklabels(xl, fontsize=8)
    a2.set_xlim(-0.4, len(band) - 0.4)
    a2.set_ylabel("Lead time (days)")
    a2.set_title("Lead time by distance quartile")
    a2.legend(loc="upper left")
    xs2 = np.arange(len(ship))
    a3.errorbar(xs2, ship.late_rate, yerr=[ship.late_rate - ship.late_rate_lo, ship.late_rate_hi - ship.late_rate], fmt="o", color=BLUE, capsize=3)
    for xi, r in zip(xs2, ship.itertuples()):
        a3.text(xi + 0.08, r.late_rate, f"{r.late_rate:.1%}", fontsize=8.5, color=INK2, va="center")
    a3.set_xticks(xs2)
    a3.set_xticklabels([f"{r.label}\n(n={int(r.n_delivered):,})" for r in ship.itertuples()], fontsize=8)
    a3.set_xlim(-0.5, len(ship) - 0.3)
    a3.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a3.set_ylim(bottom=0)
    a3.set_title("Shipment type")
    fig.suptitle("Distance (straight-line ZIP-prefix centroids, not road distance) and delivery outcomes, single-seller orders", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "04_distance_shipment")

    # F5 funnel plot
    sel = res["sellers"]
    fn = res["funnel_limits"]
    p0 = res["seller_reference_rate"]
    fig, ax = plt.subplots(figsize=(9.5, 5.6))
    ax.fill_between(fn.n, fn.lo998, fn.hi998, color=GREY, alpha=0.12, linewidth=0, label="99.8% limits")
    ax.fill_between(fn.n, fn.lo95, fn.hi95, color=GREY, alpha=0.22, linewidth=0, label="95% limits")
    ax.axhline(p0, color=INK2, linestyle="--", linewidth=1)
    low_c = sel[(sel.n_delivered >= SELLER_LOW_CONF_N) & (sel.n_delivered < SELLER_RANK_N)]
    rk = sel[sel.n_delivered >= SELLER_RANK_N]
    ax.scatter(low_c.n_delivered, low_c.late_rate, s=16, facecolor="none", edgecolor=GREY, linewidth=0.8, label="n = 30-49 (low confidence)", zorder=3)
    ax.scatter(rk[~rk.screen_signal].n_delivered, rk[~rk.screen_signal].late_rate, s=16, color=BLUE, alpha=0.7, label="n >= 50", zorder=3)
    ax.scatter(rk[rk.screen_signal].n_delivered, rk[rk.screen_signal].late_rate, s=26, color=ORANGE, edgecolor=dr.SURFACE, linewidth=0.5,
               label="n >= 50, Wilson interval above portfolio rate (screening signal)", zorder=4)
    ax.set_xscale("log")
    ax.set_xlim(25, sel.n_delivered.max() * 1.15)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylim(0, min(0.5, sel[sel.n_delivered >= SELLER_LOW_CONF_N].late_rate.max() * 1.05))
    ax.set_xlabel("Single-seller delivered orders per seller (log scale)")
    ax.set_ylabel("Late-delivery rate")
    ax.text(ax.get_xlim()[1], p0, f"portfolio {p0:.1%} ", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.set_title("Seller funnel plot (sellers with n >= 30; smaller sellers pooled and not shown)")
    ax.legend(loc="upper right", fontsize=8)
    save(fig, "05_seller_funnel")

    # F6 threshold sensitivity
    th = res["seller_thresholds"]
    fig, ax = plt.subplots(figsize=(8, 4.4))
    xs = np.arange(len(th))
    ax.bar(xs - 0.2, th.flagged_wilson_above, width=0.38, color=ORANGE, label="Observed: Wilson interval above portfolio rate")
    ax.bar(xs + 0.2, th.expected_false_flags_if_no_differences, width=0.38, color=GREY, label="Expected by chance if no seller differed")
    for xi, a, b, e in zip(xs, th.flagged_wilson_above, th.expected_false_flags_if_no_differences, th.eligible_sellers):
        ax.text(xi - 0.2, a + 1, f"{int(a)}", ha="center", fontsize=9, color=INK2)
        ax.text(xi + 0.2, b + 1, f"{b:.0f}", ha="center", fontsize=9, color=INK2)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"n >= {int(t.min_orders)}\n({int(t.eligible_sellers)} sellers, {t.share_of_orders_covered:.0%} of orders)" for t in th.itertuples()], fontsize=9)
    ax.set_ylabel("Number of sellers")
    ax.set_title("Seller screening signals vs the number expected by chance, by minimum-volume threshold")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right")
    save(fig, "06_threshold_sensitivity")

    # F7 decomposition
    d = res["decomposition"]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ys = np.arange(len(d))[::-1]
    ax.barh(ys, d.within_segment * 100, color=ORANGE, height=0.5, label="Within-segment change in late rate")
    ax.barh(ys, d.composition * 100, left=d.within_segment * 100, color=BLUE, height=0.5, label="Change in geographic / seller mix")
    for y, r in zip(ys, d.itertuples()):
        ax.text(r.difference * 100 + 0.4, y, f"total {r.difference * 100:.1f} pts  (mix {r.composition * 100:+.1f})", va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(ys)
    ax.set_yticklabels(d.segmentation, fontsize=9)
    ax.set_xlim(0, d.difference.max() * 100 * 1.55)
    ax.set_xlabel("Percentage points of late rate: high-delay months (2017-11, 2018-02, 2018-03) minus other months")
    ax.set_title("What explains the higher late rate in the high-delay months? Mix vs within-segment change")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8.5)
    save(fig, "07_period_decomposition")

    # F8 adjusted vs unadjusted
    rp = st[~st.low_volume]
    fig, ax = plt.subplots(figsize=(7, 5.4))
    ax.axhline(1, color=GREY, linestyle="--", linewidth=1)
    ax.axvline(ref, color=GREY, linestyle="--", linewidth=1)
    for r in rp.itertuples():
        ax.errorbar(r.late_rate, r.oe_month_promised, yerr=[[r.oe_month_promised - r.oe_month_promised_lo], [r.oe_month_promised_hi - r.oe_month_promised]], fmt="o",
                    color=region_colors[r.macro_region], capsize=0, alpha=0.9, markersize=5, elinewidth=0.8)
        ax.annotate(r.customer_state, (r.late_rate, r.oe_month_promised), xytext=(4, 3), textcoords="offset points", fontsize=8, color=INK2)
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(0.04))
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Observed late rate")
    ax.set_ylabel("Observed / expected late orders given month and promised lead time")
    ax.set_title("States: unadjusted rate vs standardised ratio (n >= 100 states; 95% bars)")
    for reg, col in region_colors.items():
        ax.plot([], [], "o", color=col, label=reg)
    ax.legend(loc="upper left", fontsize=8)
    save(fig, "08_state_adjusted")
    return paths


def main() -> int:
    if not DB.exists():
        print("Model not found. Run: python scripts/build_model.py", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    try:
        res = compute_all(con)
    finally:
        con.close()
    write_outputs(res)
    for p in make_figures(res):
        print("wrote", p.relative_to(ROOT))
    print("wrote", STATS_JSON.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
