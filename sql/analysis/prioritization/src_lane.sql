-- Source rows for the LANE level: validated single-seller view; segment = seller state > customer state.
SELECT
    order_id,
    seller_state || '>' || customer_state AS segment,
    purchase_month,
    is_late_calendar                AS is_late,
    review_row_count,
    review_score,
    review_before_delivery_flag
FROM v_single_seller_orders
