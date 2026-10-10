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
    ISCROSSFILTERED ( dim_seller[seller_id] ) || ISCROSSFILTERED ( dim_origin_state[origin_state_code] ),
    "A seller or origin-state selection filters only the single-seller visuals. Order-level visuals on this page are not filtered by it."
)

Note: snapshot visuals =
"Fixed-period snapshot (purchases 2017-01 to 2018-08). Date, state and seller slicers do not recalculate these figures."
```

`Note: seller selection...` uses `ISCROSSFILTERED` on a **column**, which works in all Power BI Desktop versions (passing a whole table to `ISFILTERED` is not accepted everywhere) and also fires when the slicer is on another column of the same table, for example `seller_label`.

## 6. Snapshot measures (disconnected tables `snap_*`)

These return stored values for **exactly one segment**. `MAX` alone would silently return the largest value when several segments are in filter context (a total row, no level slicer, a multi-select), so each segment-specific measure returns blank unless `COUNTROWS ( snap_priority_candidates ) = 1`. In a table or scatter visual grouped by `segment_label` (with a `snapshot_level` filter) every row has one segment and shows its value; total rows and multi-selected cards show blank by design. They are **not** recalculated from facts and ignore the date, state and seller slicers (no relationships). Only the `snapshot_level` and `tier` slicers (which belong to the snapshot table itself) affect them.

```dax
Snapshot: late rate =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[late_rate] ) )

Snapshot: late rate lower =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[late_rate_lo] ) )

Snapshot: late rate upper =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[late_rate_hi] ) )

Snapshot: excess late orders =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[excess_late] ) )

Snapshot: excess late lower =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[excess_late_lo] ) )

Snapshot: excess late upper =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[excess_late_hi] ) )

Snapshot: delivered orders =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[n_delivered] ) )

Snapshot: observed to expected ratio (month x promise) =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[oe_s1] ) )

Snapshot: low-score rate, on-time orders =
IF ( COUNTROWS ( snap_priority_candidates ) = 1, MAX ( snap_priority_candidates[low_ontime_rate] ) )


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

## 8. Objects in the saved dashboard that are not in this dictionary (manual confirmation required)

The saved report `powerbi/Olist-Operations-Intelligence.pbix` (four pages) uses seven measures and six calculated columns that were
created in Power BI Desktop after the Stage A dictionary above was written. They were found by inspecting the report definition inside
the `.pbix`, which lists the fields each visual uses. The `.pbix` data model, which holds the DAX and Power Query text, is a
compressed binary that cannot be read outside Power BI Desktop, and no formula for these objects exists elsewhere in this repository.
**Their formulas are therefore deliberately not reproduced here.** The tables record what is verifiable (name, home table, where it is
used) and what must still be checked. The *Closest Stage A measure* and *Related physical column* entries are guesses from the name
only and are **not** verified equivalences.

Sections 1-7 are unchanged. The automated tests validate that dictionary and its Python equivalents, **not** the objects below.
The exported dashboard PDF (`powerbi/visualisation/Olist-Operations-Intelligence.pdf`) shows displayed values that agree with the
documented anchors (96,203 delivered, 6.79% late, 2.97% severe, 95,037 single-review orders, 10 / 9 / 8 Investigate segments, threshold 20),
which is supporting evidence but not a check of the formulas.

### 8.1 Measures (table `_Measures`)

| Dashboard measure | Used on | Role in visual | Closest Stage A measure (guess) | To confirm |
|---|---|---|---|---|
| `Late Rate Minimum 100` | Page 2 Delivery Performance | bar chart value | `Late-delivery rate (n >= 100)` | formula, population, 100-order rule |
| `Seller Late Rate` | Page 2 Delivery Performance | two column charts | `Seller: late-delivery rate` | reads `fact_seller_orders` only |
| `Review Score Share` | Page 3 Customer Experience | chart tooltip | none | numerator, denominator, population (P0 or P1) |
| `Investigate States` | Page 4 Operational Priorities | card (shows 10) | `Snapshot: Investigate segments`, level = state | counts tier = Investigate at state level |
| `Investigate Lanes` | Page 4 Operational Priorities | card (shows 9) | same, level = lane | as above |
| `Investigate Sellers` | Page 4 Operational Priorities | card (shows 8) | same, level = seller | as above |
| `Screening Threshold` | Page 4 Operational Priorities | card (shows 20) | `Snapshot: primary screening threshold` | read from `snap_metadata`, not hard-coded |

### 8.2 Calculated columns

| Dashboard column | Table | Used on | Role in visual | Related physical column (guess) | To confirm |
|---|---|---|---|---|---|
| `Delay Period Group` | `dim_date` | Page 4 | column chart axis | `year_month` | grouping; high-delay months are 2017-11, 2018-02, 2018-03 |
| `Delivery Time Group` | `fact_orders` | Page 2 | column chart axis | `lead_time_days` | source column and bin edges |
| `Delivery Status Label` | `fact_orders` | Page 3 | axis of two column charts | `delivery_outcome` | label mapping |
| `Review Star Label` | `fact_orders` | Page 3 | column chart axis | `review_score` | label text and sort order |
| `Distance Range` | `fact_seller_orders` | Page 2 | chart axis | `distance_band` | bin edges vs quartile cut points (184 / 434 / 799 km) |
| `Shipment Type` | `fact_seller_orders` | Page 2 | column chart axis | `is_cross_state` | label mapping |

Calculated columns created in Desktop are not rebuilt by `scripts/export_powerbi.py`. Anyone rebuilding the report from the package
must recreate them; consider moving them into the export or Power Query so the package alone reproduces the report.

### 8.3 How to close this section

In Power BI Desktop, select each object, copy its formula from the formula bar into the tables (or into a `dax` block that follows
the dictionary conventions), run the reconciliation checklist in `README.md` section 9, then remove the "guess" columns. Do not
describe any object above as validated until its values are reconciled.
