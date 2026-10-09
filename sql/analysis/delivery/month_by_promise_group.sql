-- Cohort-month x promised-lead group cell counts (for stratified / standardised comparisons).
SELECT
    purchase_month,
    CASE WHEN promised_lead_days <= 14 THEN '1: <=14 days'
         WHEN promised_lead_days <= 21 THEN '2: 15-21 days'
         WHEN promised_lead_days <= 28 THEN '3: 22-28 days'
         WHEN promised_lead_days <= 35 THEN '4: 29-35 days'
         ELSE                               '5: 36+ days' END AS promised_group,
    count(*)                              AS n_delivered,
    sum(is_late_calendar::INT)            AS n_late
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1, 2
ORDER BY 1, 2;
