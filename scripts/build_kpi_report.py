"""Render reports/kpi_validation.md from reports/kpi_validation_stats.json.

Usage: python scripts/validate_kpis.py && python scripts/build_kpi_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
s = json.loads((ROOT / "reports" / "kpi_validation_stats.json").read_text(encoding="utf-8"))
q1, q2, q3, q4, q5 = s["q1"], s["q2"], s["q3"], s["q4"], s["q5"]
cl = q2["classification_of_multi_review_orders"]
win, sn, st = s["q6_window_candidates"], s["q6_seller_n"], s["q6_state_n"]
ci = s["q6_ci_halfwidth_pp_at_p08"]
pc = q5["pair_counts"]
n = lambda x: f"{x:,}"  # noqa: E731
below = ", ".join(f"{k} ({v})" for k, v in st["below_100"].items())
w = win["2017-01..2018-08"]

report = f"""# KPI and Methodology Validation

Computed by `scripts/validate_kpis.py` (raw CSVs read-only) and rendered by `scripts/build_kpi_report.py`; raw numbers in `reports/kpi_validation_stats.json`. Primary population unless stated: **delivered orders with an actual delivery timestamp (N = {n(q1['denominator'])})**.

> **Correction to `dataset_feasibility.md`:** the earlier 8.11% late rate used exact timestamps, and the 4.29 vs 2.57 review means were built on a different review treatment. Sections 1 and 3 below supersede them.

## 1. Late-delivery rate

| Definition | Late (numerator) | Denominator | Rate |
|---|---:|---:|---:|
| (a) Calendar date: delivery date > estimated date | {n(q1['late_calendar_n'])} | {n(q1['denominator'])} | **{q1['late_calendar_pct']}%** |
| (b) Exact timestamp: delivery timestamp > estimated timestamp | {n(q1['late_exact_n'])} | {n(q1['denominator'])} | {q1['late_exact_pct']}% |

The estimated delivery date has no time component in the data (all values are 00:00:00), so the exact comparison counts any delivery after midnight at the start of the promised day as late. That adds {n(q1['late_exact_but_not_calendar'])} orders delivered *on* the promised calendar day. **Decision:** use calendar-date comparison (the promise is a date, not an instant). Time zones are not documented; the exact-timestamp figure is retained only as a sensitivity.

## 2. Orders with multiple review rows

- {n(q2['orders_with_multiple_review_rows'])} orders have >1 review row ({n(q2['rows_in_those_orders'])} rows; max {q2['max_rows_per_order']} per order).
- **Exact duplicate rows:** {q2['exact_duplicate_rows_whole_table']} (table-wide). **Repeated `review_id` within the same order:** {q2['repeated_review_id_within_same_order']}. So every extra row has a distinct `review_id`.
- Separately, `review_id` is reused *across* orders: {n(q2['review_ids_appearing_in_multiple_orders'])} IDs appear on more than one order ({n(q2['rows_with_shared_review_id_across_orders'])} rows). `review_id` is not a valid key; use (`order_id`, `review_id`).
- Content of the {n(q2['orders_with_multiple_review_rows'])} multi-review orders: **{cl['same_score_and_text']} same score and text** (distinct IDs, identical content: likely re-submissions/duplicates), **{cl['same_score_different_text']} same score, different text**, **{cl['different_score']} different scores** (ordered by creation date: {q2['different_score_later_higher']} later review higher, {q2['different_score_later_lower']} later lower).
- Timestamps: {q2['multi_orders_with_distinct_creation_dates']} orders have distinct `review_creation_date` values (consistent with possible updates); {q2['multi_orders_with_identical_creation_date']} share one creation date, so order between their rows cannot be established. Creation dates have day precision only, and the data has no field marking a review as a revision, so "update" cannot be confirmed. **The latest review is therefore not treated as authoritative.**

| Delivered orders with delivery date (N = {n(q2['delivered_with_date_denominator'])}) | Count | % |
|---|---:|---:|
| Exactly one review row | {n(q2['delivered_exactly_one_review'])} | {round(100*q2['delivered_exactly_one_review']/q2['delivered_with_date_denominator'],2)} |
| No review | {n(q2['delivered_zero_reviews'])} | {round(100*q2['delivered_zero_reviews']/q2['delivered_with_date_denominator'],2)} |
| Multiple rows (ambiguous) | {n(q2['delivered_multiple_reviews_ambiguous'])} | {round(100*q2['delivered_multiple_reviews_ambiguous']/q2['delivered_with_date_denominator'],2)} |

## 3. Review score by calendar-date lateness (descriptive only)

Population: delivered with date and exactly one review row (N = {n(q3['population'])}); excluded: {n(q3['excluded_zero_reviews'])} with no review, {n(q3['excluded_multiple_reviews'])} with multiple rows.

| Group | Orders | Mean score | 1–2★ share |
|---|---:|---:|---:|
| On time | {n(q3['on_time_n'])} | {q3['on_time_mean']} | {q3['ontime_share_1_2_star_pct']}% |
| Late | {n(q3['late_n'])} | {q3['late_mean']} | {q3['late_share_1_2_star_pct']}% |

Difference {q3['mean_difference']} points. Missing-review rate: late {q3['missing_review_rate_late_pct']}% vs on-time {q3['missing_review_rate_ontime_pct']}% (of {n(q3['late_total_n'])} late and {n(q3['ontime_total_n'])} on-time orders); multi-review rate: late {q3['multi_review_rate_late_pct']}% vs on-time {q3['multi_review_rate_ontime_pct']}%. Late orders are somewhat less likely to have any review, so non-response differs by group. This is an association: it does not isolate lateness from product, seller, region or order-value effects. {n(q3['reviews_created_before_delivery_date_n'])} reviews in this population carry a creation date before the delivery date, so a review may not always reflect the delivery outcome.

## 4. Multi-seller reconciliation

| Measure | Count | Denominator | % |
|---|---:|---:|---:|
| Multi-seller orders | {n(q4['multi_seller_orders'])} | {n(q4['all_orders'])} all orders | {q4['multi_seller_pct_all_orders']}% |
| Multi-seller orders | {n(q4['multi_seller_orders'])} | {n(q4['orders_with_items'])} orders with items | {q4['multi_seller_pct_orders_with_items']}% |
| Delivered (with date) with exactly one seller | {n(q4['delivered_exactly_one_seller'])} | {n(q4['delivered_with_date_denominator'])} | {round(100*q4['delivered_exactly_one_seller']/q4['delivered_with_date_denominator'],2)}% |
| Delivered (with date) with multiple sellers | {n(q4['delivered_multi_seller'])} | {n(q4['delivered_with_date_denominator'])} | {q4['delivered_multi_seller_pct']}% |

All {n(q4['delivered_with_date_denominator'])} delivered-with-date orders have items (0 without), so every one is either single- or multi-seller. The 3 multi-seller orders outside this population are not delivered-with-date.

## 5. Timestamp-sequence violations

{n(q5['orders_with_any_of_five_checked'])} orders have at least one violation among five checked pairs (distinct pair counts below overlap):

| Pair | Violations |
|---|---:|
| Approval < purchase | {pc['approved_lt_purchase']} |
| Carrier handoff < purchase | {pc['carrier_lt_purchase']} (all also have carrier < approval) |
| Carrier handoff < approval | {n(pc['carrier_lt_approved'])} (median gap {q5['carrier_lt_approved_gap_hours_median']} h, p95 {q5['carrier_lt_approved_gap_hours_p95']} h) |
| Customer delivery < purchase | {pc['customer_lt_purchase']} |
| Customer delivery < carrier handoff | {pc['customer_lt_carrier']} |
| Estimated date < purchase | {pc['estimated_lt_purchase']} |

{n(q5['violations_inside_primary_population'])} of the {n(q5['orders_with_any_of_five_checked'])} fall inside the primary population. **Impact on primary KPIs:** purchase-to-delivery uses only purchase and customer-delivery timestamps, which have 0 violations (0 non-positive durations); the promised-date KPI uses delivery vs estimate, with 0 estimates before purchase. Excluding all {n(q5['violations_inside_primary_population'])} flagged orders moves the calendar late rate from {q5['late_rate_calendar_all_pct']}% to {q5['late_rate_calendar_excl_violations_pct']}% (N = {n(q5['late_rate_calendar_excl_violations_denominator'])}), median lead time from {q5['median_days_all']} to {q5['median_days_excl_violations']} days, and mean from {q5['mean_days_all']} to {q5['mean_days_excl_violations']}. **Decision:** keep these orders in the primary KPIs; the anomalies affect the carrier-handoff stage, so exclude only seller-handling-time or carrier-leg metrics (approval→carrier, carrier→customer). Causes are unverified (clock/entry errors are a hypothesis). Separately, {q5['status_conflicts']['delivered_status_no_customer_date']} `delivered` orders lack a delivery date and {q5['status_conflicts']['non_delivered_with_customer_date']} non-delivered orders have one; the former are excluded by definition.

## 6. Proposed analysis populations and reporting rules

**Date window (by purchase month):** 2017-01 to 2018-08. Rationale: 2016 has 267 delivered-with-date orders in 3 sparse months (none in 2016-11); 2018-09 and 2018-10 have 20 orders in total and none delivered with a date (the extract appears to end there, so they are not usable as delivery cohorts). In the window: N = {n(w['n'])}, calendar late rate {w['late_calendar_pct']}% (vs {win['all']['late_calendar_pct']}% for all months), so the window choice barely moves the headline. Delivered share per month rises from ~93% (2017-01) to ~98% (2018-08); this suggests earlier cohorts have more cancellations/unavailability, but I did not investigate cause.

**Populations**
- *Delivery KPIs:* delivered with delivery date, in window ({n(w['n'])}). Report cancellations/unavailability as a separate rate; delivery KPIs exclude them, so state this on every chart.
- *Review analysis:* delivered-in-window orders with exactly one review row; report counts of no-review and multi-review orders alongside.
- *Seller analysis:* single-seller delivered orders in window ({n(sn['window_single_seller_delivered_orders'])}); multi-seller orders excluded from seller scorecards (≈1.3%).
- *Region analysis:* customer state of the order, same delivered-in-window population.

**Proposed minimum-N policy (reporting convention, not a statistical guarantee):**
- States: show rates when n ≥ 100 delivered orders. {st['ge_100']} of {st['states']} states qualify; below threshold: {below} (flag as "low volume"). The sampling margin shrinks with n: for an illustrative 8% event rate, a 95% normal-approximation half-width is ≈{ci['30']} pp at n=30, {ci['50']} at 50, {ci['100']} at 100 and {ci['300']} at 300; this ignores clustering and non-random selection.
- Sellers: rank/compare only at n ≥ 50 single-seller delivered orders ({sn['ge_50']} sellers, covering {sn['orders_in_sellers_ge_50_pct']}% of single-seller orders). Show 30–49 ({sn['ge_30']} sellers have n ≥ 30) as "low confidence", and pool or suppress below 30. At n ≥ 100 there are {sn['ge_100']} sellers.
- Prefer intervals or shrinkage over bare rates for any ranking.

## 7. Recommended KPI definitions

| KPI | Definition | Population |
|---|---|---|
| Late-delivery rate | share with `DATE(delivered_customer) > DATE(estimated_delivery)` | Delivered with date, window |
| Lead time (days) | `delivered_customer − purchase`, report median and p95 | Same |
| Promise error (days) | `DATE(delivered_customer) − DATE(estimated)`, signed | Same |
| Mean review score | mean of `review_score` | Single-review delivered orders; show coverage % |
| Review coverage | share with ≥1 review; multi-review share reported separately | Delivered, window |
| Cancellation/unavailable rate | share of window orders with those statuses | All orders, window |

## 8. Unresolved limitations
- Whether multi-review orders are updates, duplicates or separate surveys is not determinable; 525 delivered orders are excluded from score KPIs.
- Time zone of timestamps and cause of carrier-handoff inversions are unknown.
- Review non-response differs by lateness, and reviews are voluntary, so scores are not representative of all customers.
- Multi-seller orders are excluded rather than allocated; seller results hold only for single-seller orders.
- The 2017-01 start and n ≥ 50/100 thresholds are judgement-based; sensitivity to them was checked only for the late rate by window.
- This validation does not use SQL; the `sql-analytics-reviewer` skill was therefore not applied. Join cardinalities were verified with pandas counts.
"""
(ROOT / "reports" / "kpi_validation.md").write_text(report, encoding="utf-8")
print("wrote reports/kpi_validation.md;", len(report.split()), "words")
