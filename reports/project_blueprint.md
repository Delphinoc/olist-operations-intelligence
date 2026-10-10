# Project Blueprint: Olist Operations Intelligence

**Status:** DESIGN DOCUMENT (Revision 3). Revision 2 incorporated the first reviewer decisions; Revision 3 (after the Workstream 4 review) corrects the Operational Priorities design and the Power BI plan. At the time of Revision 3 the DuckDB data model and the four analysis workstreams are implemented and validated (see `reports/`); the Power BI dashboard and the optional ML extension were **not** built at that time. **Update (2026-10):** a four-page Power BI report (`powerbi/Olist-Operations-Intelligence.pbix`) now exists, built from the Stage A package; the ML extension was not built and is out of scope. The design text below is the historical plan and has not been rewritten.
**Inputs:** `reports/dataset_feasibility.md`, `reports/kpi_validation.md`, `reports/profile_stats.json`, `reports/kpi_validation_stats.json`.

**Labelling convention used throughout**

| Tag | Meaning |
|---|---|
| **[F]** | Confirmed dataset fact, computed in the feasibility or KPI-validation phase (source figure cited). |
| **[D]** | Design decision (including the reviewer's decisions); a choice, not an observed result. |
| **[H]** | Hypothesis: to be tested, not assumed. |
| **[TBC]** | Number or property not yet computed; must be established before it is relied on. |

Where `kpi_validation.md` and `dataset_feasibility.md` disagree, `kpi_validation.md` is authoritative (it states this itself: late rate 6.77% not 8.11%; review means 4.291 / 2.273 not 4.29 / 2.57).

**Revision 3 summary of changes (after the Workstream 4 review).** (a) The primary Power BI priority visual is *excess late orders versus late-delivery rate* (with order volume, Wilson intervals and evidence tier); the excess-late versus excess-low-score matrix is a secondary/appendix visual. (b) Customer-experience measures (low-score rate among on-time orders, review-timing limitations) stay on the Customer Experience page and out of the evidence tiers. (c) The minimum excess of 20 late orders is documented as an *operational screening policy*, not statistical significance. (d) Seller results are *secondary screening evidence*: only 2 of 8 Investigate sellers remain Investigate when the three high-delay months are excluded. (e) Lane labels are *seller state > customer state*; "Northeast-bound" is defined by the destination state only. No validated KPI definition or population was changed.

**Revision 2 summary of changes.** Statistical scope reduced to a core set (Section 2.3); empirical-Bayes shrinkage, permutation tests and rank-stability analysis demoted to optional, justification-gated methods. Power BI reduced to four pages with a single-seller view for seller KPIs (Section 5). Delivered-only late rate is described as a *conditional observed rate*, not a lower bound (Section 2.1). Cancelled/unavailable outcomes are now separate from open past-promise orders (KPIs F1-F4). C8 renamed and its causal reading removed. Cohort-maturity 30 days is a sensitivity assumption. DuckDB fixed as the SQL engine. Severe lateness fixed at > 7 calendar days. Portfolio rate is the primary excess reference. Product category is exploratory; macro-regions are display-only. Phases and validation updated to match.

---

## 1. Business problem and scope

**Objective.** Help e-commerce operations managers understand delivery reliability, customer satisfaction and where operational risk is concentrated, and decide which segments (states, origin-destination lanes, sellers) deserve further investigation first.

**What this project is not.** It is not a causal study, a forecast, or a seller-performance verdict. Outputs are *investigation priorities* backed by uncertainty-aware descriptive statistics. Whether a flagged segment has a real operational problem must be confirmed by operations staff with information not in this dataset.

**Tooling [D].** DuckDB is the SQL analytical engine (reads the raw CSVs directly; builds the analytical model and views). Python (pandas, plus a standard statistics library) performs statistical analysis and writes result tables. Independent pandas recomputation is used for validation.

**Fixed analytical rules (inherited from validation; retained unchanged)**

| # | Rule | Tag |
|---|---|---|
| R1 | Lateness is calendar-date based: `DATE(order_delivered_customer_date) > order_estimated_delivery_date`. The estimate has no time component (all `00:00:00`). Exact-timestamp lateness (8.11%) is a sensitivity only. | [F] basis, [D] rule |
| R2 | Analysis window = purchase month 2017-01 to 2018-08. 2016 is sparse (267 delivered orders in 3 months, none in 2016-11); 2018-09/10 have 20 orders and none delivered with a date. | [F] basis, [D] rule |
| R3 | Delivery KPIs use **delivered orders with a delivery timestamp** (N = 96,203 in window). They never include canceled/unavailable/open orders. | [F] N, [D] rule |
| R4 | **All-order fulfilment outcomes** use all window orders and are a *separate* KPI family. Within it, cancelled/unavailable orders are kept apart from open orders that are past their promised date (R9). | [D] |
| R5 | Seller attribution uses **single-seller orders only** (94,931 delivered-in-window; 2,925 sellers). Multi-seller orders (1.3%) are excluded from seller KPIs, not allocated. | [F] N, [D] rule |
| R6 | Primary review analysis uses **orders with exactly one review row**. Zero-review and multi-review orders are counted and reported beside every score KPI. The "latest review" is *not* treated as authoritative. | [F] basis, [D] rule |
| R7 | Timestamp-sequence violations (1,382 orders) stay in primary KPIs; they are excluded only from carrier-leg / seller-handling-time metrics. | [F] basis, [D] rule |
| R8 | Minimum-N: states shown at n ≥ 100 delivered orders (24 of 27 qualify; AC 80, AP 67, RR 40 flagged "low volume"); sellers ranked at n ≥ 50 (412 sellers, 75.14% of single-seller orders), "low confidence" at 30-49, suppressed or pooled below 30. Thresholds are judgement-based and subject to sensitivity. | [F] counts, [D] thresholds |
| R9 | **Fulfilment outcome classes (mutually exclusive, all window orders):** (a) delivered with date, on time; (b) delivered with date, late; (c) cancelled or unavailable; (d) open and past promise (non-terminal status, estimated date earlier than the reference date); (e) open and not yet due; (f) status "delivered" without delivery date (8 in all months **[F]**, a data inconsistency). Cancelled/unavailable orders are never classified as overdue shipments. Reference date = latest timestamp in the extract, 2018-10-17 **[F]**. | [D] |
| R10 | **Severe lateness** = delivery more than 7 calendar days after the estimated date (promise error > 7). | [D] |
| R11 | **Excess orders** use the portfolio rate as the primary reference; adjusted (stratified) expected rates are secondary. | [D] |

**Confirmed headline facts used as reconciliation anchors** (all from the validation reports)

| Fact | Value | Source |
|---|---:|---|
| Delivered with delivery date, all months | 96,470 | KPI q1 |
| Delivered with delivery date, window | 96,203 | KPI q6 |
| Calendar late rate: all months / window | 6.77% / 6.79% | KPI q1, q6 |
| Exact-timestamp late rate (sensitivity) | 8.11% (7,826 orders) | KPI q1 |
| Single-seller delivered-in-window orders / sellers | 94,931 / 2,925 | KPI q6 |
| Sellers with n ≥ 30 / 50 / 100 (window, single-seller) | 614 / 412 / 201 | KPI q6 |
| Review population, all months (delivered, dated, exactly one review) | 95,299 | KPI q2 |
| Mean score on-time vs late (all months, descriptive) | 4.291 vs 2.273 | KPI q3 |
| 1-2★ share on-time vs late | 9.25% vs 62.36% | KPI q3 |
| No-review rate late vs on-time | 2.34% vs 0.55% | KPI q3 |
| Reviews created before delivery date (in the 95,299) | 4,940 | KPI q3 |
| Median lead time / p95 (all months) | 10.22 / 29.27 days | Feasibility |
| Median promised lead time | 23.2 days | Feasibility |
| Canceled + unavailable orders (all months) | 1,234 | Feasibility |

Counts that restrict the review population to the 2017-01..2018-08 window, and window counts of fulfilment classes (c)-(f), have **not** been computed yet [TBC]; Phase 1 must produce them.

---

## 2. Cross-cutting methodological design

### 2.1 Incomplete cohort observation

- **[F]** The extract ends 2018-10-17. Delivered share by purchase month rises from 93.8% (2017-01) to 98.0-98.9% (2018-04..06) and is 97.5% in 2018-08.
- **Interpretation [D].** The delivered-only late rate is a **conditional observed rate**: the share of orders *that were observed as delivered by the extract date* which arrived after the promised date. It is not an estimate of the share of *all* orders that broke their promise, and no direction of difference from that quantity is assumed. Orders that are cancelled, unavailable or still open are not in the denominator, and their delivery performance, had they been delivered, is unknown. The rate is therefore comparable across segments or cohorts only to the extent that the fraction excluded is similar and unrelated to lateness, which cannot be verified from this data.
- **Cohort comparability [H].** Recent cohorts (2018-07/08) have had less time for slow deliveries to complete before the extract, so their conditional rate may differ from that of mature cohorts. The direction is not assumed. The p99 lead time is 46 days **[F]**.
- **Design response [D]:**
  1. Report every time trend **by purchase-month cohort**, always alongside the fulfilment outcome mix (R9) so the excluded share is visible, not hidden.
  2. **Cohort-maturity sensitivity.** Define a cohort as "mature" if its purchase month ended at least 30 days before the extract end. The 30-day figure is a **sensitivity assumption**, chosen to be roughly consistent with the observed p99 lead time; it is **not evidence that observation is complete**. Re-run headline KPIs with alternatives (e.g. 0 / 30 / 60 days) and with the window ending at 2018-07 (known all-months value for 2017-01..2018-07: 6.83%, N = 89,852 **[F]**). Report how much KPIs move, not a verdict of completeness.
  3. Review KPIs: a review can exist only after delivery, so recent cohorts may have had less time to respond. Report coverage by cohort and check stability of score KPIs when 2018-07/08 are dropped.
  4. **[H]** The lower delivered share in early 2017 reflects higher cancellation/unavailability during ramp-up; the cause was not investigated **[F]**. Cross-cohort comparisons of delivered-only KPIs therefore compare differently selected populations; trend charts must say so.

### 2.2 Selection bias

| Source | Effect | Handling [D] |
|---|---|---|
| Delivered-only conditioning | Rates are conditional on observed delivery; selection differs by cohort and possibly by state/seller | Pair with the fulfilment outcome mix (R9); label every delivery chart "delivered orders only" |
| Voluntary reviews (99.3% of delivered orders have one **[F]**, but late orders are ~4× likelier to have none) | Score KPIs describe responders; non-response differs by lateness (2.34% vs 0.55%) | Report coverage beside every score; simple non-response scenario bounds on the late/on-time gap (3.6) |
| Single-seller restriction | Seller results do not generalise to multi-seller orders (1.3%) | Report exclusion count; compare single- vs multi-seller late rate descriptively |
| Single-review restriction | Excludes 525 delivered orders (all months) whose scores may differ systematically | Sensitivity with alternative treatments (earliest / lowest / mean score) |
| Minimum-N rules | Small segments vanish, possibly the worst ones | Show suppressed volume explicitly; pooled "Other / low volume" row |
| Window truncation | 2016 and Sep/Oct 2018 dropped | Window effect already checked for late rate (6.77 vs 6.79%); repeat for each KPI family |

### 2.3 Statistical scope

**Core methods [D]** (these are sufficient for all primary deliverables):

1. **Descriptive statistics:** counts, rates, means, medians, p90/p95, distributions, by cohort and segment.
2. **Wilson score intervals** for every proportion (late rate, low-score share, coverage, cross-state share).
3. **Selected bootstrap intervals,** only where no simple formula exists: medians and p95 of lead time, and the late-vs-on-time score gap (resampling customers, `customer_unique_id`, as clusters). No other bootstrap is part of the core.
4. **Adjusted regression for review associations** (Workstream 3): logistic regression for low score, linear regression for score as a secondary check, with cluster-robust standard errors. Reported as adjusted associations, never as effects.
5. **Volume-aware seller comparisons:** minimum-N tiers (R8), Wilson intervals beside every seller rate, funnel plots with binomial control limits around the portfolio rate, and a rule that a seller is flagged only if the Wilson interval lies entirely above the portfolio rate. This accounts for volume without fitting any hierarchical model.
6. **Multiplicity is handled by caution, not by test machinery:** with hundreds of seller comparisons, some intervals will lie entirely above the portfolio rate by chance (roughly 2.5% of them for 95% intervals if no true differences existed, an approximate reference). Results are labelled "screening flags for investigation". Split-window consistency (flag must hold in both halves of the window) is a simple replication check.

**Optional methods: use only with written justification [D]**

| Method | Allowed only if… | Cost / risk |
|---|---|---|
| Empirical-Bayes (beta-binomial) shrinkage | The Wilson-interval funnel view leaves seller ranking visibly unstable or reviewers need point estimates for small sellers | Extra modelling assumptions; harder to explain |
| Permutation tests | A specific claim (e.g. concentration of excess late orders) cannot be judged against chance with simple reference values | Compute and explanation overhead |
| Rank-stability / bootstrap flag stability | Seller or lane flags are to be presented as a ranked list for action | Adds complexity; the split-window check may suffice |
| Benjamini-Hochberg FDR | A formal significance claim across many segments is made | Not needed if results stay "screening flags" |

The justification, the alternative considered and the result's effect on conclusions must be recorded in the findings report if any of these is used.

### 2.4 Confounders (catalogue)

Available in the data **[F]**: customer state, seller state, same-state flag, approximate distance (99.51% of item rows have both ZIP centroids; centroid median within-prefix SD ≈ 0.72 km), product category (610 products lack one), weight and dimensions (2 missing), price, freight, item count, promised lead time, purchase month and weekday. **Not available:** carrier identity, warehouse, courier SLA, customer expectations, product defect rates, true cause of delay. No causal claim is made; adjustment is used only to show whether a descriptive pattern survives stratification.

### 2.5 Geographic and category grouping

- **Brazilian macro-regions (display grouping only) [D].** North: AC, AP, AM, PA, RO, RR, TO. Northeast: AL, BA, CE, MA, PB, PE, PI, RN, SE. Central-West: DF, GO, MT, MS. Southeast: ES, MG, RJ, SP. South: PR, RS, SC. This is the standard IBGE grouping applied from outside knowledge, not derived from the dataset; it is used only to organise charts and tables. Statistical comparison, minimum-N rules and tiers operate on states.
- **Product category (exploratory dimension) [D].** Order-level `order_category` = the category if all items of the order share one, `mixed` if several, `unknown` if missing (610 products have no category **[F]**). Category is not used in headline KPIs or evidence tiers; it appears in exploratory views and as an extra covariate in a secondary regression.

---

## 3. Workstreams

### Workstream 1: Delivery reliability

**Stakeholder decision.** Is delivery performance acceptable and stable, is the promise (estimated date) well calibrated, and which cohorts or lead-time bands need attention?

**Analytical questions**

- Q1.1 What share of delivered orders arrive after the promised calendar date, and how has that moved by purchase-month cohort?
- Q1.2 How early/late is the typical order relative to the promise, and how long do customers wait?
- Q1.3 Is the promise padded (median promise 23.2 days vs median actual 10.2 days **[F]**)?
- Q1.4 How severe is lateness when it happens?
- Q1.5 What happened to the orders outside delivery KPIs: how many were cancelled/unavailable, how many are open past their promise?

**Hypotheses**

- H1.1 [H] Late rate is higher in peak-demand months (e.g. 2017-11, 7,544 orders **[F]**) than in adjacent months, beyond what Wilson intervals allow by chance.
- H1.2 [H] Late rate depends on promised lead-time length.
- H1.3 [H] The conditional late rate of the most recent cohorts is not directly comparable with that of mature cohorts (Section 2.1); tested through the maturity sensitivity, with no direction assumed.
- H1.4 [H] Purchase weekday relates to lateness. Exploratory, low priority.

**KPI set**

| ID | KPI | Exact definition | Population / grain |
|---|---|---|---|
| D1 | Late-delivery rate | `COUNT(DATE(delivered_customer) > estimated_date) / COUNT(*)` | Delivered with delivery date, window; grain order |
| D2 | Lead time (days) | `delivered_customer − purchase`, fractional days; median, p90, p95 | Same |
| D3 | Promise error (days) | `DATE(delivered_customer) − estimated_date`, signed integer; negative = early | Same |
| D4 | Promised lead time (days) | `estimated_date − DATE(purchase)` | Same |
| D5 | Severe-late rate | Share with D3 > 7 | Same |
| D6 | Promise slack | Median of (−D3) among on-time orders | On-time delivered orders |
| F1 | Cancelled/unavailable rate | `COUNT(status IN ('canceled','unavailable')) / COUNT(*)` | **All** window orders |
| F2 | Open past-promise rate | Share with non-terminal status (created, approved, invoiced, processing, shipped) **and** `estimated_date < reference date` | All window orders |
| F3 | Open not-yet-due rate | Non-terminal status and `estimated_date ≥ reference date` | All window orders |
| F4 | Delivered-status, no-date rate | Status `delivered` with null delivery timestamp (data inconsistency) | All window orders |
| F-mix | Fulfilment outcome mix | Shares of classes (a)-(f) in R9; sum to 100% | All window orders |

Notes. (i) F1-F4 and D-class (a)/(b) partition all window orders; no KPI blends delivered-late with open or cancelled orders. (ii) Cancelled/unavailable orders are never in F2. (iii) Six non-delivered orders carry a delivery timestamp **[F]**; they are excluded from D1 by the status rule and logged. (iv) Because the window ends at 2018-08 purchases and the extract runs to 2018-10-17, F3 is expected to be small or zero **[TBC]**; open orders in the window are probably stalled or abandoned in the data **[H]**.

**Methods.** DuckDB: cohort-month aggregation, percentiles (`quantile_cont`), outcome classification. Python: Wilson intervals; bootstrap CIs for median and p95 lead time; monthly late-rate chart with Wilson bands; stratified late rate by promised-lead bucket and by month to examine H1.1 and H1.2; cohort-maturity sensitivity table.

**Visualizations.** Monthly late-rate line with Wilson band and sensitivity marking; promise-error histogram (early/late split); ECDFs of actual vs promised lead time; severity-band bars; fulfilment outcome mix by cohort beside the delivered-only late rate.

**Possible confounders.** Promised lead time, customer state, distance, product category/weight, seller, seasonality, order composition changing over the ramp-up.

**Limitations.** Conditional-on-delivery rates; no carrier detail; time zone undocumented; carrier-leg timestamps unreliable in 1,373 in-population orders so no stage decomposition beyond purchase→delivery; one marketplace over a 20-month window.

**Validation.** Reproduce 96,203 / 6.79% (window) and 96,470 / 6.77% (all months); calendar vs exact late = 6,534 vs 7,826 (all months); recompute with the 1,373 flagged orders excluded and confirm the movement is small (all-months benchmark 6.77% → 6.85%); fulfilment classes are mutually exclusive and sum to the window order count; no cancelled/unavailable order is in F2; maturity sensitivity table produced.

---

### Workstream 2: Geographic and seller performance

**Stakeholder decision.** Which destination states, origin-destination lanes and sellers show worse delivery performance than the portfolio by more than chance would suggest, and is the shortfall concentrated enough to justify targeted investigation?

**Analytical questions**

- Q2.1 How do late rate and lead time vary by customer state (and display macro-region)?
- Q2.2 Do cross-state shipments (63.8% of item rows; 36.18% are same-state **[F]**) and longer approximate distance have longer lead times and higher late rates?
- Q2.3 Among qualifying sellers, how are late rates distributed relative to what volume alone would produce (funnel view)?
- Q2.4 Is excess lateness concentrated in a few sellers or lanes?
- Q2.5 Do apparent seller or lane differences persist after stratifying by promised lead time and cross-state shipping?
- Q2.6 (Exploratory) Does lateness differ by product category?

**Hypotheses**

- H2.1 [H] Late rate and lead time rise with distance and cross-state shipment; the promise compensates only partly.
- H2.2 [H] Northern/north-eastern states have longer lead times and higher late rates than SP/RJ/MG (66.61% of orders **[F]**); estimates are noisier due to low n.
- H2.3 [H] More qualifying sellers have Wilson intervals entirely above the portfolio rate than the ≈2.5% expected by chance if sellers did not differ.
- H2.4 [H] A minority of qualifying sellers accounts for a majority of excess late orders. Descriptive Lorenz/Pareto only; the result is read against the possibility that small-n noise inflates concentration.
- H2.5 [H] Most apparent "bad" low-volume lanes are noise.

**KPI set**

| ID | KPI | Definition | Population / grain |
|---|---|---|---|
| G1 | State late rate + Wilson interval | D1 by `customer_state` | Delivered-in-window; n ≥ 100 |
| G2 | State median lead time, p95 | D2 by `customer_state` | Same |
| G3 | Region summary (display) | G1/G2 aggregated to macro-region | Same; display only |
| G4 | Cross-state share | Share of orders where `seller_state ≠ customer_state` | Single-seller delivered-in-window |
| G5 | Distance (km) | Haversine distance between seller and customer ZIP-prefix centroids; order level, median over the order's items (single seller) | Single-seller orders with both centroids |
| S1 | Seller late rate + Wilson interval | D1 by `seller_id` | Single-seller delivered-in-window; n ≥ 50 ranked, 30-49 low confidence |
| S2 | Seller excess late orders (primary) | `late_orders − n × portfolio late rate` | Same |
| S3 | Seller excess late orders (adjusted, secondary) | `late_orders − Σ expected`, expected rate from strata of promised-lead bucket × cross-state flag | Same |
| S4 | Seller median lead time | D2 by seller | Same |
| S5 | Seller concentration | Share of total positive excess late orders in top-k% qualifying sellers | Qualifying sellers |
| L1 | Lane late rate + interval | D1 by (`seller_state`, `customer_state`) | Single-seller; n ≥ 100 shown, otherwise pooled |
| X1 | Late rate by category (exploratory) | D1 by `order_category` | Delivered-in-window; n ≥ 100; labelled exploratory |

**Methods.** DuckDB: order-grain aggregation first, then by state / seller / lane; item-grain CTE for distance collapsing to one order-level distance (median, never a sum). Python: Wilson intervals; funnel plots with binomial limits; binned rate versus distance (quantile bins); adjusted logistic regression `late ~ distance + cross_state + promised_lead + state` as a check that the distance pattern survives adjustment (association only); indirect standardization for S3; Lorenz curve for S5. Optional methods (2.3) only with justification.

**Visualizations.** State map or tile-map coloured by late rate with n and interval (low-volume states hatched), grouped by macro-region; seller funnel plot; scatter/binned curve of lead time vs distance; Lorenz/Pareto curve of excess late orders; lane heatmap with n-masking; seller interval plot for top-N by excess; exploratory category bar chart.

**Possible confounders.** Product mix and weight, seller location (SP holds 59.74% of sellers **[F]**), destination remoteness, order size, seasonality, promised lead time, seller volume.

**Limitations.** Straight-line centroid distance; centroids from a non-unique prefix table; 278 customer and 7 seller prefixes absent from geolocation **[F]**; delivery timestamps are order-level, so seller handling and carrier transit cannot be separated; small sellers cannot be ranked; seller results hold only for single-seller orders; customer state is the order's recorded state; who sets the estimate is unknown; category is missing for some products and ambiguous for mixed orders.

**Validation.** Reconcile to 2,925 sellers, 94,931 orders, 614/412/201 qualifying sellers and 24/27 qualifying states (AC 80, AP 67, RR 40); state and seller order counts sum to the population; no multi-seller order reaches a seller table; distance coverage ≈ 99.51% of item rows **[F]**; seller results checked against an independent pandas computation; sensitivity to n thresholds (30/50/100) and to excluding timestamp-violation orders; macro-region totals equal the sum of states.

---

### Workstream 3: Customer satisfaction (reported to users as "Customer Experience")

**Stakeholder decision.** How strongly are low scores associated with late delivery, and is delivery performance worth prioritising relative to other drivers of dissatisfaction that this data cannot see?

**Analytical questions**

- Q3.1 What are the score distribution and 1-2★ share overall and by cohort?
- Q3.2 How does the score differ between on-time and late deliveries, and does it fall with days late?
- Q3.3 Does the association persist after adjusting for state, promised lead time, price, freight burden, item count and purchase month (and, exploratorily, category)?
- Q3.4 What share of low-score reviewed orders were delivered late (C8), and what share of late orders are low-scored? Both are descriptive and depend on how common lateness is.
- Q3.5 How sensitive are the findings to review-timing anomalies, multi-review handling and non-response?
- Q3.6 (Optional) Do text reviews (40,977 with comments **[F]**, Portuguese) add anything? Keyword counts only.

**Hypotheses**

- H3.1 [H] Late delivery is associated with lower scores after adjustment. The raw gap (4.291 vs 2.273, all months **[F]**) is large; the adjusted size is unknown.
- H3.2 [H] Scores fall as days late increase (on-time, 1-3, 4-7, 8+ days late).
- H3.3 [H] The association shrinks but remains within sellers (single-seller subset).
- H3.4 [H] Excluding reviews created before delivery changes the gap only modestly; if it changes materially, the headline depends on timing artefacts.
- H3.5 [H] Most low-score reviews come from on-time orders in absolute count (9.25% of on-time orders are 1-2★ **[F]** on a very large base), so lateness is associated with only part of dissatisfaction.

**KPI set**

| ID | KPI | Definition | Population / grain |
|---|---|---|---|
| C1 | Mean review score | `AVG(review_score)` | Delivered-in-window, exactly one review row; grain order |
| C2 | Low-score share | Share with score ≤ 2 (+ Wilson interval) | Same |
| C3 | Top-box share | Share with score = 5 | Same |
| C4 | Review coverage | Share of delivered-in-window orders with ≥ 1 review row | Delivered-in-window |
| C5 | Multi-review share | Share with ≥ 2 review rows | Delivered-in-window |
| C6 | Score gap, late vs on-time | `C1(late) − C1(on-time)` and the same for C2; unadjusted | Same as C1 |
| C7 | Score by days-late band | C1 and C2 by D3 bands (≤0, 1-3, 4-7, ≥8); the ≥8 band equals R10 severe lateness | Same as C1 |
| C8 | **Share of low-score reviewed orders delivered late** | Among orders with score ≤ 2: share that were late (D1 definition) | Same as C1 |
| C9 | Early-review flag rate | Share with `review_creation_date < DATE(delivered_customer)` | Same as C1 |
| C10 | Adjusted late association | Adjusted odds ratio (logistic, low score) and adjusted score difference (linear), with cluster-robust intervals | Same as C1 |

C8 is a descriptive composition measure. It depends on the prevalence of lateness and says nothing about how much dissatisfaction lateness causes or how many low scores would disappear without late deliveries. It must not be presented as an upper bound or as an attributable share.

Primary vs sensitivity populations for C1-C10:

| Variant | Definition | Role |
|---|---|---|
| P0 (primary) | Delivered-in-window, exactly one review row | Headline |
| P1 | P0 minus reviews with `review_creation_date < DATE(delivered_customer)` (strictly earlier; same-day kept) | **Required sensitivity.** All-months analogue: 4,940 such reviews of 95,299 **[F]** (5.2%) |
| P2 | Add multi-review orders using earliest / lowest / mean score | Sensitivity on R6 |
| P3 | P0 with non-response scenario bounds | See below |

Why P1 matters [H]: a review dated before the recorded delivery cannot reflect the delivery as recorded; it may follow an earlier actual delivery, a non-arrival complaint or a data error. Also report the late share within these early reviews; if concentrated among late orders, dropping them affects the comparison asymmetrically. Creation dates have day precision, so same-day reviews are ambiguous and kept in P1.

P3: since no-review rates differ (late 2.34% vs on-time 0.55%), recompute the gap under stated scenarios (e.g. a fraction of missing late-order reviews being 1★, or equal to the observed late mean). A sensitivity analysis, not an estimate.

**Methods.** DuckDB: pre-aggregate reviews to one row per order (count; score only when count = 1) before joining. Python: Wilson intervals; bootstrap (clustered on `customer_unique_id`) for the C6 gap; logistic regression `low_score ~ late + customer_state + promised_lead_bucket + log(items_value) + freight_share + n_items + purchase_month`, plus a linear model for score; cluster-robust standard errors; a seller-fixed-effects variant (linear probability model) on the single-seller subset for H3.3; an exploratory variant adding `order_category`. Text analysis, if any, is keyword-count only.

**Visualizations.** Score distribution by on-time/late (100%-stacked bars); score and low-score share by days-late band with intervals; coverage and multi-review panel; forest plot of the late/on-time association across P0-P3 and across model variants (unadjusted, adjusted, seller-FE, with category); composition chart of low-score orders split into late vs on-time (labelled descriptive, C8).

**Possible confounders.** Product category and quality (unmeasured), seller, state, price, freight burden, item count, the expectation set by the promised date, review timing, prior experience (2,997 customers with multiple `customer_id`s **[F]**).

**Limitations.** Association only; voluntary reviews; the score covers the whole order, not one seller or product; multi-review ambiguity (updates vs duplicates cannot be told apart **[F]**); day-precision creation dates; some reviews pre-date delivery; regression adjusts for observed covariates only; reviews of undelivered orders (2,863 **[F]**) are out of scope.

**Validation.** Reproduce 95,299 / 88,946 / 6,353 and 4.291 / 2.273 / 62.36% / 9.25% on the all-months population before applying the window; publish windowed values as new, labelled numbers [TBC]. P0 + zero-review + multi-review = delivered-in-window; independent pandas recomputation of C1-C8; verify the P1 count (4,940 all months) before windowing; no `review_id`-only joins (789 IDs repeat across orders **[F]**); regression sanity checks (unadjusted model reproduces the raw gap; sign and size across variants reported); C8 verified against the cross-tabulation of lateness by low-score status.

---

### Workstream 4: Operational prioritization (reported to users as "Operational Priorities")

**Stakeholder decision.** With limited investigative capacity, which segments (states, lanes, sellers) should operations examine first, and which have too little evidence?

**Analytical questions**

- Q4.1 Which segments contribute the most excess late orders and the most excess low-score orders relative to the portfolio?
- Q4.2 Which of those have intervals clearly separated from the portfolio rate?
- Q4.3 Are flagged segments consistent across the two halves of the window?
- Q4.4 How much of total positive excess lateness would the top k flagged segments cover?
- Q4.5 Which flags change when adjusted rather than portfolio-based expected rates are used?

**Hypotheses**

- H4.1 [H] A small number of segments covers a large share of excess late orders.
- H4.2 [H] Segments with high excess lateness and high excess low-score share overlap only partly.
- H4.3 [H] A material fraction of nominal flags are not consistent across window halves.

**Method [D]** (deliberately simple; no weighted composite index)

1. Candidate segments: customer state, lane (seller_state × customer_state), seller (single-seller orders). Category is exploratory and not a tiering dimension.
2. For each qualifying segment: n, observed late orders, late rate with Wilson interval, excess late orders against the portfolio rate (primary) and the adjusted expected rate (secondary), observed vs expected 1-2★ orders (P0 population), review coverage.
3. **Evidence tier** (rules fixed before results are inspected):
   - *Investigate*: meets minimum-N (R8), Wilson lower bound above the portfolio late rate, excess late orders ≥ a minimum set in advance (provisionally 20), and window-half consistency not 'inconsistent' (a half with fewer than 30 orders is 'unverified', not negative).
   - *Watch*: positive excess with an interval above the reference or excess ≥ the minimum, but not Investigate; low-confidence sellers (30-49 orders) with a signal.
   - *Insufficient data*: below minimum-N; shown, not ranked.
   - *No signal*: everything else.
   **The minimum excess (20 late orders; 10 and 30 reported as sensitivity) is an operational screening policy about how large a gap is worth a manager's attention. It is not a statistical significance level and has no probability interpretation.** Seller-level tiers are *secondary screening evidence* (only 2 of 8 Investigate sellers persist without the three high-delay months).
4. **Primary priority view: excess late orders versus late-delivery rate**, with order volume (marker area), 95% Wilson intervals and evidence tier. The **two-axis matrix (excess late × excess low-score orders) is a secondary/appendix view**: its axes use different populations (delivered vs reviewed orders) and are strongly coupled.
5. Within *Investigate*, order by absolute excess late orders (impact), not rate; rate is shown on the primary view's horizontal axis.
6. **Customer-experience measures are separate from the tiers** (low-score rate among on-time orders with its interval, review coverage, share of reviews written before delivery, after-delivery excess).

**KPI set**

| ID | KPI | Definition | Grain |
|---|---|---|---|
| P1 | Excess late orders (primary) | `observed late − n × portfolio late rate` | Segment |
| P2 | Excess late orders (adjusted, secondary) | Observed minus stratified expected | Segment |
| P3 | Late rate + Wilson interval | As S1/G1/L1 | Segment |
| P4 | Excess low-score orders | `observed 1-2★ − n_reviewed × portfolio low-score share` | Segment (P0 population) |
| P5 | Evidence tier | Rule-based tier above | Segment |
| P6 | Split-window consistency | Flag (yes/no): point estimate above portfolio rate in both window halves | Segment |
| P7 | Coverage at k | Share of total positive excess late orders in the top-k Investigate segments | Portfolio |

**Visualizations.** Primary: excess late orders vs late rate (scatter; size = n; Wilson bars; colour = tier); ranked excess bars with Wilson whiskers; tier summary table. Secondary/appendix: the excess-late vs excess-low-score matrix (all reviews and after-delivery reviews only). Lane labels read *seller state > customer state*; "Northeast-bound" means the destination state is in the Northeast.

**Possible confounders.** Mix (category, distance, promise length); small-n noise; overlapping levels (a flagged seller may drive a flagged lane or state; overlap is reported and excess is never summed across levels).

**Limitations.** Excess is relative to the portfolio rate, not a target or SLA; no cost data, so impact is in order counts, not money; no information on remediation feasibility; a flag means "investigate", never "underperforming"; the number of comparisons means some flags will be chance findings.

**Validation.** Tier rules written down before results are inspected; independent pandas reproduction of tiers; state-level excess sums to zero when the portfolio rate is the reference (reconciliation check); both window halves produce overlapping tiers or the inconsistency is reported; adjusted vs primary comparison reported; if an optional method is used, its justification is recorded.

---

## 4. Analytical data model (minimal relational design in DuckDB)

**Principle.** One fact table per grain; every measure is aggregated to its own grain **before** any join; dimensions join many-to-one. No raw join of orders to items, payments or reviews feeds any KPI.

```
dim_date ───────────────┐
dim_state ──────────────┤
                        ▼
                  fact_orders  (1 row/order)
                        │
        ┌───────────────┼───────────────────┐
        ▼               ▼                   ▼
 fact_order_items   fact_reviews     v_single_seller_orders
 (order×item)       (order×review)    (1 row/order; single-seller only)
        │                                   │
   dim_product                          dim_seller
   dim_zip_geo (distance only)
```

### 4.1 Tables and grain

| Table / view | Grain (one row per…) | Primary key | Join keys | Source |
|---|---|---|---|---|
| `fact_orders` | order | `order_id` | `customer_unique_id`, `customer_state`, `purchase_date` | orders + customers + pre-aggregated items and reviews |
| `fact_order_items` | order item | (`order_id`, `order_item_id`) | `order_id`, `product_id`, `seller_id` | order_items |
| `fact_reviews` | order × review | (`order_id`, `review_id`) | `order_id` | reviews |
| `bridge_order_seller` | order × seller | (`order_id`, `seller_id`) | both | derived from order_items; **used in DuckDB validation only, not exported to Power BI** |
| `v_single_seller_orders` | order (single-seller orders only) | `order_id` | `seller_id`, `customer_state`, `purchase_date` | `fact_orders` filtered on `n_sellers = 1`, with `seller_id`, `seller_state`, distance |
| `dim_seller` | seller | `seller_id` | `seller_zip_code_prefix` | sellers |
| `dim_product` | product | `product_id` | category | products + translation |
| `dim_zip_geo` | 5-digit ZIP prefix | `zip_code_prefix` | — | geolocation de-duplicated: one row per prefix, median lat/lng after dropping out-of-bounds points |
| `dim_date` | calendar date | `date_key` | — | generated |
| `dim_state` | customer state | `state_code` | — | 27 states + `macro_region` (display attribute, 2.5) |

`customer_id` is one-per-order **[F]** and only bridges to `customer_unique_id`, the person key for clustering. Payments are **excluded** from the minimal model: no payment KPI is required and the table is 1:N (2,961 multi-row orders **[F]**). Order value, if needed, is `SUM(price + freight_value)` from items.

### 4.2 Columns of `fact_orders` (analysis-ready; all pre-computed at order grain)

- Keys/attributes: `order_id`, `customer_unique_id`, `customer_state`, `customer_zip_prefix`, `order_status`, `purchase_ts`, `purchase_date`, `purchase_month`, `estimated_date`.
- Timestamps retained (nullable): approved, carrier, delivered_customer.
- Population flags: `in_window`, `is_delivered_dated`, `fulfilment_class` (a-f per R9), `reference_date`.
- Delivery measures (null unless `is_delivered_dated`): `lead_time_days`, `promised_lead_days`, `promise_error_days`, `is_late_calendar`, `is_severe_late` (promise error > 7), `is_late_exact` (sensitivity), `days_late_band`.
- Anomaly flags: `ts_sequence_violation`, `carrier_leg_unreliable`.
- Seller attribution: `n_sellers`, `n_items`, `is_single_seller`, `primary_seller_id` (**null unless `n_sellers = 1`**), `seller_state`, `is_cross_state`, `distance_km_median` (null when not single-seller or centroids missing).
- Category: `order_category` (single / `mixed` / `unknown`), exploratory.
- Review summary: `review_row_count`, `review_score` (**null unless `review_row_count = 1`**), `review_creation_date` (same rule), `review_before_delivery_flag`.
- Value: `items_value`, `freight_value`.

### 4.3 Safeguards against double counting

| Risk | Safeguard |
|---|---|
| Orders × items inflates orders (113,425 vs 99,441 rows **[F]**) | Order-grain KPIs read `fact_orders` only; item aggregates are pre-aggregated in a CTE with `GROUP BY order_id` |
| Orders × reviews inflates (99,992 rows **[F]**) | Reviews collapsed to `review_row_count` and a score that exists only when count = 1 |
| `review_id` reused across orders (789 IDs **[F]**) | Never join or dedupe on `review_id` alone; key is (`order_id`, `review_id`) |
| Multi-seller orders attributed to several sellers | Seller KPIs read only `v_single_seller_orders`, whose key is `order_id` (so one row per order, one seller per order) |
| Customers × geolocation (15,083,733 rows **[F]**) | Geolocation reduced to one row per ZIP prefix before any join; uniqueness asserted |
| Order-level measures summed across item or seller rows | Order measures are never stored on item or bridge tables |
| Segment excess double-counted across levels (state, lane, seller) | Excess is reported per level; never summed across levels |

**Build-time assertions** (fail loudly): `fact_orders` has 99,441 rows and unique `order_id`; `fact_order_items` has 112,650 rows; `bridge_order_seller` has ≥ 1 row for each of the 98,666 orders with items and exactly 1,278 orders with > 1 seller; `v_single_seller_orders` has unique `order_id`; its window delivered-dated count equals 94,931; `dim_zip_geo` unique on prefix; fulfilment classes partition all orders; window counts equal the validation figures.

---

## 5. Power BI dashboard plan (four pages)

**Audience.** Operations managers. **Rule:** every page states its population in a subtitle ("Delivered orders, purchases Jan 2017-Aug 2018, n = …") and shows n next to every rate. KPI definitions come first: each measure maps 1:1 to a KPI ID; no measure may be added without a dictionary entry.

| Page | Content | Key measures |
|---|---|---|
| 1. Executive Overview | KPI cards (late rate, median lead time, severe-late rate, mean score, low-score share, cancelled/unavailable rate); fulfilment outcome mix; monthly late-rate trend with interval; top "Investigate" segments | D1, D2, D5, C1, C2, F1, F-mix |
| 2. Delivery Performance | Promise-error distribution; lead time vs promise; severity bands; state map/tiles grouped by macro-region with n-masking; lane view; distance curve; fulfilment mix by cohort; exploratory category view (labelled) | D1-D6, F1-F4, G1-G5, L1, X1 |
| 3. Customer Experience | Score by on-time/late; days-late bands; coverage panel; sensitivity forest plot (P0-P3, adjusted); C8 composition shown with its descriptive caveat; **low-score rate among on-time orders (with interval) and share of reviews written before delivery, by candidate segment, labelled as feedback associations with the review-timing limitation** | C1-C10 |
| 4. Operational Priorities | **Primary: excess late orders vs late rate (volume, Wilson intervals, evidence tier)**; tier list with threshold sensitivity (10/20/30) and a high-delay-months toggle view; lane and state candidate tables; seller funnel plot and seller table **marked as secondary screening evidence** with tier and low-confidence tags; **appendix visual: excess late vs excess low-score matrix** | S1-S5, P1-P7 |

**Operational Priorities page rules [D].** (a) The page states that tiers are provisional evidence labels, that the 20-order minimum is an operational screening policy and not statistical significance, and that nothing implies cause. (b) No visual or measure sums excess late orders across state, lane and seller levels (they overlap). (c) Seller visuals carry the note that only 2 of 8 Investigate sellers remain Investigate without 2017-11, 2018-02 and 2018-03. (d) Customer-experience measures appear on the Customer Experience page and in the appendix visual only; they never feed the tier logic. (e) Lane direction is explicit (origin state, destination state); any "Northeast" grouping uses the destination region.

**Accessible definitions and limitations [D].** The model is limited to four pages, so definitions are carried in the following ways rather than on a separate page: (a) a population subtitle on every page; (b) an "About this page" info button on every page that opens a bookmark overlay listing the page's KPI definitions, populations and top limitations (drawn from this document); (c) tooltips on every KPI card showing definition, numerator, denominator and n; (d) a persistent footer note "Delivered-only rates are conditional on observed delivery; see fulfilment mix". The full KPI dictionary is also published in the repository README.

**Slicers.** Purchase month, customer state (and macro-region), evidence tier. No "population" selector: populations are fixed so users cannot silently mix delivered-only and all-order KPIs.

**Seller filtering design [D].** To avoid ambiguous many-to-many filter paths:
- Seller KPIs use a **single validated single-seller view** (`v_single_seller_orders`, one row per order) loaded as its own table `seller_orders`. `bridge_order_seller` and `fact_order_items` are not loaded.
- Star schema with conformed dimensions: `dim_date`, `dim_state` (with macro-region) and `dim_seller` each connect one-to-many, single direction, to `fact_orders` (where relevant) and to `seller_orders`. `dim_seller` connects only to `seller_orders`.
- There are no relationships between the two fact tables and no bidirectional filtering. A seller slicer therefore filters seller visuals only; it does not filter `fact_orders` visuals, and the page says so.
- Seller measures reference `seller_orders` only, so multi-seller orders cannot appear in seller results by construction.
- Model check: Power BI model view shows no many-to-many relationships and no bidirectional filters; this is part of validation.

**Implementation constraints.** Statistical outputs (Wilson bounds, tiers, regression results) are computed in Python and loaded as tables; DAX does not re-derive them. Only pages 1 and 4 show ranked segments.

**Review gates:** apply `bi-dashboard-reviewer` at completion: measure definitions match the dictionary, filter directions, totals reconcile to anchors, small-n suppression active.

---

## 6. Optional extension: late-delivery ML classification (NOT part of the core scope; NOT implemented)

**Purpose.** Triage of in-flight orders likely to miss the promised date. Separate from the descriptive project; core deliverables do not depend on it.

**Prediction moment [D]:** at order placement (immediately after `order_purchase_timestamp`, when the estimated delivery date has been issued). A model at approval time would be a different task and named as such.

**Target [D]:** `is_late_calendar` among delivered-with-date orders (R1). Non-delivered orders have no label and are excluded from training and evaluation, with the selection caveat of 2.1. An optional variant may label *open past-promise* orders (R9d) as late. Cancelled/unavailable orders are never labelled late.

**Allowed features (known at the prediction moment)**
- Order: purchase timestamp (month, weekday, hour), promised lead days, estimated date, item count, items value, freight value, product category, weight, volume.
- Geography: customer state, seller state, cross-state flag, centroid distance.
- Seller *history*: late rate, order count, mean score, computed **only from orders whose outcome was known before this order's purchase timestamp** (strictly lagged).

**Prohibited (post-prediction information; any use is leakage)**
- `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date` and any difference involving them.
- `order_status` and any status-derived flag.
- All review data (score, text, dates, counts).
- Any seller/state/category aggregate computed over the full period or the test period.
- Lead time, promise error, days late, `is_late_exact`, timestamp-anomaly flags.
- Customer history that includes orders not yet delivered at the prediction moment.
- Encodings or imputations fit on anything other than the training fold.

**Evaluation design [D].** Temporal split by purchase date (train earlier, validation middle, test last) with a gap at least as long as the maximum label-resolution delay. Baselines first: base rate; promised-lead-only rule; historical state rate. Metrics: PR-AUC and calibration (prevalence ≈ 6.8% **[F]**), precision/lift at a fixed daily review capacity, performance by state and cohort. sklearn `Pipeline` fit on training data only. A null result is acceptable and reported as such. Expect modest signal [H]: the information available at order time may be weak.

**Gate.** Starts only after Phases 1-7 are reviewed and `ml-experiment-reviewer` is applied to the design.

---

## 7. Validation plan

### 7.1 Global checks

1. **Reconciliation anchors:** every number in the "Confirmed headline facts" table must be reproduced exactly from the DuckDB model before new analysis is trusted. A mismatch blocks progress.
2. **Population waterfall:** 99,441 orders → in window → delivered-dated (96,203) → single-seller (94,931) / single-review (TBC). Exclusion counts are stored and shown in the README and page info panels.
3. **Dual implementation:** each primary KPI computed in DuckDB SQL and independently in pandas; agreement to the unit (counts) or 1e-9 (rates).
4. **Grain tests:** key uniqueness; row count after each join equals the left table's count; no fan-out.
5. **Null/zero-denominator handling:** explicit; segments with n = 0 yield null, not 0.
6. **Sensitivity suite** (stored, rerunnable): exact-timestamp lateness; window ending 2018-07; cohort-maturity thresholds 0/30/60 days (assumption, not proof of completeness); excluding timestamp-violation orders; n thresholds 30/50/100; review variants P1/P2/P3; excluding the peak month.
7. **Reproducibility:** fixed seeds for any bootstrap; one command rebuilds all tables from `data/raw/`.

### 7.2 By workstream

| Workstream | Specific validation |
|---|---|
| WS1 Delivery | Reproduce 6.79% / 96,203 (and 6.77% / 96,470 all months); calendar vs exact late counts; promise-error sign tested on hand-built cases (delivered on the estimate date = on time); severe-late = promise error > 7 tested at 7 and 8; fulfilment classes (R9) mutually exclusive and sum to all window orders; cancelled/unavailable never in F2; maturity table produced |
| WS2 Geo/seller | 2,925 / 614 / 412 / 201 sellers and 24/27 states reproduced; order counts sum to population; no multi-seller order in seller tables; distance coverage ≈ 99.51% of item rows; independent pandas check; sensitivity to n thresholds; macro-region totals equal sums of states; funnel limits verified against exact binomial quantiles for a few test cases |
| WS3 Satisfaction | Reproduce 95,299 / 88,946 / 6,353 / 4.291 / 2.273 on all months; P0 + zero + multi = delivered-in-window; P1 count 4,940 verified before windowing; no `review_id`-only joins; unadjusted regression reproduces the raw gap; C8 equals the cross-tab figure; direction and size reported across P0-P3 and model variants |
| WS4 Priorities | Tier rules fixed before results seen; tiers recomputed independently; state excess sums to zero under the portfolio reference; split-window consistency reported; adjusted vs primary comparison reported; any optional method carries a written justification |
| Dashboard | Each page total equals the Python/SQL anchor; slicer combinations tested so populations never change meaning; `seller_orders` row count equals the single-seller view; model view shows no many-to-many relationships and no bidirectional filters; small-n suppression verified; measures traced to the KPI dictionary; definitions overlay present on all four pages |
| ML extension | Every feature has a documented "known-at" time earlier than the prediction moment; temporal split with gap; baselines first; calibration reported; `ml-experiment-reviewer` applied |

---

## 8. Prioritised implementation phases

Each phase ends at a **review gate**; no phase starts before the previous one is accepted.

| Phase | Deliverable | Depends on | Exit criteria | Priority |
|---|---|---|---|---|
| **0** | Sign-off of this revised blueprint | — | Remaining open items (Section 9) recorded | Must |
| **1** | DuckDB analytical model: `fact_orders`, `fact_order_items`, `fact_reviews`, `bridge_order_seller` (validation only), `v_single_seller_orders`, dimensions; build assertions; population waterfall including windowed review and fulfilment-class counts [TBC] | 0 | All anchors reproduced; grain tests pass; `data-quality-auditor` applied | Must |
| **2** | WS1 Delivery reliability: D1-D6, F1-F4, F-mix, cohort-maturity sensitivity | 1 | Anchors match; H1.1-H1.3 assessed; findings written | Must |
| **3** | WS3 Customer satisfaction: C1-C10, P0-P3, non-response bounds, adjusted regression | 1, 2 | Sensitivity forest plot; findings written | Must |
| **4** | WS2 Geography and seller performance: state, region (display), lane, seller funnel, distance; exploratory category | 1, 2 | Wilson-based volume-aware comparisons; `sql-analytics-reviewer` applied to the SQL | Must |
| **5** | WS4 Operational prioritisation: excess orders (portfolio primary, adjusted secondary), tiers, split-window consistency, coverage | 2, 3, 4 | Tier rules committed before results are inspected | Must |
| **6** | Power BI dashboard (4 pages, single-seller view, definitions overlays) | 2-5 | `bi-dashboard-reviewer` applied; totals tie to anchors; model has no many-to-many paths | Must |
| **7** | Project write-up: README with KPI dictionary, findings, limitations, reproducibility instructions; `portfolio-project-finisher` | 6 | Clean-clone reproduction | Must |
| **8a** | Optional statistical methods (shrinkage, permutation tests, rank stability, FDR) | 4, 5 | Only with written justification per Section 2.3 | Optional |
| **8b** | Optional ML late-delivery classification (Section 6) | 1-5 | Leakage audit passed; baselines beaten or null result documented | Optional |

Ordering rationale: WS3 follows WS1 for the lateness definition and day-late bands; WS4 consumes the other workstreams; the dashboard waits for stable KPIs.

---

## 9. Decisions recorded and remaining open items

**Recorded (reviewer decisions, Revision 2)** [D]: four workstreams and validated populations retained; DuckDB + Python; reduced statistical core with optional justification-gated methods; four-page dashboard with accessible definitions; delivered-only rate framed as conditional observed rate; cancelled/unavailable separated from open past-promise; C8 renamed and caveated; 30-day maturity is a sensitivity assumption; portfolio rate primary and adjusted secondary; severe lateness > 7 calendar days; category exploratory, macro-regions display-only, ML optional; single-seller view for Power BI seller KPIs.

**Still open (minor; defaults proposed)**

1. Minimum excess late orders for the *Investigate* tier: **provisionally 20, as an operational screening policy (not statistical significance); 10 and 30 reported as sensitivity.** To be revisited with carrier data or newer months.
2. Exact regression covariate list for WS3 (proposed in 3.Methods); confirm or trim.
3. Whether open non-terminal statuses are reported as one group (proposed) or by status.
4. Whether to include text keyword counts (Q3.6) at all (proposed: skip unless time permits).

---

## 10. Known risks to the project itself

- Seller-level differences may be indistinguishable from volume-driven noise. Then seller conclusions will be "insufficient evidence", a legitimate result reported as such.
- The late/low-score association is large and easy to over-interpret; the report will not claim lateness *causes* dissatisfaction, and C8 will not be framed as attributable.
- Delivered-only KPIs exclude non-delivered orders; their implications for the true broken-promise rate cannot be determined from this data.
- No monetary data on the cost of delay, so no currency impact is claimed; impact is in order counts.
- Single marketplace, historical (2017-2018) data: findings describe this dataset, not current operations.

**STOP: awaiting review of this revision before any implementation.**
