-- Lateness severity bands (delivered-only, window). Band '8+' equals severe lateness (> 7 calendar days late).
SELECT days_late_band, count(*) AS n_orders
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1
ORDER BY 1;
