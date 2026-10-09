# KPI and Methodology Validation

Computed by `scripts/validate_kpis.py` (raw CSVs read-only) and rendered by `scripts/build_kpi_report.py`; raw numbers in `reports/kpi_validation_stats.json`. Primary population unless stated: **delivered orders with an actual delivery timestamp (N = 96,470)**.

> **Correction to `dataset_feasibility.md`:** the earlier 8.11% late rate used exact timestamps, and the 4.29 vs 2.57 review means were built on a different review treatment. Sections 1 and 3 below supersede them.

## 1. Late-delivery rate

| Definition | Late (numerator) | Denominator | Rate |
|---|---:|---:|---:|
| (a) Calendar date: delivery date > estimated date | 6,534 | 96,470 | **6.77%** |
| (b) Exact timestamp: delivery timestamp > estimated timestamp | 7,826 | 96,470 | 8.11% |

The estimated delivery date has no time component in the data (all values are 00:00:00), so the exact comparison counts any delivery after midnight at the start of the promised day as late. That adds 1,292 orders delivered *on* the promised calendar day. **Decision:** use calendar-date comparison (the promise is a date, not an instant). Time zones are not documented; the exact-timestamp figure is retained only as a sensitivity.

## 2. Orders with multiple review rows

- 547 orders have >1 review row (1,098 rows; max 3 per order).
- **Exact duplicate rows:** 0 (table-wide). **Repeated `review_id` within the same order:** 0. So every extra row has a distinct `review_id`.
- Separately, `review_id` is reused *across* orders: 789 IDs appear on more than one order (1,603 rows). `review_id` is not a valid key; use (`order_id`, `review_id`).
- Content of the 547 multi-review orders: **225 same score and text** (distinct IDs, identical content: likely re-submissions/duplicates), **120 same score, different text**, **202 different scores** (ordered by creation date: 83 later review higher, 119 later lower).
- Timestamps: 392 orders have distinct `review_creation_date` values (consistent with possible updates); 155 share one creation date, so order between their rows cannot be established. Creation dates have day precision only, and the data has no field marking a review as a revision, so "update" cannot be confirmed. **The latest review is therefore not treated as authoritative.**

| Delivered orders with delivery date (N = 96,470) | Count | % |
|---|---:|---:|
| Exactly one review row | 95,299 | 98.79 |
| No review | 646 | 0.67 |
| Multiple rows (ambiguous) | 525 | 0.54 |

## 3. Review score by calendar-date lateness (descriptive only)

Population: delivered with date and exactly one review row (N = 95,299); excluded: 646 with no review, 525 with multiple rows.

| Group | Orders | Mean score | 1–2★ share |
|---|---:|---:|---:|
| On time | 88,946 | 4.291 | 9.25% |
| Late | 6,353 | 2.273 | 62.36% |

Difference 2.019 points. Missing-review rate: late 2.34% vs on-time 0.55% (of 6,534 late and 89,936 on-time orders); multi-review rate: late 0.43% vs on-time 0.55%. Late orders are somewhat less likely to have any review, so non-response differs by group. This is an association: it does not isolate lateness from product, seller, region or order-value effects. 4,940 reviews in this population carry a creation date before the delivery date, so a review may not always reflect the delivery outcome.

## 4. Multi-seller reconciliation

| Measure | Count | Denominator | % |
|---|---:|---:|---:|
| Multi-seller orders | 1,278 | 99,441 all orders | 1.29% |
| Multi-seller orders | 1,278 | 98,666 orders with items | 1.3% |
| Delivered (with date) with exactly one seller | 95,195 | 96,470 | 98.68% |
| Delivered (with date) with multiple sellers | 1,275 | 96,470 | 1.32% |

All 96,470 delivered-with-date orders have items (0 without), so every one is either single- or multi-seller. The 3 multi-seller orders outside this population are not delivered-with-date.

## 5. Timestamp-sequence violations

1,382 orders have at least one violation among five checked pairs (distinct pair counts below overlap):

| Pair | Violations |
|---|---:|
| Approval < purchase | 0 |
| Carrier handoff < purchase | 166 (all also have carrier < approval) |
| Carrier handoff < approval | 1,359 (median gap 17.17 h, p95 86.84 h) |
| Customer delivery < purchase | 0 |
| Customer delivery < carrier handoff | 23 |
| Estimated date < purchase | 0 |

1,373 of the 1,382 fall inside the primary population. **Impact on primary KPIs:** purchase-to-delivery uses only purchase and customer-delivery timestamps, which have 0 violations (0 non-positive durations); the promised-date KPI uses delivery vs estimate, with 0 estimates before purchase. Excluding all 1,373 flagged orders moves the calendar late rate from 6.77% to 6.85% (N = 95,097), median lead time from 10.217 to 10.272 days, and mean from 12.558 to 12.618. **Decision:** keep these orders in the primary KPIs; the anomalies affect the carrier-handoff stage, so exclude only seller-handling-time or carrier-leg metrics (approval→carrier, carrier→customer). Causes are unverified (clock/entry errors are a hypothesis). Separately, 8 `delivered` orders lack a delivery date and 6 non-delivered orders have one; the former are excluded by definition.

## 6. Proposed analysis populations and reporting rules

**Date window (by purchase month):** 2017-01 to 2018-08. Rationale: 2016 has 267 delivered-with-date orders in 3 sparse months (none in 2016-11); 2018-09 and 2018-10 have 20 orders in total and none delivered with a date (the extract appears to end there, so they are not usable as delivery cohorts). In the window: N = 96,203, calendar late rate 6.79% (vs 6.77% for all months), so the window choice barely moves the headline. Delivered share per month rises from ~93% (2017-01) to ~98% (2018-08); this suggests earlier cohorts have more cancellations/unavailability, but I did not investigate cause.

**Populations**
- *Delivery KPIs:* delivered with delivery date, in window (96,203). Report cancellations/unavailability as a separate rate; delivery KPIs exclude them, so state this on every chart.
- *Review analysis:* delivered-in-window orders with exactly one review row; report counts of no-review and multi-review orders alongside.
- *Seller analysis:* single-seller delivered orders in window (94,931); multi-seller orders excluded from seller scorecards (≈1.3%).
- *Region analysis:* customer state of the order, same delivered-in-window population.

**Proposed minimum-N policy (reporting convention, not a statistical guarantee):**
- States: show rates when n ≥ 100 delivered orders. 24 of 27 states qualify; below threshold: AC (80), AP (67), RR (40) (flag as "low volume"). The sampling margin shrinks with n: for an illustrative 8% event rate, a 95% normal-approximation half-width is ≈9.7 pp at n=30, 7.5 at 50, 5.3 at 100 and 3.1 at 300; this ignores clustering and non-random selection.
- Sellers: rank/compare only at n ≥ 50 single-seller delivered orders (412 sellers, covering 75.14% of single-seller orders). Show 30–49 (614 sellers have n ≥ 30) as "low confidence", and pool or suppress below 30. At n ≥ 100 there are 201 sellers.
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
