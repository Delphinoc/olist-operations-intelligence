-- Power BI table dim_origin_state: a ROLE-SPECIFIC copy of the state dimension for the SELLER (origin) state.
-- GRAIN: one row per state that has at least one eligible single-seller order as origin.
-- A separate table is used (instead of a second, inactive relationship from dim_state) so that "customer state" and
-- "origin state" slicers can never be confused or conflict on the same fact table.
SELECT
    s.state_code                                 AS origin_state_code,
    s.state_name                                 AS origin_state_name,
    s.macro_region                               AS origin_macro_region,
    CASE s.macro_region WHEN 'North' THEN 1 WHEN 'Northeast' THEN 2 WHEN 'Central-West' THEN 3 WHEN 'Southeast' THEN 4 WHEN 'South' THEN 5 END AS origin_macro_region_order
FROM dim_state s
WHERE s.state_code IN (SELECT DISTINCT seller_state FROM v_single_seller_orders)
ORDER BY s.state_code
