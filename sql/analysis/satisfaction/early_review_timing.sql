-- Timing of reviews relative to the recorded delivery date and to the PROMISED date, by lateness band
-- (primary population: delivered in window, exactly one review row). Descriptive only.
-- 'early' = review created strictly before the recorded delivery date (flag from the model).
SELECT
    days_late_band,
    count(*)                                                                         AS n,
    count(*) FILTER (WHERE review_before_delivery_flag)                              AS n_early,
    count(*) FILTER (WHERE review_before_delivery_flag AND review_creation_date <= estimated_date) AS n_early_on_or_before_promise,
    count(*) FILTER (WHERE review_before_delivery_flag AND review_creation_date >  estimated_date) AS n_early_after_promise,
    sum(review_score) FILTER (WHERE review_before_delivery_flag AND review_creation_date <= estimated_date) AS sum_early_on_or_before_promise,
    sum(review_score) FILTER (WHERE review_before_delivery_flag AND review_creation_date >  estimated_date) AS sum_early_after_promise,
    count(*) FILTER (WHERE review_before_delivery_flag AND review_creation_date <= estimated_date AND review_score <= 2) AS low_early_on_or_before_promise,
    count(*) FILTER (WHERE review_before_delivery_flag AND review_creation_date >  estimated_date AND review_score <= 2) AS low_early_after_promise
FROM fact_orders
WHERE is_review_kpi_eligible
GROUP BY 1
ORDER BY 1;
