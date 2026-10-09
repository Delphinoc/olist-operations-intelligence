# DAX measure dictionary

Every measure below is validated against an independent SQL calculation on the DuckDB model (see
`reports/powerbi_preparation_validation.md`). Create them in a dedicated empty table named `_Measures`
(Home > Enter data > one empty column, then hide the column) so they are easy to find.

**Conventions**

- Table names are used without quotes (`fact_orders[is_late]`); column names are exactly those of the CSV files.
- All flags are numeric 0/1 columns. A blank flag means "not applicable" (for example `is_late` is blank unless the order was delivered with a date).
- **Every ratio uses `DIVIDE`**, so a zero or blank denominator returns blank instead of an error.
- **Populations.** *Window* = purchased 2017-01..2018-08 (`in_window = 1`). *Delivered orders* = delivered with a delivery date and purchased in the window (`is_delivery_kpi_eligible = 1`, 96,203 orders). *P0* = delivered in window with exactly one review row (95,037). *P1* = P0 minus reviews created strictly before the recorded delivery date (90,103). Orders purchased outside the window stay in the table so the 99,441 total reconciles; every delivery KPI excludes them through its flag.
- **Date filtering.** `dim_date` relates to `purchase_date` only. A date slicer therefore selects purchase cohorts; delivery dates are never used to filter.
- **Seller measures exist only on `fact_seller_orders`** and carry the prefix `Seller:`. A seller, origin-state or lane selection never filters `fact_orders` measures.
- **Snapshot measures** (prefix `Snapshot:`) read the fixed-period tables, which have no relationships and are not recalculated by any slicer.
- Intervals are 95% Wilson intervals, `z = 1.959963984540054`.

## 1. Order-level measures (`fact_orders`)

| Measure | Population | Numerator | Denominator | Notes |
|---|---|---|---|---|
| All orders in file | every order in the file | - | - | 99,441 with no filters |
| Orders in window | purchased in window | - | - | 99,092 |
| Delivered orders | delivery KPI population | - | - | 96,203 |
| Late orders | delivery KPI population | late (calendar date) | - | 6,531 |
| On-time orders | delivery KPI population | delivered - late | - | |
| Late-delivery rate | delivery KPI population | Late orders | Delivered orders | 6.7888% (reported as 6.79%) |
| Severe-late orders / rate | delivery KPI population | more than 7 calendar days late | Delivered orders | 2,860 / 2.97% |
| Median / P95 lead time | delivery KPI population | - | - | purchase to delivery, fractional days |
| Median promise error / promised lead time | delivery KPI population | - | - | calendar days; negative error = early |

```dax
All orders in file = COUNTROWS ( fact_orders )

Orders in window =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1 )

Delivered orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1 )

Late orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1, fact_orders[is_late] = 1 )

On-time orders = [Delivered orders] - [Late orders]

Late-delivery rate = DIVIDE ( [Late orders], [Delivered orders] )

Severe-late orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1, fact_orders[is_severe_late] = 1 )

Severe-late rate = DIVIDE ( [Severe-late orders], [Delivered orders] )

Median lead time (days) =
CALCULATE ( MEDIAN ( fact_orders[lead_time_days] ), fact_orders[is_delivery_kpi_eligible] = 1 )

P95 lead time (days) =
CALCULATE ( PERCENTILE.INC ( fact_orders[lead_time_days], 0.95 ), fact_orders[is_delivery_kpi_eligible] = 1 )

Median promise error (days) =
CALCULATE ( MEDIAN ( fact_orders[promise_error_days] ), fact_orders[is_delivery_kpi_eligible] = 1 )

Median promised lead time (days) =
CALCULATE ( MEDIAN ( fact_orders[promised_lead_days] ), fact_orders[is_delivery_kpi_eligible] = 1 )
```

`PERCENTILE.INC` uses linear interpolation, the same definition as the DuckDB `quantile_cont` used in the SQL reference.

### Intervals and reporting policy

```dax
Late-delivery rate, Wilson lower =
VAR n = [Delivered orders]
VAR p = [Late-delivery rate]
VAR z = 1.959963984540054
RETURN
    IF ( n > 0, DIVIDE ( p + z ^ 2 / ( 2 * n ) - z * SQRT ( p * ( 1 - p ) / n + z ^ 2 / ( 4 * n ^ 2 ) ), 1 + z ^ 2 / n ) )

Late-delivery rate, Wilson upper =
VAR n = [Delivered orders]
VAR p = [Late-delivery rate]
VAR z = 1.959963984540054
RETURN
    IF ( n > 0, DIVIDE ( p + z ^ 2 / ( 2 * n ) + z * SQRT ( p * ( 1 - p ) / n + z ^ 2 / ( 4 * n ^ 2 ) ), 1 + z ^ 2 / n ) )

Late-delivery rate (n >= 100) =
IF ( [Delivered orders] >= 100, [Late-delivery rate] )
```

`Late-delivery rate (n >= 100)` applies the minimum-volume policy to state visuals: a state below 100 delivered orders shows blank instead of a noisy rate (the low-volume states are listed in a separate table). The Wilson formula is the one used by the Python analysis; the only "division" operators in this file are inside Wilson expressions, where the denominators are guarded by `IF ( n > 0, ... )`.

## 2. Fulfilment outcomes (all orders in the window; never blended with delivery KPIs)

| Measure | Population | Denominator for the share |
|---|---|---|
| Cancelled/unavailable orders | window orders, class `cancelled_unavailable` | Orders in window |
| Open past-promise orders | window orders, non-terminal status and estimated date before 2018-10-17 | Orders in window |
| Open not-yet-due orders | window orders, non-terminal status, estimated date on/after 2018-10-17 | Orders in window |
| Delivered-status, no-date orders | window orders, status delivered but no delivery date | Orders in window |

```dax
Cancelled/unavailable orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1, fact_orders[fulfilment_class] = "cancelled_unavailable" )

Open past-promise orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1, fact_orders[fulfilment_class] = "open_past_promise" )

Open not-yet-due orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1, fact_orders[fulfilment_class] = "open_not_yet_due" )

Delivered-status, no-date orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1, fact_orders[fulfilment_class] = "delivered_status_no_date" )

Cancelled/unavailable share = DIVIDE ( [Cancelled/unavailable orders], [Orders in window] )

Open past-promise share = DIVIDE ( [Open past-promise orders], [Orders in window] )

Window orders by outcome =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[in_window] = 1 )
```

For the outcome-mix visual use `fact_orders[fulfilment_label]` on the axis and `Window orders by outcome` as the value (plus "show value as % of grand total" for shares). Cancelled/unavailable orders are never counted as open or overdue.

## 3. Reviews (`fact_orders`): primary P0 and sensitivity P1

| Measure | Population | Numerator | Denominator |
|---|---|---|---|
| Reviewed orders (P0) | delivered in window, exactly one review row | - | - |
| P0 share of delivered orders | P0 | P0 | Delivered orders |
| Review coverage (any review row) | delivery KPI population | orders with at least one review row | Delivered orders |
| No-review / Multi-review orders | delivery KPI population | zero / several review rows | - |
| Average review score (P0) | P0 | sum of scores | P0 |
| Low-score orders / share (P0) | P0 | score 1-2 | P0 |
| Reviewed orders / average score / low-score share (P1) | P1 | | P1 |
| Share of P0 reviews written before delivery | P0 | review created strictly before the recorded delivery date | P0 |
| Low-score share, on-time / late (P0) | P0 split by lateness | score 1-2 | P0 orders of that group |

```dax
Reviewed orders (P0) =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1 )

P0 share of delivered orders = DIVIDE ( [Reviewed orders (P0)], [Delivered orders] )

Review coverage (any review row) =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1, fact_orders[review_row_count] >= 1 ),
    [Delivered orders]
)

No-review orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1, fact_orders[review_row_count] = 0 )

Multi-review orders =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_delivery_kpi_eligible] = 1, fact_orders[review_row_count] > 1 )

Average review score (P0) =
CALCULATE ( AVERAGE ( fact_orders[review_score] ), fact_orders[is_review_p0] = 1 )

Low-score orders (P0) =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[review_score] <= 2 )

Low-score share (P0) = DIVIDE ( [Low-score orders (P0)], [Reviewed orders (P0)] )

Reviewed orders (P1) =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p1] = 1 )

Average review score (P1) =
CALCULATE ( AVERAGE ( fact_orders[review_score] ), fact_orders[is_review_p1] = 1 )

Low-score share (P1) =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p1] = 1, fact_orders[review_score] <= 2 ),
    [Reviewed orders (P1)]
)

Reviews written before delivery (P0) =
CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[review_before_delivery_flag] = 1 )

Share of P0 reviews written before delivery =
DIVIDE ( [Reviews written before delivery (P0)], [Reviewed orders (P0)] )

Low-score share, on-time orders (P0) =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 0, fact_orders[review_score] <= 2 ),
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 0 )
)

Low-score share, late orders (P0) =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 1, fact_orders[review_score] <= 2 ),
    CALCULATE ( COUNTROWS ( fact_orders ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 1 )
)

Average review score, on-time (P0) =
CALCULATE ( AVERAGE ( fact_orders[review_score] ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 0 )

Average review score, late (P0) =
CALCULATE ( AVERAGE ( fact_orders[review_score] ), fact_orders[is_review_p0] = 1, fact_orders[is_late] = 1 )
```

**How P0 and P1 are shown.** Always side by side with explicit labels ("Primary: all single-review orders (P0)" and "Sensitivity: reviews written on or after delivery (P1)"), never behind a toggle, with the sentence from the page specification about review timing. P1 removes reviews created before the recorded delivery date; most of those are on late orders and were written while the order was overdue. The difference between P0 and P1 is a description of review timing; it is **not** an effect size and has no causal reading. Severely late orders have almost no P1 reviews, so P1 by lateness band must show its order counts.

Review coverage appears in two forms on purpose: "any review row" (99.3% of delivered orders) and the single-review share used as the P0 population (98.8%). Label them separately; do not substitute one for the other.

## 4. Seller measures (`fact_seller_orders` only)

Population: eligible single-seller orders (delivered with date, purchased in window, exactly one seller) = 94,931 rows. These measures respond to the date, customer-state, origin-state and seller slicers (all of which relate to this table) and never to anything on `fact_orders` alone.

```dax
Seller: eligible orders = COUNTROWS ( fact_seller_orders )

Seller: late orders = CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_late] = 1 )

Seller: late-delivery rate = DIVIDE ( [Seller: late orders], [Seller: eligible orders] )

Seller: severe-late rate =
DIVIDE ( CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_severe_late] = 1 ), [Seller: eligible orders] )

Seller: median lead time (days) = MEDIAN ( fact_seller_orders[lead_time_days] )

Seller: P95 lead time (days) = PERCENTILE.INC ( fact_seller_orders[lead_time_days], 0.95 )

Seller: cross-state share =
DIVIDE ( CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_cross_state] = 1 ), [Seller: eligible orders] )

Seller: median distance (km) = MEDIAN ( fact_seller_orders[distance_km] )

Seller: late rate, cross-state =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_cross_state] = 1, fact_seller_orders[is_late] = 1 ),
    CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_cross_state] = 1 )
)

Seller: late rate, same-state =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_cross_state] = 0, fact_seller_orders[is_late] = 1 ),
    CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_cross_state] = 0 )
)

Seller: reviewed orders (P0) =
CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_review_p0] = 1 )

Seller: average review score (P0) =
CALCULATE ( AVERAGE ( fact_seller_orders[review_score] ), fact_seller_orders[is_review_p0] = 1 )

Seller: low-score share (P0) =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_seller_orders ), fact_seller_orders[is_review_p0] = 1, fact_seller_orders[review_score] <= 2 ),
    [Seller: reviewed orders (P0)]
)

Seller: late-delivery rate, Wilson lower =
VAR n = [Seller: eligible orders]
VAR p = [Seller: late-delivery rate]
VAR z = 1.959963984540054
RETURN
    IF ( n > 0, DIVIDE ( p + z ^ 2 / ( 2 * n ) - z * SQRT ( p * ( 1 - p ) / n + z ^ 2 / ( 4 * n ^ 2 ) ), 1 + z ^ 2 / n ) )

Seller: late-delivery rate, Wilson upper =
VAR n = [Seller: eligible orders]
VAR p = [Seller: late-delivery rate]
VAR z = 1.959963984540054
RETURN
    IF ( n > 0, DIVIDE ( p + z ^ 2 / ( 2 * n ) + z * SQRT ( p * ( 1 - p ) / n + z ^ 2 / ( 4 * n ^ 2 ) ), 1 + z ^ 2 / n ) )

Seller: late rate (n >= 50) =
IF ( [Seller: eligible orders] >= 50, [Seller: late-delivery rate] )
```

`Seller: late rate (n >= 50)` applies the seller ranking policy (sellers with 30-49 orders are low-confidence, below 30 pooled). Filtering by customer state: the *single-seller* orders to a state are fewer than all delivered orders to it (multi-seller orders carry no seller); expect `Seller: eligible orders` to be slightly smaller than `Delivered orders` in the same state (about 1.3% overall; 12,154 vs 12,310 for RJ).

## 5. Filter-context guards (text measures for cards)

```dax
Note: seller selection on order-level visuals =
IF (
    ISFILTERED ( dim_seller ) || ISFILTERED ( dim_origin_state ),
    "A seller or origin-state selection filters only the single-seller visuals. Order-level visuals on this page are not filtered by it."
)

Note: snapshot visuals =
"Fixed-period snapshot (purchases 2017-01 to 2018-08). Date, state and seller slicers do not recalculate these figures."
```

## 6. Snapshot measures (disconnected tables `snap_*`)

These return stored values; with one segment per row they use `MAX`. They are **not** recalculated from facts and ignore the date, state and seller slicers (no relationships). Only the `snapshot_level` and `tier` slicers (which belong to the snapshot table itself) affect them.

```dax
Snapshot: late rate = MAX ( snap_priority_candidates[late_rate] )
Snapshot: late rate lower = MAX ( snap_priority_candidates[late_rate_lo] )
Snapshot: late rate upper = MAX ( snap_priority_candidates[late_rate_hi] )
Snapshot: excess late orders = MAX ( snap_priority_candidates[excess_late] )
Snapshot: excess late lower = MAX ( snap_priority_candidates[excess_late_lo] )
Snapshot: excess late upper = MAX ( snap_priority_candidates[excess_late_hi] )
Snapshot: delivered orders = MAX ( snap_priority_candidates[n_delivered] )
Snapshot: observed to expected ratio (month x promise) = MAX ( snap_priority_candidates[oe_s1] )
Snapshot: low-score rate, on-time orders = MAX ( snap_priority_candidates[low_ontime_rate] )

Snapshot: Investigate segments =
CALCULATE ( COUNTROWS ( snap_priority_candidates ), snap_priority_candidates[tier] = "Investigate" )

Snapshot: primary screening threshold =
VALUE ( LOOKUPVALUE ( snap_metadata[value], snap_metadata[key], "primary_excess_threshold" ) )
```

```dax
Snapshot: level note =
VAR lvl = SELECTEDVALUE ( snap_priority_candidates[snapshot_level] )
RETURN
    SWITCH (
        lvl,
        "seller", LOOKUPVALUE ( snap_metadata[description], snap_metadata[key], "seller_evidence" )
                  & " Treat sellers as secondary screening evidence, not a ranking.",
        "lane", "Lane labels read seller state > customer state. Northeast-bound means the destination (customer) state is in the Northeast.",
        "state", "State results include all delivered orders (multi-seller orders included).",
        "Select one level."
    )
```

`Snapshot: primary screening threshold` returns 20, an **operational screening policy**, not a statistical significance level. Do not use `SUM` or `AVERAGE` on snapshot columns across segments of different levels: the levels overlap, so excess late orders must never be added across state, lane and seller (`snap_overlap` shows the double count).

## 7. Measures deliberately not provided

- No live "excess late orders" measure under slicers: excess depends on a fixed reference rate and the tiers on pre-specified rules; recomputing them under arbitrary slicers would imply a recalculated tier. Use the snapshot.
- No seller measure on `fact_orders`, and no cross-fact measure that combines both tables.
- No measure uses the estimated or actual delivery date as a filter.
