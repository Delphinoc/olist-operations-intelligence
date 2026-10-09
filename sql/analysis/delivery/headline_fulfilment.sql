-- Fulfilment-class counts (ALL orders, not only delivered) for the same parameterised population as headline.sql.
SELECT f.fulfilment_class, count(*) AS n_orders
FROM fact_orders f
CROSS JOIN model_params p
WHERE f.in_window
  AND f.purchase_date <  $end_excl
  AND (NOT $excl_anom OR NOT f.ts_sequence_violation)
  AND CAST(f.purchase_month + INTERVAL 1 MONTH AS DATE) - 1 + $maturity_days <= p.reference_date
GROUP BY 1
ORDER BY 1;
