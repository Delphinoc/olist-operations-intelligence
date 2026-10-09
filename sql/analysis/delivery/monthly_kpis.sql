-- Delivered-only KPIs by purchase-month cohort (window). Population = is_delivery_kpi_eligible.
SELECT
    purchase_month,
    count(*)                              AS n_delivered,
    sum(is_late_calendar::INT)            AS n_late,
    sum(is_severe_late::INT)              AS n_severe,
    sum(is_late_exact::INT)               AS n_late_exact,
    median(lead_time_days)                AS lead_median,
    quantile_cont(lead_time_days, 0.90)   AS lead_p90,
    quantile_cont(lead_time_days, 0.95)   AS lead_p95,
    median(promised_lead_days)            AS promised_lead_median,
    median(promise_error_days)            AS promise_error_median
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY purchase_month
ORDER BY purchase_month;
