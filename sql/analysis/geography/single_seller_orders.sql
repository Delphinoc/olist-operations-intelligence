-- Order-level frame for stratified comparisons (single-seller delivered-in-window orders).
-- $q1, $q2, $q3: distance quartile cut points (distance_quartiles.sql).
SELECT
    v.order_id,
    v.seller_id,
    v.seller_state,
    v.customer_state,
    v.purchase_month,
    CASE WHEN v.promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN v.promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN v.promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN v.promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                                 '5: 36+ days' END AS promised_group,
    CASE WHEN v.distance_km IS NULL THEN 'Q0: unknown'
         WHEN v.distance_km <= $q1 THEN 'Q1: nearest quarter'
         WHEN v.distance_km <= $q2 THEN 'Q2'
         WHEN v.distance_km <= $q3 THEN 'Q3'
         ELSE                           'Q4: farthest quarter' END AS distance_band,
    v.is_cross_state,
    v.distance_km,
    v.is_late_calendar AS is_late,
    v.lead_time_days
FROM v_single_seller_orders v
ORDER BY v.order_id;
