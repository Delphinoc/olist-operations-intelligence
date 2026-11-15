-- Marketplace benchmark per purchase quarter on the same single-seller population as seller_quarter.sql.
SELECT
    CAST(date_trunc('quarter', purchase_date) AS DATE)            AS quarter_start,
    count(*)                                                      AS orders,
    count(DISTINCT seller_id)                                     AS active_sellers,
    round(sum(items_value), 2)                                    AS gmv,
    sum(CAST(is_late_calendar AS INTEGER))                        AS late_orders,
    sum(CASE WHEN review_row_count = 1 AND review_score IS NOT NULL THEN 1 ELSE 0 END) AS review_orders,
    sum(CASE WHEN review_row_count = 1 THEN review_score END)     AS review_score_sum,
    min(purchase_date)                                            AS first_purchase,
    max(purchase_date)                                            AS last_purchase
FROM v_single_seller_orders
GROUP BY ALL
ORDER BY quarter_start
