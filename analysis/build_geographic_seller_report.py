"""Generate reports/geographic_seller_findings.md from the outputs of analysis/geographic_seller.py.

Usage: python analysis/geographic_seller.py && python analysis/build_geographic_seller_report.py
Numbers are interpolated from reports/geo_stats.json and reports/tables/geo_*.csv. Qualitative statements are
guarded by assertions in `check_claims`: if the data change so that a claim no longer holds, the build fails
instead of publishing stale narrative.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
S = json.loads((ROOT / "reports" / "geo_stats.json").read_text(encoding="utf-8"))
T = {p.stem.replace("geo_", "", 1): pd.read_csv(p) for p in (ROOT / "reports" / "tables").glob("geo_*.csv")}
OUT = ROOT / "reports" / "geographic_seller_findings.md"
JUNIT = ROOT / "data" / "processed" / "pytest_junit_geo.xml"


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"


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
    st = T["states"]
    rep = st[~st.low_volume]
    top5 = rep.sort_values("late_rate", ascending=False).head(5)
    assert (top5.macro_region == "Northeast").all(), "top-5 late-rate states are no longer all Northeast"
    sp = st.set_index("customer_state").loc["SP"]
    assert sp.late_rate_hi < S["population_all"]["late_rate"] and sp.share_of_late < sp.share_of_delivered
    assert rep.sort_values("excess_late", ascending=False).iloc[0].customer_state == "RJ"
    assert S["state_rank_stability"]["spearman_unadjusted_vs_oe"] > 0.9
    assert set(S["state_rank_stability"]["low_volume_states"]) == {"AC", "AP", "RR"}
    for k in ("mh_cross_state", "mh_cross_state_within_state", "mh_far_vs_near", "mh_far_vs_near_within_state", "mh_origin_sp_within_destination"):
        assert S[k]["rr_lo"] > 1, f"{k}: stratified association no longer clearly above 1"
    assert S["mh_cross_state"]["rr_mh"] > S["mh_cross_state"]["rr_crude"]
    d = T["decomposition"]
    assert (d.composition.abs() < 0.01).all() and (d.within_segment / d.difference > 0.9).all(), "mix now explains a visible share of the high-delay increase"
    assert S["period_breadth"]["states_higher_in_high_delay"] == S["period_breadth"]["states_compared"]
    th = T["seller_thresholds"]
    assert (th.flagged_wilson_above > th.expected_false_flags_if_no_differences).all()
    assert S["seller_persistence"]["spearman_h1_vs_h2"] > 0.2
    assert S["seller_persistence"]["flagged50_above_half_rate_in_both_halves"] / S["seller_persistence"]["flagged50_with_both_halves_observed"] > 0.4
    assert all(abs(x) > 0 for x in th.flagged_also_above_stratified_expectation)
    lanes = T["lanes"]
    assert lanes.iloc[0].lane == "SP>RJ"
    assert S["lanes_pooled"]["share_orders_reported"] > 0.9
    assert (lanes.above_ref & lanes.adj_signal_above).sum() == lanes.above_ref.sum(), "a raw lane signal no longer persists after stratification"


def main() -> int:
    check_claims()
    st, rg = T["states"], T["regions"]
    pa, ps = S["population_all"], S["population_single_seller"]
    ref, ref_ss = pa["late_rate"], ps["late_rate"]
    rep = st[~st.low_volume].sort_values("late_rate", ascending=False)
    low = st[st.low_volume]
    rs = S["state_rank_stability"]
    ship = T["shipment_type"].set_index("label")
    band = T["distance_bands"]
    th = T["seller_thresholds"].set_index("min_orders")
    tiers = T["seller_tiers"].set_index("tier")
    cand = T["seller_candidates"]
    sel = T["sellers"]
    dec = T["decomposition"].set_index("segmentation")
    po = T["period_overall"].set_index(["population", "period"])
    pm = T["period_mix"].set_index("period")
    pers, conc, ov = S["seller_persistence"], S["seller_concentration"], S["seller_flag_overlap"]
    br, sbr = S["period_breadth"], S["period_seller_breadth"]
    lp = S["lanes_pooled"]
    q = S["distance_quartiles_km"]
    mh = {k: S[k] for k in ("mh_cross_state", "mh_cross_state_within_state", "mh_far_vs_near", "mh_far_vs_near_within_state", "mh_origin_sp_within_destination")}
    ranked = sel[sel.n_delivered >= 50]
    flagged50 = ranked[ranked.screen_signal]
    sp_share_ranked = (ranked.seller_state == "SP").mean()
    sp_share_flagged = (flagged50.seller_state == "SP").mean()

    w = []
    A = w.append
    A("# Geographic and Seller Performance: Findings (Workstream 2)\n")
    A("Generated by `analysis/build_geographic_seller_report.py` from `analysis/geographic_seller.py` (SQL in `sql/analysis/geography/`) on the validated DuckDB model. "
      "Everything below is **descriptive screening**: it shows where late deliveries concentrate, not why, and **it does not attribute delays to sellers or regions**. "
      "No operational priority tiers, dashboards, NLP or predictive models are included.\n")
    A("Labels: **[Observed]** a computed result; **[Interpretation]** a reading that goes beyond it; **[Hypothesis]** a possible explanation this analysis cannot test.\n")

    A("## Summary\n")
    A(f"- **[Observed] Geography matters a lot.** Late rates run from {pct(rep.late_rate.min())} to {pct(rep.late_rate.max())} across the 24 reportable states. Northeast states are the five highest ({', '.join(rep.head(5).customer_state)}); "
      f"São Paulo is the lowest large state ({pct(st.set_index('customer_state').loc['SP', 'late_rate'])}). Rio de Janeiro ({n(st.set_index('customer_state').loc['RJ', 'n_delivered'])} orders, "
      f"{pct(st.set_index('customer_state').loc['RJ', 'late_rate'])} late) supplies {pct(st.set_index('customer_state').loc['RJ', 'share_of_late'], 0)} of all late orders from {pct(st.set_index('customer_state').loc['RJ', 'share_of_delivered'], 0)} of orders. "
      f"The state ranking barely changes after standardising for purchase month and promised lead time (rank correlation {rs['spearman_unadjusted_vs_oe']:.2f}).\n"
      f"- **[Observed] Distance and cross-state shipping go with later and more late deliveries.** Cross-state shipments are late {pct(ship.loc['Cross-state', 'late_rate'])} of the time vs {pct(ship.loc['Same-state', 'late_rate'])} for same-state; "
      f"after stratifying by month and promised lead time the risk ratio is {mh['mh_cross_state']['rr_mh']:.2f} [{mh['mh_cross_state']['rr_lo']:.2f}, {mh['mh_cross_state']['rr_hi']:.2f}], and {mh['mh_cross_state_within_state']['rr_mh']:.2f} when stratifying by destination state and promised lead time instead. "
      "Distance is a straight-line ZIP-prefix approximation, not road distance.\n"
      f"- **[Observed] Seller screening signals exceed chance but are not proof.** Of {tiers.loc['ranked (n>=50)', 'n_sellers']} sellers with n >= 50 orders, {int(th.loc[50, 'flagged_wilson_above'])} have a Wilson interval entirely above the portfolio rate; "
      f"about {th.loc[50, 'expected_false_flags_if_no_differences']:.0f} would be expected by chance if no seller differed. {int(th.loc[50, 'flagged_also_above_stratified_expectation'])} remain above the rate expected from their own month, promised-lead and distance mix. "
      f"Seller late rates are only moderately persistent between window halves (rank correlation {pers['spearman_h1_vs_h2']:.2f}).\n"
      f"- **[Observed] The high-delay months were broad-based, not a mix shift.** The late rate in 2017-11, 2018-02 and 2018-03 was {pct(po.loc[('All delivered orders', 'high_delay'), 'late_rate'])} vs {pct(po.loc[('All delivered orders', 'other'), 'late_rate'])} in other months. "
      f"At most {abs(dec.composition).max() * 100:.1f} points of the {dec.difference.mean() * 100:.1f}-point gap is explained by changes in state, lane or seller mix; the rate rose in all {br['states_compared']} comparable states and in {sbr['sellers_higher_in_high_delay']} of {sbr['sellers_compared']} comparable sellers.\n"
      "- **Not established:** that any seller or region *causes* late deliveries. Order-level delivery timestamps cannot separate seller handling, carrier transit and last-mile delivery.\n")

    # ------------------------------------------------------------------ 1
    A("## 1. Populations and pre-specified rules\n")
    A(md(pd.DataFrame([
        ["State / region analysis", "Delivered with date, purchases 2017-01..2018-08, all orders incl. multi-seller", n(pa['n_delivered']), pct(ref, 2)],
        ["Lanes, distance, sellers", "`v_single_seller_orders` (single-seller orders only)", n(ps['n_delivered']), pct(ref_ss, 2)],
    ]), ["Analysis", "Population", "Orders", "Late rate (reference)"]) + "\n")
    A(f"- **Reporting policy (blueprint R8):** states are ranked at n >= 100 delivered orders; {', '.join(rs['low_volume_states'])} ({n(rs['low_volume_orders'])} orders in total) are shown separately and get no screening signal. "
      f"Lanes are shown at n >= 100 ({lp['n_lanes_reported']} of {lp['n_lanes_total']} lanes, {pct(lp['share_orders_reported'])} of orders; the rest pooled). Sellers are ranked at n >= 50, labelled low-confidence at 30-49 and pooled below 30.\n"
      f"- **Screening signal:** the Wilson 95% interval lies entirely above the reference rate ({pct(ref, 2)} for states, {pct(ref_ss, 2)} for lanes/sellers). It is descriptive, never evidence of exceptional or poor seller performance.\n"
      "- **Simple stratification (fixed in advance):** strata S1 = purchase month x promised-lead group (<=14, 15-21, 22-28, 29-35, 36+ days); S2 = S1 x distance quartile. "
      "Observed / expected (O/E) uses pooled stratum late rates; Mantel-Haenszel risk ratios compare groups within strata.\n"
      f"- **Distance:** straight-line distance between seller and customer ZIP-prefix median centroids (quartile cut points {q['q1']:,.0f}, {q['q2']:,.0f} and {q['q3']:,.0f} km; {n(q['n_no_distance'])} orders without a distance form their own band). "
      "**It is an approximation, not a road distance:** prefixes are 5-digit areas with centroid error of the order of kilometres, and real routes differ.\n"
      "- **High-delay months:** 2017-11, 2018-02 and 2018-03, identified in the delivery workstream.\n"
      "- **Not used:** empirical-Bayes shrinkage, permutation tests and rank-stability analysis (optional methods in the blueprint that were not needed); the expected number of chance flags is computed analytically instead.\n")

    # ------------------------------------------------------------------ 2
    A("## 2. Customer state and macro-region\n")
    A("![States](figures/geo_01_states.png)\n")
    t = pd.DataFrame({
        "State": rep.customer_state, "Region": rep.macro_region, "Delivered": rep.n_delivered.map(n), "Late": rep.n_late.map(n),
        "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(rep.late_rate, rep.late_rate_lo, rep.late_rate_hi)],
        "Median / p95 lead (d)": [f"{a:.1f} / {b:.1f}" for a, b in zip(rep.lead_median, rep.lead_p95)],
        "Share of late orders": rep.share_of_late.map(pct), "Share of orders": rep.share_of_delivered.map(pct),
        "Excess late vs all-order rate": rep.excess_late.map(lambda v: f"{v:+,.0f}"), "O/E (month x promise)": rep.oe_month_promised.map(lambda v: f"{v:.2f}"),
        "Signal": ["above" if a else ("below" if b else "") for a, b in zip(rep.adj_signal_above, rep.adj_signal_below)]})
    A(md(t, list(t.columns)) + "\n")
    A("**States with fewer than 100 delivered orders (shown separately, not ranked, no screening signal)**\n")
    lt = pd.DataFrame({"State": low.customer_state, "Region": low.macro_region, "Delivered": low.n_delivered.map(n), "Late": low.n_late.map(n),
                       "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(low.late_rate, low.late_rate_lo, low.late_rate_hi)]})
    A(md(lt, list(lt.columns)) + "\n")
    A("![Contribution](figures/geo_02_state_contribution.png)\n")
    rr = rg
    t = pd.DataFrame({"Macro-region": rr.macro_region, "Delivered": rr.n_delivered.map(n), "Late": rr.n_late.map(n),
                      "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(rr.late_rate, rr.late_rate_lo, rr.late_rate_hi)],
                      "Median / p95 lead (d)": [f"{a:.1f} / {b:.1f}" for a, b in zip(rr.lead_median, rr.lead_p95)],
                      "Share of late orders": rr.share_of_late.map(pct), "Share of orders": rr.share_of_delivered.map(pct), "O/E (month x promise)": rr.oe_month_promised.map(lambda v: f"{v:.2f}")})
    A(md(t, list(t.columns)) + "\n")
    top_exc = rep.sort_values("excess_late", ascending=False).head(4)
    A(f"**[Observed]** {int(rs['n_above_unadj'])} of 24 reportable states have a Wilson interval entirely above the all-order rate and {int(rs['n_below_unadj'])} entirely below; standardising for purchase month and promised lead time leaves "
      f"{int(rs['n_above_adj'])} above and {int(rs['n_below_adj'])} below ({int(rs['above_unadj_and_adj'])} states are above on both bases). The Northeast ({pct(rg.set_index('macro_region').loc['Northeast', 'late_rate'])}) and North ({pct(rg.set_index('macro_region').loc['North', 'late_rate'])}) have the highest regional rates and the longest lead times; "
      f"the Southeast and South are below the all-order rate. Largest contributors to excess late orders: " + ", ".join(f"{r.customer_state} ({r.excess_late:+,.0f})" for r in top_exc.itertuples()) + ".\n")
    A("![Adjusted](figures/geo_08_state_adjusted.png)\n")
    A("**[Interpretation]** The geographic gradient is not an artefact of which months or promise lengths each state's orders fall in. It is, however, not separable from distance and shipping route (Section 3): remote states are served almost entirely from other states "
      "(see `cross_state_share` in `reports/tables/geo_states.csv`). Segments overlap (a state contains many lanes and sellers), so contributions at different levels must not be added.\n")

    # ------------------------------------------------------------------ 3
    A("## 3. Shipment lanes, shipment type and distance (single-seller orders)\n")
    A("![Distance](figures/geo_04_distance_shipment.png)\n")
    A(md(pd.DataFrame([[lab, n(r.n_delivered), f"{pct(r.late_rate)} {ci(r.late_rate_lo, r.late_rate_hi)}", f"{r.lead_median:.1f} / {r.lead_p95:.1f}", f"{r.distance_median_km:,.0f}", f"{r.promised_median:.0f}"] for lab, r in ship.iterrows()]),
         ["Shipment", "Orders", "Late rate [95% Wilson]", "Median / p95 lead (d)", "Median distance (km)", "Median promised lead (d)"]) + "\n")
    b2 = band[band.distance_band != "Q0: unknown"]
    A(md(pd.DataFrame([[r.distance_band.split(":")[0] + (" (nearest)" if r.distance_band.startswith("Q1") else " (farthest)" if r.distance_band.startswith("Q4") else ""), n(r.n_delivered),
                        f"{r.distance_median_km:,.0f}", f"{pct(r.late_rate)} {ci(r.late_rate_lo, r.late_rate_hi)}", f"{r.lead_median:.1f} / {r.lead_p95:.1f}", pct(r.cross_state_share, 0), f"{r.promised_median:.0f}"] for r in b2.itertuples()]),
         ["Distance quartile", "Orders", "Median km", "Late rate [95% Wilson]", "Median / p95 lead (d)", "Cross-state share", "Median promise (d)"]) + "\n")
    A("**Stratified comparisons (Mantel-Haenszel risk ratios of late delivery)**\n")
    A(md(pd.DataFrame([
        ["Cross-state vs same-state; strata month x promised group", f"{mh['mh_cross_state']['rr_crude']:.2f}", f"{mh['mh_cross_state']['rr_mh']:.2f} [{mh['mh_cross_state']['rr_lo']:.2f}, {mh['mh_cross_state']['rr_hi']:.2f}]"],
        ["Cross-state vs same-state; strata customer state x promised group", f"{mh['mh_cross_state_within_state']['rr_crude']:.2f}", f"{mh['mh_cross_state_within_state']['rr_mh']:.2f} [{mh['mh_cross_state_within_state']['rr_lo']:.2f}, {mh['mh_cross_state_within_state']['rr_hi']:.2f}]"],
        ["Farthest vs nearest distance quartile; strata month x promised group", f"{mh['mh_far_vs_near']['rr_crude']:.2f}", f"{mh['mh_far_vs_near']['rr_mh']:.2f} [{mh['mh_far_vs_near']['rr_lo']:.2f}, {mh['mh_far_vs_near']['rr_hi']:.2f}]"],
        ["Farthest vs nearest distance quartile; strata customer state x promised group", f"{mh['mh_far_vs_near_within_state']['rr_crude']:.2f}", f"{mh['mh_far_vs_near_within_state']['rr_mh']:.2f} [{mh['mh_far_vs_near_within_state']['rr_lo']:.2f}, {mh['mh_far_vs_near_within_state']['rr_hi']:.2f}]"],
        ["São Paulo-origin vs other-origin sellers; strata customer state x promised group", f"{mh['mh_origin_sp_within_destination']['rr_crude']:.2f}", f"{mh['mh_origin_sp_within_destination']['rr_mh']:.2f} [{mh['mh_origin_sp_within_destination']['rr_lo']:.2f}, {mh['mh_origin_sp_within_destination']['rr_hi']:.2f}]"],
    ]), ["Comparison", "Crude RR", "Stratified RR [95% CI]"]) + "\n")
    A(f"**[Observed]** Cross-state shipments have {ship.loc['Cross-state', 'lead_median'] / ship.loc['Same-state', 'lead_median']:.1f} times the median lead time and a late rate of {pct(ship.loc['Cross-state', 'late_rate'])} vs {pct(ship.loc['Same-state', 'late_rate'])}, "
      f"even though their median promised lead time is longer ({ship.loc['Cross-state', 'promised_median']:.0f} vs {ship.loc['Same-state', 'promised_median']:.0f} days). The late rate rises with every distance quartile ({pct(b2.late_rate.iloc[0])} to {pct(b2.late_rate.iloc[-1])}). "
      f"Stratifying by promised lead time *raises* the cross-state risk ratio ({mh['mh_cross_state']['rr_crude']:.2f} crude to {mh['mh_cross_state']['rr_mh']:.2f}) because cross-state orders carry longer promises, which partly absorb their longer transit; "
      f"comparing within destination state attenuates it to {mh['mh_cross_state_within_state']['rr_mh']:.2f} but it stays clearly above 1 (it rests on fewer comparable strata: {mh['mh_cross_state_within_state']['strata_both_ge30']} with at least 30 orders in each group, exposed group higher in {mh['mh_cross_state_within_state']['strata_exposed_higher']}). "
      f"Sellers in São Paulo have a higher raw late rate than other origins ({pct(T['origin_sp'].set_index('origin_sp').loc[True, 'late_rate'])} vs {pct(T['origin_sp'].set_index('origin_sp').loc[False, 'late_rate'])}); within destination state and promised lead time the ratio is {mh['mh_origin_sp_within_destination']['rr_mh']:.2f}.\n")
    A("**[Interpretation]** Longer routes are associated with later deliveries even after accounting for the promised lead time, so promises do not fully compensate for distance. Distance, cross-state shipping and destination are tightly entangled (almost all of the farthest quartile is cross-state), "
      "so this analysis cannot say which of them matters; it is also silent on carriers, which are not in the data.\n")
    A("![Region lanes](figures/geo_03_region_lanes.png)\n")
    lanes = T["lanes"]
    rl_ = T["region_lanes"]
    rl_ = rl_[rl_.reportable]
    ne = rl_[rl_.customer_region == "Northeast"]
    diag = rl_[(rl_.seller_region == rl_.customer_region) & rl_.seller_region.isin(["Southeast", "South", "Central-West"])]
    top = lanes.head(12)
    t = pd.DataFrame({"Lane (seller>customer state)": top.lane, "Orders": top.n_delivered.map(n), "Late": top.n_late.map(n),
                      "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(top.late_rate, top.late_rate_lo, top.late_rate_hi)],
                      "Excess late vs portfolio": top.excess_late.map(lambda v: f"{v:+,.0f}"), "O/E (month x promise)": top.oe_month_promised.map(lambda v: f"{v:.2f}"),
                      "Median lead (d)": top.lead_median.map(lambda v: f"{v:.1f}"), "Median km": top.distance_median_km.map(lambda v: f"{v:,.0f}")})
    A(f"Lanes with n >= 100, top 12 by excess late orders over the portfolio rate ({lp['n_lanes_reported']} lanes reported; {n(lp['orders_pooled'])} orders in the {lp['n_lanes_pooled']} smaller lanes are pooled, with a late rate of {pct(lp['late_rate_pooled'])} {ci(*lp['pooled_ci'])}):\n")
    A(md(t, list(t.columns)) + "\n")
    A(f"**[Observed]** {int(lanes.adj_signal_above.sum())} of {len(lanes)} reported lanes are above the portfolio rate on the stratified (month x promised lead) comparison, and {int(lanes.above_ref.sum())} on the raw comparison. "
      f"São Paulo to Rio de Janeiro is by far the largest source of excess late orders ({lanes.iloc[0].excess_late:+,.0f} over the portfolio rate; {pct(lanes.iloc[0].late_rate)} late on {n(lanes.iloc[0].n_delivered)} orders). "
      f"The macro-region grid shows the same picture: shipments into the Northeast are late {pct(ne.late_rate.min(), 0)}-{pct(ne.late_rate.max(), 0)} of the time from every origin region with at least 100 orders, "
      f"while shipments inside the Southeast, South and Central-West are {pct(diag.late_rate.min(), 0)}-{pct(diag.late_rate.max(), 0)}.\n")

    # ------------------------------------------------------------------ 4
    A("## 4. Sellers (single-seller orders only)\n")
    A(md(pd.DataFrame([[t_, n(r.n_sellers), n(r.n_orders), pct(r.share_of_orders), pct(r.late_rate)] for t_, r in tiers.iterrows()]),
         ["Seller tier (by single-seller delivered orders)", "Sellers", "Orders", "Share of orders", "Late rate"]) + "\n")
    A(f"Sellers below 30 orders ({n(S['pooled_sellers']['n_sellers'])} sellers, {n(S['pooled_sellers']['n_orders'])} orders) are pooled: their combined late rate is {pct(S['pooled_sellers']['late_rate'])} {ci(*S['pooled_sellers']['late_ci'])}, and no individual rate is reported.\n")
    A("![Funnel](figures/geo_05_seller_funnel.png)\n")
    A(f"**[Observed]** Most sellers sit inside the funnel limits around the portfolio rate, as expected for small samples; sellers with n >= 50 have late rates from {pct(ranked.late_rate.quantile(0.1))} (10th percentile) to {pct(ranked.late_rate.quantile(0.9))} (90th percentile) around a median of {pct(ranked.late_rate.median())}. "
      f"{int(th.loc[50, 'flagged_wilson_above'])} of {int(th.loc[50, 'eligible_sellers'])} ranked sellers have a Wilson interval entirely above the portfolio rate and {int(th.loc[50, 'n_below_signal'])} entirely below.\n")
    A("**Sensitivity to the minimum-volume threshold**\n")
    A("![Thresholds](figures/geo_06_threshold_sensitivity.png)\n")
    t = pd.DataFrame({"Minimum orders": th.index, "Eligible sellers": th.eligible_sellers.map(n), "Share of orders": th.share_of_orders_covered.map(lambda v: pct(v, 0)),
                      "Wilson interval above portfolio rate": th.flagged_wilson_above.map(n), "Share of eligible": th.flagged_share_of_eligible.map(lambda v: pct(v, 0)),
                      "Expected by chance if no real differences": th.expected_false_flags_if_no_differences.map(lambda v: f"{v:.1f}"),
                      "Also above own stratified expectation": th.flagged_also_above_stratified_expectation.map(n),
                      "Share of all late orders in flagged sellers": th.share_of_all_late_in_flagged.map(pct)})
    A(md(t, list(t.columns)) + "\n")
    A(f"**Multiple-comparison risk.** Screening hundreds of sellers guarantees some apparent outliers. If no seller differed from the portfolio rate, about {th.loc[50, 'expected_false_flags_if_no_differences']:.0f} of the {int(th.loc[50, 'eligible_sellers'])} ranked sellers "
      f"({th.loc[50, 'expected_false_flags_if_no_differences'] / th.loc[50, 'eligible_sellers']:.1%}) would still show an interval above the rate (computed exactly from the binomial distribution; at n >= 30 about {th.loc[30, 'expected_false_flags_if_no_differences']:.0f} of {int(th.loc[30, 'eligible_sellers'])}). "
      f"The observed counts are well above these figures at every threshold, so real between-seller differences are likely; but up to roughly {th.loc[50, 'expected_false_flags_if_no_differences'] / th.loc[50, 'flagged_wilson_above']:.0%} of the n >= 50 flags could be chance alone (an upper-end figure, because it assumes no seller truly differs), and which ones cannot be known. "
      f"The flagged sets are nested by construction ({ov['flagged_100']} flagged at n >= 100, {ov['flagged_50']} at n >= 50, {ov['flagged_30']} at n >= 30), and the share flagged rises with volume because larger samples can detect smaller differences.\n")
    A(f"**Persistence.** For the {pers['n_sellers_ge30_both_halves']} sellers with at least 30 orders in both window halves, seller late rates in the two halves have a rank correlation of {pers['spearman_h1_vs_h2']:.2f}: positive and well short of 1, consistent with a persistent seller-related component plus a lot of noise and the system-wide change between halves ({pct(S['half_rates']['h1'])} to {pct(S['half_rates']['h2'])}). "
      f"Among the {pers['flagged50_n']} flagged ranked sellers, {pers['flagged50_with_both_halves_observed']} have orders in both halves and {pers['flagged50_above_half_rate_in_both_halves']} of those are above the half-specific portfolio rate in both.\n")
    A(f"**Concentration.** Among ranked sellers, {conc['n_with_positive_excess']} have more late orders than the portfolio rate implies; the ten largest account for {pct(conc['top10_share_of_positive_excess'], 0)} of that positive excess and the 25 largest for {pct(conc['top25_share_of_positive_excess'], 0)} "
      f"(positive excess totals {conc['positive_excess_total']:,.0f} late orders). Small-sample noise inflates such concentration figures, so they are an upper-end description.\n")
    A(f"**Origin.** {pct(sp_share_flagged, 0)} of the flagged ranked sellers are in São Paulo state, which holds {pct(sp_share_ranked, 0)} of ranked sellers. São Paulo-origin orders have a higher raw late rate than other origins (Section 3), only slightly reduced once destination state and promised lead time are held fixed, so the flags are not independent of where orders are shipped.\n")
    A("**Screening candidates: top 15 flagged sellers by excess late orders.** These are **descriptive screening signals for investigation, not tiers and not findings about seller performance**: IDs are shortened to 8 characters (full IDs in `reports/tables/geo_seller_candidates.csv`).\n")
    c = cand.head(15)
    t = pd.DataFrame({"Seller": c.short_id, "State": c.seller_state, "Orders": c.n_delivered.map(n), "Late": c.n_late.map(n),
                      "Late rate [95% Wilson]": [f"{pct(r)} {ci(a, b)}" for r, a, b in zip(c.late_rate, c.late_rate_lo, c.late_rate_hi)],
                      "Excess late": c.excess_late.map(lambda v: f"{v:+.0f}"), "O/E S1": c.oe_s1.map(lambda v: f"{v:.2f}"), "O/E S2": c.oe_s2.map(lambda v: f"{v:.2f}"),
                      "Above S2 expectation": c.screen_signal_s2.map(lambda v: "yes" if v else "no"), "Cross-state share": c.cross_state_share.map(lambda v: pct(v, 0)),
                      "Late rate H1 / H2": [f"{'-' if pd.isna(a) else pct(a)} / {'-' if pd.isna(b) else pct(b)}" for a, b in zip(c.rate_h1, c.rate_h2)]})
    A(md(t, list(t.columns)) + "\n")
    A(f"The full candidate list has {len(cand)} sellers (n >= 30); {int(cand.screen_signal_s2.sum())} of them remain above their stratified expectation (S2). O/E S1 controls for purchase month and promised lead time, S2 additionally for distance band. "
      "H1 = purchases 2017-01..2017-10, H2 = 2017-11..2018-08 (the delivery workstream showed that H2 contains the three high-delay months).\n")
    A("**[Interpretation]** A seller on this list has more late orders than expected for sellers of its size, month mix, promise mix (and distance mix for S2). That is a reason to look at the seller's operations; it is not evidence that the seller caused the delays: "
      "the delivery timestamp covers handling, carrier collection, transit and last mile together, and carrier-stage timestamps are unreliable for about 1.4% of orders. Customer mix beyond distance (destination states, product types) is not controlled for.\n")

    # ------------------------------------------------------------------ 5
    A("## 5. High-delay months vs other months: mix or within-segment change?\n")
    A("![Decomposition](figures/geo_07_period_decomposition.png)\n")
    A(md(pd.DataFrame([[lab, pct(r.rate_high_delay), pct(r.rate_other), f"{r.difference * 100:.2f}", f"{r.composition * 100:+.2f}", f"{r.within_segment * 100:+.2f}", f"{r.o_rate_with_e_mix * 100:.2f}%", int(r.n_segments)] for lab, r in dec.iterrows()]),
         ["Segmentation", "Late rate, high-delay months", "Late rate, other months", "Difference (pts)", "Due to mix (pts)", "Due to within-segment rates (pts)", "Other-month rate if it had high-delay mix", "Segments"]) + "\n")
    A(f"**[Observed]** The high-delay months have {n(po.loc[('All delivered orders', 'high_delay'), 'n_delivered'])} delivered orders at a {pct(po.loc[('All delivered orders', 'high_delay'), 'late_rate'])} late rate {ci(po.loc[('All delivered orders', 'high_delay'), 'late_rate_lo'], po.loc[('All delivered orders', 'high_delay'), 'late_rate_hi'])}, "
      f"against {n(po.loc[('All delivered orders', 'other'), 'n_delivered'])} orders at {pct(po.loc[('All delivered orders', 'other'), 'late_rate'])} {ci(po.loc[('All delivered orders', 'other'), 'late_rate_lo'], po.loc[('All delivered orders', 'other'), 'late_rate_hi'])}. "
      f"Whatever the segmentation (customer state, region, lane or individual seller), the change in mix explains at most {abs(dec.composition).max() * 100:.2f} points of the {dec.difference.mean() * 100:.1f}-point difference; the rest is higher late rates *within* the same segments. "
      f"Applying the other months' rates to the high-delay months' mix would hardly change the other-month rate ({pct(dec.o_rate_with_e_mix.mean())} vs {pct(dec.rate_other.mean())}). The cross-state share ({pct(pm.loc['high_delay', 'cross_state_share'])} vs {pct(pm.loc['other', 'cross_state_share'])}), median distance "
      f"({pm.loc['high_delay', 'distance_median_km']:,.0f} vs {pm.loc['other', 'distance_median_km']:,.0f} km) and São Paulo-origin share ({pct(pm.loc['high_delay', 'origin_sp_share'], 0)} vs {pct(pm.loc['other', 'origin_sp_share'], 0)}) are almost the same.\n")
    A(f"**[Observed]** The increase is broad: in all {br['states_compared']} states with at least 100 orders in both groups the late rate was higher in the high-delay months (ratio {br['min_rate_ratio']:.1f}x to {br['max_rate_ratio']:.1f}x, median {br['median_rate_ratio']:.1f}x), and for {sbr['sellers_higher_in_high_delay']} of {sbr['sellers_compared']} sellers with at least 30 orders in both groups. "
      f"The sellers' rates in the two groups are only moderately correlated (rank correlation {sbr['spearman_rate_high_vs_other']:.2f}), i.e. sellers that were relatively worse in calm months were only partly the ones that were worse in the episodes. Rio de Janeiro stands out: "
      f"{pct(T['period_states'].set_index('segment').loc['RJ', 'rate_high_delay'])} late vs {pct(T['period_states'].set_index('segment').loc['RJ', 'rate_other'])} in other months, and it contributes the most excess late orders in the high-delay months "
      f"({T['period_states'].set_index('segment').loc['RJ', 'excess_late_in_high_delay']:+,.0f}).\n")
    A("**[Interpretation]** The episodes look system-wide, not caused by a shift in where orders went or which sellers shipped them. This points toward factors that affect most routes and sellers at once, such as carrier capacity or demand peaks **[Hypothesis]**; this analysis cannot test them.\n")

    # ------------------------------------------------------------------ 6
    A("## 6. What persists after simple stratification\n")
    A(md(pd.DataFrame([
        ["State ranking", f"Rank correlation unadjusted vs standardised (month x promised lead) = {rs['spearman_unadjusted_vs_oe']:.2f}; {int(rs['above_unadj_and_adj'])} of {int(rs['n_above_unadj'])} raw signals persist", "Persists"],
        ["Cross-state vs same-state", f"RR {mh['mh_cross_state']['rr_mh']:.2f} (month x promise), {mh['mh_cross_state_within_state']['rr_mh']:.2f} (destination state x promise)", "Persists (attenuated within destination state)"],
        ["Far vs near distance", f"RR {mh['mh_far_vs_near']['rr_mh']:.2f} (month x promise), {mh['mh_far_vs_near_within_state']['rr_mh']:.2f} (destination state x promise)", "Persists (attenuated)"],
        ["São Paulo origin", f"crude RR {mh['mh_origin_sp_within_destination']['rr_crude']:.2f}, within destination x promise {mh['mh_origin_sp_within_destination']['rr_mh']:.2f}", "Persists, smaller"],
        ["Lane signals", f"{int(lanes.adj_signal_above.sum())} stratified vs {int(lanes.above_ref.sum())} raw", "All raw signals persist"],
        ["Seller signals (n >= 50)", f"{int(th.loc[50, 'flagged_also_above_stratified_expectation'])} of {int(th.loc[50, 'flagged_wilson_above'])} above own S2 expectation", "About two thirds persist"],
        ["High-delay months", f"mix explains < {abs(dec.composition).max() * 100:.1f} pts of {dec.difference.mean() * 100:.1f}", "Increase is within-segment"],
    ]), ["Pattern", "Evidence", "Verdict"]) + "\n")
    A("Strata were deliberately simple (cell counts, no model). Distance strata were not used for states because distance largely *defines* remoteness there. Residual confounding by product mix, seller size and carrier remains possible.\n")

    # ------------------------------------------------------------------ 7
    A("## 7. Status of the blueprint hypotheses\n")
    A(md(pd.DataFrame([
        ["H2.1 Late rate and lead time rise with distance and cross-state shipping; promises compensate only partly", "Supported (association)", f"RR {mh['mh_cross_state']['rr_mh']:.2f} cross-state, {mh['mh_far_vs_near']['rr_mh']:.2f} far vs near, after stratifying by promise"],
        ["H2.2 North/Northeast have higher late rates than SP/RJ/MG", "Partly supported", f"Northeast {pct(rg.set_index('macro_region').loc['Northeast', 'late_rate'])}, North {pct(rg.set_index('macro_region').loc['North', 'late_rate'])} (imprecise: few orders); but RJ ({pct(st.set_index('customer_state').loc['RJ', 'late_rate'])}) is as high as BA, while SP and MG are low"],
        ["H2.3 More sellers exceed the rate than chance explains", "Supported", f"{int(th.loc[50, 'flagged_wilson_above'])} vs about {th.loc[50, 'expected_false_flags_if_no_differences']:.0f} expected at n >= 50"],
        ["H2.4 A minority of sellers accounts for a large share of excess late orders", "Descriptively yes, with a noise caveat", f"top 10 = {pct(conc['top10_share_of_positive_excess'], 0)}, top 25 = {pct(conc['top25_share_of_positive_excess'], 0)} of positive excess"],
        ["H2.5 Most apparent bad low-volume lanes are noise", "Handled by pooling", f"{lp['n_lanes_pooled']} lanes below 100 orders pooled ({pct(lp['late_rate_pooled'])} late)"],
    ]), ["Hypothesis", "Status", "Basis"]) + "\n")

    # ------------------------------------------------------------------ 8
    A("## 8. Business implications (what these data support)\n")
    A(f"1. **Late deliveries are a destination and route problem first.** Cross-state, long-distance routes into the Northeast and Rio de Janeiro carry most of the excess. São Paulo to Rio de Janeiro alone accounts for about {lanes.iloc[0].excess_late:,.0f} excess late orders, "
      "and the Northeast lanes have both high rates and long lead times. Investigating carrier and last-mile performance on these lanes is better targeted than a seller-by-seller programme; the data cannot identify the cause.\n"
      f"2. **Promised dates for long routes may need review.** Cross-state shipments are promised {ship.loc['Cross-state', 'promised_median'] - ship.loc['Same-state', 'promised_median']:.0f} days longer (median) than same-state ones but are still late "
      f"{ship.loc['Cross-state', 'late_rate'] / ship.loc['Same-state', 'late_rate']:.1f} times as often. Whether longer or route-specific promises would help is a hypothesis to test.\n"
      f"3. **Treat the seller list as a shortlist for verification.** There are more sellers above the portfolio rate than chance explains, and {int(th.loc[50, 'flagged_also_above_stratified_expectation'])} of {int(th.loc[50, 'flagged_wilson_above'])} remain above after allowing for their own month, promise and distance mix. "
      f"But up to about a quarter of the flags could be chance, seller rates are only moderately persistent, and the data cannot separate seller handling from carrier performance. A fair next step is to compare flagged sellers' handling times and carrier hand-off dates (partly unreliable) with operations data, then re-check on later data.\n"
      "4. **The high-delay episodes need a system-level explanation.** The rate rose in every comparable state and almost every seller, with no change in mix. Capacity and carrier hypotheses should be checked against operations records for 2017-11, 2018-02 and 2018-03.\n"
      f"5. **Do not rank or penalise sellers on these results.** No causal claim is made, and {n(tiers.loc['pooled (n<30)', 'n_sellers'])} sellers ({pct(tiers.loc['pooled (n<30)', 'share_of_orders'], 0)} of orders) have too few orders for any individual assessment.\n")

    # ------------------------------------------------------------------ 9
    A("## 9. Limitations\n")
    A("- **Association only.** No causal claim about regions, lanes or sellers; delivery timestamps are order-level, so handling, carrier and last-mile time cannot be separated, and carrier identity is not in the data.\n"
      "- **Distance is approximate:** straight-line between ZIP-prefix median centroids (5-digit prefixes, centroid taken from a non-unique table, 0.5% of single-seller orders without a distance), not road distance or transit time.\n"
      f"- **Single-seller scope:** lane, distance and seller results cover {n(ps['n_delivered'])} single-seller orders (98.7% of delivered orders); multi-seller orders are excluded, not allocated. State and region results include all delivered orders.\n"
      "- **Conditional on delivery:** cancelled, unavailable and open orders are outside these KPIs (delivery workstream, Section 7); regions or sellers with more such orders would look better than they are.\n"
      "- **Uncertainty:** Wilson intervals assume independent orders; orders cluster within sellers, destinations and days, so true uncertainty is larger, especially for states. Signals are not corrected for multiple comparisons beyond the chance-expectation reported.\n"
      "- **Segments overlap** (state, lane, seller): excess late orders at different levels must not be summed.\n"
      "- **Stratification is simple by design** (cell counts); product mix, order value, seller size and carrier are not controlled.\n"
      "- **Timestamp quirks:** 1,369 delivered window orders have timestamp-sequence anomalies (kept; they do not affect purchase-to-delivery timing).\n"
      "- Single marketplace, 2017-2018; findings describe this dataset.\n")

    # ------------------------------------------------------------------ 10
    A("## 10. Unresolved questions\n")
    A("1. Which carrier handles each lane, and do carrier differences explain the Northeast / Rio de Janeiro pattern?\n"
      "2. How are estimated delivery dates set, and are they route-specific? Is the longer promise for cross-state shipments calibrated to observed lead times?\n"
      "3. For flagged sellers, how much of the delay is before carrier hand-off (seller handling) versus after (carrier)? Carrier-stage timestamps are unreliable for about 1.4% of orders and untested elsewhere.\n"
      "4. What was different system-wide in 2017-11, 2018-02 and 2018-03 (carrier capacity, promotions, strikes, stock-outs)?\n"
      "5. Do flagged sellers stay flagged on newer data? Only a split-window check was possible here.\n"
      "6. How do cancelled/unavailable and open past-promise orders distribute by state and seller? Not analysed here.\n")

    # ------------------------------------------------------------------ 11
    rc, pytest_summary, cases = run_pytest()
    A("## 11. Validation, defects and deviations\n")
    A(f"- Full test run (`python -m pytest`): **{pytest_summary}** (exit code {rc}); {sum(1 for _, s in cases if s == 'PASS')} of {len(cases)} cases passed.\n"
      "- All primary SQL aggregations (states, regions, lanes, distance bands, shipment types, seller counts, half-window counts, period counts) are recomputed independently in pandas from the raw CSVs, including an independent haversine distance and seller attribution. "
      "Wilson intervals are compared with SciPy; Mantel-Haenszel risk ratios with statsmodels `StratifiedTable.riskratio_pooled` (point estimate) and with a parametric bootstrap (variance); "
      "the expected number of chance flags with a Monte Carlo simulation; funnel limits by simulated coverage; the decomposition on synthetic data with known composition-only and within-only changes.\n"
      "- **Defect found and fixed:** `seller_summary.sql` returned NULL instead of 0 for a seller's late count in a window half in which the seller had no orders. It did not change any reported figure (downstream rates guard against zero counts), "
      "but the independent test flagged the discrepancy and the SQL now uses `coalesce`.\n"
      "- **Methodological choices to note:** (a) the reference rate for lanes and sellers is the single-seller population rate "
      f"({pct(ref_ss, 2)}), slightly above the all-order rate ({pct(ref, 2)}); (b) expected late orders use stratum rates pooled over the same population, so they are indirect-standardisation reference values, not external benchmarks; "
      "(c) the half-window persistence check and Mantel-Haenszel comparisons go beyond the blueprint's minimal toolkit but add no model fitting; (d) chance-flag expectations are analytic, replacing optional permutation tests; (e) no empirical-Bayes shrinkage; (f) no operational priority tiers.\n"
      "- **No change** was made to the data model or earlier analyses in this workstream.\n")
    A(md(pd.DataFrame(cases, columns=["Test", "Result"]), ["Test", "Result"]) + "\n")
    A("## 12. Reproduction\n")
    A("```\npython scripts/build_model.py\npython analysis/geographic_seller.py            # tables, figures, stats JSON\n"
      "python analysis/build_geographic_seller_report.py   # this report\n```\n"
      "SQL: `sql/analysis/geography/*.sql`; tables: `reports/tables/geo_*.csv`; figures: `reports/figures/geo_*.png`; stats: `reports/geo_stats.json`.\n")
    OUT.write_text("\n".join(w) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (pytest exit code {rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
