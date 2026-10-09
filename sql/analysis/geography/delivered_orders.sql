-- Order-level frame of ALL delivered-in-window orders (incl. multi-seller) for state-level standardisation.
SELECT
    f.order_id,
    f.customer_state,
    s.macro_region,
    f.purchase_month,
    CASE WHEN f.promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN f.promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN f.promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN f.promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                                 '5: 36+ days' END AS promised_group,
    f.is_late_calendar AS is_late
FROM fact_orders f
JOIN dim_state s ON s.state_code = f.customer_state
WHERE f.is_delivery_kpi_eligible
ORDER BY f.order_id;
