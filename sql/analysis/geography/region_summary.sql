-- Delivered-only KPIs by IBGE macro-region of the customer (display grouping; population as in state_summary.sql).
SELECT
    s.macro_region,
    count(*)                                AS n_delivered,
    sum(f.is_late_calendar::INT)            AS n_late,
    sum(f.is_severe_late::INT)              AS n_severe,
    median(f.lead_time_days)                AS lead_median,
    quantile_cont(f.lead_time_days, 0.95)   AS lead_p95
FROM fact_orders f
JOIN dim_state s ON s.state_code = f.customer_state
WHERE f.is_delivery_kpi_eligible
GROUP BY 1
ORDER BY 1;
