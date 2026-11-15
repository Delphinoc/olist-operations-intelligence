# Page 5: Partner Scorecard & QBR (build guide)

This page adds a quarterly seller (partner) scorecard and a quarterly business review (QBR) view to the existing
four-page report. It uses two **aggregate** tables written by `analysis/seller_scorecard.py`. They don't touch
the existing model or relationships.

Method, rules and findings: [`reports/seller_scorecard_findings.md`](../reports/seller_scorecard_findings.md).

## 1. Rebuild the data (optional)

```bash
python scripts/build_model.py
python analysis/seller_scorecard.py   # writes powerbi/data/scorecard_*.csv
```

| File | Grain | Rows |
|---|---|---:|
| `scorecard_seller_quarter.csv` | seller x purchase quarter (single-seller orders) | 8,134 |
| `scorecard_marketplace_quarter.csv` | purchase quarter (marketplace benchmark) | 7 |

## 2. Power Query

Uses the existing `DataFolder` parameter. Home > New Source > Blank Query > Advanced Editor, paste each block, and
name the query exactly as in the header comment.

```m
// ---------------- query name: scorecard_seller_quarter ----------------
let
    Source   = Csv.Document(File.Contents(DataFolder & "scorecard_seller_quarter.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed    = Table.TransformColumnTypes(Promoted, {{"seller_id", type text}, {"seller_label", type text}, {"quarter_start", type date}, {"quarter_label", type text}, {"is_partial_quarter", Int64.Type}, {"orders", Int64.Type}, {"customers", Int64.Type}, {"gmv", type number}, {"aov", type number}, {"gmv_share_of_marketplace", type number}, {"late_orders", Int64.Type}, {"on_time_rate", type number}, {"review_orders", Int64.Type}, {"review_score_sum", Int64.Type}, {"avg_review", type number}, {"low_review_orders", Int64.Type}, {"low_review_share", type number}, {"is_scored", Int64.Type}, {"bench_on_time_rate", type number}, {"bench_avg_review", type number}, {"on_time_vs_bench_pp", type number}, {"review_vs_bench", type number}, {"status", type text}, {"has_prev_quarter", Int64.Type}, {"prev_is_scored", Int64.Type}, {"prev_gmv", type number}, {"gmv_qoq_pct", type number}, {"orders_qoq_pct", type number}, {"on_time_qoq_pp", type number}, {"review_qoq", type number}}, "en-US")
in
    Typed

// ---------------- query name: scorecard_marketplace_quarter ----------------
let
    Source   = Csv.Document(File.Contents(DataFolder & "scorecard_marketplace_quarter.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed    = Table.TransformColumnTypes(Promoted, {{"quarter_start", type date}, {"orders", Int64.Type}, {"active_sellers", Int64.Type}, {"gmv", type number}, {"late_orders", Int64.Type}, {"review_orders", Int64.Type}, {"review_score_sum", Int64.Type}, {"first_purchase", type date}, {"last_purchase", type date}, {"quarter_label", type text}, {"on_time_rate", type number}, {"avg_review", type number}, {"is_partial_quarter", Int64.Type}, {"gmv_qoq_pct", type number}}, "en-US")
in
    Typed
```

## 3. Model

- Relationship: `scorecard_marketplace_quarter[quarter_label]` (1) → `scorecard_seller_quarter[quarter_label]` (*), single direction.
- No relationship to `fact_orders`, `fact_seller_orders` or `dim_date`. The scorecard is its own island, so the date slicer on pages 1–4 doesn't filter it.
- Sort `quarter_label` by `quarter_start` in both tables (Column tools > Sort by column).
- Set `status` sort order with a calculated column, if you want On track → Watch → Review: `status_sort = SWITCH(scorecard_seller_quarter[status], "On track", 1, "Watch", 2, "Review", 3, 4)`.

## 4. Measures (put them in `_Measures`)

```dax
SC GMV = SUM ( scorecard_seller_quarter[gmv] )

SC Orders = SUM ( scorecard_seller_quarter[orders] )

SC On-time Rate =
1 - DIVIDE ( SUM ( scorecard_seller_quarter[late_orders] ), SUM ( scorecard_seller_quarter[orders] ) )

SC Mean Review =
DIVIDE ( SUM ( scorecard_seller_quarter[review_score_sum] ), SUM ( scorecard_seller_quarter[review_orders] ) )

SC Sellers Scored =
CALCULATE ( COUNTROWS ( scorecard_seller_quarter ), scorecard_seller_quarter[is_scored] = 1 )

SC Sellers On Track =
CALCULATE ( COUNTROWS ( scorecard_seller_quarter ), scorecard_seller_quarter[status] = "On track" )

SC Sellers Watch =
CALCULATE ( COUNTROWS ( scorecard_seller_quarter ), scorecard_seller_quarter[status] = "Watch" )

SC Sellers Review =
CALCULATE ( COUNTROWS ( scorecard_seller_quarter ), scorecard_seller_quarter[status] = "Review" )

SC Scored GMV Share =
DIVIDE ( CALCULATE ( [SC GMV], scorecard_seller_quarter[is_scored] = 1 ), [SC GMV] )

SC Benchmark On-time =
1 - DIVIDE ( SUM ( scorecard_marketplace_quarter[late_orders] ), SUM ( scorecard_marketplace_quarter[orders] ) )

SC Benchmark Review =
DIVIDE ( SUM ( scorecard_marketplace_quarter[review_score_sum] ), SUM ( scorecard_marketplace_quarter[review_orders] ) )

SC Marketplace GMV = SUM ( scorecard_marketplace_quarter[gmv] )

SC Marketplace GMV QoQ =
VAR CurStart = MAX ( scorecard_marketplace_quarter[quarter_start] )
VAR Cur = [SC Marketplace GMV]
VAR Prev =
    CALCULATE (
        [SC Marketplace GMV],
        REMOVEFILTERS ( scorecard_marketplace_quarter ),
        scorecard_marketplace_quarter[quarter_start] = EDATE ( CurStart, -3 )
    )
RETURN IF ( HASONEVALUE ( scorecard_marketplace_quarter[quarter_label] ), DIVIDE ( Cur - Prev, Prev ) )

SC Quarter Note =
IF (
    SELECTEDVALUE ( scorecard_marketplace_quarter[is_partial_quarter] ) = 1,
    "Partial quarter: July-August purchases only",
    IF ( HASONEVALUE ( scorecard_marketplace_quarter[quarter_label] ), "Full quarter", "Select one quarter" )
)
```

Formats: GMV as whole-number currency (BRL), rates and QoQ as percentage with 1 decimal place, review with 2 decimals.

## 5. Visuals

Canvas 16:9, the same theme as pages 1–4. Page title: **Partner Scorecard & QBR**. Subtitle: *Single-seller orders.
Sellers are scored with 30+ orders in a quarter. Status compares each seller with the marketplace benchmark,
with a tolerance of 1.0 pp on-time and 0.10 review points.*

| # | Visual | Fields | Notes |
|---|---|---|---|
| 1 | Slicer (dropdown, single select) | `scorecard_marketplace_quarter[quarter_label]` | Default **2018-Q2**. |
| 2 | Card row (6 cards) | `SC Marketplace GMV`, `SC Marketplace GMV QoQ`, `SC Benchmark On-time`, `SC Benchmark Review`, `SC Sellers Scored`, `SC Quarter Note` | |
| 3 | Card row (3 cards) | `SC Sellers On Track`, `SC Sellers Watch`, `SC Sellers Review` | Colour the callout values green `#2a9d8f`, amber `#e9c46a` and red `#e76f51`. |
| 4 | Scatter chart, "Seller scorecard matrix" | X `on_time_vs_bench_pp` (Don't summarize), Y `review_vs_bench` (Don't summarize), Size `gmv`, Legend `status`, Values `seller_label` | Visual-level filter `is_scored = 1`. Add X and Y constant lines at 0 (solid) and at -1.0 / -0.10 (dashed). |
| 5 | Table, "Top 10 sellers by GMV" | `seller_label`, `orders`, `gmv`, `gmv_qoq_pct`, `on_time_rate`, `on_time_vs_bench_pp`, `avg_review`, `status` | Filter `is_scored = 1` and Top N 10 by `gmv`. Conditional formatting: `status` background by rules; `gmv_qoq_pct` and `on_time_vs_bench_pp` with green/red font. |
| 6 | Table, "Sellers to review" | Same columns as 5 | Filter `status = "Review"`, Top N 10 by `gmv`. |
| 7 | Stacked column, "GMV by status over time" | Axis `scorecard_seller_quarter[quarter_label]`, Values `SC GMV`, Legend `status` | Filter `is_partial_quarter = 0`. Format > Edit interactions: set the quarter slicer to **None** on this visual, so it always shows every full quarter. Blank status = sellers with fewer than 30 orders; rename it "Not scored" with a calculated column if you like. |

Footnote text box: *Descriptive screening labels for a QBR conversation, not verdicts on sellers. Order-level
timestamps can't separate seller handling from carrier transit. Benchmarks are marketplace averages, not
commercial targets.*

## 6. Reconciliation checklist (slicer = 2018-Q2)

| Check | Expected |
|---|---:|
| SC Marketplace GMV | 2,733,335 |
| SC Marketplace GMV QoQ | 3.0% |
| SC Benchmark On-time | 95.8% |
| SC Benchmark Review | 4.27 |
| SC Sellers Scored | 128 |
| On track / Watch / Review | 61 / 52 / 15 |
| Top seller in table 5 | 955fee92, 488 orders, GMV 61,596 |
| Stacked column total, 2017-Q1 | 701,560 |

If a value differs, check the `en-US` culture in Power Query and that `is_scored` was typed as a whole number.

When the page is built, export it as `docs/dashboard/05_partner_scorecard.png` and add it to the README.
