-- Review outcomes by on-time / late (calendar-date rule). Population: is_review_kpi_eligible (delivered in window,
-- exactly one review row). Parameters select the primary population or the timing sensitivities:
--   $excl_early     BOOLEAN  drop reviews created strictly before the delivery date (sensitivity P1)
--   $excl_same_day  BOOLEAN  also drop reviews created ON the delivery date (stricter stress test, P1b)
SELECT
    is_late_calendar                            AS is_late,
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
