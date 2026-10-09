-- Source rows for the SELLER level: validated single-seller view; segment = seller_id (one seller per order by construction).
SELECT
    order_id,
    seller_id                       AS segment,
    purchase_month,
    is_late_calendar                AS is_late,
    review_row_count,
    review_score,
    review_before_delivery_flag
FROM v_single_seller_orders
