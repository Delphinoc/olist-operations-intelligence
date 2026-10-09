-- Quartile cut points of the straight-line seller-customer distance (km), single-seller orders with a distance.
-- Band edges depend only on the distance distribution, never on outcomes.
SELECT
    quantile_cont(distance_km, 0.25) AS q1,
    quantile_cont(distance_km, 0.50) AS q2,
    quantile_cont(distance_km, 0.75) AS q3,
    count(*) FILTER (WHERE distance_km IS NULL) AS n_no_distance,
    count(*)                                    AS n
FROM v_single_seller_orders;
