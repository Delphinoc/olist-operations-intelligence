-- Candidate counts per segment. The source subquery below is filled in by the Python code from one of src_state.sql /
-- src_lane.sql / src_seller.sql, so all three levels share identical definitions. Parameter: $exclude_episodes BOOLEAN (drop the three high-delay purchase months).
--
-- Populations
--   delivered : the level's delivery-KPI population (state: delivered in window; lane/seller: single-seller delivered in window)
--   reviewed  : delivered orders with exactly one review row (never the 'latest' of several; review_score is NULL otherwise)
-- Review timing: 'early' = review created strictly before the recorded delivery date; 'after' = on or after it.
-- Segments are not mutually exclusive ACROSS levels (a lane sits inside one state); nothing here is summed across levels.
WITH src AS (
    {{SRC}}
), flagged AS (
    SELECT
        *,
        purchase_month IN (DATE '2017-11-01', DATE '2018-02-01', DATE '2018-03-01') AS is_episode,
        purchase_month <= DATE '2017-10-01'                                         AS is_h1,
        review_row_count = 1                                                        AS has_one_review,
        review_row_count = 1 AND review_score <= 2                                  AS is_low
    FROM src
)
SELECT
    segment,
    count(*)                                                                     AS n_delivered,
    sum(is_late::INT)                                                            AS n_late,
    -- window halves (H1 = purchases 2017-01..2017-10, H2 = 2017-11..2018-08)
    count(*) FILTER (WHERE is_h1)                                                AS n_h1,
    coalesce(sum(is_late::INT) FILTER (WHERE is_h1), 0)                          AS late_h1,
    count(*) FILTER (WHERE NOT is_h1)                                            AS n_h2,
    coalesce(sum(is_late::INT) FILTER (WHERE NOT is_h1), 0)                      AS late_h2,
    -- high-delay months
    count(*) FILTER (WHERE is_episode)                                           AS n_ep,
    coalesce(sum(is_late::INT) FILTER (WHERE is_episode), 0)                     AS late_ep,
    -- reviewed orders and low scores (score <= 2)
    count(*) FILTER (WHERE has_one_review)                                       AS n_rev,
    count(*) FILTER (WHERE is_low)                                               AS n_low,
    count(*) FILTER (WHERE has_one_review AND review_before_delivery_flag)       AS n_rev_early,
    count(*) FILTER (WHERE is_low AND review_before_delivery_flag)               AS n_low_early,
    count(*) FILTER (WHERE has_one_review AND NOT review_before_delivery_flag)   AS n_rev_after,
    count(*) FILTER (WHERE is_low AND NOT review_before_delivery_flag)           AS n_low_after,
    count(*) FILTER (WHERE has_one_review AND NOT is_late)                       AS n_rev_ontime,
    count(*) FILTER (WHERE is_low AND NOT is_late)                               AS n_low_ontime,
    count(*) FILTER (WHERE has_one_review AND is_late)                           AS n_rev_late,
    count(*) FILTER (WHERE is_low AND is_late)                                   AS n_low_late
FROM flagged
WHERE NOT ($exclude_episodes AND is_episode)
GROUP BY segment
ORDER BY segment;
