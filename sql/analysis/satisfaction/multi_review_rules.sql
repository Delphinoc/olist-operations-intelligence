-- Alternative handling of multi-review orders (sensitivity only). Population: delivered-in-window orders with
-- at least one review row. NO rule is presented as authoritative; 'latest' is included purely for comparison, and the
-- lowest/highest rules bound every other per-order choice. Units are orders unless stated.
-- Columns: rule, is_late, n_units, sum_score, n_low (score <= 2; for the mean rule: mean score <= 2).
WITH rv AS (
    SELECT
        order_id,
        count(*)           AS n_rows,
        min(review_score)  AS min_score,
        max(review_score)  AS max_score,
        avg(review_score)  AS mean_score,
        CASE WHEN count(DISTINCT review_creation_date) = count(*) THEN arg_min(review_score, review_creation_date) END AS earliest_score,
        CASE WHEN count(DISTINCT review_creation_date) = count(*) THEN arg_max(review_score, review_creation_date) END AS latest_score
    FROM fact_reviews
    GROUP BY order_id
), o AS (
    SELECT f.is_late_calendar AS is_late, rv.*
    FROM fact_orders f
    JOIN rv USING (order_id)
    WHERE f.is_delivery_kpi_eligible
)
SELECT '1 primary: single-review orders only' AS rule, is_late, count(*) AS n_units,
       sum(min_score) AS sum_score, count(*) FILTER (WHERE min_score <= 2) AS n_low
FROM o WHERE n_rows = 1 GROUP BY is_late
UNION ALL
SELECT '2 lowest score per order', is_late, count(*), sum(min_score), count(*) FILTER (WHERE min_score <= 2)
FROM o GROUP BY is_late
UNION ALL
SELECT '3 highest score per order', is_late, count(*), sum(max_score), count(*) FILTER (WHERE max_score <= 2)
FROM o GROUP BY is_late
UNION ALL
SELECT '4 mean score per order', is_late, count(*), sum(mean_score), count(*) FILTER (WHERE mean_score <= 2)
FROM o GROUP BY is_late
UNION ALL
SELECT '5 earliest-dated review (orders with distinct dates)', is_late, count(*), sum(earliest_score), count(*) FILTER (WHERE earliest_score <= 2)
FROM o WHERE earliest_score IS NOT NULL GROUP BY is_late
UNION ALL
SELECT '6 latest-dated review (orders with distinct dates; comparison only)', is_late, count(*), sum(latest_score), count(*) FILTER (WHERE latest_score <= 2)
FROM o WHERE latest_score IS NOT NULL GROUP BY is_late
UNION ALL
SELECT '7 every review row as a unit (units = rows)', f.is_late_calendar, count(*), sum(r.review_score), count(*) FILTER (WHERE r.review_score <= 2)
FROM fact_reviews r
JOIN fact_orders f USING (order_id)
WHERE f.is_delivery_kpi_eligible
GROUP BY f.is_late_calendar
ORDER BY 1, 2;
