-- KPIs by distance band. Parameters $q1, $q2, $q3 are the quartile cut points from distance_quartiles.sql.
-- Distance is a straight-line ZIP-prefix centroid approximation, NOT a road distance.
SELECT
    CASE WHEN distance_km IS NULL THEN 'Q0: unknown'
         WHEN distance_km <= $q1 THEN 'Q1: nearest quarter'
         WHEN distance_km <= $q2 THEN 'Q2'
         WHEN distance_km <= $q3 THEN 'Q3'
         ELSE                         'Q4: farthest quarter' END AS distance_band,
    count(*)                                AS n_delivered,
    sum(is_late_calendar::INT)              AS n_late,
    sum(is_severe_late::INT)                AS n_severe,
    median(lead_time_days)                  AS lead_median,
    quantile_cont(lead_time_days, 0.95)     AS lead_p95,
    median(distance_km)                     AS distance_median_km,
    avg(is_cross_state::INT)                AS cross_state_share,
    median(promised_lead_days)              AS promised_median
FROM v_single_seller_orders
GROUP BY 1
ORDER BY 1;
