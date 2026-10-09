-- High-delay periods versus other months, by lane (seller state -> customer state), single-seller orders.
SELECT
    CASE WHEN purchase_month IN (DATE '2017-11-01', DATE '2018-02-01', DATE '2018-03-01') THEN 'high_delay' ELSE 'other' END AS period,
    seller_state || '>' || customer_state   AS segment,
    count(*)                                AS n_delivered,
    sum(is_late_calendar::INT)              AS n_late
FROM v_single_seller_orders
GROUP BY 1, 2
ORDER BY 1, 2;
