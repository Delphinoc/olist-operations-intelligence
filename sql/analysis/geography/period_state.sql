-- High-delay periods versus other months, by customer state (all delivered-in-window orders).
-- High-delay cohorts are the three purchase months identified in the delivery workstream: 2017-11, 2018-02, 2018-03.
SELECT
    CASE WHEN purchase_month IN (DATE '2017-11-01', DATE '2018-02-01', DATE '2018-03-01') THEN 'high_delay' ELSE 'other' END AS period,
    customer_state                          AS segment,
    count(*)                                AS n_delivered,
    sum(is_late_calendar::INT)              AS n_late
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1, 2
ORDER BY 1, 2;
