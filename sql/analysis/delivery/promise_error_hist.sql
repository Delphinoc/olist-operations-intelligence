-- Distribution of signed promise error in calendar days (negative = delivered before the promised date).
SELECT promise_error_days, count(*) AS n_orders
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1
ORDER BY 1;
