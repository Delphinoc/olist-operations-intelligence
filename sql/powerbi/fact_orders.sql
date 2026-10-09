-- Power BI table fact_orders. GRAIN: one row per order (all 99,441 orders; eligibility is a flag, nothing is dropped).
-- Source: fact_orders of the validated DuckDB model. Columns are the minimum the four dashboard pages need;
-- no personal/customer ids and no raw timestamps are exported. Booleans are exported as 0/1 so DAX can SUM/filter them.
-- Labels that must sort in a natural order carry a numeric prefix (ordering without helper tables).
SELECT
    order_id,
    purchase_date,                                   -- the ONLY date used for filtering (relationship to dim_date)
    customer_state,
    order_status,
    fulfilment_class,
    CASE fulfilment_class
        WHEN 'delivered_on_time'         THEN '1. Delivered on time'
        WHEN 'delivered_late'            THEN '2. Delivered late'
        WHEN 'cancelled_unavailable'     THEN '3. Cancelled / unavailable'
        WHEN 'open_past_promise'         THEN '4. Open, past promised date'
        WHEN 'open_not_yet_due'          THEN '5. Open, not yet due'
        WHEN 'delivered_status_no_date'  THEN '6. Delivered status, no date'
    END                                              AS fulfilment_label,
    CAST(in_window AS INTEGER)                       AS in_window,
    CAST(is_delivery_kpi_eligible AS INTEGER)        AS is_delivery_kpi_eligible,   -- delivered with date AND purchased in window
    CAST(is_late_calendar AS INTEGER)                AS is_late,                    -- NULL unless delivered with date
    CAST(is_severe_late AS INTEGER)                  AS is_severe_late,             -- > 7 calendar days late
    CASE WHEN is_late_calendar THEN 'Late' WHEN NOT is_late_calendar THEN 'On time' END AS delivery_outcome,   -- text legend for charts; NULL unless delivered with a date
    lead_time_days,
    promised_lead_days,
    promise_error_days,
    CASE days_late_band
        WHEN '<=0' THEN '0. On time'
        WHEN '1-3' THEN '1. 1-3 days late'
        WHEN '4-7' THEN '2. 4-7 days late'
        WHEN '8+'  THEN '3. 8+ days late'
    END                                              AS days_late_band,
    CASE WHEN promised_lead_days IS NULL THEN NULL
         WHEN promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                               '5: 36+ days' END AS promised_group,
    review_row_count,
    review_score,                                    -- NULL unless exactly one review row
    CAST(review_before_delivery_flag AS INTEGER)     AS review_before_delivery_flag, -- NULL unless one review on a delivered order
    CAST(is_review_kpi_eligible AS INTEGER)          AS is_review_p0,               -- P0: delivered in window, exactly one review row
    CAST(is_review_p1_eligible AS INTEGER)           AS is_review_p1,               -- P1: P0 minus reviews created before delivery
    CAST(is_single_seller AS INTEGER)                AS is_single_seller,
    CAST(ts_sequence_violation AS INTEGER)           AS ts_sequence_violation
FROM fact_orders
ORDER BY order_id
