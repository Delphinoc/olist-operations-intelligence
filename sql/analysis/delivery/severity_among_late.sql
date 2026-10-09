-- Days-late distribution among late orders only (delivered-only, window).
SELECT
    count(*)                                  AS n_late,
    median(promise_error_days)                AS days_late_median,
    quantile_cont(promise_error_days, 0.90)   AS days_late_p90,
    max(promise_error_days)                   AS days_late_max
FROM fact_orders
WHERE is_delivery_kpi_eligible AND is_late_calendar;
