-- Shipment context by customer state, single-seller orders only (v_single_seller_orders):
-- how many orders ship from another state and how far (straight-line ZIP-prefix centroid distance).
SELECT
    customer_state,
    count(*)                                AS n_single_seller,
    avg(is_cross_state::INT)                AS cross_state_share,
    median(distance_km)                     AS distance_median_km,
    quantile_cont(distance_km, 0.90)        AS distance_p90_km,
    count(*) FILTER (WHERE distance_km IS NULL) AS n_no_distance
FROM v_single_seller_orders
GROUP BY 1
ORDER BY 1;
