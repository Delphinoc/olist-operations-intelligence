# Power BI import package and implementation guide (Stage A)

This folder contains everything needed to build the four-page dashboard in **Power BI Desktop**: validated data
files, typed Power Query code, the semantic-model design, the DAX measure dictionary, and page specifications.
**Current state (2026-10).** A four-page report, `Olist-Operations-Intelligence.pbix`, has since been built from this package. It is not tracked in Git for now because it embeds row-level data (section 11). The Stage A statement that follows is kept as the historical context of this package: **No `.pbix` file is included; this is a preparation package.** The package is still the source of the report's data. The seven measures and six calculated columns that the report adds are documented with their exact formulas in `dax_measures.md`, section 8 (exported from the saved model with Tabular Editor 2 into `dax_export.csv`; reconciled in SQL/Python, not executed in Power BI Desktop by this project). Nothing here changes a business definition: all
flags, populations and snapshot values come from the validated DuckDB model and the four analysis workstreams.

| File | Purpose |
|---|---|
| `data/*.csv` | Ten import tables (two facts, four dimensions, four fixed-period snapshot tables) |
| `data/manifest.json`, `data/data_dictionary.csv` | Row counts, SHA-256 checksums, column types, column roles |
| `power_query.m` | One typed Power Query query per table (generated) |
| `dax_measures.md` | Complete measure dictionary with exact DAX, populations and denominators |
| `page_specs.md` | Detailed visual specification for the four pages |
| `../reports/powerbi_preparation_validation.md` | Validation of counts, relationships and measures |

## 1. Rebuild the package (optional)

```
pip install -r requirements.txt
python scripts/build_model.py              # DuckDB model from data/raw/ (see the root README for the Kaggle download)
python scripts/export_powerbi.py           # writes powerbi/data/*.csv, manifest.json, data_dictionary.csv, power_query.m
python -m pytest tests/test_powerbi_export.py
```

**Format decision.** The files are **CSV** (UTF-8, comma-separated, ISO dates `yyyy-mm-dd`, `.` decimal separator,
booleans as 0/1, empty = missing). Parquet was not used because Power BI Desktop compatibility could not be confirmed
in this environment. The Power Query code reads the files with an explicit `en-US` culture so the regional settings of the
machine do not matter. The raw Olist source CSVs are not part of the package.

## 2. Tables

| Table | Kind | Grain (one row per) | Rows | Key |
|---|---|---|---:|---|
| `fact_orders` | fact | order (all orders; eligibility is a flag) | 99,441 | `order_id` |
| `fact_seller_orders` | fact | eligible single-seller order | 94,931 | `order_id` |
| `dim_date` | dimension | purchase date (2016-09-01 to 2018-10-31) | 791 | `date_key` |
| `dim_state` | dimension | customer state | 27 | `state_code` |
| `dim_origin_state` | dimension (role copy of `dim_state`) | seller (origin) state | 22 | `origin_state_code` |
| `dim_seller` | dimension | seller with at least one eligible order | 2,925 | `seller_id` |
| `snap_priority_candidates` | **snapshot** | candidate segment (state, lane or seller; long table) | 3,360 | (`snapshot_level`, `segment`) |
| `snap_tier_counts` | **snapshot** | level x screening threshold | 9 | (`snapshot_level`, `min_excess_late`) |
| `snap_overlap` | **snapshot** | membership cell or summary row | 14 | `label` |
| `snap_metadata` | **snapshot** | metadata key | 15 | `key` |

`fact_seller_orders[order_id]` repeats keys of `fact_orders` so totals can be reconciled, but **the two fact tables are
never related to each other**.

## 3. Import into Power BI Desktop

1. **Home > Get data > Text/CSV is not needed.** Use the supplied queries instead:
   1. Home > Transform data > Manage Parameters > New Parameter. Name `DataFolder`, Type Text, Current Value = the full path to `powerbi\data\` **including the trailing backslash**.
   2. For each block in `power_query.m`: New Source > Blank Query > Advanced Editor, paste the block (from `let` to `Typed`), name the query exactly the table name (`fact_orders`, `dim_date`, ...).
   3. Close & Apply. Verify the row counts against section 2.
2. **Check types** after loading (they are set by the queries): dates are *Date*, flags and counts *Whole number*, `lead_time_days`, `distance_km` and all rates *Decimal number*, keys and labels *Text*.
3. If a column loads as Text with commas or dots in the wrong places, the machine culture was applied: re-run the query as supplied (it forces `en-US`).

## 4. Semantic model

### Relationships (create in Model view)

| ID | From (one side) | To (many side) | Cardinality | Cross-filter | Active |
|---|---|---|---|---|---|
| R1 | `dim_date[date_key]` | `fact_orders[purchase_date]` | one-to-many | single (dimension -> fact) | yes |
| R2 | `dim_date[date_key]` | `fact_seller_orders[purchase_date]` | one-to-many | single | yes |
| R3 | `dim_state[state_code]` | `fact_orders[customer_state]` | one-to-many | single | yes |
| R4 | `dim_state[state_code]` | `fact_seller_orders[customer_state]` | one-to-many | single | yes |
| R5 | `dim_origin_state[origin_state_code]` | `fact_seller_orders[seller_state]` | one-to-many | single | yes |
| R6 | `dim_seller[seller_id]` | `fact_seller_orders[seller_id]` | one-to-many | single | yes |

Disable *Autodetect new relationships* and do not add anything else. **There is no relationship between the two fact
tables, none between dimensions, none involving a `snap_*` table, no many-to-many and no bidirectional filtering.**
Every dimension key is unique and every foreign key matches a dimension row (checked in the validation report), so each
relationship is a true one-to-many.

### Diagram

```
                                   dim_date
                                 (date_key)
                                  |       |
                       R1 (1 -> *)|       |R2 (1 -> *)
                                  v       v
   dim_state ----R3 (1 -> *)--> fact_orders        fact_seller_orders <--R6 (1 -> *)-- dim_seller
  (state_code)                                           ^   ^
        |                                                |   |
        +--------------------------R4 (1 -> *)-----------+   +---R5 (1 -> *)--- dim_origin_state

   fact_orders  .  .  .  NO RELATIONSHIP  .  .  .  fact_seller_orders

   snap_priority_candidates   snap_tier_counts   snap_overlap   snap_metadata     (disconnected snapshot tables)
```

Filters travel one way only, from a dimension to the fact tables it is related to. `dim_state` and `dim_date` are
shared by both facts: this is a conformed dimension, not an ambiguous path, because no path leads from one fact table to
the other. `dim_origin_state` is a separate role-specific copy so that "customer state" and "origin state" slicers can never
be confused or compete for the same relationship.

### Slicer behaviour (what each selection filters)

| Slicer | `fact_orders` visuals | `fact_seller_orders` visuals | `snap_*` visuals |
|---|---|---|---|
| `dim_date` (month range, purchase date) | filtered | filtered | **not filtered** |
| `dim_state` (customer state / macro-region) | filtered | filtered (orders to that destination) | **not filtered** |
| `dim_origin_state` | **not filtered** | filtered | **not filtered** |
| `dim_seller` | **not filtered** | filtered | **not filtered** |
| `snap_*[snapshot_level, tier, display_region]` | not filtered | not filtered | filtered |

Consequences to expect: a customer-state selection gives `Delivered orders` for all delivered orders to that state, while
`Seller: eligible orders` is a little smaller (multi-seller orders carry no seller; for RJ 12,154 vs 12,310). A seller or
origin-state selection leaves order-level cards unchanged; the measure `Note: seller selection on order-level visuals` makes
that visible.

### Date handling

- `dim_date[date_key]` relates to the **purchase date** only; mark `dim_date` as the date table (Table tools > Mark as date table, column `date_key`). Date slicers therefore select purchase cohorts, which is how every analysis in this project defines its cohorts.
- **Estimated and actual delivery dates are not in the model** and have no relationship (an active relationship on them would silently redefine the cohort). Delivery timing enters only through numeric columns (`lead_time_days`, `promise_error_days`, flags).
- Orders purchased before Jan 2017 or after Aug 2018 (349 orders) are in the file so the 99,441 total reconciles, but **every delivery KPI excludes them via its flag**, whatever the date slicer shows. Set the date slicer to Jan 2017 - Aug 2018 by default.

### Column settings

- Sort `dim_date[month_label]` by `month_sort`; `dim_state[macro_region]` by `macro_region_order`; `dim_origin_state[origin_macro_region]` by `origin_macro_region_order`; `snap_priority_candidates[tier]` by `tier_sort`.
- Hide all key and flag columns from the field list (`order_id`, `purchase_date` in the facts, `in_window`, `is_*`, `review_*` flags, `lead_time_days`, `promise_error_days`, ...): visuals use measures. Keep `delivery_outcome`, `days_late_band`, `promised_group`, `distance_band`, `fulfilment_label`, `lane`, `review_score` and `promise_error_days` visible as axes.
- Summarisation: set every numeric column to *Don't summarize* so nobody sums a rate or a snapshot value by accident.

## 5. Measures

1. Home > Enter data > create a one-column empty table named `_Measures`; hide its column.
2. Copy each measure from `dax_measures.md` into `_Measures` (New measure). Names must match exactly; the page specifications refer to them.
3. Check one measure at a time against the checklist below. All ratios use `DIVIDE`.

## 6. Build the pages

Follow `page_specs.md` page by page (Executive Overview, Delivery Performance, Customer Experience, Operational
Priorities): it lists title, business question, visual type, fields, measures, slicers, interactions, tooltip content and
interpretation caveats. Key rules repeated here:

- Use **Edit interactions** so card visuals do not cross-filter.
- Page 4 uses only the `snap_*` tables and their own slicers. Do not sync the date, state or seller slicers to it.
- State the population and n in every visual subtitle.
- Put the review-timing caveat on page 3 and the "screening policy, not statistical significance" sentence on page 4.

## 7. P0 and P1 reviews

- **P0 (primary):** delivered in window with exactly one review row (95,037 orders). The latest review is never assumed authoritative; orders with zero or several review rows are excluded and counted separately.
- **P1 (sensitivity):** P0 minus reviews created *strictly before* the recorded delivery date (90,103 orders; same-day reviews kept). It is shown **next to** P0 with its own order counts and the review-timing sentence. The gap between P0 and P1 reflects *when* reviews were written; it is not an effect size and not causal.

## 8. Fixed-period snapshot tables

`snap_*` tables hold values computed once for purchases 2017-01 to 2018-08 by the prioritization workstream (excess late
orders, Wilson intervals, adjusted observed/expected, tiers at 10/20/30 and without the three high-delay months,
consistency, review context). Power BI never recalculates them. They have no relationships, so date, state and seller
slicers cannot change them; every visual that uses them must carry the note `Note: snapshot visuals`. The tiers are
provisional screening labels; the 20-order minimum is an **operational screening policy, not statistical significance**.
Sellers are secondary screening evidence (only 2 of 8 Investigate sellers persist without the three high-delay months).

## 9. KPI reconciliation checklist

Complete this before building visuals. Expected values are validated in `reports/powerbi_preparation_validation.md`
(DAX-equivalent calculation on these exact files vs independent SQL on the DuckDB model).

**A. Anchors, no slicers applied**

| Check | Measure (table visual or card) | Expected |
|---|---|---:|
| [ ] All orders | `All orders in file` | 99,441 |
| [ ] Orders in the purchase window | `Orders in window` | 99,092 |
| [ ] Delivered with date, in window | `Delivered orders` | 96,203 |
| [ ] Late orders | `Late orders` | 6,531 |
| [ ] Severely late orders | `Severe-late orders` | 2,860 |
| [ ] Calendar-date late rate | `Late-delivery rate` | 6.7888% (displays 6.79%) |
| [ ] Eligible single-seller orders | `Seller: eligible orders` | 94,931 |
| [ ] Eligible single-review orders (P0) | `Reviewed orders (P0)` | 95,037 |
| [ ] P1 population | `Reviewed orders (P1)` | 90,103 |
| [ ] Cancelled/unavailable, window | `Cancelled/unavailable orders` | 1,182 |
| [ ] Open past-promise, window | `Open past-promise orders` | 1,699 |
| [ ] Median / P95 lead time | `Median lead time (days)`, `P95 lead time (days)` | 10.21 / 29.22 days |
| [ ] Average score / low-score share (P0) | `Average review score (P0)`, `Low-score share (P0)` | 4.157 / 12.78% |
| [ ] Row counts after load | Model view / Power Query | `fact_orders` 99,441; `fact_seller_orders` 94,931; `dim_seller` 2,925; `snap_priority_candidates` 3,360 |

**B. Filter-context scenarios** (set the slicers, read the cards)

| Slicer setting | Measure | Expected |
|---|---|---:|
| Customer state = RJ | `Delivered orders` / `Late orders` / `Late-delivery rate` | 12,310 / 1,495 / 12.14% |
| Customer state = RJ | `Seller: eligible orders` / `Seller: late orders` | 12,154 / 1,490 |
| Customer state = RJ | `Reviewed orders (P0)` / `Low-score share (P0)` / `Low-score share (P1)` | 12,103 / 18.29% / 11.23% |
| Region = Northeast | `Delivered orders` / `Late orders` | 9,015 / 1,150 |
| Months Nov 2017 - Mar 2018 | `Delivered orders` / `Late orders` / `Late-delivery rate` | 33,428 / 3,972 / 11.88% |
| Origin state = SP and customer state = RJ | `Seller: eligible orders` / `Seller: late orders` | 8,031 / 1,147 |
| Origin state = SP and customer state = RJ | `Delivered orders` (order-level card, **not** filtered by origin) | 12,310 |
| Any seller selected | `Delivered orders` | unchanged (no relationship) |

**C. Snapshot checks:** `Snapshot: Investigate segments` with level = state, lane, seller shows 10, 9, 8 (tier counts at the 20-order policy); `Snapshot: primary screening threshold` = 20; changing any date, state or seller slicer must **not** change any snapshot figure.

**D. Structure checks:** exactly six relationships (R1-R6), all one-to-many, single direction; no relationship touches a fact table pair or a `snap_*` table.

## 10. Known limitations and open decisions

- Fixed-snapshot tables are not live; refresh them only by re-running `scripts/export_powerbi.py` after the model is rebuilt.
- Distance is a straight-line ZIP-prefix approximation; late rate and lead time are conditional on delivery; review measures are associations (see `page_specs.md`).
- **Population on page 2.** The shipment-type and shipping-distance charts use `Seller Late Rate`, which reads `fact_seller_orders` only: the **94,931 single-seller orders** (6.87% late). The state, lead-time and headline visuals use the 96,203 delivered orders (6.79%). State the population in those chart subtitles.
- **Lead-time bins.** `Delivery Time Group` applies integer-looking labels to fractional days with half-open intervals: "0–4 days" is under 5.0 days, "5–9 days" is 5.0 to under 10.0, "10–14 days" 10.0 to under 15.0, "15–19 days" 15.0 to under 20.0, "20–29 days" 20.0 to under 30.0, "30+ days" 30.0 or more.
- **Desktop verification.** The 13 dashboard-only formulas are exact copies from the saved model, but their values were reconciled in SQL and Python, not in Power BI Desktop. Complete the checklist in section 9 there.
- Error bars on scatter charts require a Power BI Desktop version with scatter error bars; otherwise intervals stay in tooltips and tables.
- Open decisions (listed with the validation report): whether the large fact CSVs (about 36 MB together) should be committed to Git, whether a Parquet variant should be added once compatibility is confirmed, and whether the published dashboard needs a refresh schedule.

## 11. Publishing the report (decisions and safe workflow)

**What the `.pbix` contains.** Four report pages (1920x1080) and a compressed data model. The model includes imported row-level tables
derived from Olist (`fact_orders`, `fact_seller_orders`), so the file is *data-bearing*. It is kept out of public Git staging for now;
see the root README, "Data licence and attribution".

**Is a data-free Power BI Project (`.pbip`) feasible?** Probably, but this is **not verified here**. Power BI Desktop can save a report as
a Power BI Project (a folder of text files: a `.Report` folder with the report definition and a `.SemanticModel` folder with the model
definition), and a project is not required to carry the imported data cache. Checked in this repository: the `.pbix` report definition is
already in the text (PBIR-style) format; `Settings` show relationship auto-detection disabled; `power_query.m` uses a `DataFolder`
parameter rather than an absolute path. Not done: no `.pbip` has been created or inspected, so **nothing may be called data-free until
its files have been read**. The Desktop menu names and the folder layout vary by version.

Safe workflow, to be run by the report owner in Power BI Desktop:

1. Copy the `.pbix` and open the copy.
2. Enable the Power BI Project (`.pbip`) save format under Options > Preview features if needed, then File > Save as > Power BI Project into a **new empty folder outside the repository**.
3. Close Desktop and scan the whole folder for data: no `cache.abf`, no `*.csv`, no unexpectedly large files, and no literal dataset values in `*.tmdl` files (for example search for a real `order_id` copied from `powerbi/data/fact_orders.csv`).
4. Inspect the `DataFolder` parameter and every `Source` step in the model files and replace any personal absolute path with a placeholder such as `C:\path\to\powerbi\data\`.
5. Confirm that `.pbi/localSettings.json` and `.pbi/cache.abf` (normally created by Desktop) are git-ignored.
6. Only after steps 3-5 pass, copy the project into the repository (for example `powerbi/project/`), review `git status` and the diff, and commit with the repository owner's approval.
7. Add page screenshots to `docs/dashboard/` and reference them in the root README. The exported PDF in `powerbi/visualisation/` can be rendered to images for this purpose.

Until then the repository publishes code, SQL, tests, reports, the Power Query script, the data dictionary, the DAX dictionary and the
fixed snapshot tables (`snap_*`), but not the `.pbix` or the row-level fact CSVs.
