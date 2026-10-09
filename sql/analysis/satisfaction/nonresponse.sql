-- Review coverage by lateness among ALL delivered-in-window orders (delivery KPI population).
SELECT
    is_late_calendar                                       AS is_late,
    count(*)                                               AS n_total,
    count(*) FILTER (WHERE review_row_count = 1)           AS n_one_review,
    count(*) FILTER (WHERE review_row_count = 0)           AS n_no_review,
    count(*) FILTER (WHERE review_row_count > 1)           AS n_multi_review,
    count(*) FILTER (WHERE review_row_count = 1 AND review_score <= 2) AS n_low_among_one,
    sum(review_score)                                      AS sum_score_among_one
FROM fact_orders
WHERE is_delivery_kpi_eligible
GROUP BY 1
ORDER BY 1;
