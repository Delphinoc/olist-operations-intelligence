-- Origin-destination lanes (seller state -> customer state), single-seller orders.
SELECT
    v.seller_state,
    v.customer_state,
    ss.macro_region                         AS seller_region,
    cs.macro_region                         AS customer_region,
    count(*)                                AS n_delivered,
    sum(v.is_late_calendar::INT)            AS n_late,
    median(v.lead_time_days)                AS lead_median,
    quantile_cont(v.lead_time_days, 0.95)   AS lead_p95,
    median(v.distance_km)                   AS distance_median_km
FROM v_single_seller_orders v
JOIN dim_state ss ON ss.state_code = v.seller_state
JOIN dim_state cs ON cs.state_code = v.customer_state
GROUP BY 1, 2, 3, 4
ORDER BY n_delivered DESC, 1, 2;
