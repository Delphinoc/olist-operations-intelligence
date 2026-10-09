-- Power BI table dim_seller. GRAIN: one row per seller that has at least one eligible single-seller order.
-- Deliberately minimal (id and a short display label): seller attributes that depend on the analysis window
-- (volume tier, signals) live in the fixed-period snapshot table, not here.
SELECT
    seller_id,
    substr(seller_id, 1, 8) AS seller_label
FROM (SELECT DISTINCT seller_id FROM v_single_seller_orders)
ORDER BY seller_id
