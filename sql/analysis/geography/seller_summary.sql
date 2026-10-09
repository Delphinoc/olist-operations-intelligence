-- Seller-level delivered-only KPIs from the validated single-seller view (one row per eligible order).
-- Volume, late counts and context only; no ranking is done here. Half-window counts support a persistence check
-- (first half = purchases 2017-01..2017-10, second half = 2017-11..2018-08).
SELECT
    v.seller_id,
    min(v.seller_state)                                                       AS seller_state,
    min(ss.macro_region)                                                      AS seller_region,
    count(*)                                                                  AS n_delivered,
    sum(v.is_late_calendar::INT)                                              AS n_late,
    sum(v.is_severe_late::INT)                                                AS n_severe,
    median(v.lead_time_days)                                                  AS lead_median,
    avg(v.is_cross_state::INT)                                                AS cross_state_share,
    median(v.distance_km)                                                     AS distance_median_km,
    count(DISTINCT v.customer_state)                                          AS n_destination_states,
    count(*) FILTER (WHERE v.purchase_month <= DATE '2017-10-01')             AS n_h1,
    coalesce(sum(v.is_late_calendar::INT) FILTER (WHERE v.purchase_month <= DATE '2017-10-01'), 0) AS late_h1,
    count(*) FILTER (WHERE v.purchase_month >  DATE '2017-10-01')             AS n_h2,
    coalesce(sum(v.is_late_calendar::INT) FILTER (WHERE v.purchase_month >  DATE '2017-10-01'), 0) AS late_h2
FROM v_single_seller_orders v
JOIN dim_state ss ON ss.state_code = v.seller_state
GROUP BY v.seller_id
ORDER BY n_delivered DESC, v.seller_id;
