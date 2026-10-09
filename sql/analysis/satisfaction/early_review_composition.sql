-- Reviews created before the recorded delivery date versus not, by lateness (primary population).
-- Shows who the P1 sensitivity removes.
SELECT
    review_before_delivery_flag                 AS created_before_delivery,
    is_late_calendar                            AS is_late,
    count(*)                                    AS n,
    sum(review_score)                           AS sum_score,
    count(*) FILTER (WHERE review_score <= 2)   AS n_low
FROM fact_orders
WHERE is_review_kpi_eligible
GROUP BY 1, 2
ORDER BY 1, 2;
