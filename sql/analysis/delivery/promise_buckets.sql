-- KPIs by promised-lead-time group. Group edges were fixed BEFORE looking at any outcome:
-- <=14, 15-21, 22-28, 29-35, 36+ calendar days from purchase date to estimated date.
SELECT
    CASE WHEN promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                               '5: 36+ days' END AS promised_group,
    count(*)                              AS n_delivered,
    sum(is_late_calendar::INT)            AS n_late,
    sum(is_severe_late::INT)              AS n_severe,
    median(lead_time_days)                AS lead_median,
    quantile_cont(lead_time_days, 0.95)   AS lead_p95,
    median(promise_error_days)            AS promise_error_median
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1
ORDER BY 1;
