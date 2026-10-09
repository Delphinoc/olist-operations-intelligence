-- Original order statuses within each fulfilment class (window, all orders).
SELECT fulfilment_class, order_status, count(*) AS n_orders
FROM fact_orders
WHERE in_window
GROUP BY 1, 2
ORDER BY 1, 3 DESC, 2;
