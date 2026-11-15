-- Seller (partner) quarterly scorecard. GRAIN: one row per seller x purchase quarter with at least one order.
-- Population: v_single_seller_orders (eligible single-seller orders, delivered with a date, purchased 2017-01..2018-08).
-- GMV = items_value (item prices, excluding freight). Review metrics use P0 orders (exactly one review row).
-- Late = delivered after the promised calendar date (is_late_calendar). All results are descriptive.
SELECT
    seller_id,
    CAST(date_trunc('quarter', purchase_date) AS DATE)            AS quarter_start,
    strftime(date_trunc('quarter', purchase_date), '%Y') || '-Q' ||
        CAST(quarter(purchase_date) AS VARCHAR)                   AS quarter_label,
    count(*)                                                      AS orders,
    count(DISTINCT customer_unique_id)                            AS customers,
    round(sum(items_value), 2)                                    AS gmv,
    sum(CAST(is_late_calendar AS INTEGER))                        AS late_orders,
    sum(CASE WHEN review_row_count = 1 AND review_score IS NOT NULL THEN 1 ELSE 0 END) AS review_orders,
    sum(CASE WHEN review_row_count = 1 THEN review_score END)     AS review_score_sum,
    sum(CASE WHEN review_row_count = 1 AND review_score <= 2 THEN 1 ELSE 0 END) AS low_review_orders
FROM v_single_seller_orders
GROUP BY ALL
ORDER BY quarter_start, seller_id
