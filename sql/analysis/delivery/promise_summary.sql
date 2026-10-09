-- Promise-error and earliness summary (delivered-only, window).
SELECT
    count(*)                                              AS n_delivered,
    avg(promise_error_days)                               AS error_mean,
    quantile_cont(promise_error_days, 0.01)               AS error_p01,
    quantile_cont(promise_error_days, 0.05)               AS error_p05,
    quantile_cont(promise_error_days, 0.25)               AS error_p25,
    median(promise_error_days)                            AS error_p50,
    quantile_cont(promise_error_days, 0.75)               AS error_p75,
    quantile_cont(promise_error_days, 0.95)               AS error_p95,
    quantile_cont(promise_error_days, 0.99)               AS error_p99,
    count(*) FILTER (WHERE promise_error_days = 0)        AS n_on_promised_day,
    count(*) FILTER (WHERE promise_error_days < 0)        AS n_before_promised_day,
    count(*) FILTER (WHERE promise_error_days <= -7)      AS n_7plus_days_early,
    count(*) FILTER (WHERE promise_error_days <= -14)     AS n_14plus_days_early,
    median(-promise_error_days) FILTER (WHERE promise_error_days <= 0)                AS slack_median_on_time,
    quantile_cont(-promise_error_days, 0.25) FILTER (WHERE promise_error_days <= 0)   AS slack_p25_on_time,
    quantile_cont(-promise_error_days, 0.75) FILTER (WHERE promise_error_days <= 0)   AS slack_p75_on_time,
    median(promised_lead_days)                            AS promised_lead_median,
    median(lead_time_days)                                AS lead_median,
    median(lead_time_days / promised_lead_days)           AS lead_to_promise_ratio_median
FROM fact_orders
WHERE is_delivery_kpi_eligible;
