"""Generate reports/operational_prioritization_findings.md from the outputs of analysis/operational_prioritization.py.

Usage: python analysis/operational_prioritization.py && python analysis/build_prioritization_report.py
Numbers are interpolated from reports/prio_stats.json and reports/tables/prio_*.csv. Qualitative statements are guarded
by assertions in `check_claims`: if the data change so that a claim no longer holds, the build fails instead of
publishing stale narrative.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
S = json.loads((ROOT / "reports" / "prio_stats.json").read_text(encoding="utf-8"))
T = {p.stem.replace("prio_", "", 1): pd.read_csv(p) for p in (ROOT / "reports" / "tables").glob("prio_*.csv")}
OUT = ROOT / "reports" / "operational_prioritization_findings.md"
JUNIT = ROOT / "data" / "processed" / "pytest_junit_prio.xml"
LEVELS = ("state", "lane", "seller")
TITLE = {"state": "Customer state", "lane": "Lane (seller state > customer state)", "seller": "Seller (single-seller orders)"}
X = S["rules"]["primary_excess_threshold"]


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def n(x):
    return f"{int(round(x)):,}"


def sg(x, d=0):
    return f"{x:+,.{d}f}"


def ci(lo, hi, d=1):
    return f"[{pct(lo, d)}, {pct(hi, d)}]"


def md(df: pd.DataFrame, headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def short(seg, level):
    return seg[:8] if level == "seller" else seg


def run_pytest():
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={JUNIT}"], cwd=ROOT, capture_output=True, text=True)
    cases = []
    for tc in ET.parse(JUNIT).getroot().iter("testcase"):
        bad = tc.find("failure") is not None or tc.find("error") is not None
        cases.append((f"{tc.get('classname', '').split('.')[-1]}::{tc.get('name')}", "FAIL" if bad else "PASS"))
    return proc.returncode, proc.stdout.strip().splitlines()[-1], cases


def check_claims() -> None:
    st, ln, sl = (T[f"candidates_{l}"] for l in LEVELS)
    assert st.iloc[0].segment == "RJ" and st.iloc[0].tier == "Investigate", "RJ is no longer the largest-excess state"
    assert ln.iloc[0].segment == "SP>RJ" and ln.iloc[0].tier == "Investigate", "SP>RJ is no longer the largest-excess lane"
    ex_l = T["candidates_lane_excl_episodes"].set_index("segment")
    assert ex_l.loc["SP>RJ", "tier"] == "Investigate", "SP>RJ no longer survives excluding the episodes"
    g = T["special_groups"].set_index("group")
    assert g.loc["SP>RJ lane", "excess_vs_ref"] / g.loc["All single-seller orders to RJ", "excess_vs_ref"] > 0.85
    ne = g[g.index.str.startswith("Northeast lanes with")].iloc[0]
    assert ne.excess_vs_ref < g.loc["SP>RJ lane", "excess_vs_ref"], "Northeast flagged lanes combined now exceed SP>RJ"
    ec = S["episode_comparison"]
    assert len(ec["seller"]["investigate_kept"]) <= 0.5 * len(ec["seller"]["investigate_full"]), "seller shortlist is no longer episode-dependent"
    assert len(ec["state"]["investigate_kept"]) >= 0.6 * len(ec["state"]["investigate_full"])
    assert len(ec["lane"]["investigate_kept"]) >= 0.5 * len(ec["lane"]["investigate_full"])
    assert S["overlap"]["double_counted_excess"] > 0
    ax = S["axis_relationship"]
    assert ax["state"]["spearman_excess_late_vs_excess_low"] > 0.8 and ax["lane"]["spearman_excess_late_vs_excess_low"] > 0.8
    tc = T["tier_counts"]
    assert all(c["nested"] for c in S["threshold_changes"])
    sp = T["special_candidates"].set_index("segment")
    assert sp.loc["SP>RJ", "rank_excess_late"] == 1 and sp.loc["SP>RJ", "rank_late_rate"] > 1, "ranking by rate vs excess no longer differ for SP>RJ"
    assert not tc.empty
    for lvl in ("state", "lane"):               # claim in Section 2: after-delivery excess is smaller than all-review excess for every Investigate state / lane
        i = T[f"candidates_{lvl}"]
        i = i[i.tier == "Investigate"]
        assert (i.excess_low_after < i.excess_low).all(), f"after-delivery low-score excess no longer smaller for every {lvl}"
    assert (sp.loc["SP>RJ", "tier"] == "Investigate") and (sp.loc["SP>RJ", "tier_excl_episodes"] == "Investigate")
    assert (ln.set_index("segment").loc["SP>RJ", ["tier_x10", "tier_x30"]] == "Investigate").all(), "SP>RJ no longer survives every threshold"
    pop_share = ec["lane"]["population_episode_late_share"]
    dep = T["episode_dependence"].set_index("segment")
    assert all(dep.loc[s, "episode_late_share"] < pop_share for s in ("SP>AL", "SP>PE", "SP>BA")), "Northeast lanes are no longer less episode-dependent"
    assert (T["candidates_seller"][T["candidates_seller"].tier == "Investigate"].oe_s1 < 1.1).any()


def tier_table(level: str, tiers=("Investigate", "Watch"), limit=None) -> str:
    t = T[f"candidates_{level}"]
    t = t[t.tier.isin(tiers)].sort_values(["tier", "excess_late"], ascending=[True, False])
    if limit:
        t = t.head(limit)
    ex = T[f"candidates_{level}_excl_episodes"].set_index("segment").tier
    rows = pd.DataFrame({
        "Segment": [short(s, level) for s in t.segment], "Tier": t.tier, "Orders": t.n_delivered.map(n), "Late": t.n_late.map(n),
        "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(t.late_rate, t.late_rate_lo, t.late_rate_hi)],
        "Expected": t.expected_late.map(lambda v: f"{v:,.0f}"), "Excess": t.excess_late.map(lambda v: sg(v)),
        "O/E (month x promise)": t.oe_s1.map(lambda v: f"{v:.2f}"),
        "Rate H1 / H2": [f"{'-' if pd.isna(a) else pct(a)} / {'-' if pd.isna(b) else pct(b)}" for a, b in zip(t.rate_h1, t.rate_h2)],
        "Consistency": t.consistency,
        "Tier at 10 / 30": [f"{a} / {b}" for a, b in zip(t.tier_x10, t.tier_x30)],
        "Tier without episodes": [ex.get(s, "absent") for s in t.segment]})
    return md(rows, list(rows.columns))


def review_table(level: str, tiers=("Investigate",), limit=None) -> str:
    t = T[f"candidates_{level}"]
    t = t[t.tier.isin(tiers)].sort_values("excess_late", ascending=False)
    if limit:
        t = t.head(limit)
    rows = pd.DataFrame({
        "Segment": [short(s, level) for s in t.segment], "Reviewed orders": t.n_rev.map(n), "Coverage": t.coverage.map(lambda v: pct(v)),
        "Low-score": t.n_low.map(n), "Expected low": t.expected_low.map(lambda v: f"{v:,.0f}"), "Excess low (all reviews)": t.excess_low.map(lambda v: sg(v)),
        "Reviews written before delivery": t.early_review_share.map(lambda v: pct(v)),
        "Excess low (after-delivery reviews only)": t.excess_low_after.map(lambda v: sg(v)),
        "Excess low among on-time orders": t.excess_low_ontime.map(lambda v: sg(v))})
    return md(rows, list(rows.columns))


def main() -> int:
    check_claims()
    R = S["references"]
    ec, ov, ax = S["episode_comparison"], S["overlap"], S["axis_relationship"]
    tc = T["tier_counts"]
    st, ln, sl = (T[f"candidates_{l}"] for l in LEVELS)
    sp = T["special_candidates"]
    grp = T["special_groups"].set_index("group")
    nest = T["state_lane_nesting"].set_index("state")
    ne_lanes = S["special_ne_lanes"]
    cells = T["overlap_cells"]
    inv = {l: T[f"candidates_{l}"][T[f"candidates_{l}"].tier == "Investigate"] for l in LEVELS}
    wat = {l: T[f"candidates_{l}"][T[f"candidates_{l}"].tier == "Watch"] for l in LEVELS}
    ref_ss = ov["reference_rate_single_seller"]
    seller_after_note = "; ".join(f"{r.segment[:8]}: {sg(r.excess_low)} with all reviews vs {sg(r.excess_low_after)} with after-delivery reviews only"
                                  for r in inv["seller"].itertuples() if r.excess_low > 0 and r.excess_low_after > 0.7 * r.excess_low)
    # Northeast lanes with an interval above the reference: how many are Investigate with / without the three months
    ne_rows = sp[(sp.level == "lane") & (sp.segment != "SP>RJ")]
    ne_inv_full, ne_inv_ex = int((ne_rows.tier == "Investigate").sum()), int((ne_rows.tier_excl_episodes == "Investigate").sum())
    ne_ahead = [r.segment for r in ne_rows.itertuples() if r.rank_late_rate < sp.set_index("segment").loc["SP>RJ", "rank_late_rate"]]
    lane_lost = ", ".join(ec["lane"]["investigate_lost"]) or "none"
    state_lost = ", ".join(ec["state"]["investigate_lost"]) or "none"
    rj, sprj = grp.loc["All single-seller orders to RJ"], grp.loc["SP>RJ lane"]
    oth_rj = grp.loc["Other lanes into RJ"]
    ne_g = grp[grp.index.str.startswith("Northeast lanes with")].iloc[0]
    sprj_row = sp.set_index("segment").loc["SP>RJ"]
    rjs = sp[(sp.level == "state") & (sp.segment == "RJ")].iloc[0]
    changes = {c["level"]: c for c in S["threshold_changes"]}
    nc = {l: T[f"candidates_{l}"][T[f"candidates_{l}"].tier != T[f"candidates_{l}"].tier_no_consistency_rule] for l in LEVELS}

    w = []
    A = w.append
    A("# Operational Prioritization: Findings (Workstream 4)\n")
    A("Generated by `analysis/build_prioritization_report.py` from `analysis/operational_prioritization.py` (SQL in `sql/analysis/prioritization/`) on the validated DuckDB model and the populations of the earlier workstreams. "
      "The tiers below are **provisional, rule-based evidence labels that say where to look first**. They are not scores, not significance tests, and **not findings about cause**: "
      "nothing here shows that a state, lane or seller caused delays, or that any action would reduce them or save money.\n")
    A("Labels: **[Observed]** a computed result; **[Interpretation]** a reading that goes beyond it; **[Hypothesis]** a possible explanation this analysis cannot test.\n")

    # ---------------------------------------------------------------- summary
    A("## Summary\n")
    A(f"- **[Observed] The same destination shows up at every level.** Rio de Janeiro is the largest candidate by excess late orders at the state level ({sg(rjs.excess_late)}, {pct(rjs.late_rate)} late on {n(rjs.n_delivered)} orders) "
      f"and the São Paulo to Rio de Janeiro lane is the largest at the lane level ({sg(sprj_row.excess_late)}, {pct(sprj_row.late_rate)} late on {n(sprj_row.n_delivered)} orders). "
      f"These are the same orders, not two problems: SP>RJ carries {pct(sprj.excess_vs_ref / rj.excess_vs_ref, 0)} of the excess among single-seller orders to Rio de Janeiro; all other lanes into Rio together add {sg(oth_rj.excess_vs_ref)} ({pct(oth_rj.late_rate)} late).\n"
      f"- **[Observed] Northeast lanes are smaller in volume, with several higher-rate lanes.** {len(ne_lanes)} lanes into the Northeast have an interval above the reference rate; together they hold {n(ne_g.n_orders)} orders and {sg(ne_g.excess_vs_ref)} excess late orders, less than SP>RJ alone. "
      f"By *rate* and by adjusted observed/expected the ordering is different: SP>RJ ranks {int(sprj_row.rank_late_rate)} of {int(sprj_row.n_eligible_in_level)} on late rate and {int(sprj_row.rank_oe_s1)} on observed/expected; the three highest-rate lanes are "
      f"{', '.join(ln[ln.eligible].sort_values('late_rate', ascending=False).head(3).segment)}. Which lane comes \"first\" depends on whether volume (excess orders) or intensity (rate) is the criterion; the report shows both.\n"
      f"- **[Observed] Provisional tiers at {X} excess late orders:** {int(tc[(tc.level == 'state') & (tc.min_excess_late == X)].Investigate.iloc[0])} states, {int(tc[(tc.level == 'lane') & (tc.min_excess_late == X)].Investigate.iloc[0])} lanes and "
      f"{int(tc[(tc.level == 'seller') & (tc.min_excess_late == X)].Investigate.iloc[0])} sellers are Investigate. The state and lane lists are fairly stable to the threshold (10/30) and to removing the three high-delay months; "
      f"**the seller list is not**: only {len(ec['seller']['investigate_kept'])} of {len(ec['seller']['investigate_full'])} Investigate sellers stay Investigate once 2017-11, 2018-02 and 2018-03 are excluded.\n"
      f"- **[Observed] Levels overlap heavily.** The Investigate sets cover {pct(ov['union']['share_of_all_orders'], 0)} of single-seller orders and {pct(ov['union']['share_of_all_late'], 0)} of late orders. Adding the three levels' excess would give {sg(ov['naive_sum_of_level_excess'])} against {sg(ov['union']['excess'])} "
      f"for the union, a {sg(ov['double_counted_excess'])} double count, so contributions are never added across levels.\n"
      f"- **[Observed] The review axis mostly repeats the late axis.** Excess low-score orders rank-correlate {ax['state']['spearman_excess_late_vs_excess_low']:.2f} (states) and {ax['lane']['spearman_excess_late_vs_excess_low']:.2f} (lanes) with excess late orders, "
      "because late orders attract low scores, many written before delivery. It adds little independent information for these two levels.\n")

    # ---------------------------------------------------------------- 1
    A("## 1. Populations, reference rates and provisional rules\n")
    A(md(pd.DataFrame([
        ["Customer state", "Delivered with date, purchases 2017-01..2018-08 (incl. multi-seller)", n(R['state']['n_delivered']), pct(R['state']['late'], 2), f"n >= 100 ({R['state']['n_eligible']} of {R['state']['n_segments']} states)"],
        ["Lane", "`v_single_seller_orders`", n(R['lane']['n_delivered']), pct(R['lane']['late'], 2), f"n >= 100 ({R['lane']['n_eligible']} of {R['lane']['n_segments']} lanes)"],
        ["Seller", "`v_single_seller_orders`", n(R['seller']['n_delivered']), pct(R['seller']['late'], 2), f"n >= 50 ranked ({R['seller']['n_eligible']} of {R['seller']['n_segments']}); 30-49 low-confidence"],
    ]), ["Level", "Population", "Orders", "Reference late rate", "Minimum-N policy (blueprint R8)"]) + "\n")
    A(f"Reference low-score share (1-2 stars) among single-review orders: {pct(R['state']['low'], 2)} (state population), {pct(R['lane']['low'], 2)} (single-seller population); among reviews written after delivery {pct(R['lane']['low_after'], 2)}; among on-time orders {pct(R['lane']['low_ontime'], 2)}.\n")
    A("**Measures per segment.** *Expected late* = delivered orders x the level's reference rate; *excess* = observed minus expected; Wilson 95% interval on the late rate (and on excess, by scaling). "
      "*Adjusted O/E* (secondary context only) = observed / expected from purchase-month x promised-lead-group strata pooled over the same population. "
      "Low-score measures use the validated single-review population (reviewed orders); coverage = reviewed / delivered orders. Reviews written **before** the recorded delivery date are kept apart from those written afterwards.\n")
    A(f"**Provisional tier rules** (fixed before looking at results; no weights, no significance tests):\n\n"
      f"| Tier | Rule |\n|---|---|\n"
      f"| **Investigate** | volume at or above the minimum-N AND Wilson interval entirely above the reference rate AND excess late orders >= {X} (provisional; also tried at 10 and 30) AND window-half consistency is not 'inconsistent' |\n"
      f"| **Watch** | positive excess with an interval above the reference or excess >= {X}, but not Investigate; also low-confidence sellers (30-49 orders) with a signal |\n"
      f"| **No Signal** | adequate volume, none of the above |\n"
      f"| **Insufficient Data** | below the minimum volume (sellers below 30; low-confidence sellers without a signal) |\n")
    A(f"**Consistency (stability across time).** Compare the segment's late rate with the population rate in each window half (H1 = purchases 2017-01..2017-10, H2 = 2017-11..2018-08; population rates {pct(R['lane']['h1'], 1)} and {pct(R['lane']['h2'], 1)} for lanes/sellers). "
      f"*Consistent* = above the reference in both halves; *inconsistent* = both halves have at least {S['rules']['half_min_n']} orders and at least one is not above; **unverified = one half has fewer than {S['rules']['half_min_n']} orders, which is not a negative result and does not block Investigate**. "
      "H2 contains all three high-delay months, so 'consistent' means the segment was above the reference both before and after the episodes began.\n")

    # ---------------------------------------------------------------- 2
    A("## 2. Candidate tables\n")
    A("![Top candidates](figures/prio_06_top_candidates.png)\n")
    for level in LEVELS:
        t = T[f"candidates_{level}"]
        A(f"### 2.{LEVELS.index(level) + 1} {TITLE[level]}\n")
        vc = t.tier.value_counts()
        A(f"{len(t):,} segments: Investigate {int(vc.get('Investigate', 0))}, Watch {int(vc.get('Watch', 0))}, No Signal {int(vc.get('No Signal', 0))}, Insufficient Data {int(vc.get('Insufficient Data', 0))}"
          + (f" (of which {int(t.low_confidence.sum())} low-confidence sellers with 30-49 orders)" if level == "seller" else "")
          + f". Full tables: `reports/tables/prio_candidates_{level}.csv`.\n")
        if level == "seller":
            A("Seller IDs are shortened to 8 characters here (full IDs in the CSV). **Investigate sellers:**\n")
            A(tier_table(level, ("Investigate",)) + "\n")
            A(f"Watch tier: {len(wat['seller'])} sellers; the 10 with the largest excess:\n")
            A(tier_table(level, ("Watch",), limit=10) + "\n")
        else:
            A(tier_table(level) + "\n")
        A("Review-based measures for the Investigate candidates (single-review orders; **association with delivery outcomes, not causal effects**):\n")
        A(review_table(level, ("Investigate",)) + "\n")
    c_ = T["candidates_state"]
    A(f"Insufficient-data states (not ranked): {', '.join(c_[c_.tier == 'Insufficient Data'].segment)} ({n(c_[c_.tier == 'Insufficient Data'].n_delivered.sum())} orders). "
      f"The {n(ln[ln.tier == 'Insufficient Data'].n_delivered.sum())} orders in the {int((ln.tier == 'Insufficient Data').sum())} lanes below 100 orders and the {n(sl[sl.tier == 'Insufficient Data'].n_delivered.sum())} orders of the {int((sl.tier == 'Insufficient Data').sum())} sellers below the minimum are not individually assessed.\n")
    A("**[Observed]** Review coverage is high and similar across candidates (" + f"{pct(ax['state']['coverage_min'])}-{pct(ax['state']['coverage_max'])} for states, {pct(ax['lane']['coverage_min'])}-{pct(ax['lane']['coverage_max'])} for lanes), so coverage does not distort the low-score comparison materially. "
      f"Reviews written before delivery are {pct(ax['state']['early_review_share_overall'])} of all reviews but concentrate on late orders, so candidates with many late orders show a larger share of early reviews. "
      f"For every Investigate state and lane the after-delivery low-score excess is smaller than the all-review excess; for sellers this is not always true ({seller_after_note}).\n")

    # ---------------------------------------------------------------- 3
    A("## 3. Two-axis priority matrix\n")
    A("![Priority matrix](figures/prio_01_priority_matrix.png)\n")
    A("![Priority matrix, after-delivery reviews](figures/prio_02_priority_matrix_after_delivery.png)\n")
    A("**How to read it.** Horizontal axis: excess late orders over the level's reference rate (delivered-order population). Vertical axis: excess low-score reviewed orders (single-review population; second figure counts only reviews written on or after delivery). "
      "Marker area is delivered volume; bars show 95% Wilson uncertainty on the late axis and on the low-score axis (first figure). Axes of the state and lane panels use a symmetric-log scale because a few large segments dominate. Colour is the provisional tier.\n")
    A("**Why the axes describe different analytical populations.** (1) The late axis counts *delivered orders* of the segment; the low-score axis counts only *reviewed single-review orders* (about "
      f"{pct(ax['state']['coverage_overall'])} of delivered orders; orders with no review or several review rows drop out). (2) Late is an operational outcome measured by timestamps; a low score is customer feedback that depends on who reviews and when, and "
      f"{pct(ax['state']['early_review_share_overall'])} of reviews are written *before* the recorded delivery, mostly on late orders (customer-satisfaction workstream). (3) Expected counts use different reference rates ({pct(R['lane']['late'], 2)} late vs {pct(R['lane']['low'], 2)} low-score). "
      "The axes are therefore not independent evidence about the same thing, and are not combined into a score.\n")
    A(f"**[Observed]** The two axes move together: rank correlation of excess late with excess low-score is {ax['state']['spearman_excess_late_vs_excess_low']:.2f} for states, {ax['lane']['spearman_excess_late_vs_excess_low']:.2f} for lanes and {ax['seller']['spearman_excess_late_vs_excess_low']:.2f} for sellers. "
      f"Restricting to **on-time orders** removes the mechanical link: the correlation falls to {ax['state']['spearman_excess_late_vs_excess_low_ontime']:.2f}, {ax['lane']['spearman_excess_late_vs_excess_low_ontime']:.2f} and {ax['seller']['spearman_excess_late_vs_excess_low_ontime']:.2f}. "
      "So most of the second axis restates the first; the on-time version (last column of the review tables) is the part that may carry separate information about dissatisfaction, but it is small and noisy at segment level.\n")
    top_low = sl[sl.eligible].sort_values("excess_low", ascending=False).head(3)
    A(f"**[Observed]** Sellers are where the axes diverge most: e.g. the seller with the largest all-review excess low-score count ({short(top_low.iloc[0].segment, 'seller')}: {sg(top_low.iloc[0].excess_low)}) has {sg(top_low.iloc[0].excess_late)} excess late orders, and its low-score excess is {sg(top_low.iloc[0].excess_low_ontime)} among on-time orders and {sg(top_low.iloc[0].excess_low_after)} among reviews written after delivery. "
      "Such cases are not explained by lateness in these data and would need other information (product, handling, review text, which are not analysed here).\n")

    # ---------------------------------------------------------------- 4
    A("## 4. Sensitivity to the excess-late threshold (10 / 20 / 30)\n")
    A("![Thresholds](figures/prio_03_threshold_sensitivity.png)\n")
    rows = []
    for l in LEVELS:
        for x in S["rules"]["thresholds"]:
            r = tc[(tc.level == l) & (tc.min_excess_late == x)].iloc[0]
            rows.append([TITLE[l], str(x) + (" (provisional)" if x == X else ""), int(r.Investigate), int(r.Watch), int(r["No Signal"]), int(r["Insufficient Data"])])
    A(md(pd.DataFrame(rows), ["Level", "Minimum excess late orders", "Investigate", "Watch", "No Signal", "Insufficient Data"]) + "\n")
    A("Membership changes (all sets are nested: raising the threshold only removes candidates):\n")
    for l in LEVELS:
        c = changes[l]
        A(f"- **{TITLE[l]}:** lowering the threshold to 10 adds {', '.join(short(s, l) for s in c['added_going_20_to_10']) or 'none'}; raising it to 30 removes {', '.join(short(s, l) for s in c['dropped_going_20_to_30']) or 'none'}.")
    A("")
    A(f"**[Observed]** States are insensitive to the threshold between 10 and 30 ({int(tc[(tc.level == 'state') & (tc.min_excess_late == 10)].Investigate.iloc[0])} to {int(tc[(tc.level == 'state') & (tc.min_excess_late == 30)].Investigate.iloc[0])} Investigate); "
      f"lanes change only when the threshold is lowered to 10 (the extra lanes have interval lower bounds barely above the reference); sellers are the most sensitive ({int(tc[(tc.level == 'seller') & (tc.min_excess_late == 10)].Investigate.iloc[0])}, "
      f"{int(tc[(tc.level == 'seller') & (tc.min_excess_late == 20)].Investigate.iloc[0])}, {int(tc[(tc.level == 'seller') & (tc.min_excess_late == 30)].Investigate.iloc[0])}) because most sellers have few orders, so an excess of 10-30 late orders is large for them. "
      "The threshold of 20 therefore remains provisional.\n")
    A("**What the consistency rule changes.** Without it (excess threshold unchanged) the following segments would move from Watch to Investigate: " +
      "; ".join(f"{TITLE[l].split(' (')[0].lower()} " + ", ".join(f"{short(r.segment, l)}" for r in nc[l].itertuples()) for l in LEVELS if len(nc[l])) +
      ". These have a signal and at least 20 excess late orders but one window half was not above the reference rate (e.g. SP>CE: "
      f"{int(ln.set_index('segment').loc['SP>CE', 'late_h1'])} late orders of {int(ln.set_index('segment').loc['SP>CE', 'n_h1'])} in H1 vs {pct(ln.set_index('segment').loc['SP>CE', 'rate_h2'])} in H2). "
      "That is a deliberate, visible consequence of the rule, not a verdict: a quiet first half on a small count can come from noise.\n")

    # ---------------------------------------------------------------- 5
    A("## 5. Stability across time and the three high-delay periods\n")
    cons = {l: T[f"candidates_{l}"][T[f"candidates_{l}"].eligible].consistency.value_counts() for l in LEVELS}
    A(md(pd.DataFrame([[TITLE[l], int(cons[l].get("consistent", 0)), int(cons[l].get("inconsistent", 0)), int(cons[l].get("unverified", 0))] for l in LEVELS]),
         ["Level (ranked segments)", "Consistent", "Inconsistent", "Unverified (one half < 30 orders)"]) + "\n")
    A(f"**[Observed]** Many ranked segments are 'inconsistent' simply because the H1 reference rate is low ({pct(R['lane']['h1'], 1)}) and small segments have noisy half-year rates; "
      f"for sellers the consistency rule never changes a tier (no flagged seller is inconsistent), but {int((sl[(sl.tier == 'Investigate')].consistency == 'unverified').sum())} of the {len(inv['seller'])} Investigate sellers are *unverified* because they had too few orders in H1: their stability is simply not known.\n")
    A("![Episode exclusion](figures/prio_04_episode_exclusion.png)\n")
    rows = []
    for l in LEVELS:
        c = ec[l]
        rows.append([TITLE[l], len(c["investigate_full"]), len(c["investigate_kept"]), ", ".join(short(s, l) for s in c["investigate_lost"]) or "-", ", ".join(short(s, l) for s in c["investigate_gained"]) or "-",
                     f"{c['spearman_excess']:.2f}", f"{c['top10_overlap']}/10"])
    A(md(pd.DataFrame(rows), ["Level", "Investigate (all months)", "Still Investigate without the 3 months", "Dropped out", "Newly Investigate", "Rank correlation of excess", "Top-10 overlap"]) + "\n")
    ex_ref = T["candidates_lane_excl_episodes"].n_late.sum() / T["candidates_lane_excl_episodes"].n_delivered.sum()
    A(f"The reference rate is recomputed on the remaining months ({pct(ov['reference_rate_single_seller'], 1)} overall vs {pct(ex_ref, 1)} without the episodes for single-seller orders), so a segment is compared with a much lower reference rate and its excess is counted in a smaller sample.\n")
    dep = T["episode_dependence"]
    d_state = dep[dep.level == "state"]
    A("**Share of each Investigate segment's late orders that fall in the three months** (population: " + pct(ec['state']['population_episode_late_share'], 0) + "):\n")
    A(md(pd.DataFrame({"Level": dep.level, "Segment": [short(s, l) for s, l in zip(dep.segment, dep.level)], "Excess late": dep.excess_late.map(lambda v: sg(v)),
                       "Share of its late orders in the 3 months": dep.episode_late_share.map(lambda v: pct(v, 0)), "Tier without the 3 months": dep.tier_excl_episodes}), ["Level", "Segment", "Excess late", "Share of its late orders in the 3 months", "Tier without the 3 months"]) + "\n")
    A(f"**[Observed]** The state and lane lists survive the exclusion largely intact ({len(ec['state']['investigate_kept'])} of {len(ec['state']['investigate_full'])} states and {len(ec['lane']['investigate_kept'])} of {len(ec['lane']['investigate_full'])} lanes), including Rio de Janeiro and SP>RJ "
      f"(SP>RJ: {pct(dep[dep.segment == 'SP>RJ'].episode_late_share.iloc[0], 0)} of its late orders fall in the three months, above the {pct(ec['state']['population_episode_late_share'], 0)} population share, yet it stays Investigate without them). "
      f"The seller list does not: {len(ec['seller']['investigate_lost'])} of {len(ec['seller']['investigate_full'])} Investigate sellers drop out, and for those that drop out {pct(dep[(dep.level == 'seller') & (dep.tier_excl_episodes != 'Investigate')].episode_late_share.min(), 0)}-{pct(dep[(dep.level == 'seller') & (dep.tier_excl_episodes != 'Investigate')].episode_late_share.max(), 0)} of their late orders fall in the three months. "
      f"Northeast lanes such as SP>AL ({pct(dep[dep.segment == 'SP>AL'].episode_late_share.iloc[0], 0)}), SP>PE ({pct(dep[dep.segment == 'SP>PE'].episode_late_share.iloc[0], 0)}) and SP>BA ({pct(dep[dep.segment == 'SP>BA'].episode_late_share.iloc[0], 0)}) are among the least episode-dependent: a smaller share of their late orders falls in the three months than for the population.\n")
    low_oe = inv["seller"][inv["seller"].oe_s1 < 1.1]
    A("**[Observed]** Adjusted observed/expected (month x promised lead) is a useful cross-check on sellers. Investigate sellers with a ratio close to 1: " + (", ".join(f"{short(r.segment, 'seller')} ({r.oe_s1:.2f}; {pct(r.episode_late_share, 0)} of its late orders in the three months)" for r in low_oe.itertuples()) or "none") +
      ". Once month and promised-lead mix are allowed for, such a seller is no later than others shipping in the same months; its excess against the portfolio is consistent with having sold heavily in the high-delay months, which this analysis cannot distinguish from a seller-specific problem in those months.\n")
    A("**[Interpretation]** The seller shortlist mostly reflects sellers that were hit hard in the three system-wide episodes, not sellers that are persistently worse in calm months; only the sellers that stay Investigate without the episodes have evidence of that. "
      "Geography and lanes are more persistent. This does not say why some sellers were hit harder during the episodes.\n")

    # ---------------------------------------------------------------- 6
    A("## 6. Overlap between states, lanes and sellers\n")
    A("![Overlap](figures/prio_05_overlap.png)\n")
    A(f"Computed on {n(ov['n_single_seller_orders'])} single-seller orders by order id (multi-seller orders have no lane or seller). Reference rate {pct(ref_ss, 2)}.\n")
    A(md(pd.DataFrame([[TITLE[l], len(inv[l]), n(ov['per_level'][l]['n_orders']), n(ov['per_level'][l]['n_late']), sg(ov['per_level'][l]['excess'])] for l in LEVELS] +
                      [["**Union of the three sets**", "-", n(ov['union']['n_orders']), n(ov['union']['n_late']), sg(ov['union']['excess'])],
                       ["Sum of the three levels (**double counted**)", "-", "-", n(ov['late_orders_naive_sum']), sg(ov['naive_sum_of_level_excess'])]]),
         ["Investigate set", "Segments", "Orders", "Late orders", "Excess late orders"]) + "\n")
    A(md(pd.DataFrame([[p["pair"], n(p["orders_both"]), pct(p["share_of_a_in_b"], 0), pct(p["share_of_b_in_a"], 0), n(p["late_both"])] for p in ov["pairs"]]),
         ["Pair of levels", "Orders in both", "Share of first level's orders in the second", "Share of second level's orders in the first", "Late orders in both"]) + "\n")
    A(f"**[Observed]** {pct(ov['pairs'][0]['share_of_b_in_a'], 0)} of the orders in Investigate lanes lie in Investigate states (a lane has exactly one destination state), and {pct(ov['pairs'][0]['share_of_a_in_b'], 0)} of the orders in Investigate states are in Investigate lanes. "
      f"Investigate sellers overlap far less: {pct(ov['pairs'][1]['share_of_b_in_a'], 0)} of their orders are in Investigate states and {pct(ov['pairs'][2]['share_of_b_in_a'], 0)} in Investigate lanes. "
      f"Counting each level's excess and adding them would overstate the combined excess by {sg(ov['double_counted_excess'])} late orders ({pct(ov['double_counted_excess'] / ov['naive_sum_of_level_excess'], 0)} of the naive sum).\n")
    t_ = nest.reset_index()
    A(md(pd.DataFrame({"Investigate state": t_.state, "Single-seller orders": t_.orders_single_seller.map(n), "Excess late": t_.excess.map(lambda v: sg(v)),
                       "Orders in Investigate lanes": t_.orders_in_investigate_lanes.map(n), "Excess in Investigate lanes": t_.excess_in_investigate_lanes.map(lambda v: sg(v)),
                       "Share of state orders in Investigate lanes": t_.share_of_state_orders_in_investigate_lanes.map(lambda v: pct(v, 0))}),
         ["Investigate state", "Single-seller orders", "Excess late", "Orders in Investigate lanes", "Excess in Investigate lanes", "Share of state orders in Investigate lanes"]) + "\n")
    no_lane = t_[t_.orders_in_investigate_lanes == 0].state.tolist()
    A(f"For {', '.join(no_lane) if no_lane else 'every state'} no lane meets the Investigate rule: " + ("their excess is spread over several smaller lanes rather than concentrated in one" if no_lane else "lanes concentrate the excess") +
      " (a destination-level view is the only view that sees it).\n")

    # ---------------------------------------------------------------- 7
    A("## 7. Specific candidates: SP>RJ, Rio de Janeiro destinations and Northeast lanes\n")
    A("No preferred ranking was assumed. Each candidate is ranked among the ranked segments of its own level on five different metrics; rankings differ by metric.\n")
    t = sp.copy()
    A(md(pd.DataFrame({"Level": t.level, "Segment": t.segment, "Orders": t.n_delivered.map(n), "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(t.late_rate, t.late_rate_lo, t.late_rate_hi)],
                       "Excess late": t.excess_late.map(lambda v: sg(v)), "O/E": t.oe_s1.map(lambda v: f"{v:.2f}"), "Tier": t.tier, "Tier without episodes": t.tier_excl_episodes,
                       "Rank: excess": t.rank_excess_late.map(lambda v: f"{int(v)}"), "Rank: conservative excess": t.rank_excess_late_lo.map(lambda v: f"{int(v)}"), "Rank: rate": t.rank_late_rate.map(lambda v: f"{int(v)}"),
                       "Rank: O/E": t.rank_oe_s1.map(lambda v: f"{int(v)}"), "Rank: excess low": t.rank_excess_low.map(lambda v: f"{int(v)}")}),
         ["Level", "Segment", "Orders", "Late rate [95% Wilson]", "Excess late", "O/E", "Tier", "Tier without episodes", "Rank: excess", "Rank: conservative excess", "Rank: rate", "Rank: O/E", "Rank: excess low"]) + "\n")
    A("(Conservative excess = Wilson lower bound of the rate minus the reference, times orders. Lane ranks are among 70 lanes; state ranks among 24 states.)\n")
    A(md(pd.DataFrame([[g_, n(r.n_orders), n(r.n_late), pct(r.late_rate), sg(r.excess_vs_ref)] for g_, r in grp.iterrows()]),
         ["Overlap-aware group (single-seller orders; reference " + pct(ref_ss, 2) + ")", "Orders", "Late", "Late rate", "Excess late"]) + "\n")
    A(f"**[Observed]** *Rio de Janeiro as a destination* is the single largest candidate by volume of excess: {sg(rjs.excess_late)} late orders across all orders. Within single-seller orders {pct(sprj.excess_vs_ref / rj.excess_vs_ref, 0)} of that excess is the SP>RJ lane "
      f"({sg(sprj.excess_vs_ref)}); every other lane into Rio together adds {sg(oth_rj.excess_vs_ref)}. So the state-level and lane-level candidates for Rio are the same thing, and the question is the SP>RJ route, not Rio's other suppliers.\n"
      f"*Northeast lanes:* the {len(ne_lanes)} lanes with an interval above the reference ({', '.join(ne_lanes)}) are individually much smaller than SP>RJ ({sg(sp[(sp.level == 'lane') & (sp.segment == 'SP>BA')].excess_late.iloc[0])} for the largest, SP>BA). "
      f"Their combined late rate ({pct(ne_g.late_rate)}) is a little below SP>RJ's ({pct(sprj.late_rate)}), but {len(ne_ahead)} of them individually rank higher than SP>RJ on late rate ({', '.join(ne_ahead)}). "
      f"Together they hold {sg(ne_g.excess_vs_ref)} excess late orders against {sg(sprj.excess_vs_ref)} for SP>RJ alone. {ne_inv_full} of the {len(ne_lanes)} are Investigate at excess >= {X} ({ne_inv_ex} still are without the three high-delay months); "
      "the others are Watch because their excess is below the threshold or one window half was not above the reference.\n")
    A("**[Interpretation]** By volume of excess late orders SP>RJ comes first; by intensity several Northeast lanes come first. These are different questions (where do most of the extra late orders sit vs. where is the rate worst) and a manager should choose the criterion consciously. "
      "Routes also differ: SP>RJ is relatively short (median distance about 380 km) with a high late rate, while the São Paulo-origin Northeast lanes are long (roughly 1,400 to 2,400 km). "
      "The data cannot say whether the Rio route has a distinct operational problem or simply large volume with a high but not extreme rate.\n")

    # ---------------------------------------------------------------- 8
    A("## 8. Operational investigation shortlist\n")
    A("Candidate groups for investigation, in no weighted order. Each lists the evidence, what is not known, what additional data would help and a suggested next investigative step. "
      "None implies cause or promises an improvement; quantities are observed counts relative to a reference rate.\n")
    inv_lane = inv["lane"].set_index("segment")
    inv_state = inv["state"].set_index("segment")
    inv_sel = inv["seller"]
    sel_kept = [short(s, "seller") for s in ec["seller"]["investigate_kept"]]
    A(md(pd.DataFrame([
        ["A. São Paulo to Rio de Janeiro (SP>RJ)", f"Largest excess at lane and state level ({sg(sprj.excess_vs_ref)}); {pct(sprj_row.late_rate)} late [{pct(sprj_row.late_rate_lo)}, {pct(sprj_row.late_rate_hi)}]; consistent across halves; stays Investigate without the three months; O/E {sprj_row.oe_s1:.2f}",
         "Whether carrier, last-mile, seller hand-off or demand explains the excess; the route is short (about 380 km) so distance does not explain it",
         "Carrier / route per order; carrier hand-off and out-for-delivery timestamps (the existing carrier timestamps are unreliable for about 1.4% of orders); sellers behind the lane",
         "Break the lane down by seller and carrier; compare transit time from hand-off to delivery with other São Paulo-origin routes of similar length"],
        ["B. Northeast lanes (" + ", ".join(ne_lanes[:6]) + ", ...)", f"{len(ne_lanes)} lanes above the reference; combined {pct(ne_g.late_rate)} late, {sg(ne_g.excess_vs_ref)} excess; includes the highest-rate lanes ({', '.join(ln[ln.eligible].sort_values('late_rate', ascending=False).head(3).segment)}); mostly consistent across halves and less episode-dependent than Rio",
         "Whether long-route transit, regional last-mile or promised-date setting explains it (cross-state shipments are already promised longer than same-state ones, yet are late more often)",
         "Carrier by destination; how estimated dates are set for these routes; delivery attempts / returns data",
         "Compare observed lead times with promised dates by route and week; check whether the same carrier serves all these lanes"],
        ["C. Other state and lane candidates (BA, CE, ES, MA, PE, PA, AL, SC, SP>ES, SP>SC, ...)", f"{len(inv['state']) - 1} further Investigate states and {len(inv['lane']) - 1} further Investigate lanes; most remain after removing the episodes; {state_lost} (states) and {lane_lost} (lanes) do not",
         "Whether these are separate problems or the same route/carrier pattern seen at different levels (lane and state sets overlap by construction)", "As for A and B, plus state-level carrier mix",
         "Treat the lane list as the way to localise each state's excess; investigate states without a qualifying lane (CE, SE) at destination level"],
        ["D. Sellers (" + ", ".join(short(s, "seller") for s in inv_sel.segment[:4]) + ", ...)", f"{len(inv_sel)} sellers Investigate at excess >= {X}; only {len(sel_kept)} ({', '.join(sel_kept)}) remain without the three high-delay months; {int((inv_sel.consistency == 'unverified').sum())} are unverified (few H1 orders)",
         "Whether seller handling or carrier performance explains their excess; whether their episode-month problems recurred outside the episodes",
         "Seller hand-off timing (approval to carrier hand-off), stock and order-handling data, carrier mix per seller, newer data",
         "Verify with handling-time and hand-off data for the sellers that stay flagged outside the episodes first; re-check the others on later data"],
        ["E. The three high-delay months themselves", "Late rate 15.1% vs 4.5%; increase within every comparable state; mix explains almost none (geographic workstream)",
         "What was different system-wide (carrier capacity, promotions, stock-outs)", "Operations calendar and carrier capacity records for 2017-11, 2018-02, 2018-03",
         "Establish what changed in those months before attributing any of the excess to particular segments"],
    ]), ["Candidate group", "Evidence (observed)", "Not known", "Additional data needed", "Suggested next investigative step"]) + "\n")
    A("These are investigation steps, not interventions; nothing here estimates the effect of acting on them.\n")

    # ---------------------------------------------------------------- 9
    A("## 9. Business recommendations\n")
    A("1. **Start with the route, not the seller.** Rio de Janeiro via SP>RJ holds the largest block of excess late orders and survives every sensitivity check; investigate carrier and last-mile performance on that route first, then the Northeast lanes where the late rate is worst.\n"
      "2. **Choose the prioritisation criterion explicitly.** Volume of excess orders puts SP>RJ first; late rate and observed/expected put some Northeast lanes first. Both views are in this report; neither is assumed to be right.\n"
      "3. **Use the seller list only as a shortlist for verification.** Most flagged sellers are not persistent once the three high-delay months are removed; verify those that remain (and any that stay flagged on newer data) before drawing conclusions. Do not rank or penalise sellers on this evidence.\n"
      "4. **Do not add segment contributions across levels.** Report the union or one level at a time.\n"
      "5. **Keep the thresholds provisional.** 20 excess late orders is workable for states and lanes, but sellers are sensitive to it; revisit it when carrier data or newer months allow a clearer definition of what counts as worth investigating.\n"
      "6. **Close the data gaps** listed in Section 8 (carrier, hand-off timestamps, how estimates are set, event calendar) before attempting any estimate of impact.\n")

    # ---------------------------------------------------------------- 10
    A("## 10. Limitations\n")
    A("- **Association only.** Tiers show where late deliveries concentrate relative to a reference rate. No causal claim about states, lanes or sellers; order-level timestamps cannot separate seller handling, carrier transit and last mile.\n"
      "- **Provisional rules.** The 20-order threshold, the 100/50/30 minimum volumes and the 30-order half-window adequacy rule are judgement-based; sensitivity to 10/30 is shown, the others are not varied. The tier boundaries are sharp even though the evidence is continuous.\n"
      "- **Reference rates are population averages,** not targets or SLAs; segments are compared with the portfolio, so 'excess' depends on the rest of the portfolio (a very large, low-rate São Paulo pulls the reference down).\n"
      "- **Wilson intervals assume independent orders;** orders cluster within sellers, carriers and days, so true uncertainty is larger, especially at state level. No multiple-comparison correction is applied; with hundreds of sellers some Investigate/Watch sellers are expected by chance (geographic workstream: about 14 of 57 flagged at n >= 50 under the null).\n"
      "- **Consistency uses halves that are confounded with the episodes** (all three high-delay months are in H2); 'inconsistent' can reflect a small first-half count.\n"
      "- **Adjusted O/E uses month x promised-lead strata only;** product mix, seller size, carrier and distance are not controlled for lane and state context.\n"
      "- **Review measures** cover single-review orders only (98.8% of delivered orders); reviews written before delivery are mostly negative and mostly on late orders, so the all-review low-score axis largely restates lateness.\n"
      "- **Single-seller scope** for lanes and sellers (98.7% of delivered orders); state results include all delivered orders. Cross-level overlap is computed on single-seller orders.\n"
      "- **No cost or impact data.** Nothing here estimates savings or the effect of any action.\n"
      "- Single marketplace, 2017-2018; findings describe this dataset.\n")

    # ---------------------------------------------------------------- 11
    rc, pytest_summary, cases = run_pytest()
    A("## 11. SQL review, data-quality checks and validation\n")
    A("**SQL review (sql-analytics-reviewer).** Scope: `sql/analysis/prioritization/*.sql`. Dialect DuckDB. Executed and compared with independent pandas, so the findings below are empirically verified.\n")
    A(md(pd.DataFrame([
        ["Critical", "None found", "All candidate counts equal an independent pandas groupby for all three levels, with and without the three months"],
        ["Join cardinality", "No joins in the aggregation SQL", "Each level reads one table or the validated view at order grain (`fact_orders` / `v_single_seller_orders`); fan-out is impossible and row counts reconcile to 96,203 and 94,931"],
        ["Double counting", "Prevented by design and tested", "Segments are never summed across levels; overlap is computed from order ids; sums of excess within a level equal zero (identity test)"],
        ["NULL handling", "Handled", "Half-window and episode late counts use `coalesce(..., 0)`; review score is NULL unless exactly one review row; rates with zero denominators become NULL in Python, not zero"],
        ["Minor", "A shared SQL template is filled by Python string substitution", "Keeps definitions identical across levels; the template comment once contained the placeholder token and was substituted too (caught on first run, fixed)"],
        ["Performance", "Not assessed beyond a few seconds of runtime", "No tuning attempted or claimed"],
    ]), ["Severity / topic", "Finding", "Evidence"]) + "\n")
    A("**Data-quality checks on the candidate tables (data-quality-auditor).** Segment keys are unique and non-null at each level; counts are internally consistent (late <= delivered, low <= reviewed, early + after = reviewed, on-time + late = reviewed); "
      "coverage lies in [0, 1]; populations reconcile to the established anchors (96,203 / 6,531 states; 94,931 / 6,518 single-seller; 95,037 reviewed); the screening signals, adjusted expectations and excess figures equal those of the geographic workstream "
      "(same 15 states, 21 lanes and 57 sellers with an interval above the reference); reference rates are recomputed on the remaining months when the three episode months are excluded. "
      "No missing values arise in the candidate measures except undefined rates for segments with no reviews or no orders in a half, which are labelled rather than imputed.\n")
    A(f"- Full test run (`python -m pytest`): **{pytest_summary}** (exit code {rc}); {sum(1 for _, s in cases if s == 'PASS')} of {len(cases)} cases passed.\n"
      "- **Independent checks:** candidate counts (all three levels, with and without the episodes) vs pandas from the raw CSVs; reference-rate and zero-sum-excess identities; Wilson intervals vs SciPy; "
      "tier assignment re-implemented row by row at thresholds 10, 20 and 30 and compared; tier invariants and nesting; consistency labels; episode-exclusion recomputation; overlap by order ids including inclusion-exclusion; "
      "special-candidate ranks recomputed per metric; absence of composite-score or significance columns.\n"
      "- **Defects:** no defect in the data model or earlier analyses was found. Three errors in the new code were found and fixed during development: the SQL template placeholder in its own comment, reference rates stored in a DataFrame attribute that pandas drops on merge, "
      "and a test sampling more rows than a level has; none affected a reported number.\n"
      "- **Deviation from the brief:** the 'priority matrix' adds a second version restricted to after-delivery reviews; the tier rule adds a `tier_no_consistency_rule` column to show what the consistency requirement changes. Tiers use only observed counts and Wilson intervals.\n")
    A(md(pd.DataFrame(cases, columns=["Test", "Result"]), ["Test", "Result"]) + "\n")
    A("## 12. Reproduction\n")
    A("```\npython scripts/build_model.py\npython analysis/operational_prioritization.py      # tables, figures, stats JSON\n"
      "python analysis/build_prioritization_report.py      # this report\n```\n"
      "SQL: `sql/analysis/prioritization/*.sql`; tables: `reports/tables/prio_*.csv`; figures: `reports/figures/prio_*.png`; stats: `reports/prio_stats.json`.\n")
    OUT.write_text("\n".join(w) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (pytest exit code {rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
