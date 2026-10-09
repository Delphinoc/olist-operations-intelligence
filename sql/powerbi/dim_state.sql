-- Power BI table dim_state (customer state). GRAIN: one row per state (27). Related to fact_orders and fact_seller_orders
-- on the CUSTOMER state. IBGE macro-region is external display-grouping reference data.
SELECT
    state_code,
    state_name,
    macro_region,
    CASE macro_region WHEN 'North' THEN 1 WHEN 'Northeast' THEN 2 WHEN 'Central-West' THEN 3 WHEN 'Southeast' THEN 4 WHEN 'South' THEN 5 END AS macro_region_order
FROM dim_state
ORDER BY state_code
