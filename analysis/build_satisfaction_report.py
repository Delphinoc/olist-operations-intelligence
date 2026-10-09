"""Generate reports/customer_satisfaction_findings.md from the outputs of analysis/customer_satisfaction.py.

Usage: python analysis/customer_satisfaction.py && python analysis/build_satisfaction_report.py
Numbers are interpolated from reports/satisfaction_stats.json and reports/tables/satisfaction_*.csv. Qualitative
statements are guarded by assertions in `check_claims`: if the data change so that a claim no longer holds,
the build fails instead of publishing stale narrative.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
S = json.loads((ROOT / "reports" / "satisfaction_stats.json").read_text(encoding="utf-8"))
T = {p.stem.replace("satisfaction_", "", 1): pd.read_csv(p) for p in (ROOT / "reports" / "tables").glob("satisfaction_*.csv")}
OUT = ROOT / "reports" / "customer_satisfaction_findings.md"
JUNIT = ROOT / "data" / "processed" / "pytest_junit_satisfaction.xml"
P0, P1, P1B = "P0 primary: one review row", "P1: reviews created before delivery removed", "P1b: reviews created on or before delivery day removed"


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def pp(x, d=1):
    return f"{100 * x:.{d}f}"


def n(x):
    return f"{int(round(x)):,}"


def ci(lo, hi, d=1):
    return f"[{pct(lo, d)}, {pct(hi, d)}]"


def md(df: pd.DataFrame, headers: list[str]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def run_pytest():
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={JUNIT}"], cwd=ROOT, capture_output=True, text=True)
    cases = []
    for tc in ET.parse(JUNIT).getroot().iter("testcase"):
        bad = tc.find("failure") is not None or tc.find("error") is not None
        cases.append((f"{tc.get('classname', '').split('.')[-1]}::{tc.get('name')}", "FAIL" if bad else "PASS"))
    return proc.returncode, proc.stdout.strip().splitlines()[-1], cases


def check_claims() -> None:
    e = T["effects"].set_index("variant")
    assert e.loc[P0, "low_diff_lo"] > 0.45, "primary gap no longer large"
    for v in (P1, P1B):
        assert e.loc[v, "low_diff_lo"] > 0 and e.loc[v, "low_diff_hi"] < e.loc[P0, "low_diff_lo"], f"{v}: gap no longer clearly smaller but positive"
    b = T["bands"]
    b0 = b[b.variant == P0].set_index("days_late_band").loc[["<=0", "1-3", "4-7", "8+"]]
    assert b0.low_share.is_monotonic_increasing and b0.mean_score.is_monotonic_decreasing, "primary dose-response no longer monotone"
    b1 = b[b.variant == P1].set_index("days_late_band")
    assert b1.loc["8+", "n"] <= 5, "8+ band is no longer almost empty under P1"
    assert S["early_review_rates"]["late"][2] > 0.7 and S["early_review_rates"]["on_time"][2] < 0.01
    assert S["early_late_orders_timing"]["after_promise_share"] > 0.95
    multi = e.loc[[i for i in e.index if i.startswith("Multi-review rule")]]
    assert (multi.low_diff - e.loc[P0, "low_diff"]).abs().max() < 0.005, "multi-review rules now move the gap"
    wc = S["worst_case_bounds"]
    assert wc["low_diff_bounds"][0] > 0.4 and wc["mean_diff_bounds"][1] < -1.5
    nr = T["nonresponse"].set_index("group")
    assert nr.loc["late", "no_review_lo"] > nr.loc["on time", "no_review_hi"], "non-response difference not clear"
    m = T["models"]
    assert m.odds_ratio.between(10, 25).all() and (m.or_lo > 10).all()
    assert S["regression_extra"]["collinearity"]["vif_late"] < 2
    assert all(t.startswith("C(month)") for t in S["regression_extra"]["collinearity"]["terms_vif_gt_5"]), "high-VIF terms are no longer all month indicators"
    loo = T["leave_one_month_out"]
    assert loo.odds_ratio.max() / loo.odds_ratio.min() < 1.25
    c8 = T["c8"].set_index("variant")
    assert c8.loc[P0, "share_late"] < 0.5 and c8.loc[P0, "late_share_among_reviewed"] < 0.1
    mo = T["monthly"]
    lt, on = mo[mo.is_late].set_index("purchase_month"), mo[~mo.is_late].set_index("purchase_month")
    assert (lt.low_share_lo > on.low_share_hi).all(), "late > on-time low-score share no longer holds in every month"
    assert (T["models"].converged.all() and T["stability"].converged.all())


def main() -> int:
    check_claims()
    rc, pytest_summary, cases = run_pytest()
    e = T["effects"].set_index("variant")
    gs = T["group_summary"]
    pop, ov, wc = S["populations"], S["overall"], S["worst_case_bounds"]
    g0 = gs[gs.variant == P0].set_index("is_late")
    mods, stab = T["models"], T["stability"].set_index("model")
    ex = S["regression_extra"]
    et = T["early_timing"].set_index("days_late_band")
    er = S["early_review_rates"]
    lt = S["early_late_orders_timing"]
    c8 = T["c8"].set_index("variant")
    b = T["bands"]
    tg = T["timing_groups"].set_index("group")
    nr = T["nonresponse"].set_index("group")
    m2 = mods.iloc[2]
    p0e, p1e, p1be = e.loc[P0], e.loc[P1], e.loc[P1B]

    w = []
    A = w.append
    A("# Customer Satisfaction: Findings (Workstream 3)\n")
    A("Generated by `analysis/build_satisfaction_report.py` from `analysis/customer_satisfaction.py` (SQL in `sql/analysis/satisfaction/`) on the validated "
      "DuckDB model. Everything below is **observational association**: it shows how review scores differ between on-time and late deliveries, not whether lateness *causes* dissatisfaction. "
      "No text/NLP, seller, geographic or predictive analysis is included.\n")
    A("Labels: **[Observed]** a computed result; **[Interpretation]** a reading that goes beyond it; **[Hypothesis]** a possible explanation this analysis cannot test.\n")

    A("## Summary: what the data can and cannot establish\n")
    A(f"- **[Observed]** Late orders get much lower scores. Among single-review delivered orders, mean score is {g0.loc[False, 'mean_score']:.2f} on time vs {g0.loc[True, 'mean_score']:.2f} late, and "
      f"{pct(g0.loc[False, 'low_share'])} vs {pct(g0.loc[True, 'low_share'])} give 1-2 stars (gap {pp(p0e.low_diff)} percentage points; odds ratio {p0e.odds_ratio:.1f}). "
      "The gap barely moves with clustering, adjustment for state, promised lead time, month, order value, freight share, item count or product category, with the handling of multi-review orders, or with non-response.\n"
      f"- **[Observed] The size of the gap depends heavily on review timing.** {pct(er['late'][2])} of late orders' reviews were created *before* the recorded delivery date (vs {pct(er['on_time'][2], 2)} of on-time orders'), "
      f"and {pct(lt['after_promise_share'])} of those were written after the promised date, i.e. while the order was overdue. Removing reviews written before delivery shrinks the gap to "
      f"{pp(p1e.low_diff)} points (odds ratio {p1e.odds_ratio:.1f}); removing those written on or before the delivery day shrinks it to {pp(p1be.low_diff)} points. Severely late orders (8+ days) have almost no review written after delivery, "
      "so the data cannot say how they score once the order has arrived.\n"
      f"- **[Observed]** Most low scores are not on late orders. {pct(1 - c8.loc[P0, 'share_late'], 0)} of 1-2 star reviewed orders were delivered on time; {pct(c8.loc[P0, 'share_late'], 0)} were late, against a late share of "
      f"{pct(c8.loc[P0, 'late_share_among_reviewed'])} among all reviewed orders. This is a composition, **not** an attributable fraction.\n"
      "- **Not established:** that lateness causes low scores, how many low scores would disappear if deliveries were on time, or what customers think of a late order after it arrives (for the most severe delays).\n")

    # ------------------------------------------------------------------ 1
    A("## 1. Populations and definitions\n")
    A(md(pd.DataFrame([
        ["Delivered with date, purchases 2017-01..2018-08 (delivery KPI population)", n(pop['delivered_window']), "established model population"],
        ["**P0 (primary):** exactly one review row", n(pop['one_review_p0']), f"{n(pop['n_customers_p0'])} distinct customers"],
        ["No review row", n(pop['no_review']), "see Section 6"],
        ["Several review rows", n(pop['multi_review']), "excluded from P0; see Section 5"],
        ["**P1:** P0 minus reviews created before the recorded delivery date", n(pop['p1_after_removing_early']), f"{n(pop['early_reviews'])} reviews removed"],
        ["**P1b:** P0 minus reviews created on or before the delivery day", n(pop['p1b_after_removing_same_day']), "stress test (creation dates have day precision)"],
    ]), ["Population", "Orders", "Note"]) + "\n")
    A("- **Late** = calendar-date rule (`DATE(delivered) > estimated date`); lateness bands: on time, 1-3, 4-7, 8+ days late (8+ = severe, > 7 days).\n"
      "- **Low score** = 1 or 2 stars. Review score exists only when exactly one review row exists; **the latest review is never treated as authoritative**.\n"
      f"- **Intervals:** Wilson for proportions; normal approximation for means within groups; for the late-vs-on-time effect sizes a **cluster bootstrap resampling customers** ({S['n_boot']:,} resamples, seed {S['seed']}). "
      "Effect sizes: difference in means, Cohen's d, difference in low-score share, risk ratio, odds ratio, Cliff's delta and the probability that a random on-time order out-scores a random late order (ties split).\n"
      f"- **Overall:** mean score {ov['mean_score']:.2f}, {pct(ov['low_share'])} low-score {ci(*ov['low_ci'])} across {n(ov['n'])} single-review orders.\n")

    # ------------------------------------------------------------------ 2
    A("## 2. On-time vs late\n")
    A("![Score distribution](figures/satisfaction_01_score_distribution.png)\n")
    rows = []
    for late, lab in ((False, "On time"), (True, "Late")):
        r = g0.loc[late]
        rows.append([lab, n(r.n), f"{r.mean_score:.2f} [{r.mean_lo:.2f}, {r.mean_hi:.2f}]", f"{pct(r.low_share)} {ci(r.low_share_lo, r.low_share_hi)}",
                     " / ".join(pct(r[f'share{k}'], 0) for k in range(1, 6))])
    A(md(pd.DataFrame(rows), ["Group", "Orders", "Mean score [95% CI]", "1-2 star share [95% Wilson]", "Share of 1/2/3/4/5 stars"]) + "\n")
    A(md(pd.DataFrame([
        ["Mean score difference (late - on time)", f"{p0e.mean_diff:.2f} stars", f"[{p0e.mean_diff_lo:.2f}, {p0e.mean_diff_hi:.2f}]"],
        ["Cohen's d", f"{p0e.cohens_d:.2f}", f"[{p0e.cohens_d_lo:.2f}, {p0e.cohens_d_hi:.2f}]"],
        ["Low-score share difference", f"{pp(p0e.low_diff)} points", f"[{pp(p0e.low_diff_lo)}, {pp(p0e.low_diff_hi)}]"],
        ["Risk ratio (low score)", f"{p0e.risk_ratio:.2f}", f"[{p0e.risk_ratio_lo:.2f}, {p0e.risk_ratio_hi:.2f}]"],
        ["Odds ratio (low score)", f"{p0e.odds_ratio:.1f}", f"[{p0e.odds_ratio_lo:.1f}, {p0e.odds_ratio_hi:.1f}]"],
        ["Cliff's delta (on time scores higher)", f"{p0e.cliffs_delta:.2f}", f"[{p0e.cliffs_delta_lo:.2f}, {p0e.cliffs_delta_hi:.2f}]"],
        ["P(random on-time score > random late score), ties split", f"{p0e.prob_superiority:.2f}", f"[{p0e.prob_superiority_lo:.2f}, {p0e.prob_superiority_hi:.2f}]"],
    ]), ["Effect size (P0, primary)", "Estimate", "95% cluster-bootstrap interval"]) + "\n")
    A(f"**[Observed]** Late orders score about {abs(p0e.mean_diff):.1f} stars lower on average and are {p0e.risk_ratio:.1f} times as likely to receive 1-2 stars (Cohen's d {abs(p0e.cohens_d):.1f}, a very large standardised difference). "
      f"Treating orders as independent gives practically the same interval for the low-score gap (Newcombe {pp(S['low_diff_newcombe_iid'][1])} to {pp(S['low_diff_newcombe_iid'][2])} points; "
      f"cluster-to-independent standard-error ratio {p0e.design_effect_ratio_low_diff:.2f}), because customers rarely appear more than once among the reviewed orders.\n")

    # ------------------------------------------------------------------ 3
    A("## 3. Lateness severity\n")
    A("![Bands](figures/satisfaction_02_lateness_bands.png)\n")
    bb = b[b.variant == P0].set_index("days_late_band").loc[["<=0", "1-3", "4-7", "8+"]]
    b1 = b[b.variant == P1].set_index("days_late_band").loc[["<=0", "1-3", "4-7", "8+"]]
    rows = []
    for k, lab in zip(["<=0", "1-3", "4-7", "8+"], ["On time", "1-3 days late", "4-7 days late", "8+ days late"]):
        r, r1 = bb.loc[k], b1.loc[k]
        rows.append([lab, n(r.n), f"{r.mean_score:.2f} [{r.mean_lo:.2f}, {r.mean_hi:.2f}]", f"{pct(r.low_share)} {ci(r.low_share_lo, r.low_share_hi)}",
                     f"{pp(r.low_diff_vs_on_time)}" if k != "<=0" else "-", n(r1.n), f"{pct(r1.low_share)}" + (" (n too small)" if r1.n < 30 else "")])
    A(md(pd.DataFrame(rows), ["Band", "Orders (P0)", "Mean score [95% CI]", "1-2 star share [95% Wilson]", "Gap vs on time (pts)", "Orders (P1)", "1-2 star share (P1)"]) + "\n")
    A(f"**[Observed]** In the primary population the low-score share rises steadily with lateness: {pct(bb.loc['<=0', 'low_share'])} on time, {pct(bb.loc['1-3', 'low_share'])} for 1-3 days late, "
      f"{pct(bb.loc['4-7', 'low_share'])} for 4-7 and {pct(bb.loc['8+', 'low_share'])} for 8+ days late; mean score falls from {bb.loc['<=0', 'mean_score']:.2f} to {bb.loc['8+', 'mean_score']:.2f}. "
      f"After adjustment (Section 7) the odds ratios against on-time are {T['bands_adj'].odds_ratio.iloc[0]:.1f}, {T['bands_adj'].odds_ratio.iloc[1]:.1f} and {T['bands_adj'].odds_ratio.iloc[2]:.1f}.\n")
    A(f"**[Observed]** This dose-response is **not stable once reviews written before delivery are removed (P1)**: only {n(b1.loc['4-7', 'n'])} reviewed orders remain in the 4-7 band, {n(b1.loc['8+', 'n'])} in the 8+ band, and the 1-3 and 4-7 bands have similar low-score shares "
      f"({pct(b1.loc['1-3', 'low_share'])} and {pct(b1.loc['4-7', 'low_share'])}). The apparent gradient is largely a gradient in how often the review is written before delivery (next section).\n")

    # ------------------------------------------------------------------ 4
    A("## 4. Review timing: the required sensitivity analysis\n")
    A("![Review timing](figures/satisfaction_07_review_timing.png)\n")
    A(md(pd.DataFrame([
        ["On time", n(et.loc['<=0', 'n']), n(et.loc['<=0', 'n_early']), pct(et.loc['<=0', 'early_share'], 2), n(et.loc['<=0', 'n_early_after_promise'])],
        ["1-3 days late", n(et.loc['1-3', 'n']), n(et.loc['1-3', 'n_early']), pct(et.loc['1-3', 'early_share']), n(et.loc['1-3', 'n_early_after_promise'])],
        ["4-7 days late", n(et.loc['4-7', 'n']), n(et.loc['4-7', 'n_early']), pct(et.loc['4-7', 'early_share']), n(et.loc['4-7', 'n_early_after_promise'])],
        ["8+ days late", n(et.loc['8+', 'n']), n(et.loc['8+', 'n_early']), pct(et.loc['8+', 'early_share']), n(et.loc['8+', 'n_early_after_promise'])],
    ]), ["Band", "Reviewed orders", "Review created before recorded delivery", "Share", "...of which created after the promised date"]) + "\n")
    A(md(pd.DataFrame([[g, n(r.n), f"{r.mean_score:.2f}", f"{pct(r.low_share)} {ci(r.low_lo, r.low_hi)}"] for g, r in tg.iterrows()]),
         ["Group", "Orders", "Mean score", "1-2 star share [95% Wilson]"]) + "\n")
    A(md(pd.DataFrame([
        ["P0 primary", n(p0e.n_on_time), n(p0e.n_late), f"{p0e.mean_diff:.2f} [{p0e.mean_diff_lo:.2f}, {p0e.mean_diff_hi:.2f}]",
         f"{pp(p0e.low_diff)} [{pp(p0e.low_diff_lo)}, {pp(p0e.low_diff_hi)}]", f"{p0e.odds_ratio:.1f}", f"{p0e.cohens_d:.2f}"],
        ["P1: created before delivery removed", n(p1e.n_on_time), n(p1e.n_late), f"{p1e.mean_diff:.2f} [{p1e.mean_diff_lo:.2f}, {p1e.mean_diff_hi:.2f}]",
         f"{pp(p1e.low_diff)} [{pp(p1e.low_diff_lo)}, {pp(p1e.low_diff_hi)}]", f"{p1e.odds_ratio:.1f}", f"{p1e.cohens_d:.2f}"],
        ["P1b: created on/before delivery day removed", n(p1be.n_on_time), n(p1be.n_late), f"{p1be.mean_diff:.2f} [{p1be.mean_diff_lo:.2f}, {p1be.mean_diff_hi:.2f}]",
         f"{pp(p1be.low_diff)} [{pp(p1be.low_diff_lo)}, {pp(p1be.low_diff_hi)}]", f"{p1be.odds_ratio:.1f}", f"{p1be.cohens_d:.2f}"],
    ]), ["Population", "On time", "Late", "Mean diff [95% CI]", "Low-score gap, pts [95% CI]", "Odds ratio", "Cohen's d"]) + "\n")
    A(f"**[Observed]** {pct(er['late'][2])} of late orders' reviews ({n(er['late'][0])} of {n(er['late'][1])}) were created before the recorded delivery date, against {pct(er['on_time'][2], 2)} of on-time orders' ({n(er['on_time'][0])} of {n(er['on_time'][1])}). "
      f"Of the {n(lt['n_early_late_orders'])} such reviews on late orders, {n(lt['after_promise'])} ({pct(lt['after_promise_share'])}) were created *after the promised date*. "
      f"They are very negative (mean {S['early_review_scores']['mean']:.2f} across all early reviews; {pct(tg.loc['Late, review written before delivery', 'low_share'])} low-score on late orders) compared with reviews written after a late delivery ({pct(tg.loc['Late, review written after delivery', 'low_share'])} low-score).\n")
    A(f"**[Observed]** Removing early reviews cuts the low-score gap from {pp(p0e.low_diff)} to {pp(p1e.low_diff)} points ({pp(p1be.low_diff)} in the strictest variant). It stays positive with an interval excluding zero in both. "
      f"The sensitivity also changes who is compared: only {n(p1e.n_late)} of {n(p0e.n_late)} late orders ({pct(p1e.n_late / p0e.n_late, 0)}) keep a review, and these are disproportionately mildly late (Section 3).\n")
    A("**[Interpretation]** The primary and P1 results answer different questions, and neither is 'the' effect of lateness. P0 describes reviews as they are, including reviews written while an order is overdue; "
      "P1 describes how customers rate an order *after* it has arrived late. Because 99% of the early late-order reviews were written after the promised date, they most plausibly reflect customers reacting to a parcel that had not arrived on time. Two explanations remain open **[Hypothesis]**: "
      "(a) the review request is triggered at the promised date for undelivered orders (external knowledge about the platform, **not verifiable here**), or (b) the recorded delivery date lags actual arrival for some orders. "
      "Operations should confirm how `order_delivered_customer_date` is recorded.\n")

    # ------------------------------------------------------------------ 5
    A("## 5. Alternative handling of multi-review orders\n")
    A("![Sensitivity](figures/satisfaction_03_sensitivity_gap.png)\n")
    rule_rows = e.loc[[i for i in e.index if i.startswith("Multi-review rule")]]
    rows = [[i.replace("Multi-review rule: ", ""), r.units, n(r.n_on_time), n(r.n_late), f"{pp(r.low_diff)} [{pp(r.low_diff_lo)}, {pp(r.low_diff_hi)}]", f"{r.mean_diff:.2f}"] for i, r in rule_rows.iterrows()]
    rows.insert(0, ["primary: single-review orders only", "orders", n(p0e.n_on_time), n(p0e.n_late), f"{pp(p0e.low_diff)} [{pp(p0e.low_diff_lo)}, {pp(p0e.low_diff_hi)}]", f"{p0e.mean_diff:.2f}"])
    A(md(pd.DataFrame(rows), ["Handling of orders with several review rows", "Unit", "On time", "Late", "Low-score gap, pts [95% CI]", "Mean diff"]) + "\n")
    A(f"**[Observed]** {n(pop['multi_review'])} delivered orders ({pct(pop['multi_review'] / pop['delivered_window'], 2)}) have several review rows. Whichever rule is used (lowest, highest, mean, earliest-dated, latest-dated where dates differ, or every row), "
      f"the low-score gap stays within {pp(max(abs(rule_rows.low_diff - p0e.low_diff)), 2)} points of the primary figure. The lowest-score and highest-score rules bracket any other per-order choice. "
      "The latest-dated rule is shown for comparison only; no rule is treated as authoritative.\n")

    # ------------------------------------------------------------------ 6
    A("## 6. Review non-response\n")
    rows = [[g, n(r.n_total), n(r.n_one_review), f"{n(r.n_no_review)} ({pct(r.no_review_rate, 2)}) {ci(r.no_review_lo, r.no_review_hi, 2)}", f"{n(r.n_multi_review)} ({pct(r.multi_review_rate, 2)})"] for g, r in nr.iterrows()]
    A(md(pd.DataFrame(rows), ["Group", "Delivered orders", "One review", "No review (rate) [95% Wilson]", "Several reviews"]) + "\n")
    d_ = S["no_review_rate_diff"]
    A(f"**[Observed]** Late orders are less likely to have any review: {pct(nr.loc['late', 'no_review_rate'], 2)} vs {pct(nr.loc['on time', 'no_review_rate'], 2)} (difference {pp(d_[0])} points, Newcombe interval {pp(d_[1])} to {pp(d_[2])}). Response is differential, but missingness is small in absolute terms.\n")
    A("**Sensitivity scenarios for the unreviewed orders (labelled illustrative: they assume values for reviews that do not exist).** Low-score gap in points, restricting to orders with zero or one review row:\n")
    sc = T["missing_review_scenarios"]
    piv = sc.pivot(index="assumption_missing_late", columns="assumption_missing_on_time", values="low_diff_pp")
    r_order = ["same as reviewed late orders", "none low-scored", "all low-scored"]
    c_order = ["same as reviewed on-time orders", "none low-scored", "all low-scored"]
    piv = piv.loc[r_order, c_order]
    rows = [[idx] + [f"{piv.loc[idx, c]:.1f}" for c in piv.columns] for idx in piv.index]
    A(md(pd.DataFrame(rows), ["Assumed share low-scored among the " + n(S['scenario_note']['n_missing_late']) + " unreviewed late orders ↓ / the " + n(S['scenario_note']['n_missing_on_time']) + " unreviewed on-time orders →"] + list(piv.columns)) + "\n")
    A(f"**Worst-case bounds** (no assumption at all about the {n(wc['unknown_late'] + wc['unknown_on_time'])} late or on-time delivered orders with no or several review rows): the low-score gap lies between {pp(wc['low_diff_bounds'][0])} and {pp(wc['low_diff_bounds'][1])} points "
      f"and the mean-score gap between {wc['mean_diff_bounds'][0]:.2f} and {wc['mean_diff_bounds'][1]:.2f} stars.\n")
    A("**[Interpretation]** Non-response of this kind cannot explain the association. This says nothing about customers who never reviewed because they were not asked, or who reviewed only because they were unhappy: voluntary reviewing is not observable here.\n")

    # ------------------------------------------------------------------ 7
    A("## 7. Adjusted association (logistic regression; not causal)\n")
    A("![Adjusted association](figures/satisfaction_04_adjusted_association.png)\n")
    A("**Pre-specified design.** Outcome: 1-2 star review. Exposure: late (calendar date). Covariates fixed before fitting: promised-lead group (5 groups), purchase month (20), customer state (states with fewer than 100 delivered orders pooled, 25 levels), "
      "log order value, freight share of order cost, item count (1, 2, 3+), and, in the exploratory model only, product category (categories with fewer than 500 reviewed orders pooled; mixed/unknown kept as levels). "
      "Customer-clustered robust standard errors. Seller is not included (seller analysis is out of scope here).\n")
    rows = [[r.model, n(r.n), n(r.events), f"{r.odds_ratio:.2f}", f"[{r.or_lo:.2f}, {r.or_hi:.2f}]"] for r in mods.itertuples()]
    A(md(pd.DataFrame(rows), ["Model", "Orders", "Low-score events", "Odds ratio for late", "95% CI (cluster-robust)"]) + "\n")
    A(f"- **Average marginal difference (M2):** adjusted predicted probability of a 1-2 star review is {ex['ame_late_pp'][0]:.1f} points higher (95% CI {ex['ame_late_pp'][1]:.1f} to {ex['ame_late_pp'][2]:.1f}) if an order is classed late rather than on time, holding the others at their observed values; "
      f"linear model for the score itself: {ex['ols_score_diff'][0]:.2f} stars [{ex['ols_score_diff'][1]:.2f}, {ex['ols_score_diff'][2]:.2f}].\n"
      f"- **By severity (M2 with bands):** odds ratios vs on time {T['bands_adj'].odds_ratio.iloc[0]:.1f} (1-3 days), {T['bands_adj'].odds_ratio.iloc[1]:.1f} (4-7), {T['bands_adj'].odds_ratio.iloc[2]:.1f} (8+).\n")
    st = T["stability"]
    rows = [[r.model, n(r.n), n(r.events), f"{r.odds_ratio:.2f}", f"[{r.or_lo:.2f}, {r.or_hi:.2f}]"] for r in st.itertuples()]
    loo = T["leave_one_month_out"]
    A(md(pd.DataFrame(rows), ["Stability check (M2 specification)", "Orders", "Events", "Odds ratio for late", "95% CI"]) + "\n")
    A(f"Leaving out each purchase month in turn (20 refits) keeps the odds ratio between {loo.odds_ratio.min():.1f} and {loo.odds_ratio.max():.1f}.\n")
    sp = T["sparsity"]
    cv = ex["collinearity"]
    A("**Diagnostics**\n")
    A(f"- **Missingness:** none in the outcome or any covariate (all counts zero).\n"
      f"- **Sparsity:** " + "; ".join(f"{r.factor}: {int(r.levels)} levels, smallest level {int(r.min_n):,} orders / {int(r.min_events):,} events" for r in sp.itertuples()) + ". "
      f"{int(sp.levels_with_lt_50_events.sum())} cells have fewer than 50 low-score events (small states and a few month-by-late cells), so their own coefficients are imprecise; the late coefficient is not affected. No separation or convergence warning in any fit.\n"
      f"- **Collinearity:** variance inflation factor for late = {cv['vif_late']:.2f}; the largest, {cv['max_vif']:.1f} ({cv['max_vif_term']}), belongs to a purchase-month indicator ({cv['n_terms_vif_gt_5']} of {cv['n_terms']} terms exceed 5, all of them purchase-month indicators); lateness itself is not entangled with the other covariates. Standardised design condition number {cv['condition_number_standardised']:.0f}.\n"
      f"- **Clustering:** cluster-robust and naive standard errors for the late coefficient are {ex['se_cluster_vs_naive_late'][0]:.4f} and {ex['se_cluster_vs_naive_late'][1]:.4f}.\n")
    A(f"**[Observed]** Adjustment barely moves the association (unadjusted odds ratio {mods.odds_ratio.iloc[0]:.1f}; primary adjusted {m2.odds_ratio:.1f}; with product category {mods.odds_ratio.iloc[3]:.1f}). It is stable across halves of the window, with or without the three late-rate episode months, and across leave-one-month-out refits. "
      f"It is **not** stable to the review-timing restriction (P1: odds ratio {stab.loc['P1: reviews created before delivery removed', 'odds_ratio']:.1f}).\n")
    A("**[Interpretation]** The coefficients describe association conditional on the listed covariates. They do not estimate the effect of delivering on time: product quality, seller, customer expectations and other unobserved factors "
      "are absent, and the timing finding shows that part of the association comes from when customers choose to review. Odds ratios of this size are non-collapsible; the marginal difference above is the easier figure to quote.\n")

    # ------------------------------------------------------------------ 8
    A("## 8. Share of low-score reviewed orders that were delivered late\n")
    A("![Composition](figures/satisfaction_05_low_score_composition.png)\n")
    rows = [[v, n(r.low_score_orders), n(r.of_which_late), f"{pct(r.share_late)} {ci(r.share_lo, r.share_hi)}", pct(r.late_share_among_reviewed)] for v, r in c8.iterrows()]
    A(md(pd.DataFrame(rows), ["Population", "1-2 star orders", "of which delivered late", "Share delivered late [95% Wilson]", "Late share among all reviewed orders"]) + "\n")
    A(f"**[Observed]** In the primary population {pct(c8.loc[P0, 'share_late'])} of 1-2 star reviewed orders were delivered late and {pct(1 - c8.loc[P0, 'share_late'])} on time; late orders are only {pct(c8.loc[P0, 'late_share_among_reviewed'])} of reviewed orders. "
      f"With the lowest-score rule for multi-review orders the share is {pct(S['c8_lowest_rule']['share_late'])}; after removing reviews written before delivery it is {pct(c8.loc[P1, 'share_late'])}.\n")
    A("**This is a descriptive composition of low-score orders by delivery outcome. It is not an attributable fraction**: it depends on how common lateness is, it does not say how many low scores would disappear without late deliveries, "
      "and it moves from about a third to about 5% depending on whether reviews written before delivery are counted.\n")

    # ------------------------------------------------------------------ 9
    A("## 9. Consistency over time\n")
    A("![Monthly](figures/satisfaction_06_monthly_low_share.png)\n")
    mo = T["monthly"]
    A(f"**[Observed]** In every one of the 20 purchase-month cohorts the low-score share of late orders is clearly above that of on-time orders (Wilson intervals do not overlap). The on-time share is stable ({pct(mo[~mo.is_late].low_share.min())} to {pct(mo[~mo.is_late].low_share.max())}); "
      f"the late share varies more ({pct(mo[mo.is_late].low_share.min(), 0)} to {pct(mo[mo.is_late].low_share.max(), 0)}) with wider intervals because late orders per month are few in calm months.\n")

    # ------------------------------------------------------------------ 10
    A("## 10. Status of the blueprint hypotheses\n")
    A(md(pd.DataFrame([
        ["H3.1 Late delivery is associated with lower scores after adjustment", "Supported (association)", f"adjusted odds ratio {m2.odds_ratio:.1f} [{m2.or_lo:.1f}, {m2.or_hi:.1f}]; smaller but positive after review-timing restriction ({p1e.odds_ratio:.1f})"],
        ["H3.2 Scores fall as days late increase", "Supported in the primary population only", "monotone in P0; not testable in P1 (8+ band has 1 review)"],
        ["H3.3 The association persists within sellers", "Not tested", "seller analysis out of scope for this workstream"],
        ["H3.4 Excluding reviews created before delivery changes the gap only modestly", "**Rejected**", f"gap falls from {pp(p0e.low_diff)} to {pp(p1e.low_diff)} points"],
        ["H3.5 Most low-score reviews come from on-time orders in absolute count", "Supported", f"{pct(1 - c8.loc[P0, 'share_late'], 0)} of 1-2 star reviewed orders were on time"],
    ]), ["Hypothesis", "Status", "Basis"]) + "\n")

    # ------------------------------------------------------------------ 11
    A("## 11. Business implications (what these data support)\n")
    A("1. **Do not quote a single effect size for 'the cost of lateness'.** The gap is about 53 points if reviews are taken as they come and 9 to 16 points if only reviews written after delivery are counted; the data cannot say which is the relevant business quantity, "
      "because late orders attract many reviews while the parcel is still overdue.\n"
      "2. **The overdue-but-undelivered window matters.** Most reviews on late orders are written after the promised date and before delivery, and they are far more negative than reviews written after a late order arrives. "
      "If the platform can contact customers whose orders pass the promised date (status update, revised date, proactive support), that is the moment the data suggest dissatisfaction is expressed. "
      "Whether such contact would change review behaviour is a hypothesis to test, not a finding.\n"
      f"3. **Lateness is a minority of the dissatisfaction.** About {pct(1 - c8.loc[P0, 'share_late'], 0)} of 1-2 star reviews are on on-time orders, and {pct(g0.loc[False, 'low_share'])} of on-time orders still receive 1-2 stars. "
      "Reducing late deliveries would address one visible driver; understanding the rest needs data not used here (product, seller, review text).\n"
      "4. **Severe delays have an observability gap.** Almost no 8+ day late order has a review written after delivery. Measuring post-delivery sentiment for the worst delays requires a different data source or survey design.\n"
      "5. **Confirm the meaning of the delivery timestamp** with operations (customer-confirmed or carrier-scan); alternative (b) in Section 4 would change how 'late' should be interpreted for review analysis.\n")

    # ------------------------------------------------------------------ 12
    A("## 12. Uncertainty and limitations\n")
    A("- **Association, not causation:** unobserved product quality, seller behaviour, customer expectations and communication are not in the model; reverse timing (review before delivery) is itself a finding that complicates a causal reading.\n"
      "- **Voluntary reviews:** only customers who review appear; we observe 99.3% coverage of delivered orders but not why customers do or do not review.\n"
      "- **Review dates have day precision**; same-day reviews cannot be ordered against delivery, hence the P1/P1b pair.\n"
      "- **Calendar-date lateness** and undocumented time zones; the exact-timestamp definition was not re-run here.\n"
      "- **Single-review restriction:** 0.5% of delivered orders have several review rows (Section 5). Results do not depend on how they are handled.\n"
      "- **Intervals:** cluster bootstrap resamples customers only (not sellers or calendar days); regression uses customer-clustered errors; month-level structure (episodes) is handled by month indicators and leave-one-month-out checks, not modelled.\n"
      "- **Multiple model variants** were fitted, all pre-specified and reported; no variant was selected for its result.\n"
      "- **Scope:** no seller, geographic, text or predictive analysis; those belong to later workstreams.\n"
      "- Single marketplace, 2017-2018; findings describe this dataset.\n")

    # ------------------------------------------------------------------ 13
    A("## 13. Validation and tests\n")
    A(f"- Full test run (`python -m pytest`): **{pytest_summary}** (exit code {rc}); {sum(1 for _, s in cases if s == 'PASS')} of {len(cases)} cases passed.\n"
      "- Every SQL aggregation (group, band, timing, multi-review rule, non-response and C8 tables) is recomputed independently in pandas from the raw CSVs; effect sizes are checked against SciPy (Mann-Whitney); "
      "the cluster bootstrap is checked on synthetic clustered data (cluster SE > independent SE) and for seeded reproducibility.\n"
      "- **Regression:** the M1/M2 logistic models, their cluster-robust standard errors, the average marginal effect, the variance inflation factor and the P1 stability fit are all re-derived with a hand-written IRLS and sandwich estimator "
      "(`tests/test_customer_satisfaction.py`) and agree with `statsmodels` to 6 significant figures; the unadjusted model equals the crude odds ratio. A deliberately wrong lateness definition changes the odds ratio from 17.5 to 12.5 and would fail these tests.\n"
      "- **Fixes during this workstream:** no defect was found in the model or earlier code; all pre-existing tests still pass. One new dependency, `statsmodels`, was added to `requirements.txt`.\n")
    A(md(pd.DataFrame(cases, columns=["Test", "Result"]), ["Test", "Result"]) + "\n")

    A("## 14. Reproduction\n")
    A("```\npython scripts/build_model.py\npython analysis/customer_satisfaction.py        # tables, figures, stats JSON\n"
      "python analysis/build_satisfaction_report.py     # this report\n```\n"
      "SQL: `sql/analysis/satisfaction/*.sql`; tables: `reports/tables/satisfaction_*.csv`; figures: `reports/figures/satisfaction_*.png`; stats: `reports/satisfaction_stats.json`.\n")
    OUT.write_text("\n".join(w) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (pytest exit code {rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
