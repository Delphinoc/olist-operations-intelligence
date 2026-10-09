-- Source rows for the STATE level: all delivered-in-window orders (incl. multi-seller); segment = customer state.
-- No joins, so no row multiplication is possible: one row per order (fact_orders grain).
SELECT
    order_id,
    customer_state                  AS segment,
    purchase_month,
    is_late_calendar                AS is_late,
    review_row_count,
    review_score,
    review_before_delivery_flag
FROM fact_orders
WHERE is_delivery_kpi_eligible
