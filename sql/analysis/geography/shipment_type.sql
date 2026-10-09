-- Same-state versus cross-state shipments (single-seller orders).
SELECT
    is_cross_state,
    count(*)                                AS n_delivered,
    sum(is_late_calendar::INT)              AS n_late,
    sum(is_severe_late::INT)                AS n_severe,
    median(lead_time_days)                  AS lead_median,
    quantile_cont(lead_time_days, 0.95)     AS lead_p95,
    median(distance_km)                     AS distance_median_km,
    median(promised_lead_days)              AS promised_median
FROM v_single_seller_orders
GROUP BY 1
ORDER BY 1;
