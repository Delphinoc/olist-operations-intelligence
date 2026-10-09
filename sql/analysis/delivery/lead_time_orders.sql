-- Order-level lead times for bootstrap intervals (deterministic row order).
SELECT order_id, purchase_month, lead_time_days
FROM fact_orders
WHERE is_delivery_kpi_eligible
ORDER BY order_id;
