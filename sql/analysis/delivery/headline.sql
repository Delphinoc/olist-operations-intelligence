-- Headline delivered-only KPIs for a parameterised population (base case and sensitivity checks).
-- Parameters: $end_excl DATE      exclusive end of the purchase window
--             $excl_anom BOOLEAN  drop orders flagged ts_sequence_violation
--             $maturity_days INT  cohort kept only if its last purchase day + N days <= reference date
SELECT
    count(*)                              AS n_delivered,
    sum(f.is_late_calendar::INT)          AS n_late,
    sum(f.is_severe_late::INT)            AS n_severe,
    sum(f.is_late_exact::INT)             AS n_late_exact,
    median(f.lead_time_days)              AS lead_median,
    quantile_cont(f.lead_time_days, 0.90) AS lead_p90,
    quantile_cont(f.lead_time_days, 0.95) AS lead_p95,
    median(f.promise_error_days)          AS promise_error_median,
    min(f.purchase_month)                 AS first_cohort,
    max(f.purchase_month)                 AS last_cohort
FROM fact_orders f
CROSS JOIN model_params p
WHERE f.is_delivered_dated
  AND f.purchase_date >= p.window_start
  AND f.purchase_date <  $end_excl
  AND (NOT $excl_anom OR NOT f.ts_sequence_violation)
  AND CAST(f.purchase_month + INTERVAL 1 MONTH AS DATE) - 1 + $maturity_days <= p.reference_date;
