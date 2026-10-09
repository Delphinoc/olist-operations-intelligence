"""Validation of the Operational Prioritization workstream (analysis/operational_prioritization.py).

Candidate counts are recomputed independently in pandas from the raw CSVs (tests/reference_pandas.py); the provisional tier
rules are re-implemented row by row in a deliberately different style; overlap is recomputed with plain set arithmetic;
reference rates are cross-checked against the geographic workstream. Expected values never come from the analysis output.
"""
from __future__ import annotations

import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import binomtest, spearmanr

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(ROOT / "analysis"))
import geographic_seller as gs  # noqa: E402
import operational_prioritization as op  # noqa: E402

EPISODES = pd.to_datetime(["2017-11-01", "2018-02-01", "2018-03-01"])
LEVELS = ("state", "lane", "seller")


@pytest.fixture(scope="session")
def res(con):
    return op.compute_all(con)


@pytest.fixture(scope="session")
def geo(con):
    return gs.compute_all(con)


@pytest.fixture(scope="session")
def frames(ref):
    """Independent order frames built from the raw CSVs: all delivered-in-window orders and the single-seller subset."""
    o = ref["orders_df"]
    o = o[o.in_window & o.dated].copy()
    o["month"] = o.purchase_date.dt.to_period("M").dt.to_timestamp()
    o["promised"] = (o.est_date - o.purchase_date).dt.days
    o["pg"] = pd.cut(o.promised, [-1, 14, 21, 28, 35, 10_000], labels=["1", "2", "3", "4", "5"]).astype(str)
    o["h1"] = o.purchase_date <= pd.Timestamp("2017-10-31")
    o["episode"] = o.month.isin(EPISODES)
    o["one"] = o.review_row_count == 1
    o["low"] = o.one & (o.review_score <= 2)
    items = ref["items_df"][["order_id", "seller_id"]].drop_duplicates()
    sellers = pd.read_csv(RAW / "olist_sellers_dataset.csv")[["seller_id", "seller_state"]]
    s = o[o.n_sellers == 1].merge(items, on="order_id").merge(sellers, on="seller_id")
    s["lane"] = s.seller_state + ">" + s.customer_state
    return o, s


def pandas_candidates(frame: pd.DataFrame, key: str, exclude_episodes: bool = False) -> pd.DataFrame:
    f = frame[~frame.episode] if exclude_episodes else frame
    g = f.groupby(key)
    out = pd.DataFrame({
        "n_delivered": g.size(), "n_late": g.late.sum(),
        "n_h1": g.h1.sum(), "late_h1": f[f.h1].groupby(key).late.sum().reindex(g.size().index).fillna(0),
        "n_ep": g.episode.sum(), "late_ep": f[f.episode].groupby(key).late.sum().reindex(g.size().index).fillna(0),
        "n_rev": g.one.sum(), "n_low": g.low.sum(),
        "n_rev_early": f[f.one & f.early_review].groupby(key).size().reindex(g.size().index).fillna(0),
        "n_low_early": f[f.low & f.early_review].groupby(key).size().reindex(g.size().index).fillna(0),
        "n_rev_after": f[f.one & ~f.early_review].groupby(key).size().reindex(g.size().index).fillna(0),
        "n_low_after": f[f.low & ~f.early_review].groupby(key).size().reindex(g.size().index).fillna(0),
        "n_rev_ontime": f[f.one & ~f.late].groupby(key).size().reindex(g.size().index).fillna(0),
        "n_low_ontime": f[f.low & ~f.late].groupby(key).size().reindex(g.size().index).fillna(0),
    })
    out["n_h2"] = out.n_delivered - out.n_h1
    out["late_h2"] = out.n_late - out.late_h1
    return out.astype(float)


def level_frame(level, frames):
    o, s = frames
    return (o, "customer_state") if level == "state" else (s, "lane" if level == "lane" else "seller_id")


# ------------------------------------------------------------------ populations and counts
def test_level_populations(res):
    assert (res["references"]["state"]["n_delivered"], res["references"]["state"]["n_late"]) == (96_203, 6_531)
    for lvl in ("lane", "seller"):
        assert (res["references"][lvl]["n_delivered"], res["references"][lvl]["n_late"]) == (94_931, 6_518)
    assert [res["references"][l]["n_segments"] for l in LEVELS] == [27, 408, 2_925]
    assert [res["references"][l]["n_eligible"] for l in LEVELS] == [24, 70, 412]          # blueprint R8 minimum-N policy preserved
    assert res["references"]["state"]["n_reviewed"] == 95_037


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("excl", [False, True])
def test_candidate_counts_match_pandas(res, frames, level, excl):
    f, key = level_frame(level, frames)
    exp = pandas_candidates(f, key, excl)
    got = res[f"candidates_{level}" + ("_excl_episodes" if excl else "")].set_index("segment")
    assert set(got.index) == set(exp.index)
    cols = list(exp.columns)
    pd.testing.assert_frame_equal(got.loc[exp.index, cols].astype(float), exp[cols], check_names=False, check_exact=True)


@pytest.mark.parametrize("level", LEVELS)
def test_segment_keys_unique_and_not_null(res, level):
    t = res[f"candidates_{level}"]
    assert t.segment.is_unique and t.segment.notna().all()
    assert (t.n_late <= t.n_delivered).all() and (t.n_low <= t.n_rev).all() and (t.n_rev <= t.n_delivered).all()
    assert (t.n_rev_early + t.n_rev_after == t.n_rev).all() and (t.n_low_early + t.n_low_after == t.n_low).all()
    assert (t.n_rev_ontime + t.n_rev_late == t.n_rev).all() and (t.n_low_ontime + t.n_low_late == t.n_low).all()
    assert ((t.coverage >= 0) & (t.coverage <= 1)).all()


# ------------------------------------------------------------------ reference rates and expected counts
@pytest.mark.parametrize("level", LEVELS)
def test_reference_rates_and_zero_sum_excess(res, level):
    t = res[f"candidates_{level}"]
    r = res["references"][level]
    assert math.isclose(r["late"], t.n_late.sum() / t.n_delivered.sum(), rel_tol=1e-12)
    for exp_col, obs_col in (("expected_late", "n_late"), ("expected_low", "n_low"), ("expected_low_after", "n_low_after"), ("expected_low_ontime", "n_low_ontime")):
        assert math.isclose(t[exp_col].sum(), t[obs_col].sum(), rel_tol=1e-9)          # excess over a population reference sums to zero
    for col in ("excess_late", "excess_low", "excess_low_after", "excess_low_ontime"):
        assert abs(t[col].sum()) < 1e-6
    assert math.isclose(t.exp_s1.sum(), t.n_late.sum(), rel_tol=1e-9)                  # adjusted expectation obeys the same identity


def test_reference_rates_match_geographic_workstream(res, geo):
    assert math.isclose(res["references"]["state"]["late"], geo["population_all"]["late_rate"], abs_tol=1e-12)
    assert math.isclose(res["references"]["lane"]["late"], geo["population_single_seller"]["late_rate"], abs_tol=1e-12)
    assert res["references"]["lane"] == res["references"]["seller"] or all(
        math.isclose(res["references"]["lane"][k], res["references"]["seller"][k]) for k in ("late", "h1", "h2", "low", "low_after", "low_ontime"))
    g = geo["states"].set_index("customer_state")
    t = res["candidates_state"].set_index("segment")
    assert np.allclose(t.excess_late, g.excess_late.reindex(t.index), atol=1e-9)
    assert np.allclose(t.oe_s1, g.oe_month_promised.reindex(t.index), atol=1e-9)
    assert set(t[t.eligible & t.signal].index) == set(g[g.above_ref].index)             # same screening signals as the geographic workstream
    gl = geo["lanes"].set_index("lane")
    tl = res["candidates_lane"].set_index("segment")
    assert set(tl[tl.eligible & tl.signal].index) == set(gl[gl.above_ref].index)
    assert np.allclose(tl.loc[gl.index, "oe_s1"], gl.oe_month_promised, atol=1e-9)
    gs_ = geo["sellers"].set_index("seller_id")
    ts = res["candidates_seller"].set_index("segment")
    assert set(ts[ts.eligible & ts.signal].index) == set(gs_[(gs_.n_delivered >= 50) & gs_.screen_signal].index)
    assert np.allclose(ts.exp_s1, gs_.exp_s1.reindex(ts.index), atol=1e-9)


@pytest.mark.parametrize("level", LEVELS)
def test_wilson_and_excess_formulas(res, level):
    t = res[f"candidates_{level}"]
    ref = res["references"][level]["late"]
    for r in t.sample(min(40, len(t)), random_state=1).itertuples():
        ci = binomtest(int(r.n_late), int(r.n_delivered)).proportion_ci(method="wilson")
        assert math.isclose(r.late_rate_lo, ci.low, abs_tol=1e-9) and math.isclose(r.late_rate_hi, ci.high, abs_tol=1e-9)
        assert math.isclose(r.excess_late, r.n_late - r.n_delivered * ref, abs_tol=1e-9)
        assert math.isclose(r.excess_late_lo, r.n_delivered * (ci.low - ref), abs_tol=1e-6)
        assert bool(r.signal) == (ci.low > ref)


def test_review_reference_rates_independent(res, frames):
    o, s = frames
    assert math.isclose(res["references"]["state"]["low"], o.low.sum() / o.one.sum(), rel_tol=1e-12)
    assert math.isclose(res["references"]["state"]["low_after"], (o.low & ~o.early_review).sum() / (o.one & ~o.early_review).sum(), rel_tol=1e-12)
    assert math.isclose(res["references"]["lane"]["low_ontime"], (s.low & ~s.late).sum() / (s.one & ~s.late).sum(), rel_tol=1e-12)
    t = res["candidates_state"]
    assert (t.early_review_share.dropna().between(0, 1)).all()
    assert abs(res["axis_relationship"]["state"]["early_review_share_overall"] - o[o.one].early_review.mean()) < 1e-12


# ------------------------------------------------------------------ evidence tier rules (independent, row-by-row implementation)
def reference_tier(row, level, x, require_consistency=True):
    min_n, low_n = (50, 30) if level == "seller" else (100, None)
    n, exc = row.n_delivered, row.n_late - row.n_delivered * row.ref
    signal = row.lo > row.ref
    if n >= min_n:
        if signal and exc >= x and not (require_consistency and row.cons == "inconsistent"):
            return "Investigate"
        if exc > 0 and (signal or exc >= x):
            return "Watch"
        return "No Signal"
    if low_n is not None and n >= low_n and signal and exc > 0:
        return "Watch"
    return "Insufficient Data"


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("x", op.EXCESS_THRESHOLDS)
def test_tiers_match_independent_rule_implementation(res, frames, level, x):
    t = res[f"candidates_{level}"]
    ref = res["references"][level]
    r1, r2 = ref["h1"], ref["h2"]
    rows = []
    for r in t.itertuples():
        if r.n_h1 < 30 or r.n_h2 < 30:
            cons = "unverified"
        elif r.late_h1 / r.n_h1 > r1 and r.late_h2 / r.n_h2 > r2:
            cons = "consistent"
        else:
            cons = "inconsistent"
        lo = binomtest(int(r.n_late), int(r.n_delivered)).proportion_ci(method="wilson").low
        rows.append(pd.Series(dict(n_delivered=r.n_delivered, n_late=r.n_late, ref=ref["late"], lo=lo, cons=cons)))
    expected = [reference_tier(r, level, x) for r in rows]
    assert list(t[f"tier_x{x}"]) == expected
    assert list(t.consistency) == [("unverified" if (a.n_h1 < 30 or a.n_h2 < 30) else ("consistent" if (a.late_h1 / a.n_h1 > r1 and a.late_h2 / a.n_h2 > r2) else "inconsistent")) for a in t.itertuples()]
    exp_nc = [reference_tier(r, level, op.PRIMARY_X, require_consistency=False) for r in rows]
    assert list(t.tier_no_consistency_rule) == exp_nc


@pytest.mark.parametrize("level", LEVELS)
def test_tier_invariants_and_nesting(res, level):
    t = res[f"candidates_{level}"]
    spec = op.LEVELS[level]
    assert set(t.tier) <= set(op.TIERS)
    inv = t[t.tier == "Investigate"]
    assert (inv.n_delivered >= spec["min_n"]).all() and inv.signal.all() and (inv.excess_late >= op.PRIMARY_X).all() and (inv.consistency != "inconsistent").all()
    assert (t[t.tier == "Insufficient Data"].n_delivered < spec["min_n"]).all()
    assert (t[t.n_delivered < (spec["low_conf_n"] or spec["min_n"])].tier == "Insufficient Data").all()
    ns = t[t.tier == "No Signal"]
    assert (~ns.signal).all() and (ns.excess_late < op.PRIMARY_X).all()
    sets = {x: set(t[t[f"tier_x{x}"] == "Investigate"].segment) for x in op.EXCESS_THRESHOLDS}
    assert sets[30] <= sets[20] <= sets[10]
    assert (t.tier == t[f"tier_x{op.PRIMARY_X}"]).all()
    # 'unverified' consistency never blocks, never labels a segment negative
    assert (t.loc[(t.consistency == "unverified") & t.signal & (t.excess_late >= op.PRIMARY_X) & t.eligible, "tier"] == "Investigate").all()
    low_conf = t[t.low_confidence]
    assert set(low_conf.tier) <= {"Watch", "Insufficient Data"}


def test_tier_counts_and_threshold_changes_consistent(res):
    tc = res["tier_counts"]
    for _, r in tc.iterrows():          # column names contain spaces, so avoid itertuples
        t = res[f"candidates_{r['level']}"]
        assert (t[f"tier_x{int(r['min_excess_late'])}"] == "Investigate").sum() == r["Investigate"]
        assert sum(r[k] for k in op.TIERS) == len(t)
        for tier in op.TIERS:
            assert (t[f"tier_x{int(r['min_excess_late'])}"] == tier).sum() == r[tier]
    assert all(c["nested"] for c in res["threshold_changes"])


def test_no_composite_score_or_significance_columns(res):
    banned = ("composite", "priority_score", "weighted", "p_value", "pvalue", "significan")
    for lvl in LEVELS:
        assert not any(b in c.lower() for c in res[f"candidates_{lvl}"].columns for b in banned)


# ------------------------------------------------------------------ high-delay period sensitivity
def test_episode_exclusion_reference_and_ranks(res, frames):
    o, s = frames
    for lvl, f in (("state", o), ("lane", s), ("seller", s)):
        e = f[~f.episode]
        ex = res[f"candidates_{lvl}_excl_episodes"]
        assert math.isclose(ex.n_late.sum() / ex.n_delivered.sum(), e.late.mean(), abs_tol=1e-12)
        assert ex.n_delivered.sum() == len(e)
        assert math.isclose(ex.excess_late.sum(), 0, abs_tol=1e-6)               # reference recomputed on the remaining months
    full, ex = res["candidates_state"], res["candidates_state_excl_episodes"]
    m = full[full.eligible].merge(ex[ex.eligible], on="segment", suffixes=("", "_x"))
    rho = spearmanr(m.excess_late, m.excess_late_x).statistic
    assert math.isclose(res["episode_comparison"]["state"]["spearman_excess"], rho, abs_tol=1e-9)
    c = res["episode_comparison"]["seller"]
    assert set(c["investigate_kept"]) | set(c["investigate_lost"]) == set(c["investigate_full"])
    assert set(c["investigate_kept"]).isdisjoint(c["investigate_lost"])


def test_episode_late_share_independent(res, frames):
    o, _ = frames
    t = res["candidates_state"].set_index("segment")
    exp = o[o.episode].groupby("customer_state").late.sum() / o.groupby("customer_state").late.sum()
    assert np.allclose(t.episode_late_share, exp.reindex(t.index), atol=1e-12)
    dep = res["episode_dependence"]
    assert set(dep.level) <= set(LEVELS) and (dep.population_episode_late_share.between(0.4, 0.6)).all()


# ------------------------------------------------------------------ overlap between levels (order ids)
def test_overlap_independent_set_arithmetic(res, frames):
    _, s = frames
    sets = {l: set(res[f"candidates_{l}"][res[f"candidates_{l}"].tier == "Investigate"].segment) for l in LEVELS}
    key = {"state": "customer_state", "lane": "lane", "seller": "seller_id"}
    ids = {l: set(s[s[key[l]].isin(sets[l])].order_id) for l in LEVELS}
    ref = s.late.mean()
    late_ids = set(s[s.late].order_id)
    ov = res["overlap"]
    assert ov["n_single_seller_orders"] == len(s)
    assert math.isclose(ov["reference_rate_single_seller"], ref, abs_tol=1e-12)
    for l in LEVELS:
        assert ov["per_level"][l]["n_orders"] == len(ids[l]) and ov["per_level"][l]["n_late"] == len(ids[l] & late_ids)
    union = set().union(*ids.values())
    assert ov["union"]["n_orders"] == len(union) and ov["union"]["n_late"] == len(union & late_ids)
    for p in ov["pairs"]:
        a, b = p["pair"].split(" & ")
        assert p["orders_both"] == len(ids[a] & ids[b]) and p["late_both"] == len(ids[a] & ids[b] & late_ids)
    # inclusion-exclusion, and the double count that adding level excesses would create
    a, b, c = ids["state"], ids["lane"], ids["seller"]
    assert len(union) == len(a) + len(b) + len(c) - len(a & b) - len(a & c) - len(b & c) + len(a & b & c)
    naive = sum(ov["per_level"][l]["excess"] for l in LEVELS)
    assert math.isclose(ov["naive_sum_of_level_excess"], naive, abs_tol=1e-6)
    assert math.isclose(ov["double_counted_excess"], naive - ov["union"]["excess"], abs_tol=1e-6) and ov["double_counted_excess"] > 0
    assert ov["late_orders_naive_sum"] > ov["late_orders_union"]
    cells = res["overlap_cells"]
    assert cells.n_orders.sum() == len(s) and cells.n_late.sum() == int(s.late.sum())


def test_lane_orders_nest_inside_destination_state(res, frames):
    _, s = frames
    nest = res["state_lane_nesting"].set_index("state")
    inv_lanes = set(res["candidates_lane"][res["candidates_lane"].tier == "Investigate"].segment)
    for st in nest.index:
        d = s[s.customer_state == st]
        assert int(nest.loc[st, "orders_single_seller"]) == len(d)
        assert int(nest.loc[st, "orders_in_investigate_lanes"]) == int(d.lane.isin(inv_lanes).sum())
    for lane in inv_lanes:                       # every lane belongs to exactly one destination state
        assert s[s.lane == lane].customer_state.nunique() == 1


# ------------------------------------------------------------------ special candidates
def test_special_candidates_evaluated_without_presupposed_rank(res, frames):
    _, s = frames
    sc = res["special_candidates"]
    assert {"RJ", "SP>RJ"} <= set(sc.segment)
    ne = sc[(sc.level == "lane") & (sc.segment != "SP>RJ")]
    assert ne.segment.str.split(">").str[1].isin(["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"]).all()
    assert len(ne) == len(res["special_ne_lanes"])
    for col in ("rank_excess_late", "rank_excess_late_lo", "rank_late_rate", "rank_oe_s1", "rank_excess_low"):
        assert sc[col].notna().all() and (sc[col] >= 1).all()
    # ranks are computed per metric among eligible segments of the level, not from one preset ordering
    lane = res["candidates_lane"]
    for r in sc[sc.level == "lane"].itertuples():
        row = lane.set_index("segment").loc[r.segment]
        assert r.rank_late_rate == (lane[lane.eligible].late_rate > row.late_rate).sum() + 1
        assert r.rank_excess_late == (lane[lane.eligible].excess_late > row.excess_late).sum() + 1
    g = res["special_groups"].set_index("group")
    rj_all, sp_rj, other = g.loc["All single-seller orders to RJ"], g.loc["SP>RJ lane"], g.loc["Other lanes into RJ"]
    assert rj_all.n_orders == sp_rj.n_orders + other.n_orders and rj_all.n_late == sp_rj.n_late + other.n_late
    assert sp_rj.n_orders == int((s.lane == "SP>RJ").sum()) and sp_rj.n_late == int(s[s.lane == "SP>RJ"].late.sum())
    assert math.isclose(rj_all.excess_vs_ref, sp_rj.excess_vs_ref + other.excess_vs_ref, abs_tol=1e-6)


# ------------------------------------------------------------------ matrix axes and review handling
def test_matrix_axes_use_distinct_populations(res):
    for lvl in LEVELS:
        t = res[f"candidates_{lvl}"]
        assert (t.n_rev <= t.n_delivered).all() and (t.n_rev < t.n_delivered).any()          # reviewed population is a strict subset
    ax = res["axis_relationship"]
    assert ax["state"]["spearman_excess_late_vs_excess_low"] > 0.8                          # axes are strongly coupled (late orders attract low scores)
    assert ax["state"]["spearman_excess_late_vs_excess_low_ontime"] < ax["state"]["spearman_excess_late_vs_excess_low"]
    assert 0.9 < ax["state"]["coverage_overall"] < 1.0


def test_shortlist_is_investigate_tier_only(res):
    sl = res["shortlist"]
    assert (sl.tier == "Investigate").all()
    assert len(sl) == int(sum((res[f"candidates_{l}"].tier == "Investigate").sum() for l in LEVELS))
    for col in ("excess_late", "late_rate_lo", "oe_s1", "excess_low", "excess_low_after", "coverage", "consistency", "episode_late_share"):
        assert col in sl.columns and sl[col].notna().all()


# ------------------------------------------------------------------ SQL template safety and outputs
def test_sql_template_exclusion_parameter(con):
    full = op.load_candidates(con, "state", False)
    ex = op.load_candidates(con, "state", True)
    assert full.n_delivered.sum() - ex.n_delivered.sum() == int(full.n_ep.sum()) and ex.n_ep.sum() == 0
    assert full.segment.is_unique and ex.segment.is_unique


def test_outputs_written_and_figures_nonempty(res, tmp_path, monkeypatch):
    monkeypatch.setattr(op, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(op, "FIG_DIR", tmp_path / "figures")
    monkeypatch.setattr(op, "STATS_JSON", tmp_path / "stats.json")
    op.write_outputs(res)
    figs = op.make_figures(res)
    assert len(figs) == 6 and all(p.stat().st_size > 10_000 for p in figs)
    assert (tmp_path / "stats.json").stat().st_size > 1_000
    assert len(pd.read_csv(tmp_path / "tables" / "prio_candidates_state.csv")) == 27
