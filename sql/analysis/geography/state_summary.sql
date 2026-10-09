-- Delivered-only KPIs by customer state (delivery KPI population: delivered with date, purchases 2017-01..2018-08).
-- Includes multi-seller orders: customer state is an order-level attribute.
SELECT
    f.customer_state,
    s.macro_region,
    count(*)                                AS n_delivered,
    sum(f.is_late_calendar::INT)            AS n_late,
    sum(f.is_severe_late::INT)              AS n_severe,
    median(f.lead_time_days)                AS lead_median,
    quantile_cont(f.lead_time_days, 0.95)   AS lead_p95
FROM fact_orders f
JOIN dim_state s ON s.state_code = f.customer_state
WHERE f.is_delivery_kpi_eligible
GROUP BY 1, 2
ORDER BY 1;
