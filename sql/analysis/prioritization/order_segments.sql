-- Order-level frame (one row per delivered-in-window order) carrying the segment keys of all three levels.
-- lane and seller_id are NULL for multi-seller orders (no seller attribution). Used for stratified expectations and for
-- quantifying overlap between segment levels by order id.
SELECT
    f.order_id,
    f.customer_state,
    CASE WHEN f.n_sellers = 1 THEN f.seller_state || '>' || f.customer_state END AS lane,
    f.primary_seller_id                                                        AS seller_id,
    f.n_sellers = 1                                                            AS is_single_seller,
    f.purchase_month,
    CASE WHEN f.promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN f.promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN f.promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN f.promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                                 '5: 36+ days' END                AS promised_group,
    f.is_late_calendar                                                         AS is_late,
    f.review_row_count,
    f.review_score,
    f.review_before_delivery_flag
FROM fact_orders f
WHERE f.is_delivery_kpi_eligible
ORDER BY f.order_id;
