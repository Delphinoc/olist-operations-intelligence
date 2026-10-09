-- Review outcomes by purchase-month cohort and lateness (primary population).
SELECT
    purchase_month,
    is_late_calendar                            AS is_late,
    count(*)                                    AS n,
    sum(review_score)                           AS sum_score,
    count(*) FILTER (WHERE review_score <= 2)   AS n_low
FROM fact_orders
WHERE is_review_kpi_eligible
GROUP BY 1, 2
ORDER BY 1, 2;
