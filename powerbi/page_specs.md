# Dashboard page specifications (four pages)

Audience: marketplace operations managers. Purpose: show where delivery is late, how customers rate it, and which
segments to investigate first, without implying cause. Measures are defined in `dax_measures.md`; the model and
relationships are in `README.md`.

## Global design rules

- **Canvas** 16:9 (1280 x 720). One title bar (page title left, "Data: purchases Jan 2017 - Aug 2018" right), a slicer strip below it, then a 12-column content grid. At most **six to eight visuals per page**; whitespace is intentional.
- **Population subtitle on every visual group** (small grey text): *"Delivered orders, purchases Jan 2017-Aug 2018, n = <Delivered orders>"* (a card-less text box bound to a measure) or the equivalent for reviews and single-seller orders. **Show n beside every rate.**
- **Colour.** One neutral grey for text and axes; blue `#2A78D6` for the main measure; orange `#EB6834` for late / severe / "Investigate". Evidence tiers: Investigate `#EB6834`, Watch `#EDA100`, No Signal `#2A78D6`, Insufficient Data `#C9C8C1` (hollow or light). Never encode meaning by colour alone: tier is also in tooltips and tables as text. Both light and dark themes should keep >= 4.5:1 text contrast.
- **No dual axes.** Volume and rate are in separate visuals. **No decorative visuals.**
- **"About this page" button** (top right) opens a bookmark overlay listing the page's KPI definitions, populations and limitations (text taken from `dax_measures.md` and from the caveats below). Keep the overlay text short.
- **Slicer behaviour is fixed by the model**: date, state and region slicers act on both fact tables; seller and origin-state slicers act only on `fact_seller_orders`; snapshot visuals ignore all of them. Each page states this next to the slicers.
- **Edit interactions**: turn *off* cross-filtering from KPI cards; keep highlight on elsewhere only where stated.
- Tooltips: report-page tooltips are optional; the field lists below can be used as standard tooltips.

## Page 1: Executive Overview

**Title:** "Delivery reliability and customer experience at a glance".
**Business question:** *How reliable is delivery overall, how has it moved by purchase month, how many orders never reached delivery, and which segments does the screening flag first?*

**Slicers (top strip):** `dim_date[month_label]` as a between-range slicer (sorted by `month_sort`; default Jan 2017 to Aug 2018); `dim_state[macro_region]` (list, multi-select). Note beside them: "Slicers filter the KPI cards, trend and outcome mix. The right-hand snapshot panel is a fixed-period snapshot and does not respond."

| # | Visual | Type | Fields / measures | Notes |
|---|---|---|---|---|
| 1 | KPI row | 6 cards | `Delivered orders`, `Late-delivery rate`, `Severe-late rate`, `Median lead time (days)`, `Average review score (P0)`, `Low-score share (P0)` | Card subtitle = population; tooltip = definition, numerator, denominator |
| 2 | Late-delivery trend | line chart | axis `dim_date[month_label]`; lines `Late-delivery rate`, `Severe-late rate` | Tooltip: `Delivered orders`, `Late-delivery rate, Wilson lower`/`upper`, `Median lead time (days)`. Add `Delivered orders` as the secondary visual (3) |
| 3 | Monthly volume | column chart | axis `dim_date[month_label]`; value `Delivered orders` | Placed directly under (2), same x axis, so volume is visible without a second axis |
| 4 | Fulfilment outcomes | 100% stacked bar or table | axis `fact_orders[fulfilment_label]`; value `Window orders by outcome` | Subtitle: "All window orders. Delivery KPIs exclude cancelled/unavailable and open orders." |
| 5 | Snapshot: first candidates | table | from `snap_priority_candidates` (visual filters: `snapshot_level` is state or lane; `tier` = Investigate): `snapshot_level`, `segment_label`, `Snapshot: late rate`, `Snapshot: excess late orders`, `tier` | Sort by excess late orders (descending), top 10. Header text: `Note: snapshot visuals`. No sellers here |

**Interactions:** date and region slicers filter 1-4; visual 5 is unaffected. **Interpretation caveats (shown in the overlay):** late rate is a conditional observed rate among orders delivered by 2018-10-17; cancelled, unavailable and open orders are outside it (see visual 4); review scores are voluntary, so averages describe reviewers; the snapshot tier is a screening label, not a cause.

## Page 2: Delivery Performance

**Title:** "Where and how deliveries are late".
**Business question:** *How do lead times, promise errors and late rates vary by cohort, destination, route and distance?*

**Slicers:** `dim_date[month_label]` (range), `dim_state[macro_region]`, `dim_state[state_name]` (customer state); a separate group "Single-seller section only": `dim_origin_state[origin_state_name]`. Note: "Origin-state and seller selections filter only the single-seller visuals (bottom half)."

| # | Visual | Type | Fields / measures | Notes |
|---|---|---|---|---|
| 1 | Promise error distribution | column chart | axis `fact_orders[promise_error_days]` (visual filter -40 to 30, as a whole-number axis), value `Delivered orders` | Conditional colour: error <= 0 blue, > 0 orange; annotation line at 7 ("severe: more than 7 days late"). Subtitle gives the count outside the shown range |
| 2 | Lead time by cohort | line chart | axis `dim_date[month_label]`; lines `Median lead time (days)`, `P95 lead time (days)` | Tooltip: `Delivered orders`, `Median promise error (days)` |
| 3 | Lateness severity | column chart | axis `fact_orders[days_late_band]`; value `Delivered orders` | Data labels as % of total; band names carry their sort prefix |
| 4 | Late rate by state | bar chart | axis `dim_state[state_code]`; value `Late-delivery rate (n >= 100)`; sort descending | Tooltip: `Delivered orders`, `Late orders`, Wilson lower/upper, `Median lead time (days)`, `P95 lead time (days)`. Companion table **"States below 100 delivered orders"**: `state_code`, `Delivered orders`, `Late orders` with a visual filter `Delivered orders < 100` (shown separately, no rate ranking) |
| 5 | Region summary | table | `dim_state[macro_region]`, `Delivered orders`, `Late orders`, `Late-delivery rate`, Wilson lower/upper, `Median lead time (days)` | Sorted by `macro_region_order`; display grouping only |
| 6 | Distance and shipment type (single-seller) | column chart + two cards | axis `fact_seller_orders[distance_band]`, value `Seller: late-delivery rate`; cards `Seller: late rate, cross-state` and `Seller: late rate, same-state` | Subtitle: "Single-seller orders, n = <Seller: eligible orders>. Distance is a straight-line approximation between ZIP-prefix centroids, not road distance." Tooltip: `Seller: eligible orders`, `Seller: median distance (km)`, `Seller: median lead time (days)` |
| 7 | Lanes (single-seller) | table | `fact_seller_orders[lane]`, `Seller: eligible orders`, `Seller: late orders`, `Seller: late rate (n >= 50)`, Wilson lower/upper | Visual filter `Seller: eligible orders >= 100`; lane label means seller state > customer state; sort by late orders |

**Interactions:** all slicers except origin state filter every visual; the origin-state slicer affects 6 and 7 only (a guard text box bound to `Note: seller selection on order-level visuals` shows when it is used). Choosing a state in visual 4 cross-filters 5-7. **Interpretation caveats:** observed association only; lead time and late rate are conditional on delivery; no visual here says why orders are late; carrier is not in the data; with fewer than 100 orders per state or 50 per lane the rate is suppressed.

## Page 3: Customer Experience

**Title:** "How customers rate deliveries (association, not cause)".
**Business question:** *How do review scores differ between on-time and late deliveries, how much of that depends on when the review was written, and how far can the reviews be generalised?*

**Slicers:** `dim_date[month_label]` (range), `dim_state[macro_region]`. Note: "Review measures describe voluntary reviews of single-review orders."

| # | Visual | Type | Fields / measures | Notes |
|---|---|---|---|---|
| 1 | Review KPI row | 6 cards | `Reviewed orders (P0)`, `Average review score (P0)`, `Low-score share (P0)`, `Review coverage (any review row)`, `P0 share of delivered orders`, `Share of P0 reviews written before delivery` | Each card names its population |
| 2 | Score distribution | clustered column | axis `fact_orders[review_score]`, legend `fact_orders[delivery_outcome]` (On time / Late), value `Reviewed orders (P0)` as % of the legend's total | Shows 1-5 stars for on-time vs late orders |
| 3 | **P0 vs P1 comparison** | clustered column + table | axis `fact_orders[days_late_band]`; values `Low-score share (P0)` ("Primary: all single-review orders") and `Low-score share (P1)` ("Sensitivity: reviews written on or after delivery"); table below: `Reviewed orders (P0)`, `Reviewed orders (P1)`, `Average review score (P0)`, `Average review score (P1)` by band | **Both always shown together**; the table makes the P1 counts visible (the 8+ band has almost no P1 reviews) |
| 4 | Review timing | column chart | axis `fact_orders[days_late_band]`; value `Share of P0 reviews written before delivery` | Subtitle: "Reviews created before the recorded delivery date; most are on late orders and were written while the order was overdue." |
| 5 | Dissatisfaction among on-time orders | cards | `Low-score share, on-time orders (P0)`, `Average review score, on-time (P0)` vs `Low-score share, late orders (P0)`, `Average review score, late (P0)` | Reads as "even on-time orders have low-score reviews"; no causal wording |
| 6 | Snapshot: candidate context | table | `snap_priority_candidates` filtered to tier = Investigate and level state/lane: `segment_label`, `Snapshot: low-score rate, on-time orders`, `low_ontime_rate_lo`/`hi`, `early_review_share`, `p0_share_of_delivered` | Header text: `Note: snapshot visuals`. Customer-experience context only; it does **not** feed the tiers |

**Interactions:** slicers filter 1-5; visual 6 is a snapshot. **Interpretation caveats (mandatory text in the overlay):** (1) differences between on-time and late reviews are associations; they are not the effect of lateness; (2) P0 vs P1 differs because of when the review was written (a share of late-order reviews were written before delivery), not because of a measured change in satisfaction; (3) reviews are voluntary and some orders have none or several review rows; (4) P1 can say little about severely late orders because almost none have a review written after delivery; (5) low-score shares for small groups are imprecise.

## Page 4: Operational Priorities

**Title:** "Where to look first: provisional evidence tiers (fixed-period snapshot)".
**Business question:** *Which states, lanes and sellers have the most excess late orders and the highest late rates, how strong is the evidence, and how stable is it?*

**Banner (always visible):** `Note: snapshot visuals` plus `Snapshot: level note`, and the sentence: *"Tiers are provisional screening labels. The 20-order minimum is an operational screening policy, not statistical significance. Nothing here shows what caused the delays."*

**Slicers (snapshot fields only):** `snap_priority_candidates[snapshot_level]` (single-select; default lane), `tier` (multi-select), `display_region`. **Date, state and seller slicers do not apply to this page**; do not sync them here.

| # | Visual | Type | Fields / measures | Notes |
|---|---|---|---|---|
| 1 | **Primary: excess late orders vs late rate** | scatter | X `Snapshot: late rate`, Y `Snapshot: excess late orders`, size `Snapshot: delivered orders`, legend `tier`, details `segment_label` | Visual filter `eligible` = 1 (ranked segments). Error bars via the Analytics pane: X lower/upper = `Snapshot: late rate lower`/`upper`; Y lower/upper = `Snapshot: excess late lower`/`upper` (if the installed version has no scatter error bars, keep the intervals in the tooltip and table). Constant line at X = reference rate and at Y = 20 (labelled "screening policy"). Tooltip: segment, tier, n, late, expected, excess, rate and interval, observed to expected ratio, consistency, tier at thresholds 10/30, tier without 2017-11/2018-02/2018-03, share of late orders in those months |
| 2 | Candidate table | table | `segment_label`, `tier`, `n_delivered`, `n_late`, `late_rate`, `excess_late`, `oe_s1`, `consistency`, `tier_x10`, `tier_x30`, `tier_excl_episodes` | Sort by `excess_late` descending; conditional icon or text colour for tier; sellers show 8-character ids |
| 3 | Threshold sensitivity | stacked column | `snap_tier_counts`: axis `min_excess_late`, values `investigate`, `watch`, `no_signal`; small multiples `snapshot_level` | Highlights that 20 is a provisional policy |
| 4 | Overlap | table | `snap_overlap` summary rows: `label`, `n_orders`, `n_late`, `excess_vs_ref` | Subtitle: "Levels overlap. Never add excess late orders across state, lane and seller." |
| 5 | Level note | text box | `Snapshot: level note` | For sellers it states the "only N of M Investigate sellers persist without the three high-delay months" caveat |
| 6 | **Appendix (secondary), reached by a bookmark button** | scatter | X `excess_late`, Y `excess_low`, size `n_delivered`, legend `tier`; second scatter with `excess_low_after` | Title: "Appendix: excess late vs excess low-score orders. The axes use different populations (delivered vs reviewed orders) and move together." Not part of the main reading order |

**Interactions:** the level slicer drives 1-5; selecting a point in 1 highlights the row in 2; visual 6 is only on the appendix bookmark. **Interpretation caveats:** excess is relative to the level's population late rate (a reference, not a target); intervals assume independent orders; seller results are secondary screening evidence; several sellers' stability is unverified; Northeast-bound lanes are defined by destination state (use `northeast_bound` / `display_region`, never the origin).
