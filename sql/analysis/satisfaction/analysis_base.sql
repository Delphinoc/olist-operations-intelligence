-- Order-level analysis dataset: ALL delivered-in-window orders (is_delivery_kpi_eligible), one row per order.
-- Review fields follow the model rules: review_score only when exactly one review row exists.
-- The per-order multi-review summaries below are used ONLY for sensitivity analysis; none is treated as authoritative.
WITH rv AS (
    SELECT
        order_id,
        count(*)                          AS n_rows,
        min(review_score)                 AS min_score,
        max(review_score)                 AS max_score,
        avg(review_score)                 AS mean_score,
        -- earliest / latest by creation date are defined only when all rows have distinct dates (no ties)
        CASE WHEN count(DISTINCT review_creation_date) = count(*) THEN arg_min(review_score, review_creation_date) END AS earliest_score,
        CASE WHEN count(DISTINCT review_creation_date) = count(*) THEN arg_max(review_score, review_creation_date) END AS latest_score
    FROM fact_reviews
    GROUP BY order_id
)
SELECT
    f.order_id,
    f.customer_unique_id,
    f.purchase_month,
    f.customer_state,
    f.promised_lead_days,
    CASE WHEN f.promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN f.promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN f.promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN f.promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                                 '5: 36+ days' END AS promised_group,
    f.items_value,
    f.freight_value,
    f.n_items,
    f.order_category,
    f.is_late_calendar,
    f.days_late_band,
    f.promise_error_days,
    f.review_row_count,
    f.review_score,
    f.review_creation_date,
    f.delivered_date,
    f.review_before_delivery_flag,
    (f.review_creation_date <= f.delivered_date) AS review_on_or_before_delivery,
    rv.min_score, rv.max_score, rv.mean_score, rv.earliest_score, rv.latest_score
FROM fact_orders f
LEFT JOIN rv USING (order_id)
WHERE f.is_delivery_kpi_eligible
ORDER BY f.order_id;
