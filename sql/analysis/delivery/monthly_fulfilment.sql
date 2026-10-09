-- All-order fulfilment outcomes by purchase-month cohort (window). A KPI family separate from delivered-only KPIs.
SELECT
    purchase_month,
    count(*)                                                              AS n_orders,
    count(*) FILTER (WHERE fulfilment_class = 'delivered_on_time')        AS delivered_on_time,
    count(*) FILTER (WHERE fulfilment_class = 'delivered_late')           AS delivered_late,
    count(*) FILTER (WHERE fulfilment_class = 'cancelled_unavailable')    AS cancelled_unavailable,
    count(*) FILTER (WHERE fulfilment_class = 'open_past_promise')        AS open_past_promise,
    count(*) FILTER (WHERE fulfilment_class = 'open_not_yet_due')         AS open_not_yet_due,
    count(*) FILTER (WHERE fulfilment_class = 'delivered_status_no_date') AS delivered_status_no_date
FROM fact_orders
WHERE in_window
GROUP BY purchase_month
ORDER BY purchase_month;
