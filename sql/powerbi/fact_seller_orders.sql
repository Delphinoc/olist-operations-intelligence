-- Power BI table fact_seller_orders. GRAIN: one row per ELIGIBLE single-seller order (94,931 rows).
-- Source: the validated view v_single_seller_orders (delivered with date, purchased in window, exactly one seller).
-- Multi-seller orders are not attributed to sellers and do not appear here. order_id repeats the key of fact_orders
-- for reconciliation only: there is NO relationship between the two fact tables.
-- {{Q1}}, {{Q2}}, {{Q3}} are the distance quartile cut points (km) computed by the export script from this same view.
SELECT
    order_id,
    seller_id,
    seller_state,
    customer_state,
    seller_state || '>' || customer_state             AS lane,           -- seller state > customer state
    purchase_date,
    CAST(is_cross_state AS INTEGER)                   AS is_cross_state,
    distance_km,                                                          -- straight-line ZIP-prefix centroid distance, NOT road distance
    CASE WHEN distance_km IS NULL THEN 'Q0: unknown'
         WHEN distance_km <= {{Q1}} THEN 'Q1: nearest quarter'
         WHEN distance_km <= {{Q2}} THEN 'Q2'
         WHEN distance_km <= {{Q3}} THEN 'Q3'
         ELSE                             'Q4: farthest quarter' END AS distance_band,
    CASE WHEN promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                               '5: 36+ days' END AS promised_group,
    CAST(is_late_calendar AS INTEGER)                 AS is_late,
    CAST(is_severe_late AS INTEGER)                   AS is_severe_late,
    CASE WHEN is_late_calendar THEN 'Late' ELSE 'On time' END AS delivery_outcome,
    lead_time_days,
    promised_lead_days,
    promise_error_days,
    CASE days_late_band
        WHEN '<=0' THEN '0. On time'
        WHEN '1-3' THEN '1. 1-3 days late'
        WHEN '4-7' THEN '2. 4-7 days late'
        WHEN '8+'  THEN '3. 8+ days late'
    END                                               AS days_late_band,
    review_row_count,
    review_score,
    CAST(review_before_delivery_flag AS INTEGER)      AS review_before_delivery_flag,
    CAST(review_row_count = 1 AS INTEGER)             AS is_review_p0,
    CAST(review_row_count = 1 AND NOT coalesce(review_before_delivery_flag, FALSE) AS INTEGER) AS is_review_p1
FROM v_single_seller_orders
ORDER BY order_id
