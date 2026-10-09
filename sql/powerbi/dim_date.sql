-- Power BI table dim_date. GRAIN: one row per calendar day, contiguous from the first month of the data to the last
-- (2016-09-01 .. 2018-10-31) so every order's PURCHASE date has a match. Only the purchase date is ever related to
-- a fact table (estimated and actual delivery dates are deliberately not in the model).
SELECT
    CAST(d AS DATE)                              AS date_key,
    year(d)                                      AS year,
    month(d)                                     AS month_number,
    strftime(d, '%Y-%m')                         AS year_month,
    CAST(date_trunc('month', d) AS DATE)         AS month_start,
    strftime(d, '%b %Y')                         AS month_label,
    year(d) * 100 + month(d)                     AS month_sort,
    CAST(CAST(d AS DATE) >= p.window_start AND CAST(d AS DATE) < p.window_end_exclusive AS INTEGER) AS is_in_window
FROM generate_series(TIMESTAMP '2016-09-01', TIMESTAMP '2018-10-31', INTERVAL 1 DAY) AS t(d)
CROSS JOIN model_params p
ORDER BY date_key
