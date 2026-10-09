-- Review rows (not orders) of delivered-in-window orders, with the customer for cluster resampling.
-- Used only for the "every review row as a unit" sensitivity.
SELECT r.order_id, f.customer_unique_id, f.is_late_calendar AS is_late, r.review_score
FROM fact_reviews r
JOIN fact_orders f USING (order_id)
WHERE f.is_delivery_kpi_eligible
ORDER BY r.order_id, r.review_id;
