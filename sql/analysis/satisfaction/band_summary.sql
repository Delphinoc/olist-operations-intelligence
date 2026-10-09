-- Review outcomes by lateness severity band (on time = '<=0', then 1-3, 4-7, 8+ calendar days late).
-- Same population and timing parameters as group_summary.sql.
SELECT
    days_late_band,
    count(*)                                    AS n,
    sum(review_score)                           AS sum_score,
    stddev_samp(review_score)                   AS sd_score,
    count(*) FILTER (WHERE review_score <= 2)   AS n_low,
    count(*) FILTER (WHERE review_score = 1)    AS n1,
    count(*) FILTER (WHERE review_score = 2)    AS n2,
    count(*) FILTER (WHERE review_score = 3)    AS n3,
    count(*) FILTER (WHERE review_score = 4)    AS n4,
    count(*) FILTER (WHERE review_score = 5)    AS n5
FROM fact_orders
WHERE is_review_kpi_eligible
  AND (NOT $excl_early    OR NOT review_before_delivery_flag)
  AND (NOT $excl_same_day OR review_creation_date > delivered_date)
GROUP BY 1
ORDER BY 1;
