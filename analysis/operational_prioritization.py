"""Workstream 4 - Operational prioritization: which states, lanes and sellers deserve investigation first?

Usage (project root, after `python scripts/build_model.py`):
    python analysis/operational_prioritization.py

Builds one candidate table per segment level on its validated population, with observed / expected / excess late orders,
adjusted observed-expected context, review-based low-score measures (before- and after-delivery reviews kept apart), and
PROVISIONAL, rule-based evidence tiers (Investigate, Watch, Insufficient Data, No Signal). There is no composite score and
no significance test. Everything is observational: a tier says where to look first, not what caused the delays.
Outputs: reports/tables/prio_*.csv, reports/prio_stats.json, reports/figures/prio_*.png.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_reliability as dr  # noqa: E402  (Wilson helper, chart style)
import geographic_seller as gs  # noqa: E402  (stratified expectation helper)

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "analysis" / "prioritization"
DB = ROOT / "data" / "processed" / "olist_model.duckdb"
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
STATS_JSON = ROOT / "reports" / "prio_stats.json"

# ---- provisional, pre-specified rules ---------------------------------------------------------------
PRIMARY_X = 20                 # provisional minimum excess late orders for 'Investigate'
EXCESS_THRESHOLDS = (10, 20, 30)
HALF_MIN_N = 30                # a window half is "adequately observed" for a segment at n >= 30 orders
LEVELS = {                     # min_n: minimum volume to rank; low_conf_n: seller low-confidence floor (blueprint R8)
    "state": {"min_n": 100, "low_conf_n": None, "group_col": "customer_state", "title": "Customer state"},
    "lane": {"min_n": 100, "low_conf_n": None, "group_col": "lane", "title": "Lane (seller state > customer state)"},
    "seller": {"min_n": 50, "low_conf_n": 30, "group_col": "seller_id", "title": "Seller (single-seller orders)"},
}
EPISODE_MONTHS = gs.EPISODE_MONTHS
TIERS = ["Investigate", "Watch", "No Signal", "Insufficient Data"]
STRATA = ["purchase_month", "promised_group"]


# ------------------------------------------------------------------ data access
def load_candidates(con, level: str, exclude_episodes: bool = False) -> pd.DataFrame:
    src = (SQL_DIR / f"src_{level}.sql").read_text(encoding="utf-8").strip().rstrip(";")
    sql = (SQL_DIR / "candidates_template.sql").read_text(encoding="utf-8").replace("{{SRC}}", src)
    return con.execute(sql, {"exclude_episodes": exclude_episodes}).df()


def load_frame(con) -> pd.DataFrame:
    f = con.execute((SQL_DIR / "order_segments.sql").read_text(encoding="utf-8")).df()
    f["purchase_month"] = pd.to_datetime(f.purchase_month).dt.date
    f["is_episode"] = f.purchase_month.isin(EPISODE_MONTHS)
    return f


# ------------------------------------------------------------------ evidence tiers
def assign_tiers(d: pd.DataFrame, level: str, x: float, require_consistency: bool = True) -> pd.Series:
    """Provisional tier rules (see the findings report, Section 1). No weighting, no significance test.
    Insufficient Data : n below the minimum volume (sellers 30-49 are low-confidence: Watch only if they show a signal).
    Investigate       : n >= minimum AND Wilson interval above the reference rate AND excess late >= x
                        AND window-half consistency is not 'inconsistent' ('unverified' does not block).
    Watch             : positive excess and (interval above reference OR excess >= x) but not Investigate,
                        or a low-confidence seller with a signal.
    No Signal         : everything else with adequate volume."""
    spec = LEVELS[level]
    n, exc, signal = d.n_delivered, d.excess_late, d.signal
    eligible = n >= spec["min_n"]
    low_conf = (n >= spec["low_conf_n"]) & ~eligible if spec["low_conf_n"] else pd.Series(False, index=d.index)
    consistent_enough = (d.consistency != "inconsistent") if require_consistency else pd.Series(True, index=d.index)
    invest = eligible & signal & (exc >= x) & consistent_enough
    watch = (eligible & (exc > 0) & (signal | (exc >= x)) & ~invest) | (low_conf & signal & (exc > 0))
    insufficient = ~eligible & ~watch
    return pd.Series(np.select([invest, watch, insufficient], TIERS[0:2] + ["Insufficient Data"], "No Signal"), index=d.index)


def rank_desc(values: pd.Series, mask: pd.Series) -> pd.Series:
    return values.where(mask).rank(ascending=False, method="min")


def population_refs(d: pd.DataFrame) -> dict:
    """Reference rates of the level's own population (late, window halves, and low-score shares by review type)."""
    return {"late": d.n_late.sum() / d.n_delivered.sum(), "h1": d.late_h1.sum() / d.n_h1.sum(), "h2": d.late_h2.sum() / d.n_h2.sum(),
            "low": d.n_low.sum() / d.n_rev.sum(), "low_after": d.n_low_after.sum() / d.n_rev_after.sum(),
            "low_ontime": d.n_low_ontime.sum() / d.n_rev_ontime.sum(), "low_early": d.n_low_early.sum() / d.n_rev_early.sum()}


def enrich(cand: pd.DataFrame, level: str, frame: pd.DataFrame, exclude_episodes: bool = False) -> pd.DataFrame:
    d = cand.copy()
    spec = LEVELS[level]
    refs = population_refs(d)
    ref, ref_h1, ref_h2 = refs["late"], refs["h1"], refs["h2"]
    # --- late orders: observed, expected, excess, interval
    d = dr.add_wilson(d, "n_late", "n_delivered", "late_rate")
    d["expected_late"] = d.n_delivered * ref
    d["excess_late"] = d.n_late - d.expected_late
    d["excess_late_lo"] = d.n_delivered * (d.late_rate_lo - ref)
    d["excess_late_hi"] = d.n_delivered * (d.late_rate_hi - ref)
    d["signal"] = d.late_rate_lo > ref                      # Wilson interval entirely above the reference rate
    d["below_signal"] = d.late_rate_hi < ref
    # --- adjusted (secondary context): expected late orders from month x promised-lead strata pooled over the level's population
    fr = frame if level == "state" else frame[frame.is_single_seller]
    if exclude_episodes:
        fr = fr[~fr.is_episode]
    e = gs.expected_by_strata(fr, spec["group_col"], STRATA)[[spec["group_col"], "expected", "oe_ratio"]]
    e.columns = ["segment", "exp_s1", "oe_s1"]
    d = d.merge(e, on="segment", how="left")
    d["excess_vs_s1"] = d.n_late - d.exp_s1
    # --- reviews: coverage, low-score observed / expected / excess (single-review population only)
    d["coverage"] = d.n_rev / d.n_delivered
    d = dr.add_wilson(d, "n_low", "n_rev", "low_rate")
    d["expected_low"] = d.n_rev * refs["low"]
    d["excess_low"] = d.n_low - d.expected_low
    d["excess_low_lo"] = d.n_rev * (d.low_rate_lo - refs["low"])
    d["excess_low_hi"] = d.n_rev * (d.low_rate_hi - refs["low"])
    d["expected_low_after"] = d.n_rev_after * refs["low_after"]
    d["excess_low_after"] = d.n_low_after - d.expected_low_after
    d["expected_low_ontime"] = d.n_rev_ontime * refs["low_ontime"]
    d["excess_low_ontime"] = d.n_low_ontime - d.expected_low_ontime
    d["early_review_share"] = d.n_rev_early / d.n_rev.replace(0, np.nan)
    d["episode_late_share"] = d.late_ep / d.n_late.replace(0, np.nan)
    # --- stability across window halves; a half with < HALF_MIN_N orders is 'unverified', never a negative
    r1 = d.late_h1 / d.n_h1.replace(0, np.nan)
    r2 = d.late_h2 / d.n_h2.replace(0, np.nan)
    adequate = (d.n_h1 >= HALF_MIN_N) & (d.n_h2 >= HALF_MIN_N)
    d["rate_h1"], d["rate_h2"] = r1, r2
    d["consistency"] = np.select([~adequate, (r1 > ref_h1) & (r2 > ref_h2)], ["unverified", "consistent"], "inconsistent")
    # --- tiers at each threshold and ranks (eligible segments only)
    for x in EXCESS_THRESHOLDS:
        d[f"tier_x{x}"] = assign_tiers(d, level, x)
    d["tier"] = d[f"tier_x{PRIMARY_X}"]
    d["tier_no_consistency_rule"] = assign_tiers(d, level, PRIMARY_X, require_consistency=False)   # shows what the consistency rule changes
    d["eligible"] = d.n_delivered >= spec["min_n"]
    d["low_confidence"] = (d.n_delivered >= (spec["low_conf_n"] or 10**9)) & ~d.eligible
    for name, col in (("excess_late", "excess_late"), ("excess_late_lo", "excess_late_lo"), ("late_rate", "late_rate"), ("oe_s1", "oe_s1"), ("excess_low", "excess_low")):
        d[f"rank_{name}"] = rank_desc(d[col], d.eligible)
    d["level"] = level
    return d.sort_values("excess_late", ascending=False).reset_index(drop=True)


# ------------------------------------------------------------------ comparisons
def compare_episode_exclusion(full: pd.DataFrame, ex: pd.DataFrame) -> dict:
    m = full[["segment", "tier", "excess_late", "eligible", "n_delivered", "episode_late_share"]].merge(
        ex[["segment", "tier", "excess_late", "eligible", "n_delivered"]], on="segment", how="left", suffixes=("", "_ex"))
    both = m[m.eligible & m.eligible_ex.fillna(False)]
    top = lambda df, col: set(df.sort_values(col, ascending=False).head(10).segment)  # noqa: E731
    return {
        "n_eligible_full": int(full.eligible.sum()), "n_eligible_excl": int(ex.eligible.sum()),
        "spearman_excess": float(both.excess_late.corr(both.excess_late_ex, method="spearman")) if len(both) > 2 else float("nan"),
        "top10_overlap": len(top(full[full.eligible], "excess_late") & top(ex[ex.eligible], "excess_late")),
        "investigate_full": sorted(full[full.tier == "Investigate"].segment),
        "investigate_excl": sorted(ex[ex.tier == "Investigate"].segment),
        "investigate_kept": sorted(set(full[full.tier == "Investigate"].segment) & set(ex[ex.tier == "Investigate"].segment)),
        "investigate_lost": sorted(set(full[full.tier == "Investigate"].segment) - set(ex[ex.tier == "Investigate"].segment)),
        "investigate_gained": sorted(set(ex[ex.tier == "Investigate"].segment) - set(full[full.tier == "Investigate"].segment)),
        "transitions": m.assign(tier_ex=m.tier_ex.fillna("absent")).groupby(["tier", "tier_ex"]).size().reset_index(name="n").to_dict("records"),
        "population_episode_late_share": float(full.late_ep.sum() / full.n_late.sum()),
    }


def overlap_analysis(frame: pd.DataFrame, sets: dict[str, set]) -> dict:
    """Quantify overlap between the Investigate sets of the three levels using order ids (single-seller orders)."""
    os_ = frame[frame.is_single_seller].copy()
    ref = float(os_.is_late.mean())
    member = {"state": os_.customer_state.isin(sets["state"]), "lane": os_.lane.isin(sets["lane"]), "seller": os_.seller_id.isin(sets["seller"])}
    for k, v in member.items():
        os_[f"in_{k}"] = v
    cells = os_.groupby(["in_state", "in_lane", "in_seller"]).is_late.agg(n_orders="size", n_late="sum").reset_index()
    cells["excess_vs_ref"] = cells.n_late - cells.n_orders * ref
    union = os_[os_.in_state | os_.in_lane | os_.in_seller]
    per_level = {}
    for k in member:
        s = os_[member[k]]
        per_level[k] = {"n_orders": int(len(s)), "n_late": int(s.is_late.sum()), "excess": float(s.is_late.sum() - len(s) * ref)}
    pairs = []
    for a, b in (("state", "lane"), ("state", "seller"), ("lane", "seller")):
        ab = os_[member[a] & member[b]]
        pairs.append({"pair": f"{a} & {b}", "orders_a": per_level[a]["n_orders"], "orders_b": per_level[b]["n_orders"], "orders_both": int(len(ab)),
                      "share_of_a_in_b": float(len(ab) / per_level[a]["n_orders"]) if per_level[a]["n_orders"] else float("nan"),
                      "share_of_b_in_a": float(len(ab) / per_level[b]["n_orders"]) if per_level[b]["n_orders"] else float("nan"),
                      "late_both": int(ab.is_late.sum())})
    naive = sum(v["excess"] for v in per_level.values())
    out = {"reference_rate_single_seller": ref, "n_single_seller_orders": int(len(os_)), "per_level": per_level, "pairs": pairs,
           "union": {"n_orders": int(len(union)), "n_late": int(union.is_late.sum()), "excess": float(union.is_late.sum() - len(union) * ref),
                     "share_of_all_orders": float(len(union) / len(os_)), "share_of_all_late": float(union.is_late.sum() / os_.is_late.sum())},
           "naive_sum_of_level_excess": float(naive), "double_counted_excess": float(naive - (union.is_late.sum() - len(union) * ref)),
           "late_orders_naive_sum": int(sum(v["n_late"] for v in per_level.values())), "late_orders_union": int(union.is_late.sum())}
    return {"summary": out, "cells": cells}


# ------------------------------------------------------------------ main computation
def compute_all(con: duckdb.DuckDBPyConnection) -> dict:
    res: dict = {}
    frame = load_frame(con)
    regions = dict(con.execute((SQL_DIR / "state_regions.sql").read_text(encoding="utf-8")).fetchall())
    tables, tables_ex, comparisons = {}, {}, {}
    refs = {}
    for level in LEVELS:
        cand = load_candidates(con, level, False)
        enr = enrich(cand, level, frame, False)
        ex = enrich(load_candidates(con, level, True), level, frame, True)
        tables[level], tables_ex[level] = enr, ex
        refs[level] = {k: float(v) for k, v in population_refs(cand).items()}
        refs[level].update(n_delivered=int(enr.n_delivered.sum()), n_late=int(enr.n_late.sum()), n_reviewed=int(enr.n_rev.sum()),
                           n_segments=int(len(enr)), n_eligible=int(enr.eligible.sum()))
        comparisons[level] = compare_episode_exclusion(enr, ex)
        res[f"candidates_{level}"] = enr
        res[f"candidates_{level}_excl_episodes"] = ex
    res["references"] = refs
    res["episode_comparison"] = comparisons
    res["episode_transitions"] = pd.concat([pd.DataFrame(c["transitions"]).assign(level=lvl) for lvl, c in comparisons.items()], ignore_index=True)

    # ---- tier counts by threshold and level, and threshold-driven membership changes
    rows, changes = [], []
    for level, t in tables.items():
        for x in EXCESS_THRESHOLDS:
            vc = t[f"tier_x{x}"].value_counts()
            rows.append({"level": level, "min_excess_late": x, **{tier: int(vc.get(tier, 0)) for tier in TIERS}})
        inv = {x: set(t[t[f"tier_x{x}"] == "Investigate"].segment) for x in EXCESS_THRESHOLDS}
        changes.append({"level": level, "added_going_20_to_10": sorted(inv[10] - inv[20]), "dropped_going_20_to_30": sorted(inv[20] - inv[30]),
                        "nested": bool(inv[30] <= inv[20] <= inv[10])})
    res["tier_counts"] = pd.DataFrame(rows)
    res["threshold_changes"] = changes

    # ---- overlap between Investigate sets (order ids; single-seller orders)
    sets = {lvl: set(tables[lvl][tables[lvl].tier == "Investigate"].segment) for lvl in LEVELS}
    ov = overlap_analysis(frame, sets)
    res["overlap"] = ov["summary"]
    res["overlap_cells"] = ov["cells"]
    os_ = frame[frame.is_single_seller]
    ref_ss = float(os_.is_late.mean())
    nest = []
    for st in sorted(sets["state"]):
        s_orders = os_[os_.customer_state == st]
        l_orders = s_orders[s_orders.lane.isin(sets["lane"])]
        nest.append({"state": st, "orders_single_seller": int(len(s_orders)), "late": int(s_orders.is_late.sum()), "excess": float(s_orders.is_late.sum() - len(s_orders) * ref_ss),
                     "orders_in_investigate_lanes": int(len(l_orders)), "late_in_investigate_lanes": int(l_orders.is_late.sum()),
                     "excess_in_investigate_lanes": float(l_orders.is_late.sum() - len(l_orders) * ref_ss),
                     "share_of_state_orders_in_investigate_lanes": float(len(l_orders) / len(s_orders))})
    res["state_lane_nesting"] = pd.DataFrame(nest)

    # ---- special candidates: SP>RJ, Rio de Janeiro destination, high-rate Northeast lanes
    lanes = tables["lane"]
    ne_signal = lanes[lanes.eligible & lanes.signal & lanes.segment.str.split(">").str[1].map(regions).eq("Northeast")]
    specials = [("state", "RJ"), ("lane", "SP>RJ")] + [("lane", s) for s in ne_signal.sort_values("excess_late", ascending=False).segment]
    srows = []
    for level, seg in specials:
        r = tables[level].set_index("segment").loc[seg]
        n_el = int(tables[level].eligible.sum())
        srows.append({"level": level, "segment": seg, "n_delivered": int(r.n_delivered), "n_late": int(r.n_late), "late_rate": r.late_rate, "late_rate_lo": r.late_rate_lo, "late_rate_hi": r.late_rate_hi,
                      "excess_late": r.excess_late, "excess_late_lo": r.excess_late_lo, "oe_s1": r.oe_s1, "excess_low": r.excess_low, "excess_low_after": r.excess_low_after,
                      "tier": r.tier, "tier_excl_episodes": tables_ex[level].set_index("segment").tier.get(seg, "absent"), "consistency": r.consistency,
                      "rank_excess_late": r.rank_excess_late, "rank_excess_late_lo": r.rank_excess_late_lo, "rank_late_rate": r.rank_late_rate, "rank_oe_s1": r.rank_oe_s1,
                      "rank_excess_low": r.rank_excess_low, "n_eligible_in_level": n_el})
    res["special_candidates"] = pd.DataFrame(srows)
    # like-for-like groups on single-seller orders (overlap-aware): RJ destination, SP>RJ, other lanes into RJ, Northeast signal lanes
    def grp(mask, label):
        g = os_[mask]
        return {"group": label, "n_orders": int(len(g)), "n_late": int(g.is_late.sum()), "late_rate": float(g.is_late.mean()), "excess_vs_ref": float(g.is_late.sum() - len(g) * ref_ss)}
    ne_lane_set = set(ne_signal.segment)
    res["special_groups"] = pd.DataFrame([
        grp(os_.customer_state == "RJ", "All single-seller orders to RJ"), grp(os_.lane == "SP>RJ", "SP>RJ lane"),
        grp((os_.customer_state == "RJ") & (os_.lane != "SP>RJ"), "Other lanes into RJ"),
        grp(os_.lane.isin(ne_lane_set), f"Northeast lanes with an interval above the reference ({len(ne_lane_set)} lanes)"),
        grp(os_.customer_state.map(regions).eq("Northeast"), "All single-seller orders to the Northeast region"),
        grp(os_.customer_state.map(regions).eq("Northeast") & ~os_.lane.isin(ne_lane_set), "Northeast region, lanes not flagged"),
    ])
    res["special_ne_lanes"] = sorted(ne_lane_set)

    # ---- episode dependence of Investigate candidates
    dep = []
    for level, t in tables.items():
        inv = t[t.tier == "Investigate"]
        pop_share = comparisons[level]["population_episode_late_share"]
        for r in inv.itertuples():
            dep.append({"level": level, "segment": r.segment, "excess_late": r.excess_late, "episode_late_share": r.episode_late_share, "population_episode_late_share": pop_share,
                        "tier_excl_episodes": tables_ex[level].set_index("segment").tier.get(r.segment, "absent")})
    res["episode_dependence"] = pd.DataFrame(dep)

    # ---- shortlist (Investigate tier) and relation of the two matrix axes
    res["shortlist"] = pd.concat([t[t.tier == "Investigate"] for t in tables.values()], ignore_index=True)
    axis = {}
    for level, t in tables.items():
        e = t[t.eligible]
        axis[level] = {"spearman_excess_late_vs_excess_low": float(e.excess_late.corr(e.excess_low, method="spearman")),
                       "spearman_excess_late_vs_excess_low_ontime": float(e.excess_late.corr(e.excess_low_ontime, method="spearman")),
                       "coverage_min": float(e.coverage.min()), "coverage_max": float(e.coverage.max()), "coverage_overall": float(t.n_rev.sum() / t.n_delivered.sum()),
                       "early_review_share_overall": float(t.n_rev_early.sum() / t.n_rev.sum())}
    res["axis_relationship"] = axis
    res["episode_months"] = [str(m) for m in EPISODE_MONTHS]
    res["rules"] = {"primary_excess_threshold": PRIMARY_X, "thresholds": list(EXCESS_THRESHOLDS), "half_min_n": HALF_MIN_N,
                    "min_n": {k: v["min_n"] for k, v in LEVELS.items()}, "seller_low_confidence_n": 30}
    return res


# ------------------------------------------------------------------ outputs
def write_outputs(res: dict) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    scalars = {}
    for key, val in res.items():
        if isinstance(val, pd.DataFrame):
            val.to_csv(TABLE_DIR / f"prio_{key}.csv", index=False)
        else:
            scalars[key] = val
    STATS_JSON.write_text(json.dumps(scalars, indent=2, default=dr._jsonable), encoding="utf-8")


# ------------------------------------------------------------------ charts
def make_figures(res: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    dr._style()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    BLUE, ORANGE, GREY, INK2 = dr.BLUE, dr.ORANGE, dr.GREY, dr.INK2
    tier_color = {"Investigate": ORANGE, "Watch": "#eda100", "No Signal": BLUE, "Insufficient Data": "#c9c8c1"}
    paths = []

    def save(fig, name):
        p = FIG_DIR / f"prio_{name}.png"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)

    def label(seg, level):
        return seg[:8] if level == "seller" else seg

    # F1/F2 priority matrix (all reviews / after-delivery reviews only)
    for name, ycol, ylab, ttl in (("01_priority_matrix", "excess_low", "Excess low-score (1-2 star) reviewed orders", "all single-review orders"),
                                  ("02_priority_matrix_after_delivery", "excess_low_after", "Excess low-score orders, reviews written on/after delivery", "reviews written after delivery only")):
        fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
        for ax, level in zip(axes, LEVELS):
            t = res[f"candidates_{level}"]
            e = t[t.eligible]
            for tier in ("No Signal", "Watch", "Investigate"):
                s = e[e.tier == tier]
                if ycol == "excess_low":
                    ye = [(s.excess_low - s.excess_low_lo).clip(lower=0), (s.excess_low_hi - s.excess_low).clip(lower=0)]
                else:
                    ye = None
                xe = [(s.excess_late - s.excess_late_lo).clip(lower=0), (s.excess_late_hi - s.excess_late).clip(lower=0)]
                ax.errorbar(s.excess_late, s[ycol], xerr=xe, yerr=ye, fmt="none", ecolor=tier_color[tier], alpha=0.22 if tier == "No Signal" else 0.4, elinewidth=0.8)
                ax.scatter(s.excess_late, s[ycol], s=np.sqrt(s.n_delivered) * (0.9 if level != "seller" else 2.2), color=tier_color[tier], edgecolor=dr.SURFACE, linewidth=0.6, label=tier, zorder=3)
            for r in e[e.tier == "Investigate"].head(8).itertuples():
                ax.annotate(label(r.segment, level), (r.excess_late, getattr(r, ycol)), xytext=(4, 3), textcoords="offset points", fontsize=8, color=INK2)
            ax.axhline(0, color=GREY, linewidth=0.8)
            ax.axvline(0, color=GREY, linewidth=0.8)
            if level != "seller":      # a few very large segments dominate: symmetric-log axes keep the middle readable
                ax.set_xscale("symlog", linthresh=50)
                ax.set_yscale("symlog", linthresh=50)
                for axis in (ax.xaxis, ax.yaxis):
                    axis.set_major_locator(matplotlib.ticker.FixedLocator([-1000, -300, -100, -30, 0, 30, 100, 300, 1000]))
                    axis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
                    axis.set_minor_locator(matplotlib.ticker.NullLocator())
            ax.set_xlabel("Excess late orders (delivered orders; symmetric-log scale)" if level != "seller" else "Excess late orders (delivered orders)")
            ax.set_ylabel(ylab if level == "state" else "")
            ax.set_title(f"{LEVELS[level]['title']}: {int(e.shape[0])} ranked")
        axes[0].legend(loc="upper left", fontsize=8, title="Provisional tier")
        fig.suptitle(f"Two-axis priority matrix ({ttl}); marker area ~ delivered orders, bars = 95% Wilson uncertainty (x); axes use different populations", x=0.01, ha="left", fontweight="bold", fontsize=11)
        fig.tight_layout()
        save(fig, name)

    # F3 tier counts by threshold
    tc = res["tier_counts"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=False)
    for ax, level in zip(axes, LEVELS):
        sub = tc[tc.level == level]
        bottom = np.zeros(len(sub))
        for tier in TIERS:
            ax.bar(sub.min_excess_late.astype(str), sub[tier], bottom=bottom, color=tier_color[tier], label=tier, edgecolor=dr.SURFACE)
            for xi, (v, b) in enumerate(zip(sub[tier], bottom)):
                if v >= max(3, sub[TIERS].sum(axis=1).max() * 0.04):
                    ax.text(xi, b + v / 2, str(int(v)), ha="center", va="center", fontsize=8, color="white" if tier != "Watch" else INK2)
            bottom += sub[tier].to_numpy()
        ax.set_xlabel("Minimum excess late orders for 'Investigate'")
        ax.set_title(LEVELS[level]["title"], fontsize=10)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel("Segments")
    axes[2].legend(loc="upper right", fontsize=8)
    fig.suptitle("Tier counts under excess-late thresholds of 10, 20 (provisional) and 30", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "03_threshold_sensitivity")

    # F4 episode exclusion: excess late full vs excluding the three high-delay months
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    for ax, level in zip(axes, LEVELS):
        full, ex = res[f"candidates_{level}"], res[f"candidates_{level}_excl_episodes"]
        m = full[full.eligible][["segment", "excess_late", "tier"]].merge(ex[["segment", "excess_late", "tier"]], on="segment", suffixes=("", "_ex"))
        for tier in ("No Signal", "Watch", "Investigate"):
            s = m[m.tier == tier]
            ax.scatter(s.excess_late, s.excess_late_ex, s=22, color=tier_color[tier], edgecolor=dr.SURFACE, linewidth=0.5, label=f"{tier} (full period)")
        lim = [min(m.excess_late.min(), m.excess_late_ex.min()), max(m.excess_late.max(), m.excess_late_ex.max())]
        ax.plot(lim, lim, color=GREY, linestyle="--", linewidth=0.9)
        ax.set_xlabel("Excess late orders, all months")
        ax.set_ylabel("Excess late orders, excluding 2017-11, 2018-02, 2018-03" if level == "state" else "")
        sp = res["episode_comparison"][level]
        ax.set_title(f"{LEVELS[level]['title']}\nrank corr {sp['spearman_excess']:.2f}; top-10 overlap {sp['top10_overlap']}/10", fontsize=10)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Does the high-delay period drive the candidate list? (reference rate recomputed on the remaining months)", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "04_episode_exclusion")

    # F5 overlap
    cells = res["overlap_cells"].copy()
    cells["membership"] = cells.apply(lambda r: " + ".join([n for n, f in (("state", r.in_state), ("lane", r.in_lane), ("seller", r.in_seller)) if f]) or "none of the three sets", axis=1)
    cells = cells.sort_values("n_late", ascending=True)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.barh(cells.membership, cells.n_late, color=ORANGE)
    for y, (v, n_) in enumerate(zip(cells.n_late, cells.n_orders)):
        ax.text(v + 20, y, f"{int(v):,} late of {int(n_):,} orders", va="center", fontsize=8.5, color=INK2)
    ax.set_xlabel("Late orders (each order counted once)")
    ax.set_xlim(0, cells.n_late.max() * 1.45)
    ax.grid(axis="y", visible=False)
    ax.set_title("Late orders by membership in the Investigate sets of the three levels (single-seller orders)")
    save(fig, "05_overlap")

    # F6 top candidates per level by excess late
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))
    for ax, level in zip(axes, LEVELS):
        e = res[f"candidates_{level}"]
        e = e[e.eligible].head(12).iloc[::-1]
        ax.barh([label(s, level) for s in e.segment], e.excess_late, color=[tier_color[t] for t in e.tier])
        ax.errorbar(e.excess_late, np.arange(len(e)), xerr=[(e.excess_late - e.excess_late_lo).clip(lower=0), (e.excess_late_hi - e.excess_late).clip(lower=0)], fmt="none", ecolor=INK2, elinewidth=0.8, capsize=2)
        ax.set_xlabel("Excess late orders over the level's reference rate")
        ax.set_title(f"{LEVELS[level]['title']}: top 12", fontsize=10)
        ax.grid(axis="y", visible=False)
    fig.suptitle("Largest excess late orders per level (bars colour = provisional tier; levels overlap and must not be added)", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "06_top_candidates")
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
